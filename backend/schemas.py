"""
schemas.py — Pydantic models for the public API contract.

These exist to generate a correct OpenAPI document, which is what the
frontend's client generator consumes. They are a DESCRIPTION of the
contract that `service.build_contract` already produces — not a second
implementation of it.

That distinction matters and is enforced by a test: `test_schemas.py`
runs a real audit and validates the output against these models. If
the service adds a field and the model does not, the test fails rather
than the frontend silently losing data.

BADGE VOCABULARY is closed on purpose. The frontend switches on these
strings; a new one is a contract minor version, a renamed one is a
major. Declaring it as an enum here means the generated client gets a
union type and an unknown badge becomes a compile error rather than a
blank chip.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ----------------------------------------------------------------------
class Badge(str, Enum):
    identity = "identity"
    max_aggregate = "max_aggregate"
    min_aggregate = "min_aggregate"
    mean_aggregate = "mean_aggregate"
    nonlinear = "nonlinear"
    redundant = "redundant"
    standalone = "standalone"
    load_bearing = "load_bearing"


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class Basis(str, Enum):
    identity = "identity"
    unique_variance = "unique_variance"


class Severity(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


# ----------------------------------------------------------------------
class Summary(BaseModel):
    metrics_supplied: int = Field(..., examples=[53])
    rows_analysed: int = Field(..., examples=[455])
    independent_signals: float | None = Field(
        ..., description="Participation ratio of the correlation spectrum. "
                         "Continuous, so rarely a whole number.",
        examples=[5.097])
    true_signal_ratio: float | None = Field(
        ..., description="independent_signals / metrics_supplied. The "
                         "headline figure.", examples=[0.0962])
    components_for_95pct: int = Field(..., examples=[20])
    verdict: str
    redundancy_band: Literal["low", "moderate", "heavy"]


class TrendConfound(BaseModel):
    raw_independent_signals: float | None
    differenced_independent_signals: float | None
    gap: float | None = Field(
        ..., description="raw minus differenced. Large negative values mean "
                         "the raw view is mostly shared calendar trend.")
    trend_dominated: bool
    headline_basis: Literal["differenced"] = Field(
        "differenced",
        description="Always differenced. Metrics that all grow over time "
                    "correlate for that reason alone.")


class IdentityPair(BaseModel):
    metric_a: str
    metric_b: str
    r: float = Field(..., examples=[0.999269])
    relationship: Literal["direct", "inverse"]


class RedundancyCluster(BaseModel):
    cluster_id: int
    size: int
    metrics: list[str]


class NonlinearCoupling(BaseModel):
    metric_a: str
    metric_b: str
    r: float = Field(..., description="Near zero by selection: these pairs "
                                      "look independent to correlation.")
    mi_vs_gaussian: float = Field(
        ..., description="Empirical mutual information as a multiple of what "
                         "a Gaussian with the same r would carry.",
        examples=[111.7])
    mutual_information_bits: float


class DerivedAggregate(BaseModel):
    metric: str
    aggregate_of_others: Literal["max", "min", "mean"]
    match_fraction: float = Field(
        ..., description="Fraction of rows on which the metric EQUALS the "
                         "aggregate. Equality, not correlation.",
        examples=[0.76])
    r: float


class PruneCandidate(BaseModel):
    metric: str
    basis: Basis = Field(
        ..., description="What the recommendation rests on. Blockers are "
                         "scoped to this: sparse history weakens a "
                         "unique_variance judgement but not an identity.")
    unique_variance: float
    best_predictor: str
    best_predictor_r: float
    confidence: Confidence
    identity_partner: str | None = None
    identity_r: float | None = None
    keep_as_representative: bool = Field(
        ..., description="True for the surviving member of an identity pair. "
                         "Never recommended for deletion — removing both "
                         "halves destroys the quantity.")
    cluster_id: int | None = None
    blockers: list[str] = Field(
        ..., description="Reasons not to act. Empty is required for "
                         "recommended=true.")
    recommended: bool


class PruningSummary(BaseModel):
    candidates_total: int
    recommended_count: int
    recommended_metrics: list[str]
    potential_reduction_pct: float


class CostEstimate(BaseModel):
    available: bool = Field(
        ..., description="False when no unit cost was supplied. No figure is "
                         "synthesised: column redundancy and billing "
                         "cardinality differ by orders of magnitude.")
    amount: float | None = None
    currency: str | None = None
    confidence: Confidence | None = None
    reason: str | None = None
    assumptions: list[str] = Field(
        default_factory=list,
        description="Display these next to the figure, not behind a tooltip.")
    series_affected: int | None = None
    metrics_affected: int = 0


class Warning_(BaseModel):
    code: str = Field(..., examples=["low_rows_per_metric"])
    severity: Severity
    value: float | int | None = None
    message: str


class Usage(BaseModel):
    metrics: int
    rows: int
    billable_cells: int = Field(
        ..., description="The metering unit. Computed from data actually "
                         "analysed, after dropped columns and listwise "
                         "deletion.")
    uploaded_bytes: int
    columns_supplied: int
    columns_analysed: int
    rows_dropped: int
    duration_ms: int
    engine_version: str



class BasisSummary(BaseModel):
    """One computed basis. Every basis is computed on every run."""
    effective_signals: float | None
    metrics: int
    rows: int


class BasisInfo(BaseModel):
    """Which lens produced the headline, and whether anyone chose it.

    `declared` is the field a CI client should assert on. False means
    the engine fell back to treating rows as ordered observations --
    an assumption that fails SILENTLY on entity-indexed data, returning
    very nearly the right number for the wrong reason. The server does
    not refuse an undeclared request by default, because that would
    break every existing caller; it states the assumption instead and
    lets the client enforce.
    """
    headline: str = Field(..., examples=["differenced"])
    declared: bool = Field(
        ..., description="False means the engine assumed a basis rather "
                         "than being told one.")
    available: list[str]
    per_basis: dict[str, BasisSummary]


class BasisConflict(BaseModel):
    """A pair a transform made MORE correlated than it was.

    Ambiguous by nature and deliberately not resolved server-side: the
    basis either injected a shared factor (a defect) or exposed a real
    duplicate (a finding). Dividing ROA and ROE by total assets took r
    from +0.60 to +0.98 -- injected. Dividing net income by total assets
    took its correlation with ROA from +0.06 to +0.9997 -- exposed,
    because that quotient IS return on assets. Same signature.
    """
    basis: str
    a: str
    b: str
    r_reference: float | None
    r_basis: float | None


class AuditContract(BaseModel):
    contract_version: str = Field(..., examples=["1.0"])
    engine_version: str
    dataset_id: str | None = Field(
        None, description="Content hash. Stable across re-runs of the same "
                          "data; the natural idempotency key.")
    summary: Summary
    trend_confound: TrendConfound
    identity_pairs: list[IdentityPair]
    redundancy_clusters: list[RedundancyCluster]
    standalone_metrics: list[str]
    nonlinear_couplings: list[NonlinearCoupling]
    nonlinear_analysis_skipped: bool
    derived_aggregates: list[DerivedAggregate]
    metric_badges: dict[str, list[Badge]] = Field(
        ..., description="Structural badges per metric, from a closed "
                         "vocabulary. A metric may carry several.")
    basis: BasisInfo | None = Field(
        None, description="Added in contract 1.1. Absent from responses "
                          "generated before that version; never absent "
                          "from new ones.")
    basis_conflicts: list[BasisConflict] = Field(default_factory=list)
    pruning_queue: list[PruneCandidate]
    pruning_summary: PruningSummary
    estimated_monthly_saving: CostEstimate
    warnings: list[Warning_]
    excluded_columns: list[str] = Field(
        ..., description="Columns not audited, each with a reason. Must be "
                         "shown: a column vanishing silently turns an audit "
                         "into an audit of something else.")
    usage: Usage


# ----------------------------------------------------------------------
# request / envelope models
# ----------------------------------------------------------------------
class CostModelIn(BaseModel):
    unit_cost_per_series_month: float | None = Field(
        None, description="From your own contract. No vendor price list is "
                          "held server-side, because a stale constant would "
                          "produce wrong figures indefinitely.")
    currency: str = "USD"
    series_per_metric: dict[str, int] = Field(
        default_factory=dict,
        description="Billable series per column. Supplying this for every "
                    "affected metric is what raises confidence from low to "
                    "medium.")
    retention_multiplier: float = 1.0


class SyncAuditResponse(BaseModel):
    mode: Literal["sync"]
    status: Literal["succeeded"]
    result: AuditContract


class AsyncAuditResponse(BaseModel):
    mode: Literal["async"]
    job_id: str
    status: str
    poll: str


class PreflightResponse(BaseModel):
    metrics: int
    rows: int
    cells: int
    rows_per_metric: float
    within_quota: bool
    quota_error: dict[str, Any] | None = None
    recommended_mode: Literal["sync", "async"]
    tier: str


class JobResponse(BaseModel):
    id: str
    account_id: str
    tier: str
    status: str
    stage: str
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    duration_ms: int | None = None
    result: AuditContract | None = None
    error: dict[str, Any] | None = None
    ignore: list[str] = Field(default_factory=list)
    dataset_id: str | None = None


class ExportAvailability(BaseModel):
    always_available: list[str]
    gated: list[str]
    gate_satisfied: bool
    candidates: int
    attested: int
    missing_attestation: list[str]
    cleared_for_change: int


class ErrorResponse(BaseModel):
    error: str = Field(..., examples=["quota_exceeded"])
    detail: str | None = None
    limit: str | None = None
    actual: int | None = None
    allowed: int | None = None
    tier: str | None = None
    missing_metrics: list[str] | None = None
