# Adoption friction — pre-registered predictions

**Registered before the triage-tier work was written and before any team
has run this on a board they care about.** Nothing here has been
observed. That is the point: two critiques arrived describing where
teams will struggle, both plausible, and the work they imply is
substantial. Predictions go down first so the work can be caught being
aimed at the wrong problem.

## The instrument, and why it is weak

Every other pre-registration in this repository scores against a corpus.
This one scores against **people**, and it is a far worse instrument.
The limits, stated before any result:

- **n = 10, self-selected.** Teams who answer a page asking them to
  report findings are teams who like this kind of thing. Nothing here
  can support a claim about SREs in general. It can only describe this
  population.
- **There is no telemetry, and there will not be.** The tool cannot
  phone home; a test asserts the code cannot open a socket, and the
  privacy claim is load-bearing on the landing page. So **where a team
  stalls is unobservable unless they tell us.** This is the price of the
  zero-upload stance and it is paid here rather than quietly recovered
  by adding analytics.
- **Non-response is the dominant failure mode and it is invisible.** A
  team that installs, gets nothing, and says nothing is the most likely
  outcome and the least measurable one. So the denominator recorded is
  *teams contacted*, never *teams who replied*, and every rate below is
  reported against the larger number.
- **The observer wrote the tool.** Questions must not lead. "Was the
  worksheet difficult?" is not a question, it is a suggestion. The
  protocol is at the foot of this file and it is fixed in advance.

Given all that: **this cycle can falsify a prediction but it cannot
confirm one.** A hit means "not contradicted by ten cases", nothing
stronger, and it is written up that way or not at all.

## The critiques being tested

1. **Worksheet friction.** SREs are time-starved; a 50-row CSV requiring
   manual verification of every row creates enough cognitive load that
   the work does not happen.
2. **Deletion trauma.** Engineers have removed telemetry that turned out
   to be load-bearing. Even with conflict checks, anxiety produces
   inaction.

Both are claims about *where* the process breaks. Predictions follow.

## Predictions

| # | Prediction | Result |
|---|---|---|
| **A1** | **The stall is upstream of the worksheet.** Of teams that install the tool, **more will fail to produce an audit at all than will produce one and abandon the worksheet.** Getting a dashboard into the required shape — one column per metric, one row per timestamp, aligned — is not a thing Grafana exports in one click, and the landing page treats it as a solved step in a single sentence. Registered as the contrarian prediction: it says the reported friction is real but misattributed, and if it holds, the triage tiers fix a problem that is not the binding one. | *unscored* |
| **A2** | **Worksheet completion is partial and conflict-shaped.** Among teams that do produce a worksheet, the modal outcome is **more than zero and fewer than all** attestation rows completed, and the completed rows are disproportionately the ones flagged `CONFLICT`. Registered because it discriminates: partial completion means volume is the problem and tiering helps; **zero completion means trust is the problem and tiering will not touch it.** | *unscored* |
| **A3** | **No team archives anything within 30 days.** Zero teams report having actually archived, deleted or routed a metric in the first month. The first month's value is diagnostic — "we did not know these two panels were the same series" — not operational. Registered because it sets what a success looks like, and because any cost-savings framing on the site or in a deck is premature until this is contradicted. | *unscored* |

## What each outcome changes

- **A1 holds** → the tiering work is not the priority. Build the import
  path instead: a Grafana/Prometheus range-query fetcher that produces
  a correctly shaped CSV, so the first command a team runs cannot fail
  on file format. Say so on the site rather than reordering it.
- **A1 fails** (teams reach the worksheet easily) → the critique was
  right about location, and tiering plus zero-read verification are the
  correct next two pieces.
- **A2 shows zero completion** → **stop building worksheet ergonomics.**
  No amount of grouping fixes a document nobody trusts enough to sign.
  The problem is that attestation transfers liability to the signer, and
  a nicer CSV does not change who owns the next incident review.
- **A3 fails** (someone archives) → ask immediately what evidence they
  actually used, because that is the first real data on which output
  carries weight, and it is worth more than the other two answers
  combined.

## What would stop the tiering shipping

