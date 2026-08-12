"""routing.py — generate collector config that RELOCATES redundant series.

Step 4 of OTEL_INTEGRATION_SPEC.md, and the first thing this project
builds that could change a live system. It is governed by
`SAFETY_BOUNDARIES.md` Amendment 1:

    Redundant telemetry is relocated, not destroyed.

So the generated configuration diverts a series to cheaper storage. It
does not send it nowhere. The undo is "read it back from cold storage",
which exists only because the data still does.

THE PARTITION PROPERTY
======================
The output is two pipelines built from complementary `filter`
processors — one keeping exactly the archived metrics, one excluding
exactly them. Every series therefore has a destination, and that is
checkable by reading the file rather than by trusting this module. A
test asserts the two name lists are complements, because a config that
routed some metrics nowhere would satisfy the letter of "routing" while
destroying data.

`filter` with two pipelines rather than the `routing` connector, because
the partition is visible in the config itself. An operator reviewing the
diff should be able to see that nothing falls through, without knowing
how a connector's default branch behaves.

THE THREE CONDITIONS, IN CODE
=============================
1. Reversible — nothing is deleted; the cold exporter is required, and
   `generate()` refuses without one.
2. Reviewable — this writes a file. It never applies it, and there is no
   code path here that talks to a collector.
3. Attested — a completed blast-radius worksheet, with every operational
   cell answered and a named reviewer, or nothing is generated. The
   attestation is read by `backend/exports.py:parse_worksheet`, the same
   reader the hosted service uses, so the two cannot drift.
"""

import datetime
import os
import sys

__all__ = ["generate", "RoutingRefused"]


class RoutingRefused(Exception):
    """Raised instead of emitting a config that would breach a condition."""


