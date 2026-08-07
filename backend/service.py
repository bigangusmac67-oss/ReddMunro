"""
service.py — framework-agnostic service layer over signal_audit.

Turns an audit into a stable, versioned JSON contract suitable for a
frontend, and records the usage metadata needed for tiered billing.

DESIGN RULE: this module never modifies `signal_audit.py`. The engine is
imported and called, never edited. Its 32 validation tests are the
guarantee that the analysis is correct, and that guarantee is worth
more than any convenience gained by reaching into it. If the contract
needs a number the engine does not expose, the engine gains a function
and a test — it does not gain a special case for the API.

Layering:

    signal_audit.py     analysis engine (pure, no I/O beyond CSV read)
    service.py          contract + metering + policy   <- you are here
    jobs.py             async execution
    app.py              HTTP transport (FastAPI)

Nothing below `service.py` knows about HTTP; nothing above it knows
about eigenvalues.
"""

from __future__ import annotations

import csv
import hashlib
import math
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any

import signal_audit as SA
import cost as _COST

CONTRACT_VERSION = "1.1"   # 1.1 adds `basis` and `basis_conflicts` — additive only
ENGINE_VERSION = SA.__version__


# ----------------------------------------------------------------------
# tiers
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Tier:
    """A subscription band.

    Limits are expressed on the two axes that actually drive cost:
    metric count N (the analysis is O(N^2) pairwise and O(N^3) if the
    triadic extension lands) and row depth M (linear, but it is the
    axis that decides whether a result is statistically usable at all).

    `max_cells` exists because neither axis alone bounds the work: 500
    metrics x 100 rows and 10 metrics x 500k rows are both pathological
    in different ways.
    """
    name: str
    max_metrics: int
    max_rows: int
    max_cells: int
    max_audits_per_month: int
    async_only_above_cells: int = 200_000
    retention_days: int = 30


TIERS: dict[str, Tier] = {
    # Starter's cell cap (100k) sits below the async threshold on
    # purpose: every audit a Starter account can legally submit fits
    # inside a request, so that tier never needs the queue and never
    # shows a customer a polling state. The threshold is stated
    # explicitly rather than left to the default so the intent survives
    # a future change to either number.
    "starter": Tier("starter", max_metrics=25, max_rows=10_000,
                    max_cells=100_000, max_audits_per_month=50,
                    async_only_above_cells=100_001, retention_days=7),
    "pro": Tier("pro", max_metrics=150, max_rows=250_000,
                max_cells=5_000_000, max_audits_per_month=1_000,
                retention_days=90),
    "unlimited": Tier("unlimited", max_metrics=100_000,
                      max_rows=100_000_000, max_cells=2_000_000_000,
                      max_audits_per_month=1_000_000,
                      retention_days=365),
}


class QuotaExceeded(Exception):
    """Raised before any analysis runs. Carries structured detail so the
    API can render an upgrade prompt naming the specific limit hit."""

    def __init__(self, limit: str, actual: int, allowed: int, tier: str):
        self.limit, self.actual, self.allowed, self.tier = (
            limit, actual, allowed, tier)
        super().__init__(
            f"{limit} limit exceeded for tier '{tier}': "
            f"{actual:,} > {allowed:,}")

    def to_dict(self) -> dict:
        return {"error": "quota_exceeded", "limit": self.limit,
                "actual": self.actual, "allowed": self.allowed,
                "tier": self.tier}


class InvalidInput(Exception):
    """Input the engine legitimately refuses (too few columns, too few
    rows, unparseable). Distinguished from QuotaExceeded because one is
    a billing event and the other is a user error."""

    def to_dict(self) -> dict:
        return {"error": "invalid_input", "detail": str(self)}


# ----------------------------------------------------------------------
# usage
# ----------------------------------------------------------------------
@dataclass
class Usage:
    """Recorded per audit, whether or not it succeeded.

    `billable_cells` is the metering unit. It is computed from the data
    ACTUALLY analysed (post column-drop, post listwise deletion), not
    from the uploaded file, so a customer is never charged for columns
    the engine discarded. That asymmetry is deliberate and should
    survive any future pricing change.
    """
    metrics: int = 0
    rows: int = 0
    billable_cells: int = 0
    uploaded_bytes: int = 0
    columns_supplied: int = 0
    columns_analysed: int = 0
    rows_dropped: int = 0
    duration_ms: int = 0
    engine_version: str = ENGINE_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


