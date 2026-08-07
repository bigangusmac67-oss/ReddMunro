# Correlation drift — pre-registered predictions

**Registered before any drift code was written.** Nothing in this document
has been scored. The Result column is empty on purpose and gets filled in
once, afterwards, including the misses.

## What the check would claim

> These two metrics were independent in the first half of this export and
> are coupled in the second half.

That is a real SRE finding — a new dependency between services, a cache
that started shadowing a database, a retry loop that welded two subsystems
together — and the tool cannot currently say it. Everything today is
computed on the whole file at once, so a relationship that appeared
halfway through is averaged into a middling correlation and reported as
mildly related throughout, which is wrong in a way nobody would notice.

## Why this needs a pre-registration rather than just a patch

Because the obvious implementation — split the rows, correlate each half,
subtract — **cannot distinguish the finding from the most common event in
telemetry.** During an incident every metric moves together, so every
pair's correlation rises at once. A naive difference would report hundreds
of new couplings for what is one outage, and each one would come with a
metric pair and a number, which is exactly the shape of a finding someone
acts on.

This is the same disease as the trend confound, one level up: there, a
shared timeline inflated correlation; here, a shared *event* inflates the
change in correlation. The cure has to be built in from the start, not
added when someone complains.

## The structural facts that matter before anything is measured

**1. Rows must be ordered in time. This check has no meaning otherwise.**

The FDIC corpus is 1,915 banks; the leaderboard corpus is 541 models. In
both, row order is arbitrary — "the first half of the banks" is not a
period. Splitting those and comparing halves would produce a confident
statement about a before and an after that do not exist.

This is not hypothetical. `AI_EVAL_PREREG.md` finding 1 records the
"Calendar trend confound" check **firing on 541 language models**, naming
shared calendar movement in data with no calendar. That defect is open.
**Shipping a second time-dependent check without gating it on a declared
ordered basis would be knowingly repeating a bug we have already
published.** So the gate is a precondition of this work, not a follow-up
to it — and fixing it fixes the trend check too.

**2. Splitting the rows halves the evidence, twice over.**

Correlation on n/2 rows is noisier than on n. A pair whose true
correlation never changes will still show apparent drift from sampling
alone, and the smaller the file the more drift it will appear to show.
An absolute threshold ("flag if |Δr| > 0.3") is therefore wrong at every
sample size except the one it was tuned on.

The principled alternative is the **Fisher z-transform**: `z = arctanh(r)`
is approximately normal with standard error `1/√(n−3)`, so the difference
between two windows has `SE = √(1/(n₁−3) + 1/(n₂−3))` and the test
statistic is scale-free and sample-size aware by construction. That is
what will be used, and D5 is the prediction that it actually calibrates.

**3. There are d(d−1)/2 pairs, and they all get tested.**

At 60 metrics that is 1,770 simultaneous tests; at a nominal 5% that is
~88 false positives before any real finding. Multiplicity control is not
optional. Benjamini–Hochberg FDR, because the alternative — Bonferroni at
1,770 — would suppress everything real too.

## Predictions

| # | Prediction | Result |
|---|---|---|
| **D1** | Independent noise, 2,000 × 20 → zero pairs. | **hit** — 0 of 190 tested. |
| **D2** | Stationary correlated data (mean \|r\| > 0.6) → zero pairs. | **hit, on the second attempt at the fixture.** The first fixture reached mean \|r\| = **0.419**, not the registered 0.6 — it passed while testing something weaker than the prediction. Rebuilt at \|r\| = **0.606**; still 0 flagged. The spec is now asserted in code, so a too-easy fixture fails rather than reads green. |
| **D3** | Planted drift detected **and ranked first**. | **hit** — top pair `m00 ~ m01`, r **+0.009 → +0.801**, and the only pair flagged. |
| **D4** | Naive flags > 40% of pairs; shipped flags < 5%. | **hit, decisively.** Naive **100.0%** — every single pair — against shipped **1.5%**. The load-bearing prediction, and the naive version failed exactly as badly as feared. |
| **D5** | False-positive rate ≤ 0.05 over 200 draws. | **hit, by a wide margin** — 6 in 38,000 tests = **0.00016**. Passing 300× under the registered bound is not obviously good news; see the limitation below. |
| **D6** | At n = 120, detected or refused — **never a different pair**. | **hit** — 1 flagged and 0 wrong at n = 2,000, 500 and 120. |
| **D7** | Entity-indexed data gets no drift result. | **hit** — `status = not_applicable`, no pairs named, on undeclared row order. |
| **D8** | No existing figure moves. | **hit.** Wired in; all five corpora identical — 5.573 / 4.329 / 4.711 / 5.097 / 8.891 effective signals, and identity, cluster, subset-sum, nonlinear and archive counts unchanged. One thing did change on purpose: the trend check stopped firing on `llm_leaderboard.csv`, which is the defect being closed, not a figure moving. |
| **D9** | Real infrastructure: **0–2 pairs**, registered blind. | **hit — 2 of 55 pairs**, and the finding is real. See below. |