def _parse_worksheet(csv_text):
    """Read attestations using the SAME reader as the hosted service.

    Deliberately not a second implementation. A local copy would drift,
    and the copy that drifted would be the one deciding whether an
    export unlocks.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    backend = os.path.join(here, "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    import exports                                    # noqa: E402
    return exports.parse_worksheet(csv_text)


def _quote(name):
    return '"' + name.replace('\\', '\\\\').replace('"', '\\"') + '"'


def generate(worksheet_csv, payload, cold_exporter, primary_exporter="otlp",
             allow_drop=False, reviewer=None, cost_lines=(), source="audit"):
    """Emit an OpenTelemetry Collector configuration fragment.

    `worksheet_csv` must be COMPLETED. Metrics whose operational cells
    are unanswered are not attested, are not routed, and are listed in
    the header so the omission is visible rather than silent.

    Returns the YAML as a string. Writing it, reviewing it and merging
    it are the operator's business, in that order.
    """
    if not allow_drop and not cold_exporter:
        raise RoutingRefused(
            "no cold-storage exporter given. Amendment 1: redundant "
            "telemetry is relocated, not destroyed — a config that routes "
            "series nowhere is a delete wearing a different name. Pass an "
            "exporter, or pass allow_drop=True and accept that the data "
            "stops existing.")

    candidates = list(payload.get("archive_candidates") or [])
    if not candidates:
        raise RoutingRefused("the audit named no archive candidates; there "
                             "is nothing to route.")

    atts = _parse_worksheet(worksheet_csv)
    attested = [m for m in candidates if m in atts]
    unattested = [m for m in candidates if m not in atts]

    if not attested:
        raise RoutingRefused(
            f"none of the {len(candidates)} archive candidates is attested. "
            f"Every operational cell must be answered and a reviewer named. "
            f"A half-filled worksheet does not unlock an export — that is "
            f"the gate, not an inconvenience.")

    # A metric the reviewer marked as referenced is NOT routed, whatever
    # the arithmetic said. The worksheet is where the human overrules
    # the engine, and if it could not do that it would be a formality.
    referenced, routable = [], []
    for m in attested:
        a = atts[m]
        if (a.referenced_by_monitors or a.referenced_by_slos
                or a.referenced_by_other_dashboards
                or a.referenced_by_runbooks):
            referenced.append(m)
        else:
            routable.append(m)

    if not routable:
        raise RoutingRefused(
            f"all {len(attested)} attested candidate(s) were marked as "
            f"referenced by the reviewer. Nothing to route — which is the "
            f"worksheet doing its job.")

    who = reviewer or next((atts[m].reviewer for m in routable
                            if getattr(atts[m], "reviewer", "")), "UNNAMED")
    stamp = datetime.date.today().isoformat()
    by_name = {m["name"]: m for m in payload.get("metrics", [])}

    L = []
    L.append("# " + "=" * 68)
    L.append("# Generated by Redd Munro — DO NOT APPLY UNREVIEWED")
    L.append("# " + "=" * 68)
    L.append(f"#   source     {source}")
    L.append(f"#   basis      {(payload.get('basis') or {}).get('headline')}"
             f"  ({'declared' if (payload.get('basis') or {}).get('declared') else 'ASSUMED'})")
    L.append(f"#   grade      {(payload.get('assurance') or {}).get('grade')}")
    L.append(f"#   attested   {who} on {stamp}")
    L.append("#")
    if allow_drop:
        L.append("#   EFFECT     *** THESE SERIES STOP EXISTING FROM THE ")
        L.append("#              MOMENT THIS IS APPLIED. There is no undo. ***")
        L.append("#              Requested explicitly with --allow-drop.")
    else:
        L.append(f"#   EFFECT     {len(routable)} metric(s) diverted to "
                 f"'{cold_exporter}'.")
        L.append("#              NOTHING IS DELETED. Every series still has a")
        L.append("#              destination; the two filters below are")
        L.append("#              complements, which you can verify by reading")
        L.append("#              them.")
        L.append(f"#   UNDO       remove this processor and pipeline. History")
        L.append(f"#              remains readable in '{cold_exporter}'.")
    L.append("#")
    L.append("#   ROUTED, with the evidence that justified each:")
    for m in routable:
        info = by_name.get(m, {})
        uv = info.get("unique_variance")
        near = info.get("closest_other")
        r = info.get("closest_r")
        L.append(f"#     {m}")
        L.append(f"#       unique variance {uv}; closest {near} at r={r}")
    if referenced:
        L.append("#")
        L.append("#   NOT ROUTED — the reviewer marked these as referenced,")
        L.append("#   which overrules the arithmetic:")
        for m in referenced:
            L.append(f"#     {m}")
    if unattested:
        L.append("#")
        L.append("#   NOT ROUTED — no completed attestation:")
        for m in unattested:
            L.append(f"#     {m}")
    for line in cost_lines:
        L.append(f"#   {line}")
    L.append("# " + "=" * 68)
    L.append("")

    names = "\n".join(f"          - {_quote(m)}" for m in routable)

    if allow_drop:
        L.append("processors:")
        L.append("  filter/redd_munro_drop:")
        L.append("    metrics:")
        L.append("      exclude:")
        L.append("        match_type: strict")
        L.append("        metric_names:")
        L.append(names)
        L.append("")
        L.append("# Wire filter/redd_munro_drop into your existing metrics")
        L.append("# pipeline. Nothing else changes, and nothing replaces the")
        L.append("# data these series would have carried.")
        return "\n".join(L) + "\n"

    L.append("processors:")
    L.append("  # Keeps ONLY the routed metrics — these go to cold storage.")
    L.append("  filter/redd_munro_cold:")
    L.append("    metrics:")
    L.append("      include:")
    L.append("        match_type: strict")
    L.append("        metric_names:")
    L.append(names)
    L.append("")
    L.append("  # Keeps everything EXCEPT them — unchanged behaviour for")
    L.append("  # every other series. These two lists are complements.")
    L.append("  filter/redd_munro_primary:")
    L.append("    metrics:")
    L.append("      exclude:")
    L.append("        match_type: strict")
    L.append("        metric_names:")
    L.append(names)
    L.append("")
    L.append("service:")
    L.append("  pipelines:")
    L.append("    metrics/primary:")
    L.append("      receivers: [otlp]")
    L.append("      processors: [filter/redd_munro_primary]")
    L.append(f"      exporters: [{primary_exporter}]")
    L.append("    metrics/redd_munro_cold:")
    L.append("      receivers: [otlp]")
    L.append("      processors: [filter/redd_munro_cold]")
    L.append(f"      exporters: [{cold_exporter}]")
    return "\n".join(L) + "\n"


def routed_metrics(yaml_text):
    """The metric names a generated config acts on. For verification."""
    out, grab = [], False
    for line in yaml_text.splitlines():
        s = line.strip()
        if s == "metric_names:":
            grab = True
            continue
        if grab:
            if s.startswith("- "):
                out.append(s[2:].strip().strip('"'))
            else:
                grab = False
    return out
