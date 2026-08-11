# Continuous auditing — pre-registered predictions

**Registered before any diff code was written and before any measurement
was taken.** No number below has been checked. The Result column is empty
on purpose and gets filled in once, afterwards, misses included.

## What the feature would claim

> Your dashboard's redundancy got worse this week, and here is what
> changed.

The engine today answers *how redundant is this?* Continuous auditing
answers *did it change?* — which is a different question with a different
failure mode, and the second does not follow from the first.

## Why this needs registering rather than just building

The obvious implementation compares two `build_contract` payloads and
reports what moved. **It will fire every week**, because effective-signal
count is a continuous estimate that moves with the sampling window, the
number of rows, and whether the period contained an incident.

A governance product that emails someone every Monday because the
participation ratio drifted from 5.6 to 5.4 earns a mail rule inside a
month, and after that it is decoration that still appears on the invoice.

This is the drift problem one level up, and the drift cycle already
showed how badly the obvious version behaves: a naive absolute difference
flagged **100% of pairs** for one shared incident, where the
median-centred version flagged 1.5%. There is no reason to expect the
naive contract diff to behave better, and one specific reason to expect
it to behave worse — see the incident confound below.

## The structural fact the whole design rests on

**A contract contains two kinds of finding, and they have completely
different stability.**

| | Examples | Nature | Expected behaviour |
|---|---|---|---|
| **Set-valued** | identity pairs at r ≥ 0.999, subset sums, redundancy cluster membership | discrete membership facts | stable — a pair either is or is not an exact restatement |
| **Scalar** | effective signals, % redundant, per-metric unique variance | continuous estimates | never identical between two runs |

**The registered hypothesis is that set-valued findings are stable enough
to alert on and scalar estimates are not.** If that is wrong — if the
sets flicker as much as the numbers — then there is no stable substrate
to build alerting on, and the honest product is a scheduled report rather
than a monitor.

`build_contract` already produces a stable, versioned payload and
`dataset_id` is a content hash, so the shape to diff exists. The question
is not how to compute a difference; it is which differences mean
anything.

### Four reasons a contract changes with nothing having gone wrong

1. **Sampling.** A different window is a different sample. Correlations
   move, and so does every quantity derived from them.
2. **The incident confound.** During an outage everything moves together,
   so correlations rise and the participation ratio **falls**. A naive
   comparison reads a bad week as a sudden increase in redundancy —
   precisely backwards, and it fires hardest exactly when an operator is
   busy.
3. **Row count.** More history sharpens an estimate. The estimate moving
   toward the truth is not a finding.
4. **A changed metric set.** If a panel is added or removed, `d` changes,
   and **participation ratio is not comparable across different `d`.**
   5.6 signals from 11 metrics and 5.6 from 14 are not the same
   statement. This is the trap most likely to produce a confidently wrong
   headline.

## Predictions

| # | Prediction | Result |
|---|---|---|
| **C1** | **Overlapping windows report no set change.** Same dashboard, rows 1–N and rows 0.2N–1.2N. Zero identity-pair changes, zero subset-sum changes. | |
| **C2** | **Scalar drift over those same overlapping windows exceeds 2% of the effective-signal count.** Registered as a prediction *against* scalar alerting: if the number moves this much when nothing happened, thresholding it is not viable. If it turns out to be under 2%, my reasoning is wrong and scalar alerting deserves reconsideration. | |
| **C3** | **Disjoint windows, no structural change, report no set change.** Harder than C1 — no shared rows at all. | |
| **C4** | **A planted duplicate is reported, and only it.** Add one column that is an exact restatement of an existing metric in window B only. Exactly that pair is reported as new; no other pair changes. | |
| **C5** | **An incident does not read as degraded hygiene.** Inject a shared spike across all metrics in the last 3% of window B. Registered: **zero set changes**, while the scalar effective-signal count falls by more than 10%. This is the load-bearing prediction — it is the one that decides whether alerting is shippable. | |
| **C6** | **More rows, same panels, no change.** Window B is window A plus 50% more history. Zero set changes. | |
| **C7** | **A removed panel is reported as a removal only.** Dropping a column must not report the surviving pairs as changed, and must not report a scalar comparison at all. | |
| **C8** | **Different metric sets refuse scalar comparison.** When `d` differs between two contracts, the diff reports the membership change and explicitly declines to compare effective-signal counts, saying why. | |
| **C9** | **Archive candidates are measurably less stable than identity pairs.** They are derived from a threshold on a continuous quantity (`unique < 0.02`), so membership should flicker at the boundary. Registered: across C1's overlapping windows, archive-candidate set churn is **at least 3×** identity-pair churn. If archive candidates turn out to be stable, they become alertable and the product is better than expected. | |
| **C10** | **Real corpus, registered blind.** `prometheus_infra.csv` split into two halves treated as consecutive audits: **0–1 identity-pair changes.** More than 2 means either the instance changed materially mid-window — a genuine finding — or the set-stability premise is weaker on real data than on fixtures. | |

