"""
exports.py — actionable cleanup artefacts from a recommendation queue.

THREE RULES, each learned rather than assumed.

1. THE VERB IS NOT "DELETE".
   Nobody removes metrics from a production observability estate. What
   operators actually do is reversible: exclude from ingestion, drop a
   high-cardinality tag, or move a series to cheaper retention. A
   generated script whose verb is `delete` does not get run — it gets
   forwarded to someone senior and dies there. Every artefact here is
   reversible by construction and says how to reverse it.

2. STATISTICAL REDUNDANCY IS NOT OPERATIONAL REDUNDANCY.
   The engine can prove a metric carries no variance the others lack.
   It cannot see that the same metric is the sole condition on a paging
   monitor, an SLO error budget, or a compliance report. Those live in
   systems a CSV never touched. So destructive artefacts are GATED: the
   caller must supply a blast-radius attestation per metric before the
   export will emit an executable change. Ungated callers get a review
   worksheet instead, which is the artefact they should have asked for
   first anyway.

3. EVERY ARTEFACT CARRIES ITS EVIDENCE.
   A reviewer reading the generated file must be able to see why each
   metric is listed without going back to the web UI — unique variance,
   basis, identity partner, blockers. An export that says only "remove
   these 11" is unauditable, and an unauditable change request is one
   that gets rejected.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from typing import Any

CONTRACT_MIN = "1.0"


class ExportGated(Exception):
    """Raised when an executable artefact is requested without the
    blast-radius attestation it requires. Carries the outstanding
    metrics so the UI can render the checklist."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(
            f"blast-radius attestation missing for {len(missing)} "
            f"metric(s): {', '.join(missing[:5])}"
            + (" ..." if len(missing) > 5 else ""))

    def to_dict(self) -> dict:
        return {"error": "attestation_required",
                "missing_metrics": self.missing,
                "detail": (
                    "Executable exports require confirmation that each "
                    "metric is not referenced by monitors, SLOs, other "
                    "dashboards or runbooks. Request the review "
                    "worksheet first.")}


@dataclass
class Attestation:
    """What the customer confirms before an executable export unlocks.

    Deliberately not a single checkbox. Each field is a distinct place a
    statistically-redundant metric can still be load-bearing, and the
    operator has to have looked at each one.
    """
    metric: str
    referenced_by_monitors: bool
    referenced_by_slos: bool
    referenced_by_other_dashboards: bool
    referenced_by_runbooks: bool
    last_queried_days_ago: int | None = None
    reviewer: str = ""
    note: str = ""

    @property
    def clear(self) -> bool:
        return not (self.referenced_by_monitors or self.referenced_by_slos
                    or self.referenced_by_other_dashboards
                    or self.referenced_by_runbooks)


def _recommended(contract: dict) -> list[dict]:
    return [r for r in contract.get("pruning_queue", [])
            if r.get("recommended")]


def _gate(contract: dict, attestations: dict[str, Attestation] | None
          ) -> list[dict]:
    """Return the rows cleared for an executable export, or raise."""
    rows = _recommended(contract)
    atts = attestations or {}
    missing = [r["metric"] for r in rows if r["metric"] not in atts]
    if missing:
        raise ExportGated(missing)
    cleared = [r for r in rows if atts[r["metric"]].clear]
    return cleared


# ----------------------------------------------------------------------
# 1. review worksheet — the artefact that should come first
# ----------------------------------------------------------------------
def review_worksheet(contract: dict) -> str:
    """CSV for the operator to complete before anything executable.

    One row per candidate, with the statistical evidence pre-filled and
    the operational columns blank. Filling it in IS the blast-radius
    check; the completed file can be uploaded back to unlock exports.
    """
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "metric", "recommendation", "basis", "unique_variance",
        "identity_partner", "identity_r", "keep_as_representative",
        "cluster_id", "blockers",
        # operator fills these in
        "referenced_by_monitors", "referenced_by_slos",
        "referenced_by_other_dashboards", "referenced_by_runbooks",
        "last_queried_days_ago", "reviewer", "note",
    ])
    for r in contract.get("pruning_queue", []):
        rec = ("REMOVE" if r["recommended"]
               else "KEEP (representative)" if r.get("keep_as_representative")
               else f"REVIEW ({r['confidence']})")
        w.writerow([
            r["metric"], rec, r.get("basis", ""), r["unique_variance"],
            r.get("identity_partner") or "", r.get("identity_r") or "",
            r.get("keep_as_representative", False),
            r.get("cluster_id") if r.get("cluster_id") is not None else "",
            "|".join(r.get("blockers", [])),
            "", "", "", "", "", "", "",
        ])
    return buf.getvalue()


