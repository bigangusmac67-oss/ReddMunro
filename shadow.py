"""shadow.py — a dashboard that simulates the archive, before you do it.

Takes a Grafana dashboard export and a list of metrics an audit proposes
archiving, and emits a SECOND dashboard in which those metrics do not
resolve. Load both side by side against live traffic and the difference
is visible before anything is changed.

WHAT THIS PROVES, AND WHAT IT DOES NOT
======================================
It proves a STRUCTURAL claim: that the remaining panels still render,
that template variables still populate, and that no query has lost an
operand. That is checkable, it is the thing an engine reading a CSV of
values cannot see, and it is the failure that actually bites — a panel
computing `rate(a) / rate(b)` breaks when `b` goes, however redundant
`b` was.

It does NOT prove that no information was lost. Two metrics identical
during healthy traffic are exactly the pair that diverges during the
incident the metric was kept for, and a shadow that matched for a
fortnight says nothing about that. Worse, the audit measured
correlation over the same window, so "the shadow matched" and "the
engine called it redundant" are the SAME evidence counted twice, not
independent confirmation.

So the generated dashboard is titled `[Shadow — structural check only]`
and carries a panel saying this, at the top, where the person who opens
it will read it before drawing a conclusion.

WHY SUBSTITUTION RATHER THAN DELETION
=====================================
Removing the target would produce a dashboard that renders perfectly,
because the metric still exists in the datasource — the archive has not
happened yet. That would demonstrate nothing while looking like proof.

So the metric name is replaced with a sentinel that will not resolve.
The shadow then fails exactly where the real board would fail after the
archive, and the failure is visible rather than argued about.

SAFETY
======
This writes a new dashboard. It modifies nothing, and the undo is
deleting the file. Under SAFETY_BOUNDARIES.md it is the least dangerous
artefact here — which is also why it is worth having before anything
that changes a live system.
"""

import copy
import json

import refgraph as RG

__all__ = ["build_shadow", "SENTINEL_PREFIX"]

SENTINEL_PREFIX = "redd_munro_ARCHIVED__"


def _sentinel(name):
    return SENTINEL_PREFIX + name


def _targets(panel):
    return panel.get("targets") or []


def _expr_of(t):
    for key in ("expr", "query"):
        v = t.get(key)
        if isinstance(v, str) and v.strip():
            return key, v
    return None, None


