# MI scaling wall — pre-registered predictions

**Registered before any change to the scanning logic.** The gap was disclosed first, in `REAL_DASHBOARDS.md` §9, with measurements and no fix attempted. This document is the repair cycle.

## Why this needs a pre-registration at all

A performance fix normally does not. This one does, because **every obvious optimisation changes what gets measured**, and the thing being measured is a detector whose entire purpose is finding dependence a correlation matrix misses. Speeding it up by looking at fewer pairs, or by loosening the null it is compared against, silently converts it into a different and weaker detector that still reports under the same name.

The four rejected `derived_aggregates` designs and the two withdrawn rolling-average designs are what that mistake costs when made without controls.

## Where the time actually goes — measured, 60 metrics × 2,000 rows

| Stage | Cost | Share of MI |
|---|---:|---:|
| `_mi_matrix` — one pass over all 1,770 pairs | 1.40 s | 14% |
| `_mi_bias_floor` — **the same pass, six more times** | **8.39 s** | **86%** |
| total | 9.79 s | |

Two things this overturns:

1. **The bottleneck is the null, not the signal.** `_mi_bias_floor(reps=6)` shuffles every column and recomputes the whole pair matrix, six times. The real MI pass is a seventh of the work.
2. **The obvious screen barely helps.** `nonlinear_pairs` only ever reports pairs with `|r| < 0.5`, so MI for high-correlation pairs is computed and discarded. On this fixture that is **84 of 1,770 pairs — 5%**. Worth taking because it is free and provably lossless, but it is not the fix. My prior that this was the main lever was wrong.

## The structural fact the repair rests on

`_discretise` uses **equal-occupancy** binning. Every column is mapped to bins holding equal counts, so after discretisation **every column has the same (uniform) marginal distribution by construction**.

The shuffled null therefore does not depend on the data. For independent columns with uniform marginals, the finite-sample MI bias is a function of `n` and `bins` alone. `_mi_bias_floor` currently estimates a quantity that is close to a constant by averaging `6 × C(N,2)` samples of it.

**If that is true, the floor can be estimated from a small sample of pairs at the same accuracy.** If it is false, the floor is data-dependent and must keep being computed in full — and the whole repair collapses to Part A.

## The three parts, separated by risk

| Part | Change | Risk |
|---|---|---|
| **A** | Joint entropy via `np.bincount` on a combined index instead of `np.unique(axis=0)`, which sorts n rows per pair | **None claimed.** Must be bit-identical. |
| **B** | Skip MI on pairs with `\|r\| ≥ 0.5` in the real pass — they are gated out downstream regardless | **None claimed.** Must be provably lossless. |
| **C** | Estimate the bias floor from a sampled subset of pairs rather than all of them | **Real.** Changes a number that decides what fires. |

A and B are refactors that must not move any figure. C is the only part that can, and it carries every prediction below that could fail.

## Predictions

| # | Prediction | Basis | Result |
|---|---|---|---|
| **M1** | (as registered) | | **hit** — max absolute difference **0.000e+00** on gaussian, lognormal, integer-count and heavily-tied data. |
| **M2** | (as registered) | | **hit** — identical on all six corpora, pair-for-pair. |
| **M3** | (as registered) | | **FAILED — 68% spread**, against a 10% threshold. Five of six datasets agreed to four decimals at 0.0746; Poisson(4) came in at 0.0290. Cause: equal-occupancy binning cannot equalise data with fewer distinct values than bins — Poisson(4) has 13 distinct values and left **4 of 12 bins empty**, occupancy 19–229 instead of a flat 100. **Part C abandoned, per the registered rule.** |
| **M4** | (as registered) | | **hit** — 0.15%–0.95% error on all six datasets including Poisson. **Scored but NOT acted on:** M4 concerns sampling within one dataset, M3 concerns sharing across datasets, and the registered stopping rule was written against M3. See the note below on why that distinction nearly became a post-hoc rescue. |
| **M5** | (as registered) | | **hit for A+B**, the version shipped — identical pairs and order on all six corpora. A+B+C also matched, but was not shipped. |
| **M6** | (as registered) | | **hit** — zero on independent uniform, lognormal and Poisson at 2000×40. |
| **M7** | (as registered) | | **hit** — planted y = x² still detected; monotone transforms still correctly gated out by \|r\| ≥ 0.5. |
| **M8** | (as registered) | | **hit, with room.** Scan at 60 metrics 9.62s → **0.19s (52×)**; at 200 metrics 108.7s → **1.72s (63×)**. Full `audit()` at 200 metrics is now **12.8s**. Target was 30s. |

## What would stop this shipping

- **M3 fails** — the floor is data-dependent. Part C is abandoned; ship A and B alone and re-register the wall with whatever they achieve.
- **M5 fails** — any corpus reports a different pair set. Not shippable at any speed.
- **M6 or M7 fails** — the floor has moved enough to change the noise/signal boundary. The estimator is then wrong in a way speed does not excuse.

## What would falsify the reasoning rather than the code

**M3 failing** would mean equal-occupancy binning does not fix the marginals the way I claim, which would also cast doubt on the monotone-invariance property the binning was chosen for in the first place. That is a bigger finding than this repair and would need its own investigation before anything here proceeds.

## Explicitly NOT attempted

