# Redd Munro

**Your dashboards don't have an observability problem — they have a trust problem.**

Validates telemetry before dashboards, alerting engines and automated agents consume it: how many independent signals a metric set actually carries, which columns are the same number twice, and which are safe to archive.

A forty-metric dashboard driven by four underlying things gives its owner the feeling of forty-fold coverage and the reality of four. This tool measures the difference.

Point it at a CSV whose columns are metrics and whose rows are observations over time. It returns:

- **How many independent signals are really there** — the participation ratio of the correlation spectrum, plus the number of principal components needed for 95% of the variance
- **Definitional identities** — pairs at |r| ≥ 0.999 that are the same number wearing two names (a rate and its complement, a count and its percentage, a unit conversion)
- **Redundancy clusters** — groups where one well-chosen representative would carry nearly everything the group carries
- **Per-metric contribution** — what each column would actually cost you if you deleted it
- **Nonlinear dependence** — pairs that are strongly related but nearly uncorrelated, which a correlation matrix reports as independent

```
pip install redd-munro                       # numpy is the only dependency

redd run metrics.csv                   # formatted report
redd run metrics.csv --html report.html --json out.json
redd run metrics.csv --ignore region,cohort --top 20
redd prune metrics.csv                 # just the deletion candidates
redd prune metrics.csv --quiet         # bare names, for piping
```

Exit codes compose in CI: `0` clean, `1` heavy redundancy (signal ratio below 0.5), `2` input error. `--no-fail` always exits 0.

The module also imports directly — `signal_audit.audit(path)` for a file, `signal_audit.audit_text(csv_string)` where there is no filesystem (browser, lambda, notebook).

The HTML report is a single self-contained file — no scripts, no external requests — so it can be emailed or opened offline by someone who does not have Python.

---

## Try it

```
python make_demo.py                                    # builds demo_dashboard.csv
python signal_audit.py demo_dashboard.csv --html demo_report.html
```

`demo_dashboard.csv` is a synthetic SaaS dashboard: 17 metrics over 400 days, of the kind someone would defend in a meeting. It is generated from **four** latent drivers, with two exact identities, one near-identity, one nonlinear relationship and two genuinely independent metrics planted in it.

The audit returns **4.3 independent signals from 17 metrics**, finds all three identities, flags the trend confound, and recovers the nonlinear pair — matching the ground truth it was never told.

---

## The two things that make it trustworthy

**It computes everything twice.** Metrics that all grow over time correlate near 1.0 from shared trend alone, with no relationship whatsoever between them. This is the single most common way an audit like this produces a confident wrong answer. So every figure is computed on the raw series *and* on first differences, both are shown, and the headline uses the differenced view. On the demo data the raw view says 2.7 signals and the differenced view says 4.3 — the raw number is mostly about the calendar.

**It knows what its estimator invents.** Mutual information is biased upward on finite samples: two genuinely unrelated columns still score above zero. The tool measures that bias directly by shuffling the data and re-running, then requires real dependence to clear the floor. Without this it reports independent metrics as related — which it did, until a test with planted independent noise caught it.

Both of these come from a research programme that spent most of its effort trying to break its own measurements, and catalogued ten distinct ways a comparison like this can produce a publishable-looking wrong answer.

---

## Validation

`python test_signal_audit.py` — 52 checks, all against **known** ground truth. Every fixture is generated from a planted number of latent factors, so the tool's answer can be checked rather than admired:

| Check | Expected | Result |
|---|---|---|
| 12 metrics from 3 latent factors | ~3 | 2.6 |
| 8 genuinely independent metrics | ~8 | 7.9 |
| Unit conversion + complement pair | both found | both found |
| 6 unrelated metrics sharing a trend | raw fooled, differenced ~6 | 1.0 → 6.0 |
| y = x² with r ≈ 0 | detected | detected |
| Independent noise pairs | not flagged | not flagged |
| Demand vs temperature as a U-shape | linearly invisible, MI-flagged | r = −0.03, flagged |
| Both halves of an identity pair | never both archived | never both |

