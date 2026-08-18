# The controlled-flat archive hazard — pre-registered predictions

**Registered before any detector was written.** The defect below is
already reproduced and is not a prediction; everything under
*Predictions* is, and none of it has been measured.

## Where this came from

A review proposed importing a thermodynamic framework — Kramers escape
rates, exponential collapse of boundary-maintenance cost against
power-law growth in surface observables — and separating telemetry
channels into state observables and maintenance effort.

**The framework is not adopted, and the reasons are part of the record.**
ΔV and D are parameters of an assumed stochastic differential equation.
Estimating a barrier height from a wide CSV requires either fitting an
SDE, which means assuming its form, or observing escape events and
measuring their rate, which requires collapse events this project does
not have. Neither is available model-free, and both are the same missing
ground truth that leaves `HISTORY_STORE_PREREG.md` H1 unscored.

What survived the review is smaller, needs no physics, and is
reproducible in forty lines. That is the whole of what is registered
here.

## The defect, already measured

A metric a control loop is actively holding still becomes a
near-deterministic function of the load and the effort absorbing it. It
therefore carries no residual variance of its own, and
`deletion_candidates` archives anything at or below
`max_unique = 0.02`.

Simulated proportional controller, gain 0.92, 600 rows, six columns,
three seeds. As the loop tightens:

| independent noise in the held channel | unique variance | offered for ARCHIVE |
|---|---|---|
| 1e-1 | 0.9018 | no |
| 3e-2 | 0.4058 | no |
| 1e-2 | 0.0671 | no |
| 1e-3 | 0.0007 | **yes** |
| 1e-4 | 0.0000 | **yes** |
| 1e-6 | 0.0000 | **yes** |
| 0 | 0.0000 | **yes** |

Monotone, and stable across seeds. **The arithmetic is correct**: within
the observed window the channel genuinely is a restatement of the
others. The hazard is that the relationship is *contingent on the
controller working*, and nothing in a CSV of values distinguishes
flatness that is maintained from flatness that is natural.

**This is the first concrete mechanism for H1.** Until now "identical
metrics come apart during incidents" was an assertion with no proposed
cause. A control loop failing is a cause, and it predicts *which* pairs
come apart rather than merely that some do.

## Predictions

| # | Prediction | Result |
|---|---|---|
| **CF1** | **Effort channels are identifiable without naming them.** On a corpus containing a controller, at least one column pair shows the signature *low unique variance in one member, high variance in the other, with the low-variance member's residual predictable from the high-variance member's level*. Registered because if this signature does not separate controllers from ordinary redundancy, no detector is possible without a declaration. | *unscored* |
| **CF2** | **The signature is not present in ordinary redundant pairs.** Across the five shipped corpora, the same test fires on **fewer than 10%** of existing archive candidates. Registered as the thing most likely to fail: if it fires on most pairs it is detecting redundancy, which is already detected, and adds nothing. | *unscored* |
| **CF3** | **Held-flat channels diverge during incidents; ordinary redundant pairs do not.** Given a window somebody labelled `incident`, archive candidates with a correlated effort channel break their relationship more often than archive candidates without one. **This is the prediction that matters and it cannot be scored without a real incident window** — the same dependency as H1, and it is registered now so that it is already on disk if such a window ever arrives. | *unscored* |
| **CF4** | **A declared effort channel beats an inferred one.** Where a user declares effort columns explicitly, detection is more accurate than any name-based inference over the same corpora. Registered to settle by measurement whether `--effort` should exist at all, rather than by preference. | *unscored* |

## What would stop a detector shipping

- **CF2 fails** — it fires on ordinary redundancy. Then it is a worse
  spelling of a check that already runs.
- **It infers effort channels from metric names.** `gc_pause_seconds`
  looking like effort is the same guess as `node_load15` matching
  `node_load1`, which the reference scan already refuses. If it ships it
  ships as a declaration, in the `--basis` / `--ordered` family, absent
  by default and named in the output.
- **It ships as a domain lens.** Lenses rename findings and cannot reach
  the mathematics; that invariant is asserted in the test suite and
  stated on the site. A lens that changed what is computed would break
  the only guarantee that makes lenses safe.
- **It divides by a variance that goes to zero.** Effort over surface
  variance explodes for arithmetic reasons under tight control. Any
  floor is arbitrary, and `MI_SCALING_PREREG.md` plus
  `BINNING_PREREG.md` are two full cycles on how much work an arbitrary
  floor becomes.

## Negative controls, before the positive case

1. Independent noise columns → no controller signature.
2. An ordinary identity pair (a unit conversion) → no signature. This is
   the control that CF2 turns on, and it must contain real identity
   pairs — a fixture with none would make "no false positives" vacuously
   true, which is the defect the history cycle's C3 control shipped with
   and had to be rewritten to fix.
3. A metric plotted against its own rolling average → **expected to
   produce the signature**, and that is a false positive, not a find. A
   smoothed series and a controlled series look alike from values alone.
   Two rolling-average detectors have already been built and withdrawn
   on evidence, one of them producing 476 false positives. **A new
   detector in the same family should be assumed to inherit that until
   it is shown not to.**
4. Only then, the simulated controller and any real corpus.

## Known base-rate problem, registered in advance

Effort tracks load, and load has seasons. An autoscaler climbing on the
busiest day of the year is not a system approaching collapse. Any
detector keyed to rising effort fires every Black Friday, and CF2's 10%
bound is the first place that will show up.

---

## Result

*Unscored. No detector has been written, and CF3 needs an incident
window nobody has supplied.*

## Status of the shipped tool

The limitation is documented in `README.md` under engine boundaries and
on the site under **Engine boundaries**, stated as a defect with no flag
and no detection. **Nothing about the audit changed.** The hazard is
disclosed rather than fixed, because a fix that has not been scored
would be exactly the false confidence this project exists to refuse.
