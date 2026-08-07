# Scale basis — pre-registered predictions

**Registered 31/07/2026, before the fixture existed and before any figure was computed.** Written first and to disk deliberately: the ordering is the evidence.

## What is being tested

§7 F4/F5 established that in an entity-indexed cross-section, **institution size does what a calendar trend does in a time series** — every scale-carrying column tracks it, so everything correlates with everything. Dividing each column by the scaling variable lifted the FDIC corpus from 2.52 effective signals to 14.78.

That result is **not yet trustworthy**, and this document exists because of the reason why. Dividing every column by a total produces **compositional data**. Compositional parts carry a known artifact — the *closure problem*: because shares are constrained, correlations between them are biased negative regardless of any real relationship. The 14.78 figure may be partly closure inflating apparent independence rather than the confound genuinely lifting.

**The decision this test settles:** do we ship the plain ratio basis, or is a centred-log-ratio (CLR) transform mandatory? CLR is materially more machinery and much harder to explain in a report. It should be paid for only if the evidence demands it.

## The fixture

A synthetic cross-section whose factor count is known by construction:

- `size` per entity — log-normal, spanning ~5 orders of magnitude, mirroring FDIC's real $7.4m–$370bn spread
- `k` latent **shape** factors determining how an entity divides its total across categories, independent of size
- column *j* = `size × share_j(shape factors) × lognormal noise`

Ground truth by construction:

| Basis | Independent quantities |
|---|---|
| dollar | **k + 1** (the k shapes, plus size) |
| ratio (÷ size) | **k** (size removed) |

Three variants:

1. **incomplete** — columns overlap and omit, so shares do not sum to 1. What real dashboards look like; FDIC's 39 columns do not partition assets.
2. **complete partition** — shares sum to exactly 1. Worst case: an exact linear constraint among the ratio columns.
3. **degenerate control** — shape held constant across entities, only size varies. True shape-factor count **zero**.

Fixture parameters: n = 2000 entities, d = 40 columns, k = 4 shape factors, unless a prediction names otherwise.

**The fixture is verified numerically before any prediction is scored** — factor rank via the singular spectrum of the share matrix, size range, partition sums, and dollar-view correlation level. The energy fixture was built wrong twice (a phase-shifted demand term, then a 365-day cycle inside a 62-day window) and both were caught only after conclusions had been drawn from them. Verification-before-use is the standing cost of those two.

## Predictions

| # | Prediction | Basis | Result |
|---|---|---|---|
| **C1** | Dollar-view participation ratio is **< 3**, far below the true k+1 = 5 | Size dominates every column, so the shape factors are buried. This is F4 reproduced under controlled conditions rather than observed in the wild. | **hit** — PR = **1.14** against a true 5. F4 reproduces under controlled conditions. |
| **C2** | Ratio-view participation ratio **recovers k = 4, within [3, 7]**, on the *incomplete* variant | If the cure works, removing the scaling variable should expose exactly the structure that was hidden. A band rather than a point because PR is a soft measure and noise lifts it. | **hit** — PR = **4.10** against a planted 4. The cure recovers the truth, not an artefact. |
| **C3** | Ratio view is **at least 2× the dollar view** on the incomplete variant | The directional claim §7 F5 already made; restated here against known truth rather than an unknown corpus. | **hit** — 3.6×. |
| **C4** | On the **complete partition**, mean pairwise correlation between ratio columns is **negative, ≈ −1/(d−1) = −0.026 at d = 40**, within ±0.05 | The closure artifact, quantified. If the measured bias does not match this form, my model of the problem is wrong and every conclusion below is void. | **hit to four decimals** — measured **−0.0256** against theory −0.0256. |
| **C5** | Closure bias **scales with width**: at d = 5 the mean pairwise r is ≈ **−0.25**, at d = 40 ≈ **−0.026** | −1/(d−1). This makes closure a *narrow-board* problem. If it holds, the ratio basis needs a width caveat, not a transform. | **hit** — d=5: **−0.2499** (theory −0.2500) · d=10: **−0.1111** (−0.1111) · d=40: **−0.0256** (−0.0256). |
| **C6** | **Participation ratio is NOT materially biased by closure.** On a pure random composition the constraint removes one dimension rather than inventing several, so PR ≈ d−1, which is the *correct* answer | PR is a spectral measure; the constraint contributes one near-zero eigenvalue. If PR *is* biased, plain ratios are unusable and CLR becomes mandatory. **This is the load-bearing prediction.** | **hit** — PR = **38.28** against d−1 = 39. The headline number survives closure. |
| **C7** | **Closure moves no pair across either threshold** — not the 0.90 redundancy-cluster cutoff, nor the 0.999 identity cutoff — at d ≥ 10 | −0.25 at its worst is nowhere near 0.90. If true, **plain ratios ship and CLR is not needed**. This is the prediction the ship/don't-ship decision hangs on. | **hit** — worst \|r\| observed: **0.094** at d=40, **0.160** at d=10, **0.284** at d=5. Not close to 0.90. |
| **C8** | Degenerate control: ratio view collapses to **PR < 1.5** | With shape constant, every ratio column is constant and the loader should drop them. If the ratio basis reports structure where none was built, it is manufacturing it. | **hit** — all 40 ratio columns constant; the loader drops them. No structure invented. |
| **C9** | Detected **subset sums survive ratio normalisation** | Division by a common column is linear, so an additive relation is preserved. Already observed on FDIC (`LNRE` and `LNLSGR` found in both views); registered here as a regression guard. | **hit** — same children recovered in both bases. |

