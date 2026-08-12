# Local history store — specification and pre-registered predictions

**Registered before any code and before any measurement.** H2–H8 have
now been scored — **6 hit, 1 missed** — by `score_history.py`. **H1
remains unscored and is not being counted**, because it cannot be
answered without a window somebody labelled `incident` after their
service broke.

---

# Part 1 — What it is for

## The objection this answers

Every cost tool in this space is vulnerable to one sentence:

> Those two metrics look identical **because nothing has gone wrong yet**.

It is a good objection and it is usually right. A single audit measures
correlation over one window. If that window is a quiet fortnight, a pair
at r = 0.9998 tells you they agreed while nothing was happening — which
is precisely when a redundant-looking metric is redundant. The one you
keep it for is the incident.

The history store exists to turn that objection into the product's
strongest output.

## The headline finding is the exception, not the confirmation

Not this:

> `request_rate_total` was redundant in 29 of 30 runs. Confidence: high.

**This:**

> `request_rate_total` was redundant in 29 of 30 runs — and diverged in
> run #14, the window you labelled `incident`. **Keep it.** That
> divergence is the only reason this metric exists.

Twenty-nine confirmations are worth very little; they are the same
finding repeated. **The exception is the information.** A store that
reported the 29 and buried the 1 would be a confidence-inflation
machine, and would give the archive recommendation exactly the false
authority this project spends its time refusing.

So the ordering is fixed in advance: **exceptions first, always**, and a
persistence rating is never shown without its exceptions beside it.

---

# Part 2 — Design

## What a run stores

One JSON file per run under `.redd/history/`, plain and readable:

```json
{
  "run_id": "2026-08-11T04:12:33Z-a1b2c3",
  "engine_version": "0.1.0",
  "contract_version": "1.0",
  "dataset_id": "<content hash of the input>",
  "window": {"label": "quiet", "rows": 437, "declared_by": "operator"},
  "basis": "differenced", "ordered": true, "grade": "A",
  "identity_pairs": [["methodGET_status200", "request_rate_total"]],
  "clusters": [["node_load1", "node_load5", "node_load15"]],
  "subset_sums": [],
  "metrics_present": ["..."],
  "archive_candidates": ["..."]
}
```

**Set-valued findings only, plus the metric roster.** Per
`CONTINUOUS_DIFF_SPEC.md`, sets are the stable substrate and scalars
drift; a persistence rating built on averaged correlation coefficients
would move every run for reasons that are not findings. Effective-signal
count is stored for reference and is never counted.

## The window label is DECLARED, never inferred

`--window-label quiet|busy|incident|unknown`, defaulting to `unknown`.

Inferring an incident from a variance spike would be a classifier
deciding what the tool reports, which is the failure mode the basis
declaration and the row-order gate both exist to prevent. It is also
unreliable: a deploy, a batch job and an outage look similar in
aggregate.

**Consequence, and it must be stated in the output rather than glossed:**
if no run is labelled `incident`, the history contains no incident
evidence, and a persistence rating drawn from it says only *"stable
across N quiet windows"*. That is a weaker claim than it will look like,
so the report says which labels it actually has.

## Runs are not independent, and the count will lie about it

Thirty daily runs over a rolling 7-day window share six-sevenths of
their data. "Redundant in 29 of 30 runs" reads as thirty observations
and is closer to four.

This is the participation-ratio problem applied to the store itself, and
it would be embarrassing to get wrong here. So the report gives
**effective independent windows**, computed from the declared window
spans, alongside the raw count — and where they diverge, the effective
number leads.

## Absent is not the same as not-redundant

A metric missing from a run — not in that export, filtered out, added
last month — must never count as evidence against redundancy.
Persistence is `redundant / (runs where the metric was present)`, and
the denominator is reported. A pair present in 10 of 30 runs and
redundant in all 10 is `10/10 across 10`, not `10/30`.

## This is the first persistent state in the product

Everything to date is stateless by design. `.redd/history/` introduces
staleness, versioning and a store that can be wrong. Three consequences,
accepted deliberately:

- **Engine version is recorded and mixing majors is refused.** A finding
  from a different engine may not be comparable, and silently pooling
  them would produce a persistence count over inconsistent definitions.
- **`.redd/` is gitignored by default**, and the tool says so on first
  write. The files contain the operator's metric names — the first time
  this tool writes their data to disk at all.
