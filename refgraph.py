"""refgraph.py — where is this metric actually referenced?

The blast-radius worksheet asks a human four questions per metric:
referenced by monitors, by SLOs, by other dashboards, by runbooks. On a
board with forty candidates that is a hundred and sixty look-ups, and
doing them by hand is why nobody completes the worksheet.

This module answers the look-ups from files the customer already has in
git: Prometheus rule YAML and Grafana dashboard JSON. It does not answer
the question.

WHAT THIS IS NOT ALLOWED TO DO
==============================
`SAFETY_BOUNDARIES.md` condition 3: the attestation comes from a person.
So this module reports one of two states and never a third:

    referenced                  found here, with file and line
    not_found_in_scanned_sources    searched these files, did not see it

There is deliberately no "not referenced" and no "safe". The graph can
be stale, can miss a monitor defined in a Terraform repo it was never
pointed at, and is blind to indirect use — a Grafana template variable,
a downstream export, a quarterly report someone runs by hand. Absence of
evidence is evidence of absence only if the search was exhaustive, and
this search never is. `scanned` is reported alongside every answer so
the reviewer can see what was actually looked at.

WHY NOT SUBSTRING MATCHING
==========================
Because `node_load1` appears inside `node_load15`, `up` appears inside
`upstream_latency`, and a metric named in a comment or inside a quoted
string is not a reference to the series. A substring search returns a
confident wrong answer on every one of those, and the wrong answer here
is the dangerous direction: it says "referenced" and nobody investigates
further, or worse it says nothing and a panel gets archived.

So expressions are tokenised. Identifiers are extracted with string
literals and comments removed, label names dropped, and function names
dropped. It is not a full PromQL parser and does not need to be — the
question is which identifiers appear in metric position.

DEPENDENCIES
============
Grafana parsing needs only the standard library. Prometheus rule files
are YAML, and PyYAML is an OPTIONAL extra rather than a dependency:
`signal_audit.py` is numpy-only by design, because that is what lets it
run under Pyodide, and nothing in this module is allowed to erode that.
Import it and you get a clear message rather than a traceback.
"""

import json
import os
import re

__all__ = ["extract_metric_identifiers", "parse_prometheus_rules",
           "parse_grafana_dashboard", "ReferenceGraph", "scan_paths"]


# PromQL reserved words, aggregation modifiers and built-in functions.
# An identifier in one of these positions is not a metric name. The list
# is deliberately generous: a false entry here loses a reference, which
# shows as "not found" and sends a reviewer to look manually — the safe
# direction. Missing an entry adds a spurious metric name, which is
# noise but not dangerous.
PROMQL_KEYWORDS = {
    "by", "without", "on", "ignoring", "group_left", "group_right",
    "offset", "bool", "and", "or", "unless", "if", "default", "start",
    "end", "atan2",
}

PROMQL_FUNCTIONS = {
    "abs", "absent", "absent_over_time", "avg", "avg_over_time", "bottomk",
    "ceil", "changes", "clamp", "clamp_max", "clamp_min", "count",
    "count_over_time", "count_values", "day_of_month", "day_of_week",
    "day_of_year", "days_in_month", "delta", "deriv", "exp", "floor",
    "group", "histogram_quantile", "holt_winters", "hour", "idelta",
    "increase", "irate", "label_join", "label_replace", "last_over_time",
    "ln", "log10", "log2", "max", "max_over_time", "min", "min_over_time",
    "minute", "month", "predict_linear", "present_over_time", "quantile",
    "quantile_over_time", "rate", "resets", "round", "scalar", "sgn",
    "sort", "sort_desc", "sqrt", "stddev", "stddev_over_time", "stdvar",
    "stdvar_over_time", "sum", "sum_over_time", "time", "timestamp",
    "topk", "vector", "year", "rad", "deg", "pi", "mad_over_time",
}

_IDENT = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]*")

# Durations: the unit suffix lexes as a bare identifier, so `rate(x[5m])`
# yielded a metric called `m`. Compound forms like `1h30m` are legal, so
# the whole literal is blanked rather than just the trailing unit.
_DURATION = re.compile(r"\b\d+(?:\.\d+)?(?:ms|[smhdwy])(?:\d+(?:ms|[smhdwy]))*\b")

