"""cardinality.py — how many billable series is each metric actually?

Step 1 of OTEL_INTEGRATION_SPEC.md. The engine measures redundancy
BETWEEN metrics, from a CSV of values. Observability bills are driven by
cardinality WITHIN a metric — name x label combinations — and a CSV has
been stripped of exactly that. This module supplies the missing half.

TWO AXES, AND THEY ARE NOT THE SAME PROBLEM
===========================================
    redundancy   two metrics carry the same information   -> archive one
    cardinality  one metric emits 48,000 series           -> drop a label

The second is not what Redd Munro was built to find, and this module
will surface it constantly, because an unbounded label is the usual
cause of a large bill. Reporting them together without distinguishing
them would send operators to delete a metric when the fix was to stop
labelling it by request path. So `classify()` names which axis a finding
sits on, and the report never adds the two into one number.

WHY NOT A COLLECTOR PROCESSOR (YET)
===================================
A real OpenTelemetry Collector processor is written in Go. That is a new
language, a new build toolchain and a second implementation of the
counting logic, committed to before anyone has checked whether the cost
arithmetic downstream of it is worth having.

This reads what the operator already has: the Prometheus text exposition
format that every exporter serves, or the JSON from a `count by
(__name__)` query they run themselves. Identical arithmetic, no new
toolchain, and it works against Prometheus, Thanos, Mimir, Victoria and
anything else speaking that format. If it earns a place inline, the Go
processor can be written against a counting model that has already been
validated.

EGRESS
======
This module performs NO network access. It parses text and JSON handed
to it. Fetching is the caller's business, and `--from-url` in the CLI is
the operator's own machine talking to the operator's own Prometheus.
`test_signal_audit.py` asserts the absence of every networking import,
because "runs locally" stops being structural the moment this code could
open a socket (see SAFETY_BOUNDARIES.md Amendment 1).
"""

import json
import re
from collections import defaultdict

__all__ = ["parse_exposition", "parse_promql_series_count",
           "CardinalityReport", "classify"]


# name{label="value",...}  value   [timestamp]
# Label values may contain commas, braces and escaped quotes, so the
# label block is parsed by scanning rather than by splitting on ','.
_SAMPLE = re.compile(r"^\s*([A-Za-z_:][A-Za-z0-9_:]*)\s*(\{.*\})?\s+(.+)$")
_LABEL = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"((?:[^"\\]|\\.)*)"')

# Suffixes the Prometheus client libraries append to a single logical
# metric. `http_latency_bucket`, `_sum` and `_count` are one histogram,
# not three metrics, and counting them separately triples the apparent
# metric count while understating each one's series.
_SUFFIXES = ("_bucket", "_sum", "_count", "_total", "_created")


def _split_labels(block):
    """Label pairs from a `{...}` block, tolerating commas in values."""
    if not block:
        return {}
    return {m.group(1): m.group(2) for m in _LABEL.finditer(block)}


def parse_exposition(text):
    """Count series from Prometheus text exposition format.

    Returns {metric_name: {"series": int, "labels": {key: distinct}}}.

    Every distinct label combination is one billable series, which is
    the quantity a vendor charges for and the quantity an operator
    cannot see from a dashboard export.
    """
    seen = defaultdict(set)
    label_vals = defaultdict(lambda: defaultdict(set))
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _SAMPLE.match(s)
        if not m:
            continue
        name, block, _val = m.group(1), m.group(2), m.group(3)
        labels = _split_labels(block)
        # `le` and `quantile` are histogram/summary buckets: real series,
        # but they belong to the parent metric rather than indicating an
        # independent cardinality problem.
        key = tuple(sorted(labels.items()))
        seen[name].add(key)
        for k, v in labels.items():
            label_vals[name][k].add(v)
    return {name: {"series": len(combos),
                   "labels": {k: len(vs)
                              for k, vs in sorted(label_vals[name].items())}}
            for name, combos in sorted(seen.items())}


def parse_promql_series_count(payload):
    """Read `count by (__name__)({__name__=~".+"})` output.

    Accepts the Prometheus HTTP API JSON, or the same structure already
    decoded. This is the query an operator runs themselves and pastes;
    nothing here fetches it.
    """
    doc = json.loads(payload) if isinstance(payload, str) else payload
    result = (doc.get("data") or {}).get("result", doc.get("result", []))
    out = {}
    for row in result:
        name = (row.get("metric") or {}).get("__name__")
        val = row.get("value") or [None, None]
        if not name:
            continue
        try:
            out[name] = {"series": int(float(val[1])), "labels": {}}
        except (TypeError, ValueError):
            continue
    return out