def build_shadow(dashboard_json, archived, title_suffix="[Shadow — structural check only]"):
    """Return (shadow_dashboard_dict, report).

    `report` classifies every affected target:

        sole_operand      the query was only this metric. The panel goes
                          empty, which is the expected, harmless case.
        expression_operand
                          the metric is one of several identifiers. The
                          panel BREAKS — this is the finding, and it is
                          invisible to an audit of values.
        variable          a template variable no longer resolves, so
                          every panel using it is affected. The worst
                          case, because it cascades.
    """
    doc = json.loads(dashboard_json) if isinstance(dashboard_json, str) \
        else copy.deepcopy(dashboard_json)
    doc = copy.deepcopy(doc.get("dashboard", doc))
    archived = list(archived)

    findings = []

    def rewrite(expr, where, kind_hint):
        """Substitute every archived metric, and classify the damage."""
        names = (RG.extract_metric_identifiers(expr)
                 | RG._iter_label_selector_metrics(expr))
        hit = [a for a in archived if a in names]
        if not hit:
            return expr, None
        others = sorted(names - set(hit))
        new = expr
        for a in hit:
            new, _n = RG.replace_metric_identifier(new, a, _sentinel(a))
        # A query mentioning ONLY archived metrics goes empty. One that
        # also references something else has lost an operand, and an
        # expression missing an operand is a broken panel, not a thinner
        # one.
        kind = ("sole_operand" if not others else "expression_operand")
        if kind_hint == "variable":
            kind = "variable"
        findings.append({
            "where": where, "kind": kind, "metrics": hit,
            "other_operands": others, "before": expr, "after": new,
        })
        return new, kind

    def walk(panels, path):
        for p in panels or []:
            title = p.get("title") or "<untitled>"
            here = path + [title]
            if p.get("panels"):
                walk(p["panels"], here)
            worst = None
            for t in _targets(p):
                key, expr = _expr_of(t)
                if not key:
                    continue
                new, kind = rewrite(expr, " / ".join(here), None)
                if kind:
                    t[key] = new
                    if kind == "expression_operand":
                        worst = "expression_operand"
                    elif worst is None:
                        worst = "sole_operand"
            if worst == "expression_operand":
                p["title"] = "[BREAKS] " + title
            elif worst == "sole_operand":
                p["title"] = "[EMPTY] " + title

    walk(doc.get("panels"), [])

    for v in ((doc.get("templating") or {}).get("list") or []):
        q = v.get("query")
        raw = q.get("query") if isinstance(q, dict) else q
        if not isinstance(raw, str) or not raw.strip():
            continue
        new, kind = rewrite(raw, f"variable ${v.get('name', '?')}", "variable")
        if kind:
            if isinstance(q, dict):
                v["query"]["query"] = new
            else:
                v["query"] = new
            v["name"] = v.get("name", "var")

    # A new identity, so importing this can never overwrite the original.
    doc["title"] = f"{doc.get('title', 'dashboard')} {title_suffix}"
    doc["uid"] = None
    doc["id"] = None
    doc["version"] = 0

    breaks = [f for f in findings if f["kind"] == "expression_operand"]
    variables = [f for f in findings if f["kind"] == "variable"]
    empties = [f for f in findings if f["kind"] == "sole_operand"]

    banner = {
        "type": "text",
        "title": "What this dashboard does and does not establish",
        "gridPos": {"h": 6, "w": 24, "x": 0, "y": 0},
        "options": {"mode": "markdown", "content": _banner_text(
            archived, empties, breaks, variables)},
    }
    doc["panels"] = [banner] + list(doc.get("panels") or [])

    return doc, {
        "archived": archived,
        "empty_panels": len(empties),
        "broken_panels": len(breaks),
        "broken_variables": len(variables),
        "findings": findings,
        "structural_only": True,
    }


def _banner_text(archived, empties, breaks, variables):
    lines = [
        "## Shadow dashboard — structural check only",
        "",
        "Metrics an audit proposed archiving have been replaced here with "
        "names that do not resolve. **This board simulates the archive. "
        "The real one is untouched.**",
        "",
        f"- `[EMPTY]` panels ({len(empties)}) lost their only query. "
        "Expected, and harmless.",
        f"- **`[BREAKS]` panels ({len(breaks)}) lost an operand from an "
        "expression.** These are the finding: the query is now malformed, "
        "and no audit of values could have told you.",
        f"- Broken template variables ({len(variables)}) cascade to every "
        "panel that uses them.",
        "",
        "### What this does NOT prove",
        "",
        "That no information was lost. Two metrics identical during healthy "
        "traffic are exactly the pair that diverges during the incident the "
        "metric was kept for. The audit measured correlation over the same "
        "window this dashboard renders, so agreement between them is the "
        "same evidence twice — not independent confirmation.",
        "",
        "Archived in this shadow: " + ", ".join(f"`{a}`" for a in archived),
    ]
    return "\n".join(lines)


def report_lines(report):
    """Human-readable summary, for the terminal."""
    out = [f"{report['empty_panels']} panel(s) go empty · "
           f"{report['broken_panels']} panel(s) BREAK · "
           f"{report['broken_variables']} template variable(s) break"]
    for f in report["findings"]:
        if f["kind"] == "sole_operand":
            continue
        tag = "BREAKS" if f["kind"] == "expression_operand" else "VARIABLE"
        out.append(f"  [{tag}] {f['where']}")
        if f["kind"] == "variable":
            out.append(f"      archiving {', '.join(f['metrics'])} stops "
                       f"this variable populating — every panel using it "
                       f"is affected, which is why a variable break is the "
                       f"worst case")
        else:
            out.append(f"      archiving {', '.join(f['metrics'])} leaves "
                       f"{', '.join(f['other_operands'])} without its "
                       f"operand")
        out.append(f"      {f['before'][:90]}")
    return out