- **Any tier label that asserts safety.** Tier names describe what was
  checked and over what scope. `Zero-Risk` was proposed and refused;
  `SAFETY_BOUNDARIES.md` condition 3 says this tool supplies evidence
  and never clearance, and a tier is a very efficient way to smuggle a
  clearance in.
- **A tier that hides its scope.** `not found in scanned sources` is
  only meaningful alongside how many sources were scanned. With no
  reference scan run, an A or B tier means nothing at all and must say
  so in the cell rather than in a legend nobody reads.
- **Reordering that loses a row.** Grouping must be a permutation.
  Checked by set equality on the metric column, before and after.

## Observation protocol, fixed in advance

Asked in this order, once, without follow-up prompting:

1. "What happened when you ran it?" — open, first, and nothing is
   suggested. Everything else is contaminated by the questions after it.
2. "Where did you stop?" — not *did* you stop.
3. "Did you do anything as a result?" — not *did you archive anything*.

Recorded for every team contacted: reached-an-audit yes/no, produced-a-
worksheet yes/no, attestation rows completed, action taken yes/no, and
**silence** as its own outcome rather than an excluded case.

**Stopping rule.** Scored at ten teams contacted or 90 days, whichever
comes first, and written up including the case where three replied and
seven did not. A response rate that low would itself be the finding, and
it would say more about the ask than about the worksheet.

---

## Amendment 1 — the site contaminates A1, and it was our doing

**Registered before any team was contacted, after noticing it while
drafting the outreach note.**

The cohort section of reddmunro.com carries this sentence:

> **We predict that is where most of you will stop.**

Publishing A1 was the right call — a page that asks people to check our
predictions has to show them — but it makes A1 harder to measure on
exactly the people most likely to volunteer. Anyone who reads the site
before running the tool **has been told where we expect them to stop**
before being asked where they stopped. That primes question 2 in both
directions: some will report the export because it was suggested, others
will push past it precisely because they were told not to manage it.

There is no clean fix. Removing the sentence would hide a prediction
from the people being predicted about, which is worse. So:

- **`source` is recorded for every team** — `site` (read the prediction)
  or `direct` (cold contact, has not). See `OUTREACH.md` §3.
- **The two groups are scored separately.** A1 is measurable on the
  `direct` group only. The `site` group is reported, and reported as
  contaminated.
- If the `direct` group comes in under five teams, **A1 is recorded as
  unscoreable rather than scored on a sample that cannot carry it.**

This is a self-inflicted limitation and it is written here rather than
discovered at scoring time, which is the only thing that makes it worth
anything.

---

## Amendment 2 — the onboarding changed, on 2026-08-14, before any contact

**A1 predicts teams stall getting an export into shape. On 2026-08-14 we
made that step easier.** `README.md` gained a "Getting the CSV" section
naming the Grafana click-path (Query Inspector → Data → Join by time →
Download CSV), the per-panel limitation, and the delimiter trap.

**This is an intervention on the exact variable A1 measures**, and it has
to be recorded as one. A prediction about where people give up, scored
against an onboarding that quietly improved halfway through, is not a
scored prediction — it is two half-samples reported as one.

Two things make it survivable here:

1. **It happened before the first team was contacted.** Every team in the
   cohort meets the same onboarding, so the sample is not split. Had it
   landed mid-cohort, the correct move would have been to score the
   before and after groups separately or abandon A1.
2. **It is recorded with a date**, so a later reader cannot mistake the
   result for one obtained against the bare version.

**A1's wording is unchanged and it is not softened.** The tip makes one
panel a few clicks; it does nothing about a forty-panel board being forty
exports, which is the friction the prediction is actually about. If A1
still holds with the tip in place, that is a stronger result than it
would have been without — the easy fix was applied first and did not
move it.

**Nothing was added to `OUTREACH.md`.** The cold note carries no export
tutorial and no link to this section. A recipient who goes looking for
documentation will find it, and that is normal; putting it in the note
would make the export step salient in the same message that asks where
they stopped.

---

## Result

*Unscored. No team has run this on a dashboard they care about yet.*