def base_name(metric):
    """Strip a client-library suffix, so a histogram counts once."""
    for suf in _SUFFIXES:
        if metric.endswith(suf) and len(metric) > len(suf):
            return metric[: -len(suf)]
    return metric


class CardinalityReport:
    """Series counts, with the scope of the measurement attached.

    `scope` is not decoration. A collector or a Prometheus instance sees
    what flows through IT, over the window it was asked about. A vendor
    bills for distinct series across every ingestion path over a billing
    period, under their own definition of "custom". Those differ, the
    difference runs in both directions, and a saving figure quoted
    without the scope is the kind of confident wrong number this project
    exists to catch.
    """

    def __init__(self, metrics, scope, window=None, exact=True):
        self.metrics = dict(metrics)
        self.scope = scope
        self.window = window
        self.exact = bool(exact)

    @property
    def total_series(self):
        return sum(m["series"] for m in self.metrics.values())

    def series(self, name):
        if name in self.metrics:
            return self.metrics[name]["series"]
        # a CSV column is often one series of a labelled metric
        base = base_name(name)
        return self.metrics.get(base, {}).get("series")

    def worst_label(self, name):
        """The label key with the most distinct values, and how many."""
        info = self.metrics.get(name) or self.metrics.get(base_name(name))
        if not info or not info.get("labels"):
            return None, None
        k, v = max(info["labels"].items(), key=lambda kv: kv[1])
        return k, v

    def summary(self):
        return {
            "scope": self.scope,
            "window": self.window,
            "exact": self.exact,
            "metrics_counted": len(self.metrics),
            "total_series": self.total_series,
        }


# A metric emitting more series than this is a cardinality finding in
# its own right, whatever the audit says about redundancy. The number is
# a reporting threshold, not a claim about anyone's bill.
HIGH_CARDINALITY = 1000


def classify(payload, report):
    """Join an audit payload to a cardinality report, on two axes.

    Returns one row per metric with `redundant`, `high_cardinality` and
    a `quadrant`, deliberately kept apart. The interesting quadrant is
    `redundant_and_expensive`, and it is usually the smallest — which is
    the point. An operator shown one blended "waste score" would act on
    the largest number, and the largest number is normally an unbounded
    label on a metric nothing else duplicates.
    """
    archive = set(payload.get("archive_candidates") or [])
    rows = []
    for m in payload.get("metrics", []):
        name = m["name"]
        s = report.series(name)
        redundant = name in archive
        expensive = bool(s is not None and s >= HIGH_CARDINALITY)
        lk, lv = report.worst_label(name)
        if redundant and expensive:
            quad, advice = ("redundant_and_expensive",
                            "archive this metric: it duplicates another AND "
                            "carries real cost")
        elif redundant:
            quad, advice = ("redundant_only",
                            "duplicates another metric, but costs little; "
                            "archive for clarity, not for the bill")
        elif expensive:
            quad, advice = ("expensive_only",
                            f"NOT redundant — do not archive. The cost is "
                            f"cardinality: label '{lk}' has {lv} distinct "
                            f"values. Drop the label, keep the metric."
                            if lk else
                            "NOT redundant — do not archive. The cost is "
                            "cardinality within this metric, not duplication.")
        else:
            quad, advice = ("neither", "")
        rows.append({
            "metric": name,
            "series": s,
            "series_known": s is not None,
            "unique_variance": m.get("unique_variance"),
            "redundant": redundant,
            "high_cardinality": expensive,
            "worst_label": lk,
            "worst_label_values": lv,
            "quadrant": quad,
            "advice": advice,
        })
    rows.sort(key=lambda r: (r["quadrant"] != "redundant_and_expensive",
                             -(r["series"] or 0)))
    return {
        "scope": report.scope,
        "window": report.window,
        "exact": report.exact,
        "unmatched": [r["metric"] for r in rows if not r["series_known"]],
        "rows": rows,
    }


# ---------------------------------------------------------------------
# Step 3 — cost. Arithmetic, not estimation.
# ---------------------------------------------------------------------
#
# `API_CONTRACT.md` already fixed the rule this obeys:
#
#   `estimated_monthly_saving` appears in every contract but is NULL
#   unless the customer supplies a unit cost.
#
# Nothing below invents a price, infers one from a vendor name, or
# carries a default. The operator supplies one number they can read off
# their own invoice, and every figure here is that number multiplied by
# a series count this module actually observed. Where a term is unknown
# the result is None rather than a guess.


