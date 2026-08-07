# Signal Audit — Backend API contract v1.0

Service layer over the `signal_audit` analysis engine. The engine stays server-side; clients see only this contract.

**Layering.** `signal_audit.py` (analysis) → `service.py` (contract, metering, policy) → `jobs.py` (async) → `app.py` (HTTP). Nothing below `service.py` knows about HTTP; nothing above it knows about eigenvalues. Business rules live in `service.py` so they are testable without a web client.

**Versioning.** `contract_version` appears in every response. Additive changes bump the minor; removals or renames bump the major, and the previous major is served until clients migrate. `engine_version` is reported separately — the analysis can improve without a contract change, and a customer comparing two audits needs to know which happened.

---

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/health` | Liveness; contract and engine versions |
| `GET` | `/v1/tiers` | Published limits per subscription band |
| `POST` | `/v1/audits/preflight` | Shape, quota verdict and execution mode **without** running analysis |
| `POST` | `/v1/audits` | Run an audit (`mode=auto\|sync\|async`) |
| `GET` | `/v1/audits/{job_id}` | Job status, and the result once complete |
| `GET` | `/v1/audits` | List this account's audits (no result bodies) |
| `GET` | `/v1/usage` | Consumption against the tier's allowance |
| `GET` | `/v1/audits/{id}/exports` | What can be downloaded now, and what is gated |
| `GET` | `/v1/audits/{id}/exports/review-worksheet` | Blast-radius worksheet (CSV) — request this first |
| `GET` | `/v1/audits/{id}/exports/column-manifest` | Keep/drop per column (CSV), ungated |
| `POST` | `/v1/audits/{id}/exports/terraform` | Reversible Terraform; requires completed worksheet |
| `POST` | `/v1/audits/{id}/exports/datadog-exclusions` | Exclusion plan (JSON); requires completed worksheet |
| `POST` | `/v1/audits/{id}/cost-estimate` | Re-price against customer-supplied parameters |

### Why `preflight` exists

A large audit is a minutes-long commitment. Preflight parses the file, reports metrics × rows, says whether it clears quota, and states whether it will run inline or be queued — before any O(N²) work. It lets the UI show the user what they are about to spend and why, rather than making them discover it from a failure.

### Status codes

| Code | Meaning |
|---|---|
| `200` | Synchronous audit complete; body carries `result` |
| `202` | Queued; body carries `job_id` and a poll URL |
| `401` | Missing credentials (when auth is enabled) |
| `402` | Quota exceeded — a billing event, distinct from a user error |
| `404` | Job not found **or not yours** (deliberately indistinguishable, so job ids are not probeable across accounts) |
| `413` | Upload exceeds the hard size cap |
| `422` | Invalid input: too few columns, too few rows, unparseable |

`402` versus `422` is the distinction that matters commercially: one is an upgrade prompt, the other is a fix-your-file message. They are separate exception types in `service.py` and must not be collapsed.

---

## Response contract

Returned by `POST /v1/audits` (sync) and inside `GET /v1/audits/{id}` (async).

```jsonc
{
  "contract_version": "1.0",
  "engine_version": "0.1.0",
  "dataset_id": "a3f1c8e2b90d4f77",       // sha256 prefix; stable across re-runs

  "summary": {
    "metrics_supplied": 53,
    "rows_analysed": 455,
    "independent_signals": 5.104,          // participation ratio, continuous
    "true_signal_ratio": 0.0963,           // signals / metrics — the headline
    "components_for_95pct": 20,
    "redundancy_band": "heavy",            // low | moderate | heavy
    "verdict": "53 metrics carrying about 5.1 independent signals — ..."
  },

  "trend_confound": {
    "raw_independent_signals": 1.63,
    "differenced_independent_signals": 5.10,
    "gap": -3.47,
    "trend_dominated": true,
    "headline_basis": "differenced"
  },

  "identity_pairs": [
    { "metric_a": "case_count_7day_avg",
      "metric_b": "all_case_count_7day_avg",
      "r": 0.999300, "relationship": "direct" }
  ],

  "redundancy_clusters": [
    { "cluster_id": 0, "size": 12, "metrics": ["...", "..."] }
  ],
  "standalone_metrics": ["death_count", "..."],

  "nonlinear_couplings": [
    { "metric_a": "latency_ms", "metric_b": "churn_pct",
      "r": 0.13, "mi_vs_gaussian": 111.7, "mutual_information_bits": 0.42 }
  ],
  "nonlinear_analysis_skipped": false,

  "derived_aggregates": [
    { "metric": "aqi_site", "aggregate_of_others": "max",
      "match_fraction": 0.76, "r": 0.99 }
  ],

  "pruning_queue": [
    { "metric": "case_count_7day_avg",
      "basis": "identity",                  // identity | unique_variance
      "unique_variance": 0.0,
      "best_predictor": "all_case_count_7day_avg", "best_predictor_r": 1.0,
      "confidence": "high",                 // high | medium | low
      "identity_partner": "all_case_count_7day_avg", "identity_r": 0.9993,
      "keep_as_representative": false,      // true = this is the survivor
      "cluster_id": 2,
      "blockers": [],
      "recommended": true }
  ],
  "pruning_summary": {
    "candidates_total": 53, "recommended_count": 11,
    "recommended_metrics": ["..."], "potential_reduction_pct": 20.8
  },

  "warnings": [
    { "code": "low_rows_per_metric", "severity": "high",
      "value": 8.6, "message": "..." }
  ],
  "excluded_columns": ["'date' — dropped, looks like a time index"],

  "usage": {
    "metrics": 53, "rows": 455, "billable_cells": 24115,
    "uploaded_bytes": 812004,
    "columns_supplied": 54, "columns_analysed": 53, "rows_dropped": 1,
    "duration_ms": 1840, "engine_version": "0.1.0"
  }
}
```

### Field notes the frontend must respect

**`true_signal_ratio` is the headline.** Signals ÷ metrics. It is continuous and will rarely be a round number; render 5.1-from-53 as "about five things, measured fifty-three ways."

**`headline_basis` is always `differenced`.** Metrics that all grow over time correlate from shared trend alone. Every headline figure uses first differences for that reason, and `trend_confound` exposes both views so the UI can show the gap. When `trend_dominated` is true, the raw number is mostly about the calendar.

**`basis` says what the recommendation rests on, and blockers are scoped to it.**

- `identity` — the metric is arithmetically the same quantity as another, from one pairwise correlation over every available row. Sparse history and shared trend do not weaken that: a rate and its complement are the same number whether or not the dashboard trends.
- `unique_variance` — the metric is judged redundant by a regression against every other metric. That estimate *is* weakened by short history and shared trend, so those warnings attach as blockers.

Applying every global warning to every row was tried and is a category error. On the 53-metric NYC dashboard it blocked all 53 rows — including three pairs at r = 0.999 — and the product would have shown nothing actionable on its best example.

**`blockers` are not decoration.** A pruning recommendation is an instruction to delete something the customer is paying to collect.

| Blocker | Applies to | Meaning |
|---|---|---|
| `low_rows_per_metric` | `unique_variance` only | Too little history; the regression estimate is strained |
| `trend_dominated` | `unique_variance` only | Much of the structure is shared trend |
| `derived_aggregate_max` / `_min` / `_mean` | both | The metric is a summary of others and is linearly unpredictable, so its unique-variance score **overstates** how load-bearing it is |
| `nonlinear_coupling` | both | The metric participates in a relationship correlation cannot see |

**`keep_as_representative`** marks the survivor of an identity pair. Exactly one member of each pair carries it, chosen deterministically so repeat audits of the same data agree. A row is never `recommended` while `keep_as_representative` is true — deleting both halves destroys the quantity.

A row is `recommended: true` only when confidence is high, blockers is empty, and it is not the representative. The UI should not offer one-click bulk deletion of anything else.

Worked example, NYC: 3 identity pairs → 2 recommended. The third is withheld because it participates in a nonlinear coupling, and the blocker says so.

**`warnings` belong next to the headline, not behind a tooltip.** Each materially changes how the numbers should be read.

**`excluded_columns` must be shown.** A column vanishing without explanation is how an audit quietly becomes an audit of something else.

---

## Exports, and why they are gated

`428 Precondition Required` is returned when an executable artefact is requested without a completed blast-radius worksheet.

**The engine proves statistical redundancy. It cannot see operational redundancy.** A metric with 0% unique variance on this dashboard may be the sole condition on a paging monitor, an SLO error budget, or a compliance report — none of which appear in a CSV. So executable exports require a per-metric attestation covering monitors, SLOs, other dashboards and runbooks. A single "I confirm" checkbox cannot represent a reviewer having checked eleven metrics against four systems each, which is why the gate takes a completed worksheet rather than a boolean. A half-filled worksheet does not unlock it: any unanswered operational cell leaves that metric unattested.

**Nothing generated is destructive.** Operators do not delete metrics in production — they exclude from indexing, drop a high-cardinality tag, or move to cheaper retention, all reversible. Generated Terraform uses `datadog_metric_tag_configuration` with an empty tag list, collapsing a metric to one billable series while destroying no data; reversal is restoring the tags. No resource whose effect is deletion is ever emitted, because a generated destroy is not a change a reviewer can safely approve.

Every artefact carries its evidence inline — unique variance, basis, identity partner, blockers, attesting reviewer — so a change request is auditable without returning to the web UI.

| Artefact | Gated | Purpose |
|---|---|---|
| `review_worksheet` | no | Statistical evidence pre-filled, operational columns blank. The artefact to request first. |
| `column_manifest` | no | Keep/drop per column with reasons. A document, not a change. |
| `keep_list` | no | Column names to retain, for piping into a `SELECT`. |
| `datadog_exclusion_json` | **yes** | Reversible exclusion plan. |
| `datadog_terraform` | **yes** | Reversible cardinality collapse. |

---

## Cost estimation

`estimated_monthly_saving` appears in every contract but is **null unless the customer supplies a unit cost.**

The engine measures redundancy among *columns*. Observability billing is driven by *cardinality* — metric name × tag combinations. These differ by one to three orders of magnitude, and the error runs in both directions: one tagged metric can be tens of thousands of billable series, while eleven "redundant columns" may be eleven queries over a single underlying metric, where pruning saves nothing at all. A CSV export has been stripped of exactly the metadata that decides which case applies.

So no price list is held and no figure is synthesised. `POST /v1/audits/{id}/cost-estimate` accepts:

```jsonc
{ "unit_cost_per_series_month": 0.05,
  "currency": "GBP",
  "series_per_metric": { "checkout_latency_p99": 4200 },   // optional
  "retention_multiplier": 1.0 }