def parse_worksheet(text: str) -> dict[str, Attestation]:
    """Read a completed worksheet back into attestations.

    Accepts yes/no/true/false/1/0/y/n, case-insensitive. A blank
    operational cell is treated as UNANSWERED and the metric is left
    out, so a half-filled worksheet cannot silently unlock an export.
    """
    def as_bool(v: str) -> bool | None:
        s = (v or "").strip().lower()
        if s in ("y", "yes", "true", "1"):
            return True
        if s in ("n", "no", "false", "0"):
            return False
        return None

    out: dict[str, Attestation] = {}
    # A worksheet round-tripped through Excel comes back with a BOM,
    # which would otherwise make the first header key "\ufeffmetric"
    # and silently drop every row.
    text = text.lstrip("\ufeff")
    for row in csv.DictReader(io.StringIO(text)):
        name = (row.get("metric") or "").strip()
        if not name:
            continue
        flags = [as_bool(row.get(k, "")) for k in (
            "referenced_by_monitors", "referenced_by_slos",
            "referenced_by_other_dashboards", "referenced_by_runbooks")]
        if any(f is None for f in flags):
            continue                       # unanswered -> not attested
        lq = (row.get("last_queried_days_ago") or "").strip()
        out[name] = Attestation(
            metric=name,
            referenced_by_monitors=flags[0],
            referenced_by_slos=flags[1],
            referenced_by_other_dashboards=flags[2],
            referenced_by_runbooks=flags[3],
            last_queried_days_ago=int(lq) if lq.isdigit() else None,
            reviewer=(row.get("reviewer") or "").strip(),
            note=(row.get("note") or "").strip(),
        )
    return out


# ----------------------------------------------------------------------
# 2. cleaned column manifest — for CSV / warehouse users
# ----------------------------------------------------------------------
def column_manifest(contract: dict) -> str:
    """Keep/drop decision per column, with the reason.

    Safe to generate without attestation: it is a document, not a
    change. Dropping a column from a report is trivially reversible and
    touches no ingestion.
    """
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["column", "action", "reason", "unique_variance",
                "duplicate_of", "cluster_id", "confidence"])
    for r in contract.get("pruning_queue", []):
        if r["recommended"]:
            action = "drop"
            reason = ("exact duplicate" if r.get("basis") == "identity"
                      else "no unique variance")
        elif r.get("keep_as_representative"):
            action = "keep"
            reason = "representative of an identity pair"
        elif r["blockers"]:
            action = "review"
            reason = "; ".join(r["blockers"])
        else:
            action = "keep"
            reason = f"carries {r['unique_variance']:.0%} unique variance"
        w.writerow([r["metric"], action, reason, r["unique_variance"],
                    r.get("identity_partner") or "",
                    r.get("cluster_id") if r.get("cluster_id") is not None
                    else "", r["confidence"]])
    return buf.getvalue()


def keep_list(contract: dict) -> list[str]:
    """Just the column names to retain — for piping into a SELECT."""
    return [r["metric"] for r in contract.get("pruning_queue", [])
            if not r["recommended"]]