class Price:
    """A unit cost the OPERATOR supplied, with its own provenance.

    Two ways to give it, because the second is how anybody actually
    knows their number:

        Price(per_series_month=0.05)
        Price.from_invoice(total=4100, series=82000)   # -> 0.05

    `currency` is a label. No conversion happens, because a conversion
    would be a rate we invented.
    """

    def __init__(self, per_series_month, currency="GBP", source="stated"):
        self.per_series_month = float(per_series_month)
        self.currency = currency
        self.source = source

    @classmethod
    def from_invoice(cls, total, series, currency="GBP"):
        if not series:
            raise ValueError("cannot derive a unit price from zero series")
        return cls(float(total) / float(series), currency,
                   source=f"derived from {currency}{total:,.2f} over "
                          f"{int(series):,} series")

    def of(self, series):
        return None if series is None else series * self.per_series_month


def cost(classification, price=None):
    """Attach money to a classification, or explain why it cannot.

    Returns the same rows with `monthly_cost`, plus a summary that
    separates the two axes — because they are two different actions and
    adding them produces a number no single change can deliver.

    ARCHIVING a redundant metric removes its series.
    DROPPING A LABEL on an expensive metric collapses its series to
    roughly the number of remaining label combinations, which this
    module cannot know without being told. So that saving is reported as
    an UPPER BOUND and labelled as one.
    """
    rows = [dict(r) for r in classification["rows"]]
    if price is None:
        for r in rows:
            r["monthly_cost"] = None
        return {
            **classification, "rows": rows, "priced": False,
            "currency": None, "unit_price": None,
            "why_null": ("no unit price supplied. Series counts are "
                         "measured; the price is not, and this tool does "
                         "not carry vendor price lists because the number "
                         "that matters is the one on YOUR invoice, after "
                         "commitments and overage bands."),
            "archivable_saving": None, "label_saving_upper_bound": None,
        }

    for r in rows:
        r["monthly_cost"] = price.of(r["series"])

    archivable = sum(r["monthly_cost"] or 0 for r in rows
                     if r["quadrant"] in ("redundant_and_expensive",
                                          "redundant_only"))
    label_bound = sum(r["monthly_cost"] or 0 for r in rows
                      if r["quadrant"] == "expensive_only")
    return {
        **classification, "rows": rows, "priced": True,
        "currency": price.currency,
        "unit_price": price.per_series_month,
        "price_source": price.source,
        "why_null": None,
        # Deliverable by archiving. Every term measured or supplied.
        "archivable_saving": archivable,
        # NOT deliverable by archiving, and not fully deliverable at all:
        # dropping a label collapses a metric to some smaller number of
        # series, not to zero. Upper bound, named as one.
        "label_saving_upper_bound": label_bound,
        "unpriced_metrics": [r["metric"] for r in rows
                             if r["monthly_cost"] is None],
    }


def cost_lines(priced):
    """The working, as text. Every number traceable to an input.

    A saving figure without its derivation is the number that ends up
    in a business case and gets quoted to a CFO. If it is wrong the
    account is gone, so it is shown as arithmetic rather than asserted.
    """
    out = []
    if not priced.get("priced"):
        return [f"No cost shown: {priced['why_null']}"]
    cur = priced["currency"]
    out.append(f"unit price      {cur}{priced['unit_price']:.6f} "
               f"per series per month  ({priced['price_source']})")
    out.append(f"scope           {priced['scope']}")
    n_arch = sum(1 for r in priced["rows"]
                 if r["quadrant"].startswith("redundant"))
    out.append("")
    out.append(f"archiving {n_arch} redundant metric(s) removes "
               f"{sum(r['series'] or 0 for r in priced['rows'] if r['quadrant'].startswith('redundant')):,}"
               f" series")
    out.append(f"  = {cur}{priced['archivable_saving']:,.2f} per month, "
               f"deliverable by the archive itself")
    if priced["label_saving_upper_bound"]:
        n_exp = sum(1 for r in priced["rows"]
                    if r["quadrant"] == "expensive_only")
        out.append("")
        out.append(f"{n_exp} costly metric(s) that are NOT redundant hold "
                   f"{cur}{priced['label_saving_upper_bound']:,.2f} per month")
        out.append("  = an UPPER BOUND, and not archivable. Dropping a label "
                   "collapses these to")
        out.append("    some smaller number of series, not to zero. How much "
                   "smaller depends")
        out.append("    on which label is dropped, which this tool has not "
                   "been told.")
    if priced.get("unpriced_metrics"):
        out.append("")
        out.append(f"{len(priced['unpriced_metrics'])} audited metric(s) had "
                   f"no series count and are excluded from every figure "
                   f"above: {', '.join(priced['unpriced_metrics'][:5])}")
    return out
