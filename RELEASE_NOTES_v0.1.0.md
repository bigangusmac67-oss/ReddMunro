# v0.1.0 — first release

```
pip install redd-munro
```

Point it at a dashboard CSV export. It reports how many genuinely
distinct things you are measuring, which panels are one measurement
shown twice, and which are safe to archive — with the evidence for each,
and with the reasons you should not.

Runs entirely on your machine. No account, no upload, no telemetry —
which would be an awkward thing for this particular tool to collect.

---

## The finding that explains the product

On a live Prometheus instance, `methodGET_status200` and
`request_rate_total` matched at **r = 0.99987**. Not similar. The same
numbers, on two panels, alerting separately.

But run the reference scan and the worksheet leads with this instead:

```
** CONFLICT: engine says ARCHIVE, but this metric is behind
   a PAGING alert (RequestRateCollapse) **
```

`request_rate_total` carries 0.0011 unique variance — statistically it is
a restatement of another column. It is also the sole expression behind a
`severity: critical` alert. **Both facts are true, and only one of them
is in the CSV.** Archive it on the arithmetic alone and the next outage
pages nobody.

That is the whole product: the arithmetic is the easy half.

---

## What is verified

- **284 engine checks + 108 backend checks**, every fixture generated
  from a planted answer so the tool can be caught being wrong
- **Runs under real wasm32 in CI** — CPython computes the result,
  Pyodide reproduces it field for field. Added after an `int64` index
  that was free on x86-64 raised `TypeError` in every visitor's browser
  for five days while 134 tests passed
- **Six pre-registered studies, four of them with published misses**

| Study | Result |
|---|---|
| `SCALE_BASIS_PREREG.md` | 9 predictions, 9 hit |
| `MI_SCALING_PREREG.md` | 7 hit, 1 failed — the change was **abandoned**, as the stopping rule required |
| `AI_EVAL_PREREG.md` | 5 hit, 2 failed — both failures found defects in this tool |
| `DRIFT_PREREG.md` | 9 hit, and wiring it in exposed three more bugs |
| `HISTORY_STORE_PREREG.md` | 6 hit, 1 missed |
| `BINNING_PREREG.md` | 6 hit, 2 missed — **the misses disproved the previous cycle's diagnosis** |

Predictions are committed before the data is pulled. A tool only ever
pointed at data whose structure nobody knows can never be caught being
wrong, so the ordering is in the commit history where it is checkable
rather than merely asserted.

---

## What is NOT built

Stated here rather than discovered later.

- **Rolling-average blind spot.** A metric plotted against its own
  rolling average is not detected. Two designs were built and withdrawn
  on evidence — one flagged independent random walks, the other produced
  476 false positives while missing the confirmed real case. Renders as
  `○ not implemented`, on the page, deliberately.
- **Subset-MEAN aggregates.** A column that is the mean of a *subset* of
  others is not found. The Open LLM Leaderboard's `Average` column is
  exactly that — r = 1.00000000 on 541 of 541 models — and nothing
  flagged it.
- **Per-column MI floor.** The nonlinear check computes one bias floor
  per dataset. Integer counters have far less entropy than continuous
  gauges, so **tied columns are systematically less likely to be
  flagged** than continuous ones in the same audit. Under-detection, not
  over-detection — the safer direction, and now written down.
- **SLO and runbook parsers.** The reference scan covers Prometheus
  rules and Grafana dashboards. Of the four attestation columns it can
  answer two.
- **The hosted service must not take money.** Auth is a stub, there is
  no persistence, and the job store breaks behind more than one worker.
  All three refuse or warn rather than failing quietly.

---

## Looking for the first ten teams

**Nobody has run this on a dashboard they care about.**

Everything above was measured against public data — a live Prometheus
instance, NYC COVID counts, ACT air quality, FDIC bank filings, 541
language models. All real, none of it anyone's production board.

There is one prediction that cannot be scored without you.
`HISTORY_STORE_PREREG.md` **H1** asks whether metric pairs that look
identical actually come apart during incidents — the assumption behind
every "keep it just in case" argument in this industry. It is untested,
here and everywhere else. Testing it needs a window somebody labelled
`incident` because their service was genuinely broken.

**If you run this on your own dashboard, tell me what it found —
including if it was useless.** That is the more useful message and it is
the one I am short of.

shaun@reddmunro.com · no NDA needed to send a finding, happy to sign one
if you would rather.

---

## Install and first run

```bash
pip install redd-munro                 # numpy is the only dependency
pip install "redd-munro[refgraph]"     # + Prometheus rule parsing

redd run metrics.csv --basis differenced --ordered
```

Columns are metrics, rows are observations over time. Full workflow —
worksheet, reference scan, shadow dashboard, routing config, history —
in the README.

Or try it with no install at all: **[reddmunro.com](https://reddmunro.com)**
runs the same engine in your browser via WebAssembly. Open the network
panel and check.

**Apache 2.0.** The name is not covered by the licence, so fork the code
freely and please call the result something else.