It was also run against a system whose structure had been established independently, by different methods, before this tool existed: an 11-variable state whose recorded participation ratio was 1.84–2.64, with two known identity pairs and a documented nonlinear coupling at 42–455× its Gaussian-implied dependence. The audit returned **1.88**, found **both** identity pairs at r = −1.0000, and scored the nonlinear coupling at **153×**. Nothing about that system was encoded in this tool.

### On a real dashboard

`nyc_covid_dashboard.csv` — NYC's public COVID-19 daily counts, 53 metrics × 455 days (Jan 2021 – Apr 2022, spanning the Delta and Omicron waves). A genuine operational dashboard that informed policy in the largest US city. Source: [NYC Open Data](https://data.cityofnewyork.us/Health/COVID-19-Daily-Counts-of-Cases-Hospitalizations-an/rc75-m7u3).

**53 metrics, 5.1 independent signals.**

- The raw view says **1.6** — during a pandemic every metric rides the same wave, so raw correlation is nearly meaningless. This is the trend confound at full strength, and the gap between 1.6 and 5.1 is the tool's whole reason for computing both.
- Three **definitional identities** surfaced, all the same pattern: `case_count_7day_avg` vs `all_case_count_7day_avg` at r = 0.9993 (citywide), and the Manhattan and Queens versions at 0.9991. These differ only by probable cases — a rounding error apart, shown as separate dashboard tiles.
- The case-count family — raw, probable, 7-day, all-case, across six geographies, roughly 30 columns — collapses into a few clusters. Most of those tiles are interchangeable.
- The **load-bearing** metrics are borough-level *death* 7-day averages: Staten Island 79% unique, Bronx 63%, Manhattan 56%. The small, noisy, less-watched series carry information the headline case counts do not.

The read: a 53-tile dashboard where roughly half the tiles restate the case wave, and the genuinely independent signal sits in borough-level mortality.

Caveat, and the tool says so itself: 8.6 rows per metric is below its comfort threshold, so unique-variance percentages should be read as an ordering rather than as exact figures.

### On a second real dashboard, with predictions written first

