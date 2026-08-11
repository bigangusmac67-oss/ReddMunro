# Safety boundaries

**This is a design constraint, not a marketing position.** It is written
before the code that would test it, because the moment a customer is
waiting on a feature is the moment these questions get answered
differently.

The engine proves one thing: that a metric carries no variation the
others already carry. That is arithmetic and it is checkable. It cannot
see whether the same metric is the sole condition on a paging rule, an
SLO error budget, a compliance export or a runbook step — none of which
appear in a CSV of values.

Everything below follows from that one asymmetry.

---

## The three conditions

Any artefact this product generates that could change a live system must
satisfy **all three**. Not two. There is no tier, no flag and no
enterprise agreement that removes one.

### 1. Reversible

The change can be undone by restoring what it removed, using information
that still exists after the change is applied.

**Test:** write the undo command before shipping the do command. If the
undo requires data the change destroyed, the change is not reversible.

Already satisfied: generated Terraform uses
`datadog_metric_tag_configuration` with an empty tag list, collapsing a
metric to one billable series while destroying no data. Reversal is
restoring the tags.

**Refused:** any resource whose effect is deletion. A generated destroy
is not a change a reviewer can safely approve, because approving it
requires knowing everything the metric is used for — which is the thing
the tool cannot see.

### 2. Reviewable

The artefact is a diff a human approves before it takes effect. The tool
opens a pull request. **It never merges one.**

**Test:** between the tool emitting the artefact and the system changing,
is there a point at which a named person could read the change and stop
it? If the answer depends on how the customer configured it, the answer
is no.

Partly satisfied: exports exist and carry their evidence inline — unique
variance, basis, identity partner, blockers, attesting reviewer — so a
change request is auditable without returning to the web UI. PR
generation is not built.

**Refused:** auto-merge, "apply on approval of the audit", and any
scheduled job whose success condition is that a system changed.

### 3. Attested

A completed blast-radius worksheet gated it, with a named reviewer, and
every operational cell answered.

**Test:** can the artefact be produced without a person having answered
"is this referenced by a monitor?" for each metric in it? If yes, it is
not attested.

Already satisfied: executable exports return `428 Precondition Required`
until the worksheet is complete, and **a half-filled worksheet does not
unlock them** — any unanswered operational cell leaves that metric
unattested. A single "I confirm" checkbox cannot represent a reviewer
having checked eleven metrics against four systems each, which is why the
gate takes a worksheet rather than a boolean.

---

## The refusal list

Explicit, so that "we never discussed it" is not available later.

| Refused | Why |
|---|---|
| **Merging without review** | Breaches condition 2. There is no configuration that enables it. |
| **Emitting a `destroy`** | Breaches condition 1. Applies to Terraform, API calls, and generated scripts alike. |
| **Deriving the attestation from the reference graph** | See below. Breaches condition 3. |
| **Dropping series at the OTel collector** | Deletion at the pipeline edge wearing a different name. Breaches 1 and 2 together — nothing is written down, and what was dropped cannot be recovered. |
| **"Apply" buttons in the web UI** | The browser tool has no server and no credentials, and should not acquire them. |
| **Time-limited overrides for enterprise customers** | The conditions are the product. A tier that removes them is selling something else. |

---

## The one that will actually come up: automating the worksheet

Phase 2 connector work would pull the **reference graph** — which
monitors, SLOs and dashboards use a metric. That is genuinely valuable:
today a reviewer checks eleven metrics against four systems by hand, and
most of those cells could be filled automatically.

**Automating the look-up is the win. Automating the decision is the thing
we sell against.**

If the reference graph reports that a metric is referenced by nothing,
that is **evidence, not clearance**. The graph can be:

- stale, if it was cached before someone added a monitor
- incomplete, if a monitor lives in a Terraform repo the connector cannot
  see
- blind to indirect use — a recording rule, a Grafana variable, a
  downstream export, a quarterly report someone runs by hand
- wrong about the direction — a metric referenced by nothing may be
  referenced by nothing *because* it is the one somebody greps for during
  an incident

So the worksheet may arrive pre-filled with the graph's findings, clearly
marked as machine-supplied, and **a person still signs it**. The reviewer
field is not optional and is not a service account.

The saving is real and it is in the clerical work. It is not in the
judgement.

---

## What would let the line move, and who can move it

Not a customer, and not a deadline.

The only thing that would justify relaxing condition 2 or 3 for a
specific class of finding is **a pre-registered study showing that class
is safe to act on unattended**, scored against real dashboards, with the
misses published — the same standard every detector in this repository
has had to meet.

**The one plausible candidate:** exact identities at r = 1.0000 on every
row, where one metric is provably a restatement of another — a unit
conversion, a rate and its complement, a column published twice. Even
there, the argument has to survive the question of which of the pair is
referenced elsewhere, and that is not decidable from the values.

**The one that is not a candidate:** subset sums. The additive-family bug
is exactly the case where every member of a family looks individually
redundant, and dropping them all loses the lot. That failure is already
in the public log and it is what `subset_sum_protected` exists to
prevent.

---

## How to decide about something not on this page

A new export type, a new integration, a feature request from someone
holding a purchase order. In order:

1. **Can it change a live system?** If no, this document does not apply.
2. **Write the undo.** If you cannot, it fails condition 1. Stop.
3. **Name the point where a human could stop it.** If that point depends
   on customer configuration, it fails condition 2. Stop.
4. **Can it be produced with an unanswered operational cell?** If yes, it
   fails condition 3. Stop.
5. **If it passes all three, add it here with its undo path**, so the
   next person does not have to re-derive the reasoning.

If a feature fails at step 2 or 3 and the customer still wants it, the
honest answer is that a different vendor sells that, and that we are the
one that does not. **That refusal is the product.** Five tests already
fail if the exported summary contains "safe to archive", "verified safe"
or "approved" — the discipline is enforced in code, not in a style guide,
and this document is the reasoning behind those tests rather than a
softer restatement of them.
