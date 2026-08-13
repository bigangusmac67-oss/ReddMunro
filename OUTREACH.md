# Cohort outreach — the note, the reply shape, the record

Operational companion to `ADOPTION_PREREG.md`. The protocol there is
fixed; this file is how it actually gets asked.

**The one rule: the note must not mention the predictions.** A1 says
teams stall getting an export into shape. Telling someone that before
asking where they stopped is not a question, it is a suggestion, and the
answer stops being evidence. Nothing below names a prediction, a
suspected friction point, or a hoped-for finding.

---

## 1 · The note

Short on purpose. The recipient is being asked for twenty minutes and a
reply, by a stranger, about a tool they did not ask for.

> **Subject:** 11 metrics on a live Prometheus box carried 5.6 independent signals
>
> Hi [name] —
>
> I built an open-source tool that reads a dashboard export and reports
> how many genuinely independent signals it contains, which panels
> restate one another, and which are candidates to archive.
>
> On the public Prometheus corpus it ships with, 11 metrics carried
> **5.6 independent signals** — and two of the archive candidates turned
> out to be behind live alert rules, which is the part I care about.
>
> **Nobody has run it on a dashboard they would be paged for.** I am
> looking for ten teams to do that and tell me what came back. There is
> nothing to buy, no call, no trial. Apache 2.0, runs locally, nothing
> is uploaded — it has no network code at all and there is a test that
> asserts it.
>
> ```
> pip install redd-munro
> redd run your_export.csv --basis differenced --ordered
> ```
>
> Columns are metrics, rows are timestamps. If you would rather not
> install anything, the same engine runs in the browser at
> reddmunro.com.
>
> If you try it, three questions — and a one-line answer to each is
> genuinely enough:
>
> 1. **What happened when you ran it?**
> 2. **Where did you stop?**
> 3. **Did you do anything as a result?**
>
> A reply saying it found nothing, or that you gave up on step one, is
> as useful to me as a good finding — more so, actually, since I have no
> shortage of the good kind on public data. Results get written up
> including the nulls; your team is not named unless you ask to be.
>
> — Shaun
> shaun@reddmunro.com · github.com/bigangusmac67-oss/ReddMunro

### Notes on sending

- **One send, no chase.** The stopping rule counts teams *contacted*;
  a follow-up nudge changes the response rate and therefore the
  denominator. If it becomes necessary, record it as a protocol change
  and score the nudged group separately.
- **Never edit the three questions per recipient.** Tailoring the
  wording to what you suspect that team will say is exactly how a
  registered protocol becomes a leading one.
- **Record the send** in the sheet below on the day it goes out, before
  any reply. A denominator assembled afterwards from replies is not a
  denominator.

---

## 2 · The reply shape

Offered, never required — a form that must be filled is a form that does
not get filled. Freeform replies are scored the same way by hand.

> Copy this in if it is easier than writing prose:
>
> ```
> 1. What happened when you ran it?
>
> 2. Where did you stop?
>
> 3. Did you do anything as a result?
>
> —— optional, factual ——
> board size (panels/metrics):
> audit ran:              yes / no
> produced a worksheet:   yes / no
> worksheet rows filled in:
> ```

The three open questions come first and the factual block second,
deliberately. The factual questions name specific artefacts — worksheet,
rows — and asking them first would tell the respondent which parts of
the process are supposed to matter, contaminating question 2 with the
answer.

**Do not add checkboxes to question 2.** A list like "☐ could not
export ☐ worksheet too long" hands over the very answer being measured.

---

## 3 · The record

One row per team **contacted**, filled on the day of sending. Silence is
an outcome with a row, not a missing row.

| Team | Contacted | Source | Replied | Audit ran | Worksheet | Rows filled | Action taken | Notes |
|---|---|---|---|---|---|---|---|---|

- **Source** — `site` if they came via reddmunro.com, `direct` if
  contacted cold. This column exists because of the contamination
  recorded in `ADOPTION_PREREG.md` Amendment 1: the cohort section of
  the site publishes prediction A1, so anyone arriving that way has
  already been told where we expect them to stop. Their answer to
  question 2 is primed; a `direct` contact's is not. **Score the two
  groups separately or A1 is not measurable.**
- **Replied** — `no` is data. It is the outcome A1 and A2 both predict
  most often, and the one that disappears if only replies are recorded.
- **Rows filled** — a count, not "some". A2 discriminates on whether it
  is zero.

**Scored at ten contacted or 90 days, whichever is first** — including
the case where three replied and seven did not. A response rate that low
would itself be the finding, and it would say more about the ask than
about the worksheet.