## What would stop this shipping

- **D2 fails** — fires on stable correlation. Not shippable at any
  threshold; the premise is wrong.
- **D4 fails** — cannot separate a shared incident from pair-specific
  drift. **Withdraw the check rather than tune it.** Tuning a threshold
  until a known confound stops firing is how you get a detector that
  works on one dataset.
- **D7 fails** — would ship a second check that lies about unordered
  data, having already documented the first.
- **D8 fails** — an additive change that moved an existing number means
  something was modified that should not have been.

D5 failing is recoverable: it means the test statistic or the FDR
application is wrong, not that the idea is.

## What would falsify the reasoning rather than the code

**D4 failing in a specific way.** If the incident-adjusted statistic
cannot separate "these two coupled" from "everything moved," that is not
a bug — it is evidence that the two are **not separable from values
alone** at this data shape. The honest response is then to withdraw the
check and record why, as with the rolling-average detector, rather than
ship something that is right on quiet data and catastrophic during the
exact event people will be looking at.

## Explicitly NOT attempted

- **Changepoint detection.** "*When* did they couple" is a harder,
  different problem. A two-window split cannot answer it and will not
  pretend to.
- **Rolling windows.** More sensitive, far more multiplicity, and the
  windows overlap so the tests are not independent — which breaks the
  FDR argument this rests on. Needs its own registration.
- **Lagged correlation.** A leads B by three minutes is a real and
  useful finding, and it is not this one.
- **Any causal reading.** The check can say two metrics became related.
  Whether one caused the other, both followed a third, or someone changed
  an exporter, is not in a CSV of values.
- **Unequal or user-chosen split points.** Halves, until there is a
  reason for anything else.

## Negative controls, to run BEFORE the positive case

In this order, and D3 does not get run until D1 and D2 have passed:

1. Independent noise → zero.
2. Stationary correlated structure → zero.
3. Stationary structure **plus a shared incident** → zero, or as near as
   the D4 threshold allows.
4. Variance shift without correlation shift — one metric's variance
   multiplied by 10 in the second window while its relationships are
   unchanged. Correlation is scale-invariant so this must not fire;
   if it does, the implementation is not computing what it claims.
5. Only then the planted positive.

Three of the four rejected `derived_aggregates` designs looked correct on
a planted positive and failed only on data with no relation in it. That is
why the order is fixed here in writing.

## Known handling decisions, fixed in advance

- **Basis.** Computed on the **differenced** view. Two metrics that share
  a trend will show high correlation in both windows regardless, and the
  question is about their relationship, not their common drift.
- **Missing rows.** Listwise deletion as everywhere else, applied before
  the split, so the two windows can differ in length. The formula already
  takes `n₁` and `n₂` separately, which is one reason for choosing it.
- **Minimum size.** Fewer than 30 usable rows per window → the check
  reports insufficient evidence and names no pairs. Registered before
  seeing what that does to any corpus.
- **Output shape.** Pair, `r` in each window, the z-statistic, the
  adjusted p-value, and the direction. No verdict.
- **Assurance grade.** Drift is strictly more demanding of rows than the
  existing checks. If the file grades C or D, the drift section says so
  in place of results.
- **The lexicon entry gets two readings, like every other.** *Consistent
  with*: a new dependency between services. *But also*: a change in what
  the exporter emits, a sampling-rate change, or a deploy that altered
  one metric's definition midway. *Distinguish by*: whether either metric
  has a discontinuity in its own distribution at the split point.

---

## Result — 9 of 9, and three bugs the wiring exposed

### What closed alongside it

`AI_EVAL_PREREG.md` finding 1 — the trend check firing on 541 language
models and naming shared **calendar** movement in data with no calendar —
is fixed. The leaderboard's trend gap is **−0.503**, just past the −0.5
firing threshold, so it fired before and does not now. `nyc_covid` at
−3.467 still fires, as it must.

Both checks consult one gate (`TIME_DEPENDENT`), so they cannot drift out
of step with each other. Row order is **evidence, reported as ASSUMED**
when it comes from a time-like column in the header, and DECLARED when
someone says so — `--ordered` / `--not-ordered` on the CLI, and two
buttons on the page, since the browser has no flags and a visitor
otherwise has no way to make the statement at all.