def measure(path: str, ignore=()) -> tuple[int, int, int]:
    """Cheap pre-flight: (metrics, rows, cells) without running the
    analysis. Used to enforce quota BEFORE spending compute, which is
    the whole point of having a quota."""
    names, M, _ = SA.load_csv(path, ignore=ignore)
    return len(names), len(M), len(names) * len(M)


def enforce(tier: Tier, metrics: int, rows: int) -> None:
    cells = metrics * rows
    if metrics > tier.max_metrics:
        raise QuotaExceeded("metrics", metrics, tier.max_metrics, tier.name)
    if rows > tier.max_rows:
        raise QuotaExceeded("rows", rows, tier.max_rows, tier.name)
    if cells > tier.max_cells:
        raise QuotaExceeded("cells", cells, tier.max_cells, tier.name)


# ----------------------------------------------------------------------
# pruning queue
# ----------------------------------------------------------------------
# Confidence bands. A pruning recommendation is an instruction to delete
# something a customer is paying to collect, so the contract carries the
# reasons NOT to act as first-class data rather than as a footnote the
# frontend may or may not render.
PRUNE_HIGH = 0.02      # unique variance at or below this: safe candidate
PRUNE_MEDIUM = 0.10


def _prune_blockers(res: dict, name: str, basis: str) -> list[str]:
    """Reasons this recommendation should not be executed blindly.

    Blockers are scoped to the EVIDENCE the recommendation rests on,
    which is what `basis` records. Applying every global warning to
    every row is a category error, and an expensive one: on a real
    53-metric dashboard it blocked all 53 rows, including three pairs
    sitting at r = 0.9993, and the product would have shown nothing
    actionable on its best example.

      basis="identity"
        The metric is arithmetically the same quantity as another, from
        a single pairwise correlation over every available row. Sparse
        history and shared trend do not weaken that: a rate and its
        complement are the same number whether or not the dashboard
        trends. Only relationships correlation cannot see apply here.

      basis="unique_variance"
        The metric is judged redundant by a regression on every other
        metric. That estimate IS weakened by short history (few rows
        per predictor) and by shared trend, so those warnings attach.

    Aggregate and nonlinear blockers attach in both cases: a max-of-
    others is mis-scored under either basis, and a nonlinear coupling is
    invisible to both.
    """
    out = []
    if basis == "unique_variance":
        if res.get("crowded"):
            out.append("low_rows_per_metric")
        if res.get("trend_dominated"):
            out.append("trend_dominated")
    for _f, agg_name, kind, _r in res.get("aggregates", []):
        if agg_name == name:
            out.append(f"derived_aggregate_{kind}")
    for _ratio, a, b, _r, _mi in res.get("nonlinear", []):
        if name in (a, b):
            out.append("nonlinear_coupling")
            break
    return sorted(set(out))


def _pruning_queue(res: dict) -> list[dict]:
    """Ordered deletion candidates, cheapest-to-lose first.

    Ordering is by unique variance ascending, which is the honest
    measure of what deleting the metric costs. Identity partners are
    surfaced explicitly because they are the easiest sell: a metric that
    is arithmetically the same number as another one is a free win.
    """
    ident_partner: dict[str, tuple[str, float]] = {}
    for _ar, r, a, b in (res["diff"]["identities"] or
                         res["raw"]["identities"]):
        ident_partner.setdefault(a, (b, r))
        ident_partner.setdefault(b, (a, r))

    cluster_of: dict[str, int] = {}
    for idx, c in enumerate(res["diff"]["clusters"]):
        if len(c) > 1:
            for n in c:
                cluster_of[n] = idx

    # Only one member of an identity pair may be recommended — deleting
    # both destroys the quantity. Delegated to the engine so the CLI and
    # the service cannot disagree about which metric survives; the engine
    # version also unions the raw and differenced views, which matters for
    # pairs that are identities in levels but not in differences.
    ident_keep = SA.identity_representatives(res)

    queue = []
    for u in sorted(res["diff"]["unique"], key=lambda x: x["unique"]):
        name = u["name"]
        uv = u["unique"]
        partner = ident_partner.get(name)
        basis = "identity" if partner else "unique_variance"
        blockers = _prune_blockers(res, name, basis)

        if partner:
            confidence = "high" if name not in ident_keep else "low"
        elif uv <= PRUNE_HIGH:
            confidence = "high"
        elif uv <= PRUNE_MEDIUM:
            confidence = "medium"
        else:
            confidence = "low"
        if blockers and confidence == "high":
            confidence = "medium"

        queue.append({
            "metric": name,
            "basis": basis,
            "unique_variance": round(float(uv), 4),
            "best_predictor": u["best_partner"],
            "best_predictor_r": round(float(u["best_r"]), 4),
            "confidence": confidence,
            "identity_partner": partner[0] if partner else None,
            "identity_r": round(float(partner[1]), 4) if partner else None,
            "keep_as_representative": name in ident_keep,
            "cluster_id": cluster_of.get(name),
            "blockers": blockers,
            "recommended": (confidence == "high" and not blockers
                            and name not in ident_keep),
        })
    return queue