# Strings first, then comments. Order matters: a '#' inside a quoted
# string is not a comment, and stripping comments first would truncate
# the expression at that point.
_STRING = re.compile(r"""("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`[^`]*`)""")
_COMMENT = re.compile(r"#[^\n]*")


def _strip_noise(expr):
    """Blank out string literals and comments, preserving offsets.

    Replaced with spaces rather than removed so that character positions
    still line up with the original text — a reference reported at the
    wrong column is a reviewer sent to the wrong place.
    """
    def blank(m):
        return " " * len(m.group(0))
    return _DURATION.sub(blank, _COMMENT.sub(blank, _STRING.sub(blank, expr)))


def extract_metric_identifiers(expr):
    """Identifiers appearing in METRIC position in a PromQL expression.

    Returns a set. Excludes: anything inside a string or comment, label
    names (an identifier followed by a matcher operator), function names
    (followed by an open paren), keywords, and the contents of a
    `by (...)` / `without (...)` grouping list, which are label names.
    """
    if not expr:
        return set()
    clean = _strip_noise(strip_grafana_label_args(expr))

    # Label lists after by/without/on/ignoring/group_* are label names.
    # Blank them so their contents cannot be read as metrics.
    def blank_group(m):
        return m.group(1) + " " * (len(m.group(0)) - len(m.group(1)))
    clean = re.sub(r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^()]*\)",
                   blank_group, clean)

    out = set()
    for m in _IDENT.finditer(clean):
        name = m.group(0)
        tail = clean[m.end():]
        stripped = tail.lstrip()
        # function call
        if stripped.startswith("("):
            continue
        # label matcher: name= name=~ name!= name!~ but NOT name==
        if re.match(r"[=!]~|=[^=~]|!=", stripped):
            continue
        if name in PROMQL_KEYWORDS or name in PROMQL_FUNCTIONS:
            continue
        # a bare duration or number that lexed as an identifier
        if re.fullmatch(r"\d+[smhdwy]?", name):
            continue
        out.add(name)
    return out


# Grafana template-variable functions. `label_values(metric, label)`
# takes a LABEL as its second argument, and the tokeniser was reading it
# as a metric — so a variable on `label_values(node_load5, instance)`
# registered a metric called `instance`. Harmless in the reference graph
# (it matches nothing) and actively misleading in the shadow generator,
# which reported it as an operand a query would lose.
_LABEL_VALUES = re.compile(r"\blabel_values\s*\(([^()]*)\)")


def strip_grafana_label_args(expr):
    """Blank the label argument of `label_values`, preserving offsets."""
    if not expr or "label_values" not in expr:
        return expr

    def blank_second(m):
        inner = m.group(1)
        if "," not in inner:
            # label_values(label) — the sole argument is a label name
            return m.group(0)[: m.start(1) - m.start(0)] + " " * len(inner) + ")"
        head, _sep, tail = inner.rpartition(",")
        return (m.group(0)[: m.start(1) - m.start(0)] + head + ","
                + " " * len(tail) + ")")
    return _LABEL_VALUES.sub(blank_second, expr)


def _iter_label_selector_metrics(expr):
    """Metrics named via `{__name__="foo"}` rather than in bare position."""
    out = set()
    for m in re.finditer(r"__name__\s*=\s*\"([^\"]+)\"", expr or ""):
        out.add(m.group(1))
    return out


def parse_prometheus_rules(text, source="<rules>"):
    """Parse a Prometheus rule file.

    Returns a list of dicts with `defines` (the recording rule's output
    metric, or None for an alert), `uses` (metrics referenced in the
    expression), `kind`, `name`, `source` and `line`.

    Recording rules matter in both directions. A rule that defines
    `job:http:rate5m` from `http_requests_total` means the raw metric is
    referenced even if no dashboard mentions it — and archiving it would
    break every alert built on the derived series.
    """
    try:
        import yaml
    except ImportError:
        raise ImportError(
            "Parsing Prometheus rule files needs PyYAML, which is an "
            "optional extra so the engine itself stays numpy-only:\n"
            "    pip install 'redd-munro[refgraph]'\n"
            "Grafana dashboard JSON needs no extra dependency.")

    doc = yaml.safe_load(text) or {}
    lines = text.splitlines()
    out = []
    for group in (doc.get("groups") or []):
        gname = group.get("name", "")
        for rule in (group.get("rules") or []):
            expr = rule.get("expr", "") or ""
            defines = rule.get("record")
            name = rule.get("alert") or defines or "<unnamed>"
            kind = "alert" if rule.get("alert") else "recording_rule"
            line = next((i + 1 for i, l in enumerate(lines)
                         if name and name in l), None)
            uses = extract_metric_identifiers(expr) | _iter_label_selector_metrics(expr)
            uses.discard(defines)          # a rule does not reference itself
            labels = rule.get("labels") or {}
            # A metric behind a paging alert is a different risk from one
            # behind a dashboard panel, and the worksheet should not make
            # a reviewer infer that from the alert's name.
            paging = (str(labels.get("page", "")).lower() in ("true", "yes")
                      or str(labels.get("severity", "")).lower()
                      in ("critical", "page", "sev1", "sev-1"))
            out.append({"kind": kind, "name": name, "group": gname,
                        "defines": defines, "uses": sorted(uses),
                        "source": source, "line": line, "expr": expr,
                        "labels": labels, "paging": bool(paging)})
    return out