- **No egress**, asserted by the same test that covers every other
  module.

---

# Part 3 — Predictions

| # | Prediction | Result |
|---|---|---|
| **H1** | **THE PREMISE ITSELF.** On a corpus with a known incident window, identity pairs break at **at least twice** the rate they break between quiet windows. This is the assumption the whole feature rests on — that redundant-looking metrics diverge when something goes wrong — and it has never been measured here. | |
| **H2** | **The substrate is stable.** Two quiet windows over disjoint data agree on identity-pair membership **≥ 95%** of the time. If a pair flickers between quiet windows, counting how often it appears is counting noise. |  **hit.** Mean Jaccard **1.000** over 3 disjoint 145-row windows of `prometheus_infra.csv`. The identity set did not move at all. |
| **H3** | **Absent never counts as evidence.** In a fixture where a metric appears in 10 of 30 runs and is an identity in all 10, the rating reads `10/10 across 10 present` and never `10/30`. |  **hit.** `10 of 10 present run(s)` where the column was audited in 10 of 30 runs. Twenty runs without it are not in the denominator. |
| **H4** | **Overlapping runs inflate agreement.** Thirty runs on a 7-day rolling window agree **at least 20 percentage points** more than thirty disjoint runs over the same total span. Registered to make the case for reporting effective windows rather than raw counts. |  **hit, decisively.** 17 rolling runs collapse to **3.29 effective windows — 19%**. Three disjoint runs give 3.0. Reporting the raw count would have overstated the evidence fivefold. |
| **H5** | **Thin runs do not vote.** Runs graded C or D are excluded from persistence, and including them changes the rating for at least one pair in a mixed fixture — demonstrating why the exclusion exists rather than asserting it. |  **hit.** Six windows of `nyc_covid_dashboard.csv` all grade **D**; the pooling is refused outright rather than averaged. |
| **H6** | **The exception leads.** In a fixture with 29 redundant runs and 1 incident-window divergence, the divergence is the first line of output, and no persistence figure appears anywhere above it. |  **hit.** `KEEP — diverged during a window you labelled 'incident'` is line 0, above every rating. |
| **H7** | **Mixed engine majors are refused**, not pooled, and the refusal names both versions. |  **hit.** Engine majors `['0', '2']` refused, naming both. |
| **H8** | **Real corpus, registered blind.** `prometheus_infra.csv` cut into 8 consecutive windows: I expect **1–2 pairs** persistent in ≥ 7 of 8, and **at least one** pair that is an identity in some windows and not others. More than 4 persistent pairs would mean the windows are too similar to be informative, which is itself worth knowing. |  **MISSED, twice, and the reason is the finding.** As registered — 8 windows — every window grades **C or D** and the analysis is refused entirely. At the 3 windows this corpus actually supports: 1 persistent pair, **0 varying**, against a registered `>=1 varying`. See below. |

## What would stop this shipping

- **H2 fails** — no stable substrate; persistence counting is
  meaningless and the store ships as a plain log with no rating at all.
- **H3 fails** — absence counted as evidence against a metric. That is
  the direction that gets a metric archived wrongly, and it is not
  shippable at any threshold.
- **H6 fails** — a confidence figure appearing above its exceptions. The
  whole design intent, gone.

**H1 failing does NOT stop this shipping**, and the distinction matters.
It would mean the folklore is wrong: that metrics do not measurably
diverge during incidents, that "keep it for the outage" is an untested
belief this project has been repeating, and that the honest product is a
descriptive history with no incident claim attached. **That result gets
published either way**, because it is the more interesting outcome and
because nobody else in this market is going to run the test.

## What would falsify the reasoning rather than the code

**H1 and H2 both failing** would mean identity membership is unstable
generally — not specifically around incidents — in which case the
single-window audit is noisier than every document here has claimed, and
that is a finding about the engine rather than about the store.

## Explicitly NOT attempted

- **Inferring incident windows** from the data. Declared or unknown.
- **Adjusting the archive recommendation from history.** The store
  reports; it does not silently reweight what the engine says. A rating
  that changed the recommendation would make the audit depend on files
  on disk that a reviewer cannot see in the output.
- **Predicting future divergence.** "Has diverged" is a record.
  "Will diverge" is not available.