# ----------------------------------------------------------------------
# contract
# ----------------------------------------------------------------------
def _f(x) -> float | None:
    """JSON-safe float: NaN and inf are not valid JSON."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# Badge vocabulary for the Topology Explorer. Fixed and closed: the
# frontend switches on these strings, so adding one is a contract minor
# and renaming one is a contract major.
BADGES = ("identity", "max_aggregate", "min_aggregate", "mean_aggregate",
          "nonlinear", "redundant", "standalone", "load_bearing")


def _badges(res: dict, queue: list[dict]) -> dict[str, list[str]]:
    """Per-metric structural badges.

    One metric can carry several — a column can be both an identity
    partner and part of a wider cluster — so this is a list per metric
    and the UI must render all of them, not just the first.
    """
    out: dict[str, list[str]] = {n: [] for n in res["names"]}

    for _ar, _r, a, b in (res["diff"]["identities"] or
                          res["raw"]["identities"]):
        for n in (a, b):
            if n in out and "identity" not in out[n]:
                out[n].append("identity")

    for _f, name, kind, _r in res.get("aggregates", []):
        tag = f"{kind}_aggregate"
        if name in out and tag not in out[name]:
            out[name].append(tag)

    for _ratio, a, b, _r, _mi in res.get("nonlinear", []):
        for n in (a, b):
            if n in out and "nonlinear" not in out[n]:
                out[n].append("nonlinear")

    for c in res["diff"]["clusters"]:
        tag = "redundant" if len(c) > 1 else "standalone"
        for n in c:
            if n in out and tag not in out[n]:
                out[n].append(tag)

    # load_bearing is a UI affordance, not a new measurement: it marks
    # the metrics a customer should be steered AWAY from deleting.
    for row in queue:
        if row["unique_variance"] >= 0.30 and row["metric"] in out:
            if "load_bearing" not in out[row["metric"]]:
                out[row["metric"]].append("load_bearing")
    return out


def build_contract(res: dict, usage: Usage, *,
                   dataset_id: str | None = None,
                   cost_model=None) -> dict:
    """The single place the response shape is defined.

    Keys are stable within a CONTRACT_VERSION major. Additive changes
    bump the minor; removals or renames bump the major and the old shape
    is served until clients migrate.
    """
    d, raw = res["diff"], res["raw"]
    # Metric count from the HEADLINE basis, not the global column list.
    # A ratio basis drops its own denominator, so the two differ and the
    # ratio would otherwise be computed against the wrong denominator.
    # Same drift that was found in report_payload; fixed in both places
    # rather than in one and forgotten in the other.
    n = res["views"][res["headline"]]["n_metrics"]
    pr = _f(res["headline_pr"])
    ratio = (pr / n) if (pr is not None and n) else None

    queue = _pruning_queue(res)
    recommended = [q for q in queue if q["recommended"]]

    return {
        "contract_version": CONTRACT_VERSION,
        "engine_version": ENGINE_VERSION,
        "dataset_id": dataset_id,

        "summary": {
            "metrics_supplied": n,
            "rows_analysed": res["n_rows"],
            "independent_signals": round(pr, 3) if pr is not None else None,
            "true_signal_ratio": round(ratio, 4)
            if ratio is not None else None,
            "components_for_95pct": d["n95"],
            "verdict": SA.verdict_line(res),
            "redundancy_band": ("low" if (ratio or 0) >= 0.8
                                else "moderate" if (ratio or 0) >= 0.5
                                else "heavy"),
        },

        "trend_confound": {
            "raw_independent_signals": _f(raw["pr"]),
            "differenced_independent_signals": _f(d["pr"]),
            "gap": _f(res["trend_gap"]),
            "trend_dominated": bool(res["trend_dominated"]),
            "headline_basis": "differenced",
        },

        "identity_pairs": [
            {"metric_a": a, "metric_b": b, "r": round(float(r), 6),
             "relationship": "inverse" if r < 0 else "direct"}
            for _ar, r, a, b in (d["identities"] or raw["identities"])
        ],

        "redundancy_clusters": [
            {"cluster_id": i, "size": len(c), "metrics": c}
            for i, c in enumerate(d["clusters"]) if len(c) > 1
        ],
        "standalone_metrics": [c[0] for c in d["clusters"] if len(c) == 1],

        "nonlinear_couplings": [
            {"metric_a": a, "metric_b": b, "r": round(float(r), 4),
             "mi_vs_gaussian": round(float(ratio_), 2),
             "mutual_information_bits": round(float(mi), 4)}
            for ratio_, a, b, r, mi in res["nonlinear"]
        ],
        "nonlinear_analysis_skipped": bool(res["mi_skipped"]),

        "derived_aggregates": [
            {"metric": name, "aggregate_of_others": kind,
             "match_fraction": round(float(frac), 4),
             "r": round(float(r), 4)}
            for frac, name, kind, r in res["aggregates"]
        ],

        "metric_badges": _badges(res, queue),

        "basis": {
            "headline": res["headline"],
            "declared": bool(res["basis_declared"]),
            "available": sorted(res["views"]),
            "per_basis": {k: {"effective_signals": _f(v["pr"]),
                              "metrics": v["n_metrics"], "rows": v["n_rows"]}
                          for k, v in res["views"].items()},
        },
        "basis_conflicts": [
            {"basis": b, "a": x, "b": y,
             "r_reference": _f(ro), "r_basis": _f(rn)}
            for b, x, y, ro, rn in res["basis_conflicts"]],
        "pruning_queue": queue,
        "pruning_summary": {
            "candidates_total": len(queue),
            "recommended_count": len(recommended),
            "recommended_metrics": [q["metric"] for q in recommended],
            "potential_reduction_pct": round(
                100.0 * len(recommended) / n, 1) if n else 0.0,
        },

        "estimated_monthly_saving": _COST.estimate(
            {"pruning_queue": queue}, cost_model),

        "warnings": _warnings(res),
        "excluded_columns": res["notes"],
        "usage": usage.to_dict(),
    }


def _warnings(res: dict) -> list[dict]:
    """Machine-readable caveats. The frontend should render these next
    to the headline, not behind a tooltip: each one materially changes
    how the numbers should be read."""
    out = []
    if res.get("crowded"):
        out.append({
            "code": "low_rows_per_metric",
            "severity": "high",
            "value": round(float(res["rows_per_metric"]), 1),
            "message": (
                f"Only {res['rows_per_metric']:.1f} rows per metric. "
                f"Unique-variance figures are adjusted for predictor "
                f"count but remain strained; treat the ordering as "
                f"informative and the percentages as approximate."),
        })
    if res.get("trend_dominated"):
        out.append({
            "code": "trend_dominated",
            "severity": "medium",
            "value": _f(res["trend_gap"]),
            "message": (
                "Raw and differenced views disagree substantially. Much "
                "of the raw correlation is shared trend rather than "
                "shared behaviour; headline figures use the differenced "
                "view."),
        })
    if res.get("mi_skipped"):
        out.append({
            "code": "nonlinear_skipped",
            "severity": "low",
            "value": res["n_rows"],
            "message": (
                f"Nonlinear dependence not estimated: {res['n_rows']} "
                f"rows, needs {SA.MIN_ROWS_MI}+."),
        })
    if res.get("aggregates"):
        out.append({
            "code": "derived_aggregates_present",
            "severity": "medium",
            "value": len(res["aggregates"]),
            "message": (
                "One or more metrics are a max/min/mean of others. Such "
                "metrics are linearly unpredictable, so their unique-"
                "variance scores overstate how load-bearing they are."),
        })
    return out


# ----------------------------------------------------------------------
# entry point
# ----------------------------------------------------------------------

def validate_basis_request(path, *, scale_by=(), scale_exempt=(),
                           basis=None, require_basis=False):
    """Reject a bad basis declaration BEFORE queuing. Raises InvalidInput.

    `submit` promises that "a client should learn it is over limit from
    the submit call, not by polling a job that fails later". The same
    has to hold for a malformed declaration, or an async caller gets a
    202 followed by a failed job for what is plainly a bad request,
    while the identical sync call returns 422.

    Column names are checked against the RAW CSV header, which is one
    line of I/O. That catches a column that is not in the file at all.
    It does NOT catch a column the loader later drops for being
    non-numeric, constant or time-like — the engine raises for those and
    the job fails honestly. The cheap check is worth having anyway,
    because a typo is the common case and it is the one that should not
    cost a queue round-trip.
    """
    if require_basis and not basis:
        raise InvalidInput(
            "require_basis is set and no basis was declared. Without one "
            "the engine assumes 'differenced', which treats rows as "
            "ordered observations — an assumption that fails silently on "
            "entity-indexed data. Declare raw, differenced, or ratio:COL.")
    wanted = list(scale_by) + list(scale_exempt)
    if basis and basis.startswith("ratio:"):
        wanted.append(basis.split(":", 1)[1])
        if basis.split(":", 1)[1] not in list(scale_by):
            raise InvalidInput(
                f"basis {basis!r} names a denominator that is not in "
                f"scale_by. Add it to scale_by so the basis is computed.")
    if basis and not basis.startswith("ratio:") and basis not in (
            "raw", "differenced"):
        raise InvalidInput(
            f"unknown basis {basis!r}. Use raw, differenced, or ratio:COL.")
    if not wanted:
        return
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            header = next(csv.reader(fh))
    except (OSError, StopIteration) as exc:
        raise InvalidInput(f"could not read CSV header: {exc}") from exc
    present = {h.strip() for h in header}
    missing = [c for c in wanted if c not in present]
    if missing:
        raise InvalidInput(
            f"column(s) not in the uploaded CSV: {', '.join(missing)}. "
            f"Available: {', '.join(sorted(present))}")


def run_audit(path: str, *, tier: str = "pro", ignore=(),
              dataset_id: str | None = None,
              max_rows: int | None = None,
              cost_model=None,
              scale_by=(), scale_exempt=(), basis=None,
              require_basis: bool = False) -> dict:
    """Full pipeline: pre-flight, quota, analyse, contract.

    Quota is checked against a cheap pre-flight measurement so an
    over-limit upload is rejected before any O(N^2) work happens.
    """
    t = TIERS.get(tier)
    if t is None:
        raise InvalidInput(f"unknown tier {tier!r}")

    started = time.time()
    size = os.path.getsize(path) if os.path.exists(path) else 0

    try:
        metrics, rows, _cells = measure(path, ignore=ignore)
    except (ValueError, FileNotFoundError) as exc:
        raise InvalidInput(str(exc)) from exc

    enforce(t, metrics, rows)

    # An unknown scale_by / scale_exempt column, or a basis that was
    # never computed, is a CLIENT error. The engine raises ValueError for
    # all three; without this it would surface as a 500 and read like a
    # server fault. Caught here so the boundary returns 422.
    validate_basis_request(path, scale_by=scale_by,
                           scale_exempt=scale_exempt, basis=basis,
                           require_basis=require_basis)
    try:
        res = SA.audit(path, ignore=ignore, max_rows=max_rows,
                       scale_by=tuple(scale_by),
                       scale_exempt=tuple(scale_exempt), basis=basis)
    except (ValueError, FileNotFoundError) as exc:
        raise InvalidInput(str(exc)) from exc

    usage = Usage(
        metrics=res["n_metrics"],
        rows=res["n_rows"],
        billable_cells=res["n_metrics"] * res["n_rows"],
        uploaded_bytes=size,
        columns_supplied=metrics + len(res["notes"]),
        columns_analysed=res["n_metrics"],
        rows_dropped=max(0, rows - res["n_rows"]),
        duration_ms=int((time.time() - started) * 1000),
    )
    if dataset_id is None:
        with open(path, "rb") as fh:
            dataset_id = hashlib.sha256(fh.read()).hexdigest()[:16]
    return build_contract(res, usage, dataset_id=dataset_id,
                          cost_model=cost_model)


def should_run_async(tier: str, metrics: int, rows: int) -> bool:
    """Whether this audit belongs on the queue rather than in the
    request. Exposed so the API can answer /preflight honestly instead
    of the client guessing."""
    t = TIERS.get(tier) or TIERS["pro"]
    return metrics * rows >= t.async_only_above_cells
