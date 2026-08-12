# Tied-data binning — pre-registered predictions

**Registered before the fix was written and before anything new was
measured.** The figures quoted from `MI_SCALING_PREREG.md` were recorded
during that cycle; nothing here has been re-run yet.

## The defect

`_discretise` claims equal-occupancy binning, and cannot deliver it when
a column has fewer distinct values than bins. Measured during the MI
cycle, on Poisson(4) — 13 distinct values against 12 requested bins:

- **four bins came back empty**
- occupancy ran **19–229** instead of a flat 100
- the MI bias floor fell from **0.0746 to 0.0290**, a 61% shift driven
  entirely by tie structure

This matters because **integer counters are what SRE telemetry is made
of**: HTTP status counts, pod counts, retry counts, queue depths,
replica counts. The nonlinear check therefore behaves measurably
differently on the exact data type the buyer has, and nothing said so.

## The part that cannot be fixed, and must be said out loud

**Equal occupancy is impossible on tied data.** If 30% of a column's
values are the integer 4, no binning puts less than 30% of the mass in
whichever bin contains 4. That is arithmetic, not an implementation
defect, and any fix claiming to equalise occupancy on counter data would
be lying.

So the goal is not equal occupancy. It is:

1. **no empty bins**, because an empty bin is a bin the quantile edges
   asked for and the data cannot fill — a pure artefact
2. **the degeneracy reported**, so a tied column is visibly a tied
   column rather than silently a different measurement

## The proposed change

Quantile edges on tied data land repeatedly on the same value, and every
bin between two identical edges is empty by construction. So: **collapse
duplicate edges before digitising.** Fewer bins, none of them empty.

Then report, per column, how many bins it actually got and whether it
was reduced.

**Explicitly NOT proposed:** jittering the data to break ties, which
invents variation that is not there; sampling; or dropping tied columns,
which would discard most SRE telemetry.

## Predictions

| # | Prediction | Result |
|---|---|---|
| **B1** | **No empty bins on tied data.** After the change, Poisson(4) at 12 requested bins produces **zero** empty bins. This is the defect, stated directly. | **hit.** Poisson(4) 4 empty -> **0**; status codes 10 -> **0**; pod count 7 -> **0**. Requires label compaction as well as edge collapsing — `digitize` leaves bin 0 empty whenever the minimum equals the first edge, which is the common case once duplicates are gone. |
| **B2** | **Continuous data is untouched.** On gaussian and lognormal columns the discretisation is **bit-identical** to today's — same bin assignment, every element. If a fix for tied data moves continuous data, it is not a fix, it is a different estimator. | **hit.** Gaussian, lognormal and uniform all bit-identical. |
| **B3** | **The bias floor moves for tied data, and that is correct.** Poisson(4)'s floor changes from the recorded 0.0290. Registered as a change, not as a target: the current figure is an artefact of empty bins, so a fix that left it alone would not have fixed anything. | **MISSED, and the miss overturns the original diagnosis.** The floor does not move: Poisson 0.017468 -> 0.017468, status codes 0.000436 -> 0.000436, pod count 0.005789 -> 0.005789. **Empty bins contribute p·log p with p = 0 — nothing — so collapsing them cannot change mutual information.** See below. |
| **B4** | **Occupancy stays unequal, and the report says so.** Poisson(4) still shows uneven occupancy after the fix — because it must — and the audit reports the column as tied rather than implying it was equalised. | **hit.** Poisson(4) occupancy 46-393 across 8 bins, reported as `tied`. Unequal, as it must be. |
| **B5** | **Detection is not lost.** The planted `y = x²` nonlinear pair is still detected, and independent uniform / lognormal / Poisson noise still yields **zero** couplings. The fix must not buy tidier bins with a worse detector. | **hit.** y = x² still found; independent uniform, lognormal, Poisson and status-code columns all yield zero couplings. |
| **B6** | **Real corpora are unchanged where they should be.** On the five existing corpora, effective signals, identities, clusters, subset sums and archive candidates are **identical**. Only nonlinear-coupling figures may move, and only on corpora containing integer counters. | **hit.** All five corpora identical — 5.573 / 4.329 / 4.711 / 5.097 / 8.891 effective signals, and every identity, cluster, subset-sum, archive and grade figure unchanged. |
| **B7** | **Cross-dataset comparison becomes safe enough to state.** Registered as the thing most likely to fail: after the fix, the bias floor across gaussian, lognormal, uniform, integer-count and Poisson fixtures spreads by **less than 25%** — against the 68% measured in the MI cycle. If it does not, the floor stays genuinely data-dependent, the per-dataset computation remains mandatory, and that limitation gets restated rather than quietly dropped. | **MISSED, badly, and it is the real finding.** Spread **99%**, worse than the 68% before: gaussian 0.0426, lognormal 0.0432, uniform 0.0454, Poisson 0.0175, pod count 0.0058, **status codes 0.0004**. A hundredfold range. See below. |
| **B8** | **`prometheus_infra.csv`, registered blind.** It contains real HTTP status counters. I expect the nonlinear pair *set* to be unchanged (the same 6 pairs) with at least one `mi_vs_gaussian` ratio moving by more than 5%. If the pair set changes, the fix altered what gets reported on real telemetry and needs its own justification. | **half.** Pair set unchanged, as registered. But **no ratio moved at all** — 0.0% on all six — because MI does not move (B3). The clause predicting >5% movement was predicated on the wrong mechanism. |