# ----------------------------------------------------------------------
# 3. Datadog / Terraform — reversible, gated
# ----------------------------------------------------------------------
def datadog_exclusion_json(contract: dict,
                           attestations: dict[str, Attestation] | None = None,
                           *, namespace: str = "") -> dict:
    """Machine-readable exclusion plan.

    The `format` identifier is namespaced to the ENGINE (`signal-audit`),
    not the distribution (`redd`). Deliberate: the
    protocol string names the thing that produces the semantics, and the
    engine module keeps that name. A package can be renamed or
    re-wrapped without any consumer's parser breaking.

    Emits an EXCLUSION intent, not a deletion. Excluding a metric from
    indexing stops the billing for it while leaving ingestion and the
    raw data path intact, so the change can be undone by removing the
    entry — which is the property that makes it runnable at all.
    """
    cleared = _gate(contract, attestations)
    atts = attestations or {}
    return {
        "format": "signal-audit/datadog-exclusion",
        "version": "1.0",
        "generated_from": {
            "dataset_id": contract.get("dataset_id"),
            "contract_version": contract.get("contract_version"),
            "engine_version": contract.get("engine_version"),
        },
        "action": "exclude_from_indexing",
        "reversible": True,
        "reversal": ("Remove the metric from the exclusion list. No data "
                     "is destroyed by this operation."),
        "metrics": [
            {
                "metric": (f"{namespace}.{r['metric']}" if namespace
                           else r["metric"]),
                "reason": ("duplicate of "
                           f"{r.get('identity_partner')}"
                           if r.get("basis") == "identity"
                           else "no unique variance"),
                "unique_variance": r["unique_variance"],
                "attested_by": atts[r["metric"]].reviewer or None,
                "last_queried_days_ago":
                    atts[r["metric"]].last_queried_days_ago,
            }
            for r in cleared
        ],
        "excluded_from_plan": [
            {"metric": r["metric"],
             "reason": "attested as referenced elsewhere"}
            for r in _recommended(contract)
            if r["metric"] in atts and not atts[r["metric"]].clear
        ],
    }


def datadog_terraform(contract: dict,
                      attestations: dict[str, Attestation] | None = None,
                      *, namespace: str = "",
                      resource_prefix: str = "signal_audit") -> str:
    """Terraform for reversible metric cost control.

    Uses `datadog_metric_tag_configuration` with an empty tag list,
    which collapses a metric to a single billable series rather than
    removing it. That is the lever that actually moves an observability
    bill — cardinality, not metric count — and it is reversible by
    restoring the tags.

    Deliberately NOT generated: any resource whose effect is deletion.
    Terraform will happily destroy things on the next apply, and a
    generated destroy is not a change a reviewer can safely approve.
    """
    cleared = _gate(contract, attestations)
    atts = attestations or {}

    lines = [
        "# Generated by Signal Audit — reversible metric cost control.",
        f"# dataset: {contract.get('dataset_id')}   "
        f"engine: {contract.get('engine_version')}",
        "#",
        "# Each metric below was measured to carry no variance that the",
        "# retained metrics do not already carry, AND was attested by an",
        "# operator as unreferenced by monitors, SLOs, other dashboards",
        "# or runbooks.",
        "#",
        "# This collapses each metric to a single billable series by",
        "# removing its tag configuration. It does NOT delete data.",
        "# To reverse: restore the tags list and re-apply.",
        "#",
        "# REVIEW BEFORE APPLY. Run `terraform plan` and read it.",
        "",
    ]
    if not cleared:
        lines.append("# No metrics cleared for change.")
        return "\n".join(lines) + "\n"

    for r in cleared:
        name = f"{namespace}.{r['metric']}" if namespace else r["metric"]
        safe = "".join(c if c.isalnum() else "_" for c in r["metric"])
        att = atts[r["metric"]]
        basis = ("exact duplicate of " + str(r.get("identity_partner"))
                 if r.get("basis") == "identity"
                 else f"{r['unique_variance']:.1%} unique variance")
        lines += [
            f'# {r["metric"]}: {basis}',
            f'#   attested by: {att.reviewer or "unnamed"}'
            + (f'   last queried: {att.last_queried_days_ago}d ago'
               if att.last_queried_days_ago is not None else ""),
            f'resource "datadog_metric_tag_configuration" '
            f'"{resource_prefix}_{safe}" {{',
            f'  metric_name = "{name}"',
            f'  metric_type = "gauge"   # verify against your metric type',
            f'  tags        = []        # collapse to one billable series',
            "}",
            "",
        ]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
def available_exports(contract: dict,
                      attestations: dict[str, Attestation] | None = None
                      ) -> dict:
    """What the UI may offer right now, and what is still gated."""
    rows = _recommended(contract)
    atts = attestations or {}
    missing = [r["metric"] for r in rows if r["metric"] not in atts]
    return {
        "always_available": ["review_worksheet", "column_manifest",
                             "keep_list"],
        "gated": ["datadog_exclusion_json", "datadog_terraform"],
        "gate_satisfied": not missing and bool(rows),
        "candidates": len(rows),
        "attested": len(rows) - len(missing),
        "missing_attestation": missing,
        "cleared_for_change": sum(
            1 for r in rows if r["metric"] in atts and atts[r["metric"]].clear),
    }