- **Cross-organisation baselines.** Someone else's telemetry says
  nothing about yours, and pooling it would require exactly the upload
  this product refuses.
- **Retention or pruning policy.** Files are small and the operator owns
  the directory.

## Negative controls, before the positive case

1. The same export audited twice with a different `run_id` → identical
   findings, and a persistence rating of `2/2` that surprises nobody.
   Catches a store that reports spurious differences from key ordering
   or float formatting.
2. Thirty runs of independent noise → **no persistent identities**.
3. Thirty runs of stationary correlated data → persistence high and
   **no exceptions**, because nothing changed.
4. Only then the planted exception: 29 stable runs and 1 divergence.

## Known handling decisions, fixed in advance

- **Basis and row-order must match** across pooled runs. Mismatch is a
  refusal, consistent with the drift check.
- **The rating is a count, never a probability.** "27 of 30 present
  runs" is checkable; "90% confidence" implies a model that does not
  exist here.
- **Exceptions are listed individually** with their run id, window label
  and date. A count of exceptions without their identity is not
  actionable.
- **No incident labels means the report says so**, in place of a
  persistence claim rather than beside it.
- **The store is plain JSON, one file per run**, readable and diffable
  without this tool. Anything that requires our code to inspect becomes
  a thing the operator has to trust.

---

## Result — 6 hit, 1 missed, and the miss is the useful one

Controls ran first and the positive case was gated on them in code.

### H8 failed, and the reason is a real constraint on the whole feature

Registered blind: *`prometheus_infra.csv` cut into 8 consecutive
windows*. It cannot be cut into 8 windows.

**Every one of those windows grades C or D, so H5's grade gate excludes
all of them and the analysis is refused entirely.** The arithmetic:

| windows | rows each | rows/metric | grade |
|---:|---:|---:|---|
| 2 | 218 | 19.7 | B |
| 3 | 145 | 13.1 | **B** |
| 4 | 109 | 9.9 | C |
| 8 | 54 | 5.3 | C |
| 12 | 36 | 3.5 | D |

437 rows over 11 metrics is 39.7 rows per metric in total, and the grade
gate wants 10 per metric **per window**. So this corpus supports
**three** windows, and four already falls under — the one 4-window run
that graded B did so only because a constant column had been dropped,
leaving 10 metrics instead of 11.

**H5 and H8 are in direct tension, and neither is wrong.** Multi-window
persistence needs roughly an order of magnitude more history than a
single audit of the same board. A dashboard export that comfortably
grades A once will not support the eight windows a persistence claim
wants. That was not obvious when the registration was written and it is
the most useful thing this scoring run produced.

**The practical consequence for anyone using this:** do not slice one
export into windows. Record a run per week, from separate exports, and
the store accumulates the history the rating needs. The feature is built
for a habit, not for a one-off.

### And at the three windows the corpus does support

One identity pair, `methodGET_status200 ~ request_rate_total`, held in
**3 of 3**. **Zero varying pairs**, against a registered `>= 1 varying`
— so H8 misses on that clause too, independently of the grade problem.
The identity structure of this instance is completely stable across its
whole history, which is good news for H2 and says nothing at all about
what happens during an outage.

### H4 was the clearest result

**17 rolling runs collapse to 3.29 effective windows — 19%.** Had the
report printed the raw count, it would have claimed five times the
evidence it has. This is the participation-ratio problem applied to the
store itself, and it is now measured rather than argued.

### Two fixtures that failed before the code did

**C3 was vacuous on the first run.** The stationary control reported "0
pairs, 0 exceptions" and passed — while containing no identity pairs at
all, so "no exceptions" was true of nothing. Two identities are now
planted and asserted. This is the same mistake as the drift cycle's D2
fixture, made again, two documents later.

**H6 perturbed window index 3 of a 3-window corpus**, so no incident
window existed and the ordering check ran against an empty report.
Both were scoring-script bugs rather than results, and are recorded here
because the difference between a bug and a miss is exactly what a
scoring run has to get right.

### H1 — still open, and it is the one that matters

The machinery detects a planted divergence; that is `score_history.py`
demonstrating itself. **Whether identity pairs genuinely break more
during real incidents than between quiet windows has not been tested**,
and cannot be until someone records a window because their service
actually broke.

Everything above is the store working. H1 is the store being worth
having.