```

Confidence is `medium` only when series counts are supplied for every affected metric, `low` otherwise, and every assumption is returned alongside the number for display next to it. Only `recommended` rows are counted — a figure assuming the customer acts on candidates we ourselves flagged as blocked would be dishonest twice over. One caveat is always present: if the columns are queries over a metric still being collected, the saving is zero, and that must be confirmed at the pipeline rather than the dashboard.

---

## Metering and tiers

| Tier | Max metrics | Max rows | Max cells | Audits/mo | Retention |
|---|---|---|---|---|---|
| Starter | 25 | 10,000 | 100,000 | 50 | 7 d |
| Pro | 150 | 250,000 | 5,000,000 | 1,000 | 90 d |
| Unlimited | 100,000 | 100,000,000 | 2,000,000,000 | 1,000,000 | 365 d |

**Three axes, because no two suffice.** Metric count N drives O(N²) pairwise work (O(N³) if the triadic extension lands); row depth M is linear but decides whether a result is statistically usable at all; the cell cap catches shapes that are pathological on neither axis alone — 500 × 100 and 10 × 500,000 fail differently.

**`billable_cells` is computed from data actually analysed** — after dropped columns and after listwise deletion — so a customer is never billed for columns the engine discarded. That asymmetry is deliberate and should survive any pricing change.

**Quota is enforced pre-flight**, before analysis. Verified by test: rejection returns in ~15 ms rather than after a full run.

**Starter never queues.** Its cell cap sits below the async threshold by design, so that tier never shows a polling state.

---

## Asynchronous execution

Above a tier's `async_only_above_cells` (default 200,000), `POST /v1/audits` returns `202` with a `job_id`. Clients poll `GET /v1/audits/{job_id}` until `status` is terminal (`succeeded`, `failed`, `expired`).

Quota is checked **at submit**, synchronously — a client learns it is over limit from the submit call, not by polling a job that fails later.

`stage` is a coarse label (`queued`, `analysing`, `complete`), not a percentage. The engine exposes no intermediate progress, and a fake bar that jumps 0 → 100 is worse than an honest label.

### Deployment warning

`jobs.py` ships `InMemoryJobStore`, which is correct for development and single-instance deployment only. **Do not run it behind more than one worker process:** jobs created on one worker are invisible to the others, and a client polling a different worker gets `404` for a job that is running. `RedisJobStore` is a stub that raises with this explanation rather than half-working. Production path is Celery or RQ with Redis, replacing `submit()` with a task dispatch — the interface is deliberately narrow so that swap is one class, not a refactor.

Threads are used rather than asyncio because the work is CPU-bound and holds the GIL; numpy releases it inside BLAS calls, which is why threads help at all. A process pool or a real broker is the correct answer at scale.

`max_workers` defaults to 2. Each audit holds a full matrix plus intermediates, so concurrency is bounded by memory well before cores — and an OOM kill takes down every in-flight job on the instance, not just the greedy one.

---

## Security notes

- **Auth is stubbed.** `resolve_account` in `app.py` returns a fixed development account. Set `SIGNAL_AUDIT_REQUIRE_AUTH=1` to make it refuse rather than permit. It must be replaced before deployment.
- **Uploads are streamed** to a temp file with a hard 256 MB cap; an unbounded read is a trivial denial-of-service.
- **Source files are deleted** after the job finishes.
- **Job payloads never include server paths**, and internal tracebacks are kept server-side — clients get an error id. The engine is Apache 2.0 so this is not about IP; a stack trace names server paths and internal structure, which is a support burden and useful to anyone probing the service.
- **Cross-account job ids return 404, not 403**, so ids cannot be probed.

---

## What is not built

Stated so nobody discovers these by deploying.

1. **Persistence.** Jobs and results are in memory. No database, no durable audit history, no billing store. `/v1/usage` sums job memory, which a real deployment replaces with a read from the billing system.
2. **Rate limiting** beyond quota, and no per-account concurrency cap. One account can currently occupy every worker.
3. **Live connector ingestion.** The contract accepts CSV uploads. Datadog/Amplitude/Grafana pulls would add a `source` object to the audit request and a credential vault; neither exists. Note that Amplitude and Datadog were both trialled and neither yielded usable data — Datadog requires an organisation account, and a fresh Amplitude project has zero events — so connector work should start from a populated instance rather than a fresh signup.

   The strategic prize in connector work is **not** convenience of ingestion. It is the metadata a CSV loses: metric cardinality (which converts a column result into a cost result) and reference graph (which monitors, SLOs and dashboards use a metric). Those two fields turn `estimated_monthly_saving` from `low` confidence to defensible, and turn the blast-radius worksheet from manual labour into an automatic check. A connector that only pulls metric *values* is worth much less than one that pulls the surrounding metadata.
4. **Idempotency keys.** Re-submitting the same file creates a second job and bills twice. `dataset_id` is a content hash and is the natural key for deduplication.
5. **Scheduled/continuous governance.** The "audit on every dashboard change" product surface needs a scheduler, a diff between contracts, and alerting. None of it is here.
6. **Multi-tenancy isolation** beyond account-scoped lookups. No row-level security, no per-tenant encryption.

---

## A commercial note on the engine

Worth stating plainly, in the same spirit as the P1 novelty audit: the *methods* here — participation ratio, correlation clustering, adjusted R², mutual information against a Gaussian baseline — are standard statistics and not defensible IP. Anyone competent could reimplement them.

What is not easily reproduced is the accumulated judgement: that the headline must be differenced, that the MI estimator needs a shuffled bias floor, that R² needs adjusting when metrics approach rows, and that a max-of-others index reads as load-bearing when it is fully derived. Each of those was a bug found by running against real data, and three of the four would have produced confident wrong answers. The moat is the test suite and the failure catalogue, not the algorithm — which argues for keeping `REAL_DASHBOARDS.md` growing as a deliberate asset rather than as documentation.