def parse_grafana_dashboard(text, source="<dashboard>"):
    """Parse a Grafana dashboard export.

    Returns one entry per panel target. Handles nested rows, and reads
    `expr` (Prometheus), `query` (several datasources) and `rawSql` is
    deliberately NOT parsed — SQL is a different language and guessing
    at it would produce exactly the confident wrong answer this module
    exists to avoid.
    """
    doc = json.loads(text)
    # Grafana wraps exports from the API as {"dashboard": {...}}
    doc = doc.get("dashboard", doc)
    out = []

    def walk(panels, path):
        for p in panels or []:
            title = p.get("title") or "<untitled>"
            here = path + [title]
            if p.get("panels"):
                walk(p["panels"], here)
            for t in (p.get("targets") or []):
                expr = t.get("expr") or t.get("query") or ""
                if not isinstance(expr, str) or not expr.strip():
                    continue
                uses = extract_metric_identifiers(expr) | _iter_label_selector_metrics(expr)
                out.append({"kind": "dashboard_panel",
                            "name": " / ".join(here),
                            "dashboard": doc.get("title") or source,
                            "uses": sorted(uses), "source": source,
                            "line": None, "expr": expr})
        return out

    walk(doc.get("panels"), [])
    # Template variables can hide a metric reference behind a dropdown.
    for v in ((doc.get("templating") or {}).get("list") or []):
        q = v.get("query")
        q = q.get("query") if isinstance(q, dict) else q
        if isinstance(q, str) and q.strip():
            uses = extract_metric_identifiers(q) | _iter_label_selector_metrics(q)
            if uses:
                out.append({"kind": "template_variable",
                            "name": v.get("name", "<var>"),
                            "dashboard": doc.get("title") or source,
                            "uses": sorted(uses), "source": source,
                            "line": None, "expr": q})
    return out


class ReferenceGraph:
    """Which metrics are referenced where, and by what.

    `lookup` returns evidence, never a verdict. The two states are
    `referenced` and `not_found_in_scanned_sources`; there is no
    `unreferenced`, because this module cannot establish that.
    """

    KIND_TO_COLUMN = {
        "alert": "referenced_by_monitors",
        "recording_rule": "referenced_by_monitors",
        "dashboard_panel": "referenced_by_other_dashboards",
        "template_variable": "referenced_by_other_dashboards",
    }

    def __init__(self, entries=(), scanned=()):
        self.entries = list(entries)
        self.scanned = list(scanned)
        self._index = {}
        self._defines = {}
        for e in self.entries:
            if e.get("defines"):
                self._defines.setdefault(e["defines"], []).append(e)
            for m in e.get("uses", ()):
                self._index.setdefault(m, []).append(e)

    def direct(self, metric):
        return list(self._index.get(metric, ()))

    def transitive(self, metric, max_depth=3):
        """References reached through recording rules.

        `http_requests_total` may appear in no dashboard, while a
        recording rule derives `job:http:rate5m` from it and every alert
        uses that. Archiving the raw metric breaks all of them. Depth is
        capped and the cap is reported rather than silently applied.
        """
        seen, out, frontier, depth = {metric}, [], [metric], 0
        truncated = False
        while frontier and depth < max_depth:
            nxt = []
            for m in frontier:
                for e in self._index.get(m, ()):
                    if e not in out:
                        out.append(e)
                    d = e.get("defines")
                    if d and d not in seen:
                        seen.add(d)
                        nxt.append(d)
            frontier, depth = nxt, depth + 1
        if frontier:
            truncated = True
        return out, truncated

    def lookup(self, metric):
        refs, truncated = self.transitive(metric)
        cols = {}
        for e in refs:
            col = self.KIND_TO_COLUMN.get(e["kind"])
            if col:
                where = e["source"] + (f":{e['line']}" if e.get("line") else "")
                cols.setdefault(col, []).append(f"{e['name']} ({where})")
        return {
            "metric": metric,
            "status": "referenced" if refs else "not_found_in_scanned_sources",
            "paging": any(e.get("paging") for e in refs),
            "paging_alerts": sorted({e["name"] for e in refs
                                     if e.get("paging")}),
            "columns": {k: sorted(set(v)) for k, v in cols.items()},
            "reference_count": len(refs),
            "depth_truncated": truncated,
            "scanned": list(self.scanned),
        }