- **Row subsampling.** Changes the estimator's bias floor directly — the exact quantity we already had to fix once — and trades a known cost for an unknown bias. Rejected before measurement, on principle.
- **Caching the floor across runs.** A cache keyed on `(n, bins)` would be correct if M3 holds, but introduces persistent state whose staleness is invisible. The engine has no state today and that is worth more than the milliseconds.
- **Capping N with `○ not checked`.** A last resort if A+B+C miss M8. Honest, but it removes a check rather than fixing it.

## Negative controls, to run BEFORE the positive case

Three of the four rejected `derived_aggregates` designs looked correct on a planted positive and failed only on data with no relation in it. So, in this order:

1. Independent uniform noise, 2,000 × 40 → **zero** couplings reported.
2. Independent lognormal noise (skewed marginals, to stress equal-occupancy binning) → **zero**.
3. Pure monotone transforms of one variable (x, x³, exp x) → high MI **and** high `|r|`, so gated out by `abs(r) < 0.5`; must not appear.
4. Planted y = x² with r ≈ 0 → **detected**, as it is today.
5. The five real corpora → **identical output**, pair-for-pair and order-for-order.

Only after all five pass does any timing number get quoted.

---

## Result — 7 hits, 1 failure, and a stopping rule that nearly got rescued

**Shipped: Parts A and B only. Part C abandoned, as registered.**

| | scan at 60 metrics | scan at 200 metrics | full `audit()` at 200 |
|---|---:|---:|---:|
| before | 9.62 s | ~108.7 s | ~110 s |
| after (A+B) | **0.19 s** | **1.72 s** | **12.8 s** |
| | 52× | 63× | |

### The failure, and what it revealed

M3 was the load-bearing prediction and it failed at 68% against a 10% threshold. The cause is a real property of the engine, not of this repair: **`_discretise` claims equal-occupancy binning, but cannot deliver it when a column has fewer distinct values than bins.** Poisson(4) has 13 distinct values against 12 requested bins; four bins came back empty and occupancy ran 19–229 rather than a flat 100. The bias floor fell from 0.0746 to 0.0290 — a 61% shift driven entirely by tie structure.

That matters beyond this cycle. **Integer counts are everywhere in telemetry** — request counts, error counts, pod counts, retries — so the MI check behaves measurably differently on count data than on continuous data, and nothing said so before now. Registered as a limitation in its own right.

### The moment this nearly went wrong

M4 passed everywhere, including on Poisson. It is tempting — and I was tempted — to argue that Part C only ever depended on M4 (sampling *within* a dataset) and never on M3 (sharing *across* datasets), so the stopping rule was mis-specified and C should ship anyway.

That argument is probably correct on the merits. It is also **exactly what a post-hoc rescue looks like**, and "I wrote the wrong stopping rule" is what everyone says when they want to keep going. The registered rule said M3 failing abandons C. It does.

What made the decision cost nothing was testing **A+B in isolation**, which had not been planned as a separate arm. A+B alone delivers 63× and clears the M8 target by 17×. Part C would have added a further 5× to a stage that is no longer the bottleneck — a contested optimisation bought for a speedup nobody needs. Had A+B not been measured separately, the pressure to rescue C would have been real.

The general lesson, worth more than the speedup: **when a stopping rule fires, measure the uncontested subset before arguing about the rule.**

### What was not touched

The floor is still computed in full over every pair, six times. It remains 86% of what is left, and it is now 86% of 0.19s rather than of 9.62s. No caching, no sampling, no row subsampling. `nonlinear_pairs` reports the same pairs it always did.

---

## Postscript — Part A shipped a crash to every visitor, and the suite could not see it

Part A was validated as **bit-identical on six corpora**, and it was. It was also, for five days, the reason **every audit on the live site died before producing a number**:

```
TypeError: Cannot cast array data from dtype('int64') to dtype('int32')
according to the rule 'safe'
```

`np.bincount` casts its argument to `np.intp` under the `'safe'` rule. Part A wrote the combined index as `D[:, i].astype(np.int64) * K + D[:, j]`. **`np.intp` is 64-bit on x86-64 and 32-bit on wasm32.** So the explicit int64 upcast is free and correct on the machine the tests run on, and an unconditional `TypeError` on the platform every visitor uses.

### Why 134 tests said nothing

Not one of them runs on wasm32. Every corpus, every negative control, every bit-identical comparison was executed by CPython on a 64-bit host. **The engine was validated exhaustively against the wrong platform** — and the browser is not a deployment target here, it is *the* product. `pip install redd-munro` was never affected.

This is a different failure class from the ones catalogued so far, and worth naming: the others are cases where the tool computes a **wrong number**. This one computes nothing at all, and does so only where nobody was looking. **A test suite constrains the platform it runs on and says nothing about any other.**

### The fix, and what it does not buy

The index is now built in `np.intp`, which is correct on both platforms by construction rather than by coincidence. Verified still bit-identical against the sorting implementation Part A replaced — max |diff| **0.000e+00**.

A guard was added on `K` while fixing it, and it caught a second latent bug the first version of the test walked straight into: an out-of-range bin count asked `np.bincount` for a **6.7 GiB** dense table on 64-bit rather than failing. `_bins_for` caps K at 12 so no real call reaches it, but a `MemoryError` from inside an MI scan would have been a genuinely bewildering thing to debug.

Four checks added as section 18. They assert **the invariant, not the platform** — that no int64 upcast feeds `bincount`, and that the index is `can_cast`-safe to `intp` — because the test host is 64-bit and cannot reproduce the failure by running the code. **That is a weaker guarantee than the other 136 checks and is recorded as such.** The honest fix is a wasm32 CI job; until there is one, the browser is verified by loading it.