## What would stop the ratio basis shipping

- **C6 fails** — participation ratio is biased by closure. Then the headline number is an artifact and CLR is mandatory.
- **C7 fails** — closure pushes pairs across 0.90 or 0.999. Then clusters and identities are contaminated and CLR is mandatory.
- **C8 fails** — structure appears where none was constructed. Then the basis is unsafe at any width.

## What would falsify the reasoning rather than the tool

**C4 failing.** The −1/(d−1) form is standard compositional theory. If the fixture does not reproduce it, the fixture is wrong, not the theory — and nothing else in this document may be read until that is resolved.

## Known interpretation gap, recorded in advance

On a true partition the tool will correctly report ≈ d−1 effective signals, and a reader will take that as "no redundancy here" about columns that are self-evidently shares of one total. That is an interpretation gap, not an estimator error, and it belongs in the output wording rather than the mathematics. Recorded now so it is not later mistaken for a defect discovered in the field.

---

## Result — 9 / 9, and the decision it settles

**Plain ratios ship. CLR is not needed.** C6 and C7 were the two that could have stopped it, and both hold with room to spare: closure leaves the participation ratio essentially untouched (38.28 against a correct 39), and its worst pairwise effect is 0.284 on a five-column board against a 0.90 cluster threshold.

The closure model reproduced to **four decimal places** at three widths. That is the strongest agreement between prediction and measurement anywhere in this project, and it is worth being suspicious of rather than pleased about: it agrees that precisely because −1/(d−1) is an algebraic identity of the constraint, not an empirical regularity. The fixture confirms the arithmetic is implemented correctly. **It does not confirm that real dashboards behave like random compositions**, and no real corpus has yet been run through a declared ratio basis.

So the honest scope: the cure is sound in principle and safe at our thresholds. Whether real telemetry has a clean scaling variable to divide by — the platform-engineering analogue of total assets — is untested and is the next thing to find out.

## The fixture was built wrong twice before it was built right

Recorded because the pre-registration required it, and because it is now a pattern rather than an incident. This is the **third** fixture in this project wrong on first construction — the energy fixture twice, now this one.

1. **Shares were exponential in the latent factors.** `exp(F @ L)` is nonlinear, and the engine measures *linear* correlation, so a rank-4 construction presented as rank 8+ in the space actually being measured. The ratio view read 8.26 against a planted 4, and had the verification block not existed that gap would have been written up as closure inflating the signal count. It was nonlinearity in my own fixture.
2. **The closure variants were generated with k = 0**, intending "no shape structure". That produces shares *identical* across entities — there is no composition to close over. Every column collapsed to `size/d` and correlated at exactly **+1.0000**, which I would have been reporting as a catastrophic closure bias. It was a degenerate fixture.
3. A third, smaller error was mine in the *verification* rather than the fixture: planted rank was checked on a noisy draw, so noise singular values cleared the threshold and reported rank 12 for a planted 4. Rank is a property of the construction and is now verified noiseless, with noise added back for scoring.

**The verification block caught all three before a single prediction was scored**, which is exactly what the pre-registration said it was for: *"nothing else in this document may be read until that is resolved."* Errors 1 and 2 would both have produced confident, wrong, publishable findings.

**Cost:** `test_scale_basis.py`, 14 checks, all green. Not yet folded into the main suite — it tests a basis that is not yet built.
