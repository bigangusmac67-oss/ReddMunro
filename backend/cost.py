"""
cost.py — savings estimation, and the assumptions it rests on.

THE RULE THIS MODULE EXISTS TO ENFORCE: never invent a number that
looks like money.

The engine measures redundancy among COLUMNS. Observability billing is
driven by CARDINALITY — metric name x tag combinations. These are not
the same quantity and the difference is often two or three orders of
magnitude. One `http.request.duration` with a `pod_id` tag can be tens
of thousands of billable series; conversely eleven "redundant columns"
on a dashboard may be eleven queries against a single underlying
metric, in which case pruning them saves nothing at all in ingestion.

A CSV export has been stripped of exactly the metadata that decides
which case you are in. So this module will not guess. It requires the
caller to supply a unit cost, optionally accepts a per-metric series
count, and returns every assumption alongside the figure so the UI can
display them next to the number rather than behind a tooltip.

If `unit_cost` is absent, `estimate()` returns a null estimate with a
reason. That is the correct behaviour: a blank field a customer can
fill is recoverable, a wrong invoice figure in a first demo is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class CostModel:
    """Caller-supplied billing parameters.

    unit_cost_per_series_month
        What one billable series costs per month, in `currency`. The
        customer reads this off their own contract; we do not hold a
        price list, because vendor pricing changes and a stale constant
        would silently produce wrong figures forever.

    series_per_metric
        Optional map from column name to the number of billable series
        that column represents. This is the field that converts a
        column-level result into a cost-level one. Without it every
        column is assumed to be exactly one series, which is almost
        always an UNDERSTATEMENT for tagged metrics and an OVERSTATEMENT
        for columns that are separate queries over a shared metric —
        so the direction of the error is not even consistent, and the
        estimate is labelled low-confidence accordingly.

    retention_multiplier
        Some plans bill differently for extended retention. Applied
        uniformly; a per-metric version can be added when a customer
        needs it.
    """
    unit_cost_per_series_month: float | None = None
    currency: str = "USD"
    series_per_metric: dict[str, int] = field(default_factory=dict)
    retention_multiplier: float = 1.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["series_per_metric_supplied"] = bool(self.series_per_metric)
        d["series_per_metric_count"] = len(self.series_per_metric)
        d.pop("series_per_metric")
        return d


NO_ESTIMATE = {
    "available": False,
    "amount": None,
    "currency": None,
    "confidence": None,
    "reason": None,
    "assumptions": [],
    "series_affected": None,
    "metrics_affected": 0,
}


def estimate(contract: dict, model: CostModel | None) -> dict:
    """Estimated recurring saving from acting on the recommended queue.

    Only `recommended` rows count. Medium- and low-confidence candidates
    are excluded: a savings figure that assumes the customer will act on
    recommendations we ourselves flagged as blocked would be dishonest
    on both counts.
    """
    out = dict(NO_ESTIMATE)
    recommended = [r for r in contract.get("pruning_queue", [])
                   if r.get("recommended")]
    out["metrics_affected"] = len(recommended)

    if model is None or model.unit_cost_per_series_month is None:
        out["reason"] = (
            "No unit cost supplied. Provide your per-series monthly cost "
            "to see an estimate; we do not hold vendor price lists "
            "because a stale constant would produce wrong figures "
            "indefinitely.")
        return out

    if not recommended:
        out.update(available=True, amount=0.0,
                   currency=model.currency, confidence="high",
                   series_affected=0,
                   reason="No high-confidence prune candidates.",
                   assumptions=["Only high-confidence, unblocked "
                                "candidates are counted."])
        return out

    mapped = model.series_per_metric
    series = sum(int(mapped.get(r["metric"], 1)) for r in recommended)
    covered = sum(1 for r in recommended if r["metric"] in mapped)

    amount = (series * model.unit_cost_per_series_month
              * model.retention_multiplier)

    assumptions = [
        f"{len(recommended)} metric(s) recommended for removal; "
        f"medium- and low-confidence candidates excluded.",
        f"Unit cost {model.unit_cost_per_series_month:.4f} "
        f"{model.currency} per series per month, supplied by you.",
    ]
    if model.retention_multiplier != 1.0:
        assumptions.append(
            f"Retention multiplier {model.retention_multiplier:g} applied.")

    if covered == len(recommended):
        confidence = "medium"
        assumptions.append(
            f"Series counts supplied for all {covered} affected metrics "
            f"({series:,} series total).")
    elif covered > 0:
        confidence = "low"
        assumptions.append(
            f"Series counts supplied for {covered} of {len(recommended)} "
            f"metrics; the remainder are assumed to be 1 series each, "
            f"which is usually an understatement for tagged metrics.")
    else:
        confidence = "low"
        assumptions.append(
            "No series counts supplied — every metric assumed to be 1 "
            "billable series. For tagged metrics this understates cost, "
            "sometimes by orders of magnitude; for columns that are "
            "separate queries over one underlying metric it overstates "
            "the saving. Supply series_per_metric for a usable figure.")

    assumptions.append(
        "Assumes removal stops ingestion. If these columns are queries "
        "over a metric that is still collected, the saving is zero — "
        "confirm at the pipeline, not the dashboard.")

    out.update(available=True,
               amount=round(float(amount), 2),
               currency=model.currency,
               confidence=confidence,
               series_affected=series,
               reason=None,
               assumptions=assumptions)
    return out


def model_from_request(payload: dict | None) -> CostModel | None:
    """Build a CostModel from an untrusted request body."""
    if not payload:
        return None
    try:
        unit = payload.get("unit_cost_per_series_month")
        spm = payload.get("series_per_metric") or {}
        if not isinstance(spm, dict):
            raise ValueError("series_per_metric must be an object")
        return CostModel(
            unit_cost_per_series_month=(
                float(unit) if unit is not None else None),
            currency=str(payload.get("currency", "USD"))[:8],
            series_per_metric={str(k): int(v) for k, v in spm.items()},
            retention_multiplier=float(
                payload.get("retention_multiplier", 1.0)),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid cost model: {exc}") from exc