The header signal turns out to separate the corpora exactly: all four
time-series files carry `timestamp`, `date`, `datetime` or `month`, and
**both entity-indexed corpora carry none.**



Controls ran first and the positive case was gated on them in code, not
by intention: `score_drift.py` exits before D3 if any control fails.

### The real finding — D9

Registered blind at 0–2 pairs before running. Two came back, on 437 rows
of live Prometheus telemetry:

| pair | first half | second half | z | q |
|---|---:|---:|---:|---:|
| `node_memory_Buffers_bytes` ~ `node_memory_Cached_bytes` | **+0.895** | **+0.206** | −12.28 | 6.3e−33 |
| `node_memory_Buffers_bytes` ~ `node_memory_MemAvailable_bytes` | −0.174 | +0.088 | +3.30 | 2.7e−02 |

Buffer and page-cache memory moved almost in lockstep for the first half
of the window and then largely stopped. **The tool could not previously
say this at all** — averaged over the whole file, the pair reads as
moderately correlated throughout, which is true of no part of it.

Both flagged pairs involve the same metric, which is what a change in one
thing's behaviour looks like, and is a hint that the honest presentation
groups by metric rather than listing pairs independently.

The check is doing the intended job on the intended data. What it is
**not** doing is saying why, and it must not: a cache eviction policy
change, a workload shift and an exporter upgrade all look identical here.

### D4, and how bad the naive version was

The registration guessed the naive absolute-difference version would flag
more than 40% of pairs when a shared spike hits the last 3% of rows. It
flagged **100%** — all 66 pairs, every one of them, for a single event.
Median-centring the z-shift brought that to **1.5%**.

That gap is the entire justification for this being a pre-registration
rather than a patch. Anyone would have shipped the naive version; it
looks perfect on quiet data and produces 66 confident findings during the
one event an operator is actually looking at.

### Two things that did not go cleanly

**The D2 fixture missed its own spec.** The registration said mean
\|r\| > 0.6 and the first fixture produced 0.419. It passed — but it had
tested a weaker claim than the one written down, and the green tick would
have concealed that. Rebuilt to 0.606, and the fixture spec is now
asserted so it fails rather than flatters. A control that is easier than
registered is not a control.

**D5 passed 300× under its bound, which is a limitation, not a
triumph.** 0.00016 against a registered 0.05 means the test is far more
conservative than the theory requires. The cause is the `max(SE, MAD)`
denominator floor, which was chosen to keep the check honest on
non-stationary files and inevitably costs power. Strong drift is still
found — D3 at 24σ, D6 down to 60 rows per window — but **moderate drift
is probably being missed, and this has not been measured.** Registering a
detection-power prediction and scoring it is the obvious next cycle.

### Three bugs the wiring exposed, none of which the detector had

**1. A registered handling decision was written and then not
implemented.** The registration says a file grading C or D reports
insufficient evidence in place of drift results. The first wired version
returned **114 pairs on grade-C** `nyc_covid_dashboard.csv` — 8.3% of
1,378 pairs, on 8.6 rows per metric, halved again by the split. Splitting
the file makes drift *more* demanding of history than every other check,
not less. Now gated; NYC and MTA both return `insufficient_evidence`.

This is the failure mode pre-registration is specifically for: the
decision was correct, made in advance, and then quietly skipped during
implementation, where nothing would have contradicted it.

**2. The CLI rendered a gated check as `✓ clear`.** On FDIC with
`--not-ordered`, both time-dependent checks printed green — which reads
as *we looked and found nothing* when nothing was looked at. Three states
now: `○ not implemented`, `– not applicable`, `✓ clear`. The payload
carries `skipped` separately from `available` so an unbuilt check and a
gated one can never merge, and a test asserts they stay distinguishable.

**3. Reporting one correction while deciding by another.**

### The implementation bug, caught by reading the output

The first version decided rejections by Benjamini–Hochberg but *displayed*
a Bonferroni `p × m`. So D9's second pair showed an adjusted p of 0.053
beside a q = 0.05 threshold it had just passed. The rejection was right and
the printed number was from a different correction — reporting by one
standard while deciding by another. Now reports true BH q-values.

### Still true

- Two windows only. **Where** the change happened is not answered, and
  the check does not pretend to.
- No causal claim, ever. Two metrics became related; why is not in a CSV
  of values.
- `spread_inflation` is reported so a file too non-stationary for the
  normal theory says so rather than quietly returning a number.