`act_air_quality.csv` — ACT (Canberra) air quality monitoring, 12 metrics × 1,094 hourly readings. Source: [ACT Open Data](https://www.data.act.gov.au/Environment/Air-Quality-Monitoring-Data/94a5-zqnn). Ten predictions were recorded in `REAL_DASHBOARDS.md` **before** the data was fetched or run, because a redundancy auditor applied only to data of unknown structure can never be wrong and therefore never tested.

**12 metrics, 4.7 independent signals. Score: 6 hits, 4 misses — 2 bad predictions, 2 real tool defects.**

Hits included the ozone/NO2 titration relationship (correct negative sign, plus nonlinearity), and all four AQI columns pairing exactly with their own source pollutant. The misses were more useful: one exposed that a max-of-others index was being ranked the *most* load-bearing metric on the board when it is fully derived — now fixed with a `derived_aggregates` detector that took four attempts, each rejected by evidence. The other exposed that differencing destroys the relationship between a series and its own rolling average, which is documented rather than fixed.

Full scoring, including the three rejected detector designs, is in `REAL_DASHBOARDS.md`.

---

## Reading the output

**Independent signals** is a continuous measure, so it will rarely be a whole number. Treat 4.3-from-17 as "about four things, measured seventeen ways."

**Definitional identities** are almost always safe to consolidate — they are arithmetic restatements, and each one costs a dashboard slot and returns nothing.

**Redundancy clusters** use single linkage: if A tracks B and B tracks C, all three sit in one cluster even when A and C are not directly correlated. That is deliberate — a dashboard showing all three is showing one thing three times.

**Unique variance** is what remains unexplained when every other metric is used to predict this one. It is the honest measure of deletion cost. Low values are consolidation candidates; high values are load-bearing.

**Nonlinear pairs** are the most actionable and the least intuitive: these metrics are related, but a correlation matrix — and therefore most dashboards, most alerting rules, and most people's mental models — will report them as independent.

---

## Limitations

Stated plainly, because a tool that reports structure should be honest about its own.

- **It does not know what your metrics mean.** A statistically redundant metric may be worth keeping for regulatory, contractual, diagnostic or communication reasons the tool cannot see. It measures information, not value.
- **Thresholds are judgement calls.** `IDENTITY_R = 0.999`, `REDUNDANT_R = 0.90` and `MI_RATIO_FLAG = 3.0` are stated as constants at the top of the file so you can see and change them. There is nothing canonical about them.
- **It assumes rows are ordered observations, and it will not tell you when they are not.** Differencing is meaningless on unordered data. If your rows are not a time series, use `--ignore` on any ordering column and read the raw column only. **This failure is silent, which was measured rather than assumed.** On a cross-section of 1,915 banks — rows are institutions, so differencing subtracts one bank from an unrelated bank — the differenced headline read 2.47 effective signals against 2.52 for the raw view. The undefined operation returned approximately the valid answer, because differencing scale-dominated columns leaves differences that are still scale-dominated. Nothing in the output distinguishes that file from a time series. The engine has no notion of row ordering and does not attempt to infer one.
- **Cross-sections have their own confound. A cure now ships, but you must declare it.** `--scale-by COL` adds a ratio basis dividing every column by COL; repeatable, because platform telemetry has no single denominator the way a balance sheet has total assets. **`--scale-exempt COL` is not optional politeness** — dividing an already scale-free column by a size *injects* the confound the basis exists to remove. Measured on bank data: `ROA`~`ROE` went from +0.60 to +0.98, past the clustering threshold, making two different profitability measures look redundant. The `basis-conflict` check reports any pair a transform inflated, but cannot tell you whether a factor was injected (a defect) or a real duplicate exposed (a finding) — only someone who knows what the metrics mean can. Original limitation, for context: In a time series a calendar trend makes everything correlate, and differencing removes it. In a cross-section of entities the same role is played by **scale**: every dollar-denominated column tracks institution size, so everything correlates with everything. On the FDIC corpus that produced **2.5 effective signals from 39 metrics (93.5% redundant)**; dividing every field by total assets lifted it to **14.8 (61.1%)**. The general rule is *remove the dominant scaling variable, whichever it is* — differencing for time, ratio-normalisation for a cross-section. Only the first is implemented. Normalise before you run, or the headline will be about size rather than about your metrics. Two further detectors degrade under this confound: the derived-aggregate check flagged 28 of 39 metrics (3 after normalising), and single-linkage clustering chained 24 of 39 into one unusable group.
- **The identity check cannot distinguish a definition from a near-proportionality.** At `IDENTITY_R = 0.999` it reported bank `ASSET` and `LIAB` as "the same quantity under two names" — they differ by equity, which is the entire subject of capital regulation. Where one variable dominates the spread, the threshold stops meaning *definitionally identical* and starts meaning *both large*.
- **Listwise deletion.** Rows with any missing metric are dropped, and the count is reported. If your columns have different coverage this can discard a lot; the note in the output tells you how much.
- **Correlation and MI are pairwise.** Three-way structure — information carried jointly by a triple and by no pair within it — is not measured here. This was tested rather than assumed (`triadic_validation.py`): across four corpora, three-way analysis found **nothing** that pairwise analysis had not already found, resolution scales only as the cube root of history, and cost grows as C(N,3). Deferred deliberately, with the conditions for revisiting recorded in `REAL_DASHBOARDS.md` §5.
- **Rows per metric matters.** Below about 10 rows per metric the per-metric regression is strained even with the adjustment applied, and the tool warns you. Wide dashboards need long histories: 50 metrics wants 500+ rows.
- **Differencing hides a series against its own smoothing — but only when the averaging window exceeds the sampling interval.** Confirmed on real Prometheus data: `load1/5/15` sampled every 300s showed differencing *strengthening* their correlations (0.761→0.765, 0.859→0.916), because a 60s average sampled every 300s is not smoothed relative to the sample rate. The blind spot is real where ozone 1h vs 8h showed it, and absent where the sampling is slower than the smoothing. A raw metric and its own rolling average correlate strongly in levels and weakly in differences, because the smoothed series has little period-to-period variation left. On a real dashboard `o3_1hr` vs `o3_8hr` ran 0.749 raw and 0.258 differenced. Both columns are always printed, but the headline will understate redundancy on any board mixing raw and smoothed versions of the same metric.
- **Subset sums are detected, but only exact ones over non-negative columns.** `subset_sums` finds a column that is the exact sum of a named subset of the others — it closed the gap this list previously described. What it still will not do: **signed columns are excluded entirely**, because dominance is meaningless when a child can be negative (on an income statement that excludes most of the sheet); **approximate sums are rejected on purpose**, so a relation holding on 96% of rows is not reported; and a sum whose components are not all present as columns cannot close. It is also skipped above 250 columns, where the O(N²·rows) dominance pass stops being cheap. Missing a real sum is treated as far cheaper than inventing one.
- **Nonlinear detection needs about 200 rows.** Below that the estimator is unreliable and the tool says so rather than guessing.
- **Not causal.** Everything here is association. Two metrics in a cluster may share a driver, or one may cause the other, or both may be measuring an instrument rather than the world.

---

## Files

| File | Purpose |
|---|---|
| `signal_audit.py` | The tool. Single file, numpy only. |
| `test_signal_audit.py` | 28 validation checks against planted ground truth. |
| `make_demo.py` | Generates the demo dashboard with known structure. |
| `demo_dashboard.csv` | 17 metrics, 400 days, 4 latent drivers. |
| `demo_report.html` | Example output (synthetic). |
| `nyc_covid_dashboard.csv` | Real dashboard: 53 metrics, 455 days, NYC Open Data. |
| `nyc_report.html` | Example output (real). |
| `act_air_quality.csv` | Real dashboard: 12 metrics, 1,094 hours, ACT Open Data. |
| `act_report.html` | Example output (real, predictions pre-registered). |
| `mta_subway_otp.csv` | Real dashboard: 23 subway lines, 49 months, NY Open Data. |
| `mta_report.html` | Example output (real). |
| `triadic_validation.py` | Three-way structure test — the basis for deferring that extension. |
| `REAL_DASHBOARDS.md` | Predictions, scoring, and what each run changed in the tool. |
| `signal_audit_cli.py` | The `redd` command. Presentation only, no analysis. |
| `pyproject.toml` | Packaging. numpy-only runtime by design. |
| `check_pyodide.py` | Browser-compatibility preconditions (12 checks). |
| `backend/` | Hosted service: contract, metering, jobs, exports, OpenAPI. |
| `demo/` | Zero-server browser demo (Pyodide). See `demo/README.md`. |

## Running in a browser

`check_pyodide.py` verifies the preconditions for Pyodide/WebAssembly: stdlib-and-numpy imports only, nothing browser-hostile (no multiprocessing, threading, sockets or ctypes), and a filesystem-free route via `audit_text()` that produces bit-identical results to the file route.

`demo/index.html` is the browser demo built on that: drop a CSV, the engine runs locally via WebAssembly, nothing is transmitted. The JSON contract it renders (`report_payload`) is asserted in the engine suite, section 9, so removing a field breaks a test rather than the page.

Browser execution was **verified on 31/07/2026** — a real Pyodide run reproduced the CLI figures exactly with no console errors.

The precondition check remains useful as a regression guard: **a green run means no known blocker remains, which is necessary but not sufficient** — that needs a real Pyodide run, which the check says so itself. `np.linalg.eigvalsh` and `np.linalg.lstsq` are the calls to smoke-test first.

Note that `write_html` writes to a path; in-browser use should render from the JSON contract instead, or write to Pyodide's virtual filesystem.

Version 0.1.0 — prototype.

## Licence

Apache 2.0. See `LICENSE` and `NOTICE`.

Use it commercially, fork it, ship it inside something you sell. The
licence grants no rights in the name (Apache 2.0 §6), so please do not
call a fork Redd Munro.

**Why permissive.** The code is a couple of thousand lines of NumPy.
What is actually hard to copy is in `REAL_DASHBOARDS.md` and the three
pre-registration documents beside it: predictions written down before
each dataset was pulled, scored afterwards, misses included. A
restrictive licence would guard the part that is cheap to reproduce
while costing the trust that the rest depends on.

Contributions require a sign-off — see `CONTRIBUTING.md`, which
explains why in more detail than you probably want.