def scan_paths(paths):
    """Build a graph from rule files and dashboard exports on disk.

    Files are classified by extension and content, and anything that
    cannot be parsed is RECORDED as unreadable rather than skipped — a
    rule file that failed to parse is a hole in the search, and the
    reviewer needs to know the hole is there.
    """
    entries, scanned, failed = [], [], []

    # A path that does not exist is a TYPO, not a hole in the search,
    # and the two must not be reported the same way. Silently scanning
    # nothing produces a worksheet whose every cell is empty, which a
    # reviewer reads as "not found in the scanned sources" when in fact
    # nothing was scanned. That is the one direction this module is not
    # allowed to be wrong in, so it refuses rather than continues.
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(
            "reference source(s) do not exist: " + ", ".join(missing)
            + ".\nRefusing to continue: scanning nothing would produce an "
              "empty worksheet that reads as 'no references found'.")

    for path in paths:
        for root, _dirs, files in os.walk(path) if os.path.isdir(path) else [
                (os.path.dirname(path) or ".", [], [os.path.basename(path)])]:
            for fn in sorted(files):
                full = os.path.join(root, fn)
                low = fn.lower()
                try:
                    with open(full, encoding="utf-8") as fh:
                        text = fh.read()
                    if low.endswith(".json"):
                        entries += parse_grafana_dashboard(text, source=full)
                    elif low.endswith((".yml", ".yaml")):
                        entries += parse_prometheus_rules(text, source=full)
                    else:
                        continue
                    scanned.append(full)
                except ImportError:
                    raise
                except Exception as exc:
                    failed.append(f"{full}: {type(exc).__name__}")
    g = ReferenceGraph(entries, scanned)
    g.unreadable = failed
    return g