## What would stop this shipping

- **B2 fails** — continuous data moved. Not a fix; a different estimator
  wearing a bug report as a justification.
- **B5 fails** — the detector got worse. Empty bins were not costing
  anyone anything by comparison.
- **B6 fails** — a figure moved that has nothing to do with binning.

**B7 failing does not stop this shipping.** It would mean tie structure
genuinely changes what MI means on a dataset, that no binning repair
makes MI figures comparable across corpora, and that the honest position
is the one already in `MI_SCALING_PREREG.md`: compute the floor per
dataset and never compare MI across datasets. That result is more useful
than the fix.

## Negative controls, before the positive case

1. Gaussian and lognormal columns → discretisation bit-identical (B2).
2. Independent noise of every type → zero nonlinear couplings.
3. Monotone transforms → still gated out by `|r| >= 0.5`.
4. Only then Poisson and the integer-counter fixtures.

---

## Result — 6 hit, 2 missed, and the misses are worth more than the fix

Controls ran first: continuous data bit-identical, and every noise
control silent, before anything about tied data was measured.

### B3 disproves the diagnosis in `MI_SCALING_PREREG.md`

That document attributed the 61% bias-floor shift on Poisson(4) to the
four empty bins. **It was wrong, and this cycle proves it directly.**

Discretising the same fixture with the old and new code and computing MI
both ways gives bit-identical results to fifteen decimal places. Of
course it does: entropy sums `p·log p` over the bins, an empty bin has
`p = 0`, and `0·log 0` is zero. **Collapsing empty bins cannot change
mutual information, so it cannot have caused the floor to shift.**

What actually shifts the floor is that **tied data has less entropy to
begin with**. Poisson(4) at 12 requested bins has 13 distinct values and
a heavily peaked distribution; status codes have four values with 85% of
the mass on one. Fewer effective outcomes, lower MI between independent
draws, lower floor. That is a property of counter data, not a bug, and
no binning repair touches it.

**So this change is bookkeeping, not arithmetic.** It gives honest bin
counts, reports occupancy, and flags tied columns. It moves no number in
any report — B6 confirms that across all five corpora, and B8 confirms
it on real HTTP counters.

### B7 is the defect that actually matters, and it is not fixed

The bias floor across fixtures now spans **0.0004 to 0.0454 — a factor
of a hundred**, driven entirely by tie structure.

The consequence has not been stated anywhere before now:
`nonlinear_pairs` computes **one floor for the whole dataset**, by
shuffling every column. A dataset mixing continuous gauges with integer
counters gets a single threshold sitting far above what a counter column
can produce. **Tied columns are therefore systematically LESS likely to
be flagged than continuous ones in the same audit** — under-detection,
not over-detection.

That direction is the safer one, which is the only good news here. It
means the check is conservative on counter data rather than noisy, and
it explains why the status-code control found zero couplings. But
"conservative" and "correct" are different words, and until now the
report implied the check applied equally to every column.

**Registered in advance that B7 failing would not stop this shipping,
and it does not.** The honest position is the one already in
`MI_SCALING_PREREG.md` — compute the floor per dataset, never compare MI
figures across datasets — extended with one line it did not have: **do
not compare MI figures across columns of differing tie structure
within a dataset either.**

### What a real repair would need

A **per-column** bias floor rather than one per dataset, so each column
is judged against what its own marginal can produce. That is a different
change with its own cost — the floor is already 86% of the MI scan — and
it needs its own registration. Recorded here as the next repair rather
than attempted in the middle of this one.

### Where the corpora stand

31 tied columns across the shipped corpora: 18 in `nyc_covid`, 10 in
`act_air_quality`, 2 in `prometheus_infra`, 1 in `llm_leaderboard`. So
tied data is not hypothetical, and the under-detection above has been
applying to those columns all along without being written down.