## What would stop this shipping

- **C1 or C3 fails** — set-valued findings are not stable either. There
  is then no substrate for alerting, and continuous auditing ships as a
  **scheduled report with no alerting at all**. That is a legitimate
  product and it avoids this entire problem; it is written here so that
  falling back to it reads as the registered outcome rather than a
  climbdown.
- **C5 fails** — an incident produces set changes. Not shippable as
  alerting at any threshold. A monitor that fires during an outage,
  about the outage, misdescribed as a hygiene problem, is worse than no
  monitor.
- **C4 fails** — it cannot find the thing it exists to find.
- **C8 fails** — it would compare two numbers that are not comparable,
  which is the specific failure this project exists to catch.

## What would falsify the reasoning rather than the code

**C1 and C9 both failing** would mean the set/scalar distinction is not
real — that discreteness at r ≥ 0.999 buys no more stability than a
continuous estimate does. The design rests entirely on that distinction,
so it would not be a threshold to tune but a premise to abandon, and the
right response is scheduled reports.

**C2 failing** — scalar drift under 2% — would be good news I do not
expect, and would mean a simpler product than the one designed here.
Recorded now so that it cannot later be quietly reinterpreted as
confirming the design.

## Explicitly NOT attempted

- **Alert routing, severity, or paging.** Nothing here should page
  anyone. If it earns that later, it earns it with evidence.
- **Attributing a change to a cause.** "Redundancy rose because of the
  new checkout service" is not derivable from values.
- **Comparing across dashboards.** Two boards are not two windows.
- **Rolling or multi-window baselines.** Overlapping windows are not
  independent, which breaks any multiplicity argument. Two-window first.
- **Auto-remediation on a detected change.** Governed by
  `SAFETY_BOUNDARIES.md`; a scheduled job whose success condition is
  that a system changed is refused there.

## Negative controls, to run BEFORE the positive case

In this order, and C4 does not run until 1–4 have passed:

1. Same contract compared with itself → zero changes, trivially, but it
   catches a diff that reports spurious changes from key ordering or
   float formatting.
2. Overlapping windows (C1).
3. Disjoint windows, no structural change (C3).
4. Incident injected (C5).
5. Only then the planted duplicate (C4).

Three of four rejected `derived_aggregates` designs looked correct on a
planted positive and failed only on data with no relation in it. The
order is fixed here in writing for that reason.

## Known handling decisions, fixed in advance

- **Basis and order must match.** Two contracts computed on different
  bases are not comparable, and neither are two where one declared
  ordered rows and the other did not. Mismatch is a refusal, not a
  warning.
- **Contract version must match on the major.** `build_contract`
  guarantees stability within a major; across one, the diff refuses.
- **The unit of comparison is the metric NAME.** A renamed metric is a
  removal plus an addition, and will be reported as such. Rename
  detection is a different feature and is not attempted.
- **Windows are declared by the caller**, never inferred. Consistent with
  the basis and the row-order declaration: the engine does not guess what
  a period is.
- **Assurance grade gates it, as with drift.** Comparing two grade-C
  audits compounds thin evidence twice over. If either side grades C or
  D, the diff reports insufficient evidence and names no changes.
- **Output shape.** Added findings, removed findings, and — separately
  and clearly labelled as unreliable — the scalar deltas. Scalars are
  shown because hiding them would be its own dishonesty, and labelled
  because acting on them is the mistake this document is about.

---

## Result

*Unscored. Written after the checks run, recording what happened rather
than what was hoped for.*