def annotate_worksheet(csv_text, graph):
    """Fill `scan_evidence`, flag conflicts, and sort them to the top.

    THE CONFLICT is the reason this feature exists. A metric the engine
    marks ARCHIVE — no unique variance, statistically a restatement of
    another column — that a monitor depends on is the one row where
    acting on the arithmetic alone breaks something. Both facts were
    already present, in different columns, and a reviewer had to join
    them mentally. On eleven rows that is fine. On two hundred, sorted
    by unique variance, the row that matters sits in the middle looking
    like every other row.

    So conflicts are named in the cell, not implied by it, and sorted
    first. A paging alert is called out separately from any other
    reference, because those are different risks and a reviewer should
    not have to infer severity from an alert's name.

    The attestation columns are still left untouched — they read as
    yes/no, are parsed as booleans, and belong to a person. See
    SAFETY_BOUNDARIES.md condition 3.
    """
    import csv as _csv
    import io as _io

    rows = list(_csv.reader(_io.StringIO(csv_text)))
    if not rows:
        return csv_text
    header, body = rows[0], rows[1:]
    idx = {name: i for i, name in enumerate(header)}
    if "scan_evidence" not in idx:
        return csv_text
    mcol = idx.get("metric", 0)
    scol = idx["scan_evidence"]
    rcol = idx.get("recommendation")
    ncol = idx.get("note")
    tcol = idx.get("tier")

    label = {"referenced_by_monitors": "monitors",
             "referenced_by_other_dashboards": "dashboards"}

    annotated = []
    for order, row in enumerate(body):
        row = list(row) + [""] * (len(header) - len(row))
        info = graph.lookup(row[mcol])
        archive = (rcol is not None
                   and row[rcol].strip().upper().startswith("ARCHIVE"))
        conflict = archive and info["status"] == "referenced"

        if info["status"] == "referenced":
            parts = []
            for col, hits in sorted(info["columns"].items()):
                shown = "; ".join(hits[:2])
                if len(hits) > 2:
                    shown += f" (+{len(hits) - 2} more)"
                parts.append(f"{label.get(col, col)}: {shown}")
            evidence = " | ".join(parts)
            if conflict:
                what = ("a PAGING alert (" + ", ".join(info["paging_alerts"]) + ")"
                        if info["paging"] else "a live reference")
                evidence = (f"** CONFLICT: engine says ARCHIVE, but this metric "
                            f"is behind {what} ** {evidence}")
                if ncol is not None:
                    row[ncol] = ("CONFLICT - do not archive on the arithmetic "
                                 "alone. The engine can prove this column "
                                 "carries no unique variance; it cannot see "
                                 "that something depends on it. Resolve "
                                 "before attesting.")
            row[scol] = evidence
        else:
            row[scol] = (f"not found in {len(info['scanned'])} scanned "
                         f"source(s) - NOT a clearance; the scan cannot "
                         f"see monitors, SLOs or queries outside them")

        # The tier is rewritten here because only this function knows
        # what was scanned. A conflict becomes C; everything else keeps
        # its A/B letter and swaps the placeholder NO SCAN for the real
        # scope. The count stays in the cell: "not found" is a claim
        # about a search, and a search with no sources found nothing by
        # construction.
        if tcol is not None and row[tcol]:
            import signal_audit as _SA
            letter = row[tcol][:1]
            if conflict:
                what = ("paging alert" if info["paging"] else "live reference")
                row[tcol] = f"{_SA.TIER_C} - {what} found by the scan"
            else:
                base = (_SA.TIER_A if letter == "A" else _SA.TIER_B)
                n = len(info["scanned"])
                row[tcol] = (f"{base} - not found in {n} scanned source(s)"
                             if n else f"{base} - {_SA.TIER_UNSCANNED}")

        # sort key: conflicts first, paging conflicts before the rest,
        # then tier, then original order (ascending unique variance)
        import signal_audit as _SA2
        annotated.append(((0 if conflict else 1,
                           0 if (conflict and info["paging"]) else 1,
                           _SA2.worksheet_sort_key(row[tcol])
                           if tcol is not None else 0,
                           order), row))

    annotated.sort(key=lambda t: t[0])
    out = _io.StringIO()
    w = _csv.writer(out)
    w.writerow(header)
    for _k, row in annotated:
        w.writerow(row)
    return out.getvalue()


def replace_metric_identifier(expr, old, new):
    """Rename a metric where it appears in METRIC position, only.

    Used by the shadow-dashboard generator to simulate a metric being
    gone: substituting a name that will not resolve makes the panel fail
    in the shadow exactly as it would once the metric is archived, while
    the real dashboard is untouched.

    Relies on `_strip_noise` blanking rather than deleting, so offsets in
    the cleaned text still index the original. A comment, a string
    literal, a label name and `node_load15` are all left alone; the same
    discipline as the reference scan, for the same reason.
    """
    if not expr or not old:
        return expr, 0
    clean = _strip_noise(strip_grafana_label_args(expr))

    def blank_group(m):
        return m.group(1) + " " * (len(m.group(0)) - len(m.group(1)))
    clean = re.sub(r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^()]*\)",
                   blank_group, clean)

    spans = []
    for m in _IDENT.finditer(clean):
        if m.group(0) != old:
            continue
        tail = clean[m.end():].lstrip()
        if tail.startswith("("):
            continue
        if re.match(r"[=!]~|=[^=~]|!=", tail):
            continue
        spans.append((m.start(), m.end()))

    out, last, n = [], 0, 0
    for a, b in spans:
        out.append(expr[last:a])
        out.append(new)
        last, n = b, n + 1
    out.append(expr[last:])
    result = "".join(out)

    # `{__name__="x"}` is a metric reference too, and lives inside a
    # string, so the tokeniser above cannot see it.
    pat = r'(__name__\s*=\s*")' + re.escape(old) + r'(")'
    result, k = re.subn(pat, r"\g<1>" + new + r"\g<2>", result)
    return result, n + k
