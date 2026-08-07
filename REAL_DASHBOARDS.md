# Real dashboards — predictions and results

Testing `signal_audit.py` against dashboards built by other people for their own purposes, in domains where the correct answer is partly knowable in advance from subject knowledge.

**Predictions are written before fetching each dataset.** That is the whole point: a redundancy auditor that only ever gets applied to data whose structure is unknown can never be wrong, and therefore never tested. Where domain knowledge implies a specific structural answer, the tool either recovers it or fails to, and both outcomes are recorded here.

Selection criterion, added 31/07: prefer **long** histories. Three-way (triadic) structure is the planned extension, and its estimator bins a 3-way table — usable samples scale as n^(1/3). A 50-metric dashboard with 400 rows cannot support it. Building a corpus of long series now makes that extension testable later.

---

## 1. NYC COVID-19 daily counts — DONE

53 metrics × 455 days. Source: [NYC Open Data rc75-m7u3](https://data.cityofnewyork.us/Health/COVID-19-Daily-Counts-of-Cases-Hospitalizations-an/rc75-m7u3).

Run before this protocol existed, so no pre-registered predictions. Result recorded in README: 5.1 independent signals from 53 metrics; three definitional identities of the `case_count_7day_avg` / `all_case_count_7day_avg` form; load-bearing metrics are borough-level death 7-day averages.

Surfaced one real bug: unadjusted R² near-overfitting at 4.3 rows/metric, fixed with an adjusted R² and a crowding warning.

---

## 2. Air quality — RESULTS

**ACT (Canberra) air quality monitoring, Monash station.** 12 metrics × 1,094 hourly readings, 10 June – 31 July 2026. Source: [ACT Open Data 94a5-zqnn](https://www.data.act.gov.au/Environment/Air-Quality-Monitoring-Data/94a5-zqnn).

**Headline: 12 metrics, 4.7 independent signals.** 91 rows per metric — no crowding warning.

| # | Prediction | Basis | Result |
|---|---|---|---|
| A1 | PM2.5 and PM10 cluster together (\|r\| ≥ 0.6 on differences) | PM2.5 is a physical subset of PM10 — same particles, nested size cutoffs | **miss** — raw r = 0.854 but differenced only 0.223. Prediction wrong, not tool: the nesting shows in levels (shared regional air mass) while hour-to-hour changes are driven by different local processes. My threshold choice was naive. |
| A2 | NO2 and CO cluster together | Shared dominant source: combustion, mostly traffic | **miss as stated, informative** — linearly r = 0.03, so no cluster. But the MI detector flagged the pair at **177× the Gaussian-implied level**. They are strongly dependent, just not linearly. The tool found the relationship my prediction got the shape of wrong. |
| A3 | O3 is **negatively** correlated with NO2 | Titration: NO + O3 → NO2 + O2 consumes ozone where NOx is fresh | **hit** — r = −0.22 (differenced), correct sign |
| A4 | O3 shows **nonlinear** dependence on NO2 or temperature | Ozone is photochemically produced — depends on sunlight and precursors jointly | **hit** — NO2 ~ O3(8hr) flagged at 10.7× |
| A5 | Independent signals ≥ 3 | Distinct source classes genuinely differ | **hit** — 4.7 |
| A6 | Raw vs differenced gap smaller than NYC COVID's 3.5 | Air quality is cyclic, not monotonically trending | **hit** — 2.1 vs 3.5 |

**What would falsify the tool here:** finding PM2.5 and PM10 unrelated (A1) or reporting O3/NO2 as positively correlated (A3) would indicate the tool is mis-measuring, not that the chemistry is wrong.

### Added after seeing the schema, before seeing any results

The chosen source (ACT air quality monitoring) carries both raw pollutant concentrations and their **AQI transforms** in the same table — `no2` next to `aqi_no2`, `pm2_5` next to `aqi_pm2_5`, and so on. Provenance is weaker than A1–A6 (schema seen first), and is flagged as such, but no result had been computed when these were written.

| # | Prediction | Basis | Result |
|---|---|---|---|
| A7 | Each `aqi_X` pairs with its own `X` above every other relationship | AQI is a deterministic function of the concentration it is derived from | **hit, cleanly** — all four redundancy clusters are exactly `{X, aqi_X}`: `{no2, aqi_no2}`, `{o3_1hr, aqi_o3_1hr}`, `{pm10, aqi_pm10}`, `{pm2_5, aqi_pm2_5}`. No cross-contamination. |
| A8 | `aqi_X` ~ `X` correlation high but **below** 1.000 in several cases | AQI is a *piecewise-linear, integer-rounded* transform: monotone but not linear | **hit** — no2 0.99384, pm10 0.99975, pm2_5 0.99992, o3_1hr 1.00000. Three of four deterministic transforms fall short of the 0.999 identity threshold; only one is reported as an identity. |
| A9 | `o3_1hr`, `o3_4hr`, `o3_8hr` form a redundancy cluster | Nested rolling windows over the same series | **miss — and it is the tool's** (see finding 2 below). Raw r = 0.923 / 0.914; differenced 0.625 / 0.661, under the 0.90 clustering threshold. |
| A10 | `aqi_site` has low unique variance | It is by definition the maximum of the AQI sub-indices | **miss — and it is the tool's** (see finding 1). `aqi_site` was ranked the single **most** load-bearing metric at 56% unique variance, while in fact equalling the rowwise max of its sub-indices on 76% of rows. |

**Score: 6 hits, 4 misses — of which 2 were wrong predictions and 2 were real tool defects.** Both defects have been fixed or documented.

---

## What the air-quality run changed in the tool

### Finding 1 — derived aggregates read as load-bearing (fixed)

`aqi_site` is the worst-of index over four sub-indices. It is fully determined by columns already on the dashboard, yet a max is *linearly unpredictable* — which component is currently largest keeps switching — so the regression behind "unique variance" ranked it the most indispensable metric on the board. Exactly backwards.

A `derived_aggregates` detector now catches max / min / whole-set-mean relationships. Building it took four attempts, each rejected by evidence:

1. **Correlation against the aggregate** — flagged 39 of 53 columns on NYC and 13 of 17 on the demo. In a redundant dataset the rowwise mean *is* the common factor, so everything correlates with it. Detected redundancy in general, not derivation in particular.
2. **Near-equality instead of correlation** — fixed the false positives, but only caught `aqi_site` by luck: aggregating over *all* other columns fails whenever a second aggregate is present, since the two pollute each other's comparison set. A synthetic table with both a max-index and a grand total defeated it entirely.
3. **Aggregate over the dominated set** (columns the metric is never below) — robust to that, but z-scoring made every near-identity look like a max-aggregate, because a duplicate partner is "attained" on every row.
4. **Plus: attained by ≥ 2 distinct columns, and not a copy of any single one.** A share cap was tried here and rejected the true positive — `aqi_site` is attained by three columns but PM2.5 dominates 97% of the time, which is normal for a worst-of index when one driver dominates the period.

Final state: 1 true positive on ACT, 0 false positives on NYC and the demo, and two new regression tests.

### Finding 2 — differencing hides a series against its own smoothing (documented, not fixed)

A9 failed for a structural reason worth stating. `o3_1hr` and `o3_8hr` correlate at 0.749 raw but only 0.258 differenced: differencing an 8-hour rolling mean against a 1-hour instantaneous reading destroys the relationship, because the smooth series has almost no hour-to-hour variation to correlate with.

This is a genuine cost of the differenced headline. NYC's `7day_avg` columns *did* cluster — but they were being compared to *each other*, both smoothed. A raw series against its own rolling average is the case differencing breaks.

The tool already prints both columns, so the information is not lost — but the headline number will understate redundancy on any dashboard mixing raw and smoothed versions of the same metric, which is most operational dashboards. Recorded as a limitation.

### Finding 3 — subset aggregates are not detected at all (CLOSED — see §8)

*Original entry:* NYC's citywide `case_count` **is** the sum of its five borough columns: r = 1.00000, exact on 56.5% of rows, mean gap 1.2 cases (unknown-borough). The detector tests aggregates over *all* other columns, never over subsets, so it does not flag this. Subset-sum detection is combinatorial; the practical fix is name-based grouping (columns sharing a suffix like `_case_count`), which is not built.

**Closed in §8.** `subset_sums` is built and shipped, and it does not use name-based grouping — dominance arithmetic turns the combinatorial search into a linear peel. Note the correction it forced: `case_count` is **not** an exact identity and is correctly NOT reported. Exact on 56.5% of rows is precisely the kind of near-relation the detector is built to reject. `death_count`, exact on 99.56%, **is** reported. The wording above ("**is** the sum") was carried into `README.md` and `HANDOFF.md` as though it were exact; both have been corrected.

---

## 3. Energy grid — PREDICTIONS (written before fetching)

Physics and market design constrain the answer, so these are checkable.

| # | Prediction | Basis | Result |
|---|---|---|---|
| E1 | Demand ~ temperature is **nonlinear**: flagged by the MI detector while \|r\| < 0.5 | Both heating and cooling raise demand, giving a U-shape whose two arms cancel in a linear fit | — |
| E2 | Total generation and total demand are a **near-identity** (\|r\| ≥ 0.99 raw) | A grid must balance instantaneously; generation is dispatched to meet demand | — |
| E3 | Wind and solar generation are **not** in the same redundancy cluster | Different weather drivers; solar is diurnal-deterministic, wind is synoptic | — |
| E4 | At least one thermal source is **negatively** related to renewable output | Merit-order dispatch: cheap renewables displace gas/coal | — |
| E5 | Independent signals ≥ 3 | Demand, renewable availability and dispatch are genuinely distinct | — |
| E6 | If a "total" column is present alongside its components, the **aggregate detector fires** | This is the subset-sum case documented as a known gap — a chance to see whether the whole-set version catches it | — |

**Energy result: NOT RUN AS A REAL CORPUS — replaced by a synthetic fixture, which is a different and lesser thing.**

`fixture_energy_grid` in `test_signal_audit.py` plants the physics deliberately and scores E1–E6 in the test suite, so the benchmark stays green without an API key. It must **not** be counted as a fourth real dashboard: it tests the TOOL against known physics, not the tool against the world. Every relationship in it was put there by me, which is exactly the property that makes real corpora valuable and this fixture not.

| # | Result on the synthetic fixture |
|---|---|
| E1 | **hit** — demand vs temperature at r = −0.025 (linearly invisible) and flagged by the MI detector. The U-shape is recovered by the nonlinear check and missed by correlation, as predicted. |
| E2 | **hit** — generation vs demand r = 0.9969 |
| E3 | **hit** — wind and solar not clustered |
| E4 | **hit** — gas vs renewables r = −0.774 (merit-order dispatch) |
| E5 | **hit** — 4.16 independent signals |
| E6 | **inverted into a limitation test** — `total_generation` is the exact sum of its three sources on 100% of rows, and the detector does **not** flag it, because it only tests aggregates over *all* other columns. The test now asserts the gap, so it will flip and announce itself if subset-sum detection is ever built. |

Building the fixture took two corrections, both worth recording because they are the same class of error the tool exists to catch:

1. **A confound I introduced myself.** The first version gave demand a diurnal term phase-shifted from the temperature's diurnal term. The two cycles correlated directly at r = +0.59, swamping the U-shape — so the fixture tested a linear relationship while claiming to test a nonlinear one.
2. **A cycle that never completed.** The second version used a 365-day seasonal period over a 1,500-hour (62-day) window. Temperature rose monotonically across the entire sample and only 9% of it fell below the comfort point, giving r = +0.94. Two full cycles inside the window puts 50% either side and the linear component cancels to r = −0.01 — measured before committing rather than after.

The real energy corpus remains open. NESO's CKAN returns empty, EIA v2 requires a key, and the Socrata portals reachable from this toolchain carry billing-level consumption rather than generation-by-source.

**Original note on why it was not fetched:** No fetchable wide-format grid dataset was found within a reasonable budget. NESO's CKAN API returned empty, EIA v2 requires a key, and the Socrata portals that work with this toolchain carry billing-level consumption rather than generation-by-source. E1–E6 remain open and are the strongest untested predictions on file — E1 (demand ~ temperature as a U-shape that linear correlation reports as null) is the single best available test of the nonlinear detector. Reaching it needs an API key or a manual download.

## 4. Transit performance — RESULTS

**MTA Subway Terminal On-Time Performance, weekday, by line.** 23 lines × 49 months (2015–2019), pivoted from long to wide. Source: [NY Open Data f6rf-2a3t](https://data.ny.gov/Transportation/MTA-Subway-Terminal-On-Time-Performance-2015-2019/f6rf-2a3t).

**Headline: 23 metrics, 8.9 independent signals.** 2.1 rows per metric — the crowding warning fires hard, and correctly.

| # | Prediction | Result |
|---|---|---|
| T1 | Same-type per-line metrics cluster more tightly than different metrics on one line | **untestable** — only one metric type (`terminal_on_time_performance`) was pulled. Needs a second measure per line. |
| T2 | Lines sharing physical trunk track cluster more tightly | **hit, strongly** — see below |
| T3 | Service delivered vs on-time performance correlated but not identical | **untestable** — service-delivered is not in this dataset |
| T4 | Independent signals ≥ 2 but well below metric count | **hit** — 8.9 of 23 |
| T5 | Raw-vs-differenced gap small (< 1.5) | **miss** — 3.3. My reasoning was wrong: OTP is bounded, but it trended hard across 2015–2019 (the subway crisis and subsequent recovery), so there is a large shared trend after all. Tool correct, prediction naive. |

### T2 — the tool recovered the physical subway network

Trunk groups were defined from network knowledge **before** any correlation was inspected: 7th Ave IRT (1/2/3), Lexington IRT (4/5/6), 8th Ave IND (A/C/E), 6th Ave IND (B/D/F/M), Broadway BMT (N/Q/R).

| | pairs | mean \|r\| | median |
|---|---|---|---|
| Lines sharing trunk track | 18 | **0.419** | 0.383 |
| All other pairs | 235 | 0.195 | 0.173 |

**2.15× separation, P(within > between) = 0.84.** The strongest pairs are exactly the express/local partners that share the most track: 2~3 (0.83), 4~5 (0.75), A~C (0.73), N~Q (0.68).

The tool was given 23 columns of monthly punctuality percentages and no map. What it recovered is the track layout. That is the most legible demonstration the project has produced of what "redundancy structure" means physically — the correlations are not statistical curiosities, they are shared tunnels.

---

## 5. Triadic (three-way) structure — VALIDATED, AND THE ANSWER IS NO

`triadic_validation.py`, run against all four corpora. The question was whether to extend the auditor from pairwise to three-way, since synergy — information carried jointly by three metrics and by no pair among them — is invisible to everything currently shipped.

**Verdict: do not ship it. On every corpus with adequate samples, three-way analysis found nothing that pairwise analysis had not already found.**

### The estimator's threshold was being used incorrectly

`triadic.py`'s `shuffle_baseline()` returns the **mean** |II| under column shuffling. It was being treated as a detection threshold. It is not one — under the null, roughly half of all triples exceed their own mean by construction.

Measured directly:

| corpus | "synergistic" in REAL data | "synergistic" in SHUFFLED noise |
|---|---|---|
| Synthetic demo | 23 / 680 | **319 / 680** |
| ACT air quality | 25 / 220 | **99 / 220** |

Pure noise scored *more* apparent synergy than real data. The correct threshold is a high quantile of the null |II| distribution (95th percentile ≈ 1.4–1.5× the mean floor), and the count must then be compared against the false positives expected from multiple comparisons — with T triples and a two-tailed 95% cut, that is T × 2.5% in the negative tail.

### Results with the corrected threshold

| corpus | rows | bins | samples/cell | synergistic | expected by chance | ratio | pairwise-invisible |
|---|---|---|---|---|---|---|---|
| ACT air quality | 1,040 | 5 | 8.3 | 16 | 5.5 | 2.9× | **0** |
| NYC COVID | 455 | 4 | 7.1 | 0 | 28.5 | 0.0× | **0** |
| Synthetic demo | 400 | 4 | 6.2 | 7 | 17.0 | 0.4× | **0** |
| MTA subway | 49 | 2 | 6.1 | 130 | 28.5 | 4.6× | 6 (untrustworthy) |

The synthetic corpus is the control: no three-way structure was planted, and with the corrected threshold none was found (0.4× chance). Under the old threshold it reported nine "hidden synergies", four of which involved `nps` — a column generated as pure independent noise.

MTA's apparent 4.6× is not believable: 49 rows forces the estimator to 2 bins, which is close to degenerate, and 23 metrics over 49 rows is noise in any case.

### Three further findings

**Resolution scales as the cube root of history.** bins ≈ (n/5)^(1/3). 500 rows buys 4 bins; 8,000 rows buys 10. Doubling your data buys 26% more resolution. Most dashboards will never have enough.

**The verdict depends on an unswept parameter.** On ACT the synergistic fraction ranged 2%–19% across bin counts 3–8. Any three-way claim must report its bin count and a sensitivity sweep, or it is not a claim.

**Cost grows as C(N,3).** Measured 0.55–8.9 ms per triple. Extrapolated to a 150-metric Pro dashboard: 551,300 triples, 5–80 minutes of CPU. That is a background-job-and-a-half for a feature that has so far found nothing.

### What would change the answer

Three-way analysis becomes worth revisiting when a corpus has **≥ 5,000 rows** (6+ bins, ~20 samples per cell) **and** a domain reason to expect genuine synergy. High-frequency infrastructure telemetry is the plausible candidate; monthly business dashboards are not. Until then the extension is deferred, and the reason is recorded rather than the intuition.

---

## 6. Software / infrastructure telemetry — PREDICTIONS (written before fetching)

**Registered 31/07/2026. No data has been fetched, no source finally chosen, no row inspected.** This is the corpus entry that matters commercially: the product is sold to platform engineers, and until now the corpus contained epidemiological counts, air quality and subway punctuality. A buyer's first question is whether it has been run on anything resembling their estate.

Because this is the domain we sell into, the predictions are deliberately riskier than usual — including one that puts the marketing claim itself at risk.

### The commercial prediction

| # | Prediction | Why it is at risk | Result |
|---|---|---|---|
| **S0** | A real infrastructure dashboard is **≥ 50% redundant** — effective signals ≤ half the metric count | This is the pitch. "Strips away 60–80% of dashboard clutter" is a claim about the world, and the world has not yet been asked. If a real infra board comes back 80% independent, the deck is wrong and needs rewriting, not defending. | — |

### Structural predictions

| # | Prediction | Basis | Result |
|---|---|---|---|
| **S1** | Latency percentile correlation **decays monotonically with percentile distance**: \|r(p50,p75)\| > \|r(p50,p95)\| > \|r(p50,p99)\| | They are order statistics of one distribution, so all correlate — but p50 tracks typical load while p99 tracks tail events (GC pauses, lock contention, cold starts). Distance in the distribution is distance in behaviour. | — |
| **S2** | The percentile family collapses to **2–3 signals, not 1** | The naive expectation is "they're all the same metric". They are not: median and tail diverge during exactly the incidents that matter. Predicting 1 would be the easy call and probably wrong. | — |
| **S3** | `error_count` correlates **strongly** with `request_count`; `error_rate` correlates **weakly** with it | Counts share volume — more traffic produces more errors at a constant rate. Rates divide that volume out. A dashboard carrying both count and rate is carrying one signal and one derived view of it. | — |
| **S4** | Load averages (`load1` / `load5` / `load15`) show **high raw and degraded differenced** correlation, and the tool does **NOT** flag them as a smoothing family | These are nested rolling windows over one quantity — the textbook instance of the rolling-average blind spot, in a domain where it is guaranteed to appear. **This is a prediction of our own documented failure.** If the tool somehow catches it, our limitation note is wrong. | — |
| **S5** | If both `success_rate` and `error_rate` are present they register as an **exact identity** at r ≈ −1.0 | Complements by construction. The cheapest possible win and the clearest demo of the identity detector on real infra data. | — |
| **S6** | Saturation metrics (CPU, memory, load) **cluster together**; they do **not** cluster with latency percentiles | Saturation and response time are coupled through queueing, but nonlinearly and with a threshold — utilisation predicts latency badly until it is near capacity, then abruptly well. | — |
| **S7** | At least one **nonlinear coupling** is detected between a saturation metric and a latency metric, with \|r\| < 0.5 | The queueing relationship above is a hockey stick, which is precisely what correlation misses and mutual information catches. If S6 holds and S7 fails, the coupling is either absent or too weak to resolve. | — |
| **S8** | Any "total" column that sums a **subset** of others is **NOT** detected | Second prediction of a known gap. Infra dashboards are full of per-datacentre or per-service columns plus a total. Confirms the limitation in the domain where it costs most. | — |
| **S9** | Raw-vs-differenced gap is **smaller than NYC COVID's 3.5** | Infrastructure metrics are mean-reverting around a capacity envelope rather than riding an epidemic curve. If the gap is larger, the source is trending (growth or seasonality) and that is worth knowing before anything else is read. | — |

### Added after seeing the schema, before any range data was pulled

Source selected: **PromLabs public Prometheus demo** (`demo.promlabs.com`). Schema probed 31/07/2026 via instant queries only — metric names and label sets, no time series. Provenance is weaker than S0–S9 and flagged as such, in the same convention as A7–A10.

What the instance actually exposes: three synthetic services (`demo-service-0/1/2`) emitting `demo_api_request_duration_seconds` as a **real histogram** with `status` ∈ {200, 404, 500} and `method` ∈ {GET, POST}, plus a `node_exporter` scrape with the standard host metrics including `node_load1/5/15`.

| # | Prediction | Basis | Result |
|---|---|---|---|
| **S10** | `rate(...{status="200"})` and `rate(...{status="500"})` correlate **strongly** (\|r\| ≥ 0.6) | The concrete instance of S3. Both are driven by request volume; a synthetic generator producing errors at a fixed probability makes this near-arithmetic. If it fails, suspect the tool. | — |
| **S11** | The three `demo-service-*` instances behave near-identically — if kept as separate columns they cluster, possibly at identity level | Same workload generator, same code, three replicas. This is the cleanest available analogue of a real fleet where per-host columns are near-duplicates. | — |
| **S12** | `node_memory_MemFree_bytes`, `Cached`, `Buffers` and `MemAvailable` form a cluster, and **MemAvailable is not detected as their sum** | MemAvailable is approximately Free + Cached + reclaimable Buffers — a **subset** sum. Third registered instance of the S8 gap, this time in the exact domain we sell into. | — |
| **S13** | Any column that is constant over the window (`demo_num_cpus`, `demo_disk_total_bytes`, `node_memory_MemTotal_bytes`) is **dropped at load with a stated reason**, not silently | Real infra dashboards carry capacity constants beside utilisation. The tool must exclude them and say so — a silent drop would change what the audit is of. | — |

**Deliberately excluded from the pull:** `demo_batch_last_success_timestamp_seconds` and similar timestamp gauges. They increase monotonically with wall-clock and would inject a synthetic trend confound that tells us nothing about infrastructure — a fake positive for S9.

### RESULTS — 31/07/2026

**PromLabs public Prometheus demo.** 11 metrics × 437 rows at 5-minute step, 96-hour window. Evidence grade **A** (39.6 rows/metric).

**Headline: 11 metrics → 5.6 effective signals, 49% redundant.**

| # | Result |
|---|---|
| **S0** | **MISS — by one point.** Registered ≥ 50% redundant; measured **49%**. One percentage point is still a miss, and calling it "close enough" is the exact rationalisation this protocol exists to prevent. See the honest reading below. |
| S1 | **untestable** — percentiles could not be assembled (tooling truncation, see below) |
| S2 | **untestable** — same |
| S3 | **hit, strongly** — `status=200` ~ `status=404` at r = +0.982 differenced; `status=200` ~ total at **r = 0.99987, flagged as a definitional identity**. Volume coupling is near-arithmetic exactly as predicted. |
| S4 | **premise wrong — and the finding is better than the prediction.** See below. |
| S5 | **untestable** — no success/error *rate* pair in the recovered set |
| S6 | **miss linearly, hit nonlinearly** — memory metrics did not cluster (MemAvailable ~ Cached r = −0.048) but the MI detector flagged that exact pair at **155×** the Gaussian-implied level. Same signature as the ACT NO2/CO result: strongly dependent, not linearly. |
| S7 | **untestable** — no latency series |
| S8 | **hit (gap confirmed)** — `derived_aggregates` returned empty; MemAvailable was not detected as a function of Cached + Buffers |
| S9 | **hit** — trend confound check came back *clear*; no calendar structure at all, against NYC's 3.5 |
| S10 | **hit** — the concrete instance of S3, above |
| S11 | **untestable** — instances were aggregated at query time rather than kept as separate columns. My query design destroyed the test. |
| S12 | **hit (gap confirmed)** — as S8 |
| S13 | **untestable** — constant columns never reached the CSV; the assembly step filtered to series with ≥ 100 recovered points and the capacity constants were not among them |

**Score: 6 hits, 2 misses, 5 untestable.**

### S4 — the prediction was wrong, and why is worth more than the prediction

Registered: `load1/load5/load15` would show **high raw and degraded differenced** correlation, being nested rolling windows over one quantity — the textbook rolling-average blind spot.

Measured:

| pair | raw | differenced |
|---|---|---|
| load1 ~ load5 | +0.761 | **+0.765** |
| load5 ~ load15 | +0.859 | **+0.916** |
| load1 ~ load15 | +0.480 | **+0.563** |

Differencing did not degrade these relationships. It **strengthened** all three.

The reason: **the blind spot depends on the sampling interval relative to the averaging window.** These series were sampled at 300 seconds. `load1` averages over 60 seconds — shorter than the gap between samples — so consecutive observations are effectively independent draws, not a smoothed series. There is nothing for differencing to destroy. The ACT ozone case failed the opposite way: 1-hour samples against an 8-hour average, where the smoothing window is *longer* than the sampling interval.

That refines the documented limitation from "differencing hides a series against its own rolling average" to: **"...when the averaging window exceeds the sampling interval."** Sample slower than you smooth and the blind spot disappears. Recorded against the limitation in `README.md` and `HANDOFF.md`.

A second consequence: the S1 *shape* claim — that correlation decays monotonically with distance in a nested family — was registered about percentiles and could not be tested there. It holds cleanly on the load family instead: 0.765 (adjacent) > 0.563 (distant), and load5~load15 (0.916) > load1~load15 (0.563).

### Why S0 missing by one point is not a crisis, and not an excuse either

49% against a registered 50%. Recorded as a miss.

But the honest reading is that **this corpus is too small to test the claim.** The demo exposes 11 metrics after truncation; the pitch is about dashboards carrying 50–200. Every prior corpus at scale came in far more redundant — NYC 53 metrics → 90%, the synthetic SaaS board 17 → 75%. Redundancy compounds with width, and 11 curated metrics on a demo instance is close to the floor.

What this does mean: **"60–80% of dashboard clutter" cannot be supported by this run**, and should not be claimed on the strength of it. The claim needs a wide production dashboard to stand on, and we do not have one yet.

### Honest assessment of this corpus entry

Weaker than the ACT or NYC entries, for three reasons worth stating before anyone cites it in a deck:

1. **Only the node_exporter metrics are genuine host telemetry.** The `demo_*` series come from a synthetic workload generator — p50 latency varied by under 3% across 24 hours, which no production service does. Findings resting on `demo_*` columns describe a generator.
2. **Fetch truncation cost roughly half the intended metric set.** Responses cut at ~70KB mid-JSON; a salvage parser recovered complete data points and aligned on common timestamps, but the histogram buckets did not survive in usable numbers. Percentiles were **deliberately not computed** from the partial buckets — interpolating from a truncated set measures which buckets fit down the pipe, with a systematic bias toward the small ones, and that wrong number would have flowed straight into S1 and S2.
3. **One test was destroyed by my own query design.** S11 aggregated across instances at query time, so the per-replica comparison it was written to make became impossible.

**It half-closes the commercial gap.** We can now say the engine has been run against real Prometheus node telemetry, at grade A, and that it found a genuine identity pair and a 155× nonlinear coupling. We cannot yet say it has been run against a production service dashboard. That remains the outstanding corpus need, and it wants a real estate — not a demo instance.

### What would falsify the TOOL rather than the reasoning

Stated in advance so the distinction cannot be made retrospectively:

- **S3 failing** (error count uncorrelated with request count) would indicate a data or tool problem, not a surprising system. Volume coupling is close to arithmetic.
- **S5 failing** on a genuine complement pair would mean the identity detector is broken.
- **A 20-metric infra dashboard reporting 18+ independent signals** would contradict both the product thesis and every prior corpus. It would warrant investigating the tool before believing the result.

Conversely, **S1, S2, S6 and S7 failing are domain-reasoning risks** — real systems may simply not behave as expected, and that is a finding about infrastructure rather than about the engine.

### Source candidates, none yet chosen

Recorded now so the selection cannot be retrofitted to the predictions.

| Source | What it is | Concern |
|---|---|---|
| Prometheus public demo instance | Genuine node telemetry from real hosts — CPU, memory, load, disk, network | The most authentic option. Retention may be short; needs range queries assembled into a wide CSV |
| Wikimedia analytics API | Pageviews by project / access method / agent | Real production traffic at scale, but it is **traffic analytics, not infrastructure telemetry** — closer to a product dashboard than an SRE one. Weaker fit for S1/S4/S6 |
| Public status-page histories | Uptime and incident series | Usually too coarse and too few columns |
| Open-source CI/build telemetry | Build duration, queue time, pass rate | Good fit but usually needs authentication |

**Schema-specific predictions will be added after the columns are known but before the audit is run**, and flagged with that weaker provenance — the same convention used for A7–A10 in the air-quality run, where the distinction between "predicted blind" and "predicted after seeing the schema" is recorded rather than blurred.

---

## 7. Regulated financial reporting — RESULTS

**Registered 31/07/2026.** No data fetched, no source finally chosen. Target: US bank call reports (FDIC / FFIEC), where accounting identities are **exact by law** rather than by empirical regularity — the sharpest possible ground truth for the subset-sum gap.

### The shape problem, stated before it bites

Every prior corpus is one entity over time. A call-report panel is ~4,000 banks × ~1,500 fields × quarterly, and the shape must be chosen deliberately:

| Shape | rows/metric | Problem |
|---|---|---|
| One bank, many quarters | ~0.05 | Grade D. Unusable. |
| **Cross-section: banks as rows, fields as columns** | ~80 | **Usable — and rows are entities, not time.** |

The cross-section is the only workable shape, and it breaks an assumption the engine is built on: **first differences are meaningless when consecutive rows are different banks.** The headline defaults to the differenced view. On this data that default is wrong, and the raw view is the correct one.

### The predictions

| # | Prediction | Basis | Result |
|---|---|---|---|
| **F0** | The **differenced headline is invalid** on entity-indexed rows, and the tool gives no warning. Differenced and raw participation ratios will diverge substantially, and unlike every prior corpus the **raw** figure is the one to read. | Differencing bank *i+1* minus bank *i* computes the gap between two unrelated institutions. This predicts a **new, undocumented tool limitation** — the engine has no notion of whether rows are ordered. | **miss — and the truth is worse than the prediction.** Raw PR 2.52, differenced 2.47: they agree to within 0.05. The operation *is* undefined, but it returns almost the right answer, so **the invalidity is silent**. There is no divergence to warn anyone. See finding 1. |
| **F1** | `total_assets` ≈ `total_liabilities + total_equity` at **r = 1.0000** to reporting precision, flagged as a definitional identity | The fundamental accounting equation. Legally mandated, not an empirical regularity. If this fails, the tool is broken or the fields were misread. | **split.** The arithmetic held perfectly — r(ASSET, LIAB+EQ) = **1.00000000**. The detection did not: the engine compares column *pairs*, and this identity is a *sum*. What it flagged instead was `ASSET`~`LIAB` at 0.99967, labelled "the same quantity under two names" — which is **false**. See finding 4. |
| **F2** | `total_assets` is **NOT detected** as the sum of its components (cash + securities + loans + premises + other) | The subset-sum gap, now against legally exact ground truth. Fourth registered instance, and the one where failing to detect it is least defensible. | **hit — gap confirmed against legally exact ground truth.** Tested on the cleanest available case: `LNRE` = `LNREAG+LNRECONS+LNREMULT+LNRENRES+LNRERES`, a *complete* five-child decomposition holding on **1,911/1,915 banks (99.79%)**, r = 0.99999. Not detected as a sum. `LNRE` *was* flagged — as a **`min`** with 66% attainment, i.e. for the wrong reason. **Now CLOSED — see §8**, which builds the detector against this exact relation. |
| **F3** | Loan sub-categories (real estate, C&I, consumer, agricultural) **cluster tightly** with `total_loans` | A nested reporting hierarchy where the parent is the definitional sum of the children. | **miss.** Two of the five children (`LNRENRES`, `LNRERES`) share a cluster with `LNRE` — but that cluster holds **24 of 39 metrics**, so it is not evidence of the loan hierarchy, it is single-linkage chaining under scale dominance. `LNREAG`, `LNRECONS` and `LNREMULT` are singletons. The hierarchy was not recovered. See finding 3. |
| **F4** | **The scale confound.** Raw participation ratio will be **very low (≤ 3)** because every dollar-denominated field is dominated by institution size. A $2tn bank and a $200m bank differ on every column at once. | This is the cross-sectional analogue of the calendar trend confound: **size plays the role in a cross-section that trend plays in a time series.** If it holds, the engine has a second confound family it does not currently name. | **hit, decisively.** 39 metrics → **2.52 effective signals, 93.5% redundant** — the most extreme redundancy in the whole corpus, against a genuine 50,000× spread in bank size ($7.4m to $370bn). Registered ≤ 3; measured 2.52. |
| **F5** | Normalising every field by `total_assets` (working in ratios rather than dollars) **raises the participation ratio substantially** — the analogue of differencing | If F4 and F5 both hold, the correct general statement is: *remove the dominant scaling variable, whichever it is.* Time series need differencing; cross-sections need ratio-normalisation. Same disease, different cure. | **hit, decisively.** Dividing every dollar field by `ASSET` lifts the participation ratio from **2.52 to 14.78** — a 5.9× rise, 93.5% redundant down to 61.1%. Spurious aggregate flags collapse from 28 to 3 and the 24-metric mega-cluster breaks up. **The cure works.** |
| **F6** | Ratio fields that regulators already publish (capital ratios, ROA) are **near-identities** with the same ratio computed from their numerator and denominator columns | Both are present in the filing. A dashboard carrying both is carrying one number twice. | **hit.** `ROA` vs 4·NETINC/ASSET = **0.9995**; `ROE` vs 4·NETINC/EQ = **0.9986** after trimming the extreme 1% (untrimmed it reads 0.754 — an outlier artefact). Both sit at or just under the 0.999 threshold, repeating the A8 pattern: FDIC computes these on **average** balances while the reconstruction uses **period-end**, so a deterministic relationship again falls just short of being reported. |
| **F7** | Evidence grade **A** | ~4,000 institutions as rows against ~50 fields is far more sample than any prior corpus. | **hit.** Grade **A**, 49.1 rows per metric, 1,915 banks × 39 metrics. |

**Score: 5 hits, 2 misses, 1 split.** The two misses (F0, F3) and the split (F1) are all *tool* findings rather than wrong domain reasoning, and are the most valuable output of this run.

**What would falsify the TOOL rather than the reasoning:** F1 failing. The accounting equation is arithmetic, not behaviour — if the identity detector misses it on clean regulatory data, the detector is wrong.

**What would be a finding about the world:** F4 or F5 failing would mean cross-sectional bank data is not scale-dominated, which would genuinely surprise me and would be worth understanding before drawing any conclusion about the engine.

### Ingestion plan, with the lesson from the Prometheus run applied

The Prometheus attempt lost roughly half its metric set to a ~70KB response ceiling that truncates mid-JSON. That constraint is now known and designed around rather than discovered again:

1. **Source: the FDIC BankFind Suite REST API** (`banks.data.fdic.gov/api/financials`). JSON, no key required for basic use, supports field selection and row limits. FFIEC CDR bulk files are ZIPs behind a form and are not fetchable from here.
2. **Select fields explicitly, never `*`.** Roughly 40–60 named fields covering the balance-sheet identity, the loan hierarchy, and a few published ratios. This keeps each response inside the ceiling by construction.
3. **Page by institution count**, not by field. Rows are cheap to split across requests; columns are not.
4. **Verify before analysing.** Check that the recovered rows actually satisfy `assets = liabilities + equity` in the raw data *before* running the audit. If the arithmetic does not hold in the source, the corpus is wrong and no result from it means anything.

### What was actually ingested

**1,915 US banks × 39 metrics, 2024 Q1 (REPDTE 20240331), from the FDIC BankFind Suite REST API.** 49.1 rows per metric — evidence grade **A**. Rows are institutions; the file is deliberately not a time series.

Both constraints anticipated in the plan bound, and a third appeared:

| Constraint | Effect |
|---|---|
| ~64KB response ceiling, cutting **mid-row** | Every page ends in a partial line. `build_fdic_csv.py` drops any row whose field count does not match the header rather than trusting it — 1 row lost per page. |
| URL length cap of ~14 field names | The 39 metrics were pulled as **four field groups joined on `CERT`**, inner join. |
| **Unanticipated: pagination interacts with truncation** | Pages requested at `offset=800` began *after* the previous page had already truncated at ~669 rows, leaving an unfetched gap. The first build silently returned 669 banks instead of 1,500. Fixed by fetching bridging pages at `offset=650`. **A stray exploratory probe also joined as a fifth group and capped the intersection at its own row count** — groups are now matched on a signature field so that acceptance is explicit. |

### The arithmetic gate — FAILED as specified, then diagnosed

The registered rule was: verify `assets = liabilities + equity` in the raw data first, and if the source arithmetic is broken, discard the corpus.

| Identity | Exact to the dollar |
|---|---|
| `LNLSNET = LNLSGR − LNATRES` | **1,956 / 1,956 — 100.00%** |
| `LNRE = LNREAG+LNRECONS+LNREMULT+LNRENRES+LNRERES` | 1,952 / 1,956 — 99.80% |
| **`ASSET = LIAB + EQ`** | **1,915 / 1,956 — 97.90%** ← below the 99.5% bar |

**The gate failed at the threshold I set before seeing the data, and that is recorded rather than adjusted.** The diagnosis: of the 41 failures, **40 are positive** (`ASSET` > `LIAB+EQ`), the residual is a median 0.016% of assets, and the affected banks are ~9× larger than the median institution. That is the signature of **noncontrolling interests in consolidated subsidiaries** — a term only large banks carry, and one absent from the fetched field set. A corrupt join would produce residuals of random sign and arbitrary size; these do neither.

The explanation is **inferred, not confirmed** — the FDIC field for the term could not be located, and its API definitions file returned empty. So the 41 banks were **excluded** rather than reconciled. Exclusion is cheap here: the largest institution in the corpus passes the gate, so the full 50,000× size range survives intact and prediction F4 is not weakened by the removal.

### What this corpus changed in the tool

**Finding 1 — differencing entity-indexed rows is undefined, and fails silently.**
The engine has no notion of whether rows are ordered. On this file it differenced bank *i+1* minus bank *i* and headlined the result. F0 predicted the two views would diverge and expose the problem. They did not: 2.47 against 2.52. The invalid operation returns approximately the valid answer, because differencing scale-dominated columns leaves differences that are *still* scale-dominated. **A silent wrong answer is worse than a loud one**, and nothing in the output distinguishes this file from a time series.

**Finding 2 — the scale confound is a second confound family, with its own cure.**
F4 and F5 together establish it. In a cross-section, institution size does exactly what a calendar trend does in a time series: every dollar-denominated column moves with it, so everything correlates with everything (2.52 signals from 39 metrics). Dividing through by `ASSET` lifts that to 14.78. The general statement is therefore **remove the dominant scaling variable, whichever it is** — differencing for time, ratio-normalisation for a cross-section. The engine ships only the first cure and does not name the second.

**Finding 3 — single-linkage clustering chains catastrophically under scale dominance.**
One cluster absorbed **24 of 39 metrics**, spanning assets, deposits, loans, employees and expenses. That is not a redundancy group anyone can act on. It also destroyed the F3 test: two RE children fell into the mega-cluster and three were left as singletons, so the loan hierarchy was invisible either way. In the ratio view the same data yields 32 clusters.

**Finding 4 — the identity detector cannot tell a definition from a near-proportionality.**
`ASSET`~`LIAB` at 0.99967 was reported as "the same quantity under two names." It is not — they differ by equity, which is the entire subject of bank capital regulation. Under scale dominance the 0.999 threshold stops meaning "definitionally identical" and starts meaning "both large". Meanwhile the relationship that *is* legally exact, `ASSET = LIAB + EQ` at r = 1.00000000, is invisible because it is a sum.

**Finding 5 — the derived-aggregate detector regresses under scale dominance.**
It flagged **28 of 39 metrics**, 21 of them as `max`, including `LIAB` — which is not the maximum of anything, it is 92% of assets. This is the NYC "39 of 53" failure mode returning: the four-attempt fix was validated on time series and does not hold cross-sectionally. In the ratio view the count falls from 28 to 3, which confirms scale as the cause. **The 28 figure in the dollar-view run above should be read as a detector failure, not a finding about banks.**

### Honest limits of this corpus

- **One quarter, not a panel.** The decades-deep history is available (records reach back to 1984) but is untouched here; this is a single cross-section.
- **1,915 of 4,640 banks**, taken as a contiguous block by `CERT` rather than sampled at random. `CERT` is roughly charter-order, so this skews toward older institutions.
- **F2 was tested on `LNRE`, not on `ASSET`.** The registered wording named the asset decomposition, but the fetched field set does not contain every asset component, so no exact subset sums to `ASSET`. `LNRE` is the stronger test anyway — a complete five-child decomposition — but it is not the test as written.
- The subset-sum detector still **does not exist**. This run measures the size of the hole precisely; it does not fill it.

---

## 8. Repair cycle — the subset-sum gap, closed

The first entry in this file that runs **gap disclosed → detector built → gap shut → new bug found → new bug fixed**. Recorded in that order because the order is the point: the gap was published before there was any fix for it, in §2 finding 3 and again in §7 F2.

### What was disclosed

Across four corpora the engine could not see a column that is the **sum of a named subset** of the others. `derived_aggregates` tests max / min / mean over *all* other columns; a subset total is invisible to it. §7 measured the size of the hole against ground truth that is exact by law: FFIEC `LNRE` = its five named children on 99.79% of 1,915 banks, r = 0.99999, undetected — and flagged instead as a `min`, for the wrong reason.

### Why it was tractable

The obvious objection is combinatorics: 2^(N−1) subsets per parent, 2.7×10¹¹ at N=39. But the relation must hold on **every row**, which converts the search into arithmetic. If a parent is the sum of non-negative children then every child is ≤ the parent on every row, and after removing some children each remaining child is ≤ the **residual**. Take the largest dominated column, subtract, repeat, and require the residual to close to zero. The initial dominance set is computed once per parent in a single vectorised pass, so cost is O(N²·rows) rather than exponential.

Earlier notes in this file proposed name-based grouping (columns sharing a suffix) as the practical fix. **That turned out to be unnecessary** — the arithmetic is stronger than the naming convention, and does not break on a dashboard whose columns are named inconsistently.

### Negative controls, run before the positive case

Three of the four rejected `derived_aggregates` designs looked correct on a planted positive and failed only on data with no relation in it. So the controls came first and are permanent tests:

| Control | Expected | Result |
|---|---|---|
| Independent non-negative noise, 900×20 | zero | **zero** |
| Non-negative random walks, 1000×20 | zero | **zero** |
| **Pure scale dominance, no additive relation, 900×18** | zero | **zero** — the confound that defeated `derived_aggregates` |
| **Parent = 0.97 × sum(children)** | zero | **zero** — exactness, not correlation |
| Signed columns | excluded | **excluded** |
| All-zero column as candidate child | never used | **never used** |
| Planted `total = sum(4 parts)` + 3 decoys | found | **found**, residual 2×10⁻¹⁶ |

### What it finds on the real corpora

| Corpus | Sums found |
|---|---|
| **FDIC call reports** 39×1,915 | **3** — `LNRE` = its five children; `ASSET` = `LIAB` + `EQ`; `LNLSGR` = `LNLSNET` + `LNATRES` |
| **NYC COVID** 53×455 | **5** — `death_count` = its five boroughs, plus four `all_case = confirmed + probable` |
| ACT air quality · MTA subway · Prometheus · demo | **0** |

Zero on the four corpora with no additive structure; every relation on the two that have one. The accounting equation resolves at a worst relative residual of exactly **0.0**.

**A correction the detector forced.** This file recorded NYC `case_count` as "**is** the sum of its five borough columns", and that wording was carried into `README.md` and `HANDOFF.md` as though it were an identity. It is not: r = 0.99999998, but exact on only **56.5%** of rows, because some cases carry no borough. The detector correctly refuses it. `hospitalized_count` reaches 96% and is also refused, being under the 99% bar. Only `death_count` at 99.56% qualifies. Both summaries have been corrected — **the near-miss we had been citing as our flagship example of the gap was never an exact sum at all.**

### The worse bug this exposed

Building the detector surfaced a defect far more dangerous than the one it was written to fix, and one that had been shipping silently.

In a family of k children plus their parent there is exactly **one** linear relation, so the family carries k independent quantities. But every member is perfectly predictable from the others — the parent is the sum of the children, and each child is the parent minus its siblings. The unique-variance regression therefore scores **all** of them at zero and offered the entire family for deletion:

```
LNRE family  ->  archive candidates: LNRE, LNRERES, LNRENRES,
                                     LNREAG, LNRECONS, LNREMULT
                 6 of 6 members of one additive family
```

Acting on that list deletes an entire loan book. `LIAB` and `EQ` were likewise both offered, which drops bank equity — the subject of capital regulation — while keeping total assets.

**The bug was always there. It was invisible because nothing in the engine could see a sum.** It is the same failure class as the identity-pair bug that once offered both halves of a duplicated metric, and it is guarded the same way: name the survivors explicitly rather than trusting the ranking. `subset_sum_protected` now protects the children and archives only the parent — exactly one column per family, which is exactly the redundancy present. Archive candidates on the FDIC corpus fell from **28 to 19**.

### What is still not detected

Stated so this section is not read as a bigger claim than it is:

- **Signed columns are excluded entirely.** Dominance is meaningless when a child can be negative. On an income statement that excludes most of the sheet.
- **Approximate sums are rejected by design.** A relation holding on 96% of rows is not reported. `case_count` and `hospitalized_count` are the worked examples.
- **A sum whose components are not all present cannot close.** `ASSET` decomposes into components the fetched field set does not fully contain, so that relation is absent from the results above.
- **Skipped above 250 columns**, where the O(N²·rows) pass stops being cheap.
- The **rolling-average blind spot** and the **scale confound** remain open. This cycle closed one gap, not the list.

**Cost:** 63 engine tests, up from 52. Full suite 172, all green.

---

## 9. Scaling wall — the nonlinear/MI scan (REGISTERED, not fixed)

Registered the day it was found, before any attempt to fix it, and separated from the ratio-basis work so that a slowdown which **predates** that work cannot later be blamed on it.

### Measured, not estimated

2000 rows, wide synthetic boards, timed against the working module:

| Metrics | Pairs | Nonlinear/MI scan | All per-basis work |
|---:|---:|---:|---:|
| 20 | 190 | **1.15 s** | 0.01 s |
| 40 | 780 | **4.37 s** | 0.07 s |
| 60 | 1,770 | **9.67 s** | 0.19 s |
| 90 | 4,005 | **21.65 s** | 0.52 s |

The MI scan is **~98% of the run** and scales with the pair count, C(N,2) — quadratic in metrics. Extrapolating on that shape: **~110 s at 200 metrics**, ~170 s at 250, where `subset_sums` already caps.

### Why this is a productization blocker, not a curiosity

The pitch is about boards of **50–200 metrics**. That is precisely the band where this becomes a multi-minute wait. It also lands hardest in the browser demo, where Pyodide is slower than native and there is no progress indicator — the first impression of the product would be a page that appears to hang.

**None of this is caused by the multi-basis work.** An extra basis costs ~2% of a run (0.19 s against 9.67 s at 60 metrics). The wall is in a one-time, top-level computation that has been there since the nonlinear check was added.

### What has NOT been done

No fix is attempted here and none is implied. The obvious directions — screening pairs by correlation before paying for MI, subsampling rows for the estimate, caching the shuffle baseline across pairs, or capping N with an honest `○ not checked` — all change **which pairs get examined**, and the nonlinear check exists to find pairs a correlation matrix misses. Screening on correlation to save time on a detector whose purpose is finding non-correlated dependence is close to self-defeating, and would need its own pre-registration and negative controls before it could be trusted. Recorded as an open limit.

### Second finding, unrelated but found in the same session

A **stale `signal_audit.py` was discovered in `~/.local/lib/python3.10/site-packages`**, left by an earlier editable install. Any script run from outside the project directory imports that copy instead of the source, and it predates both `subset_sums` and `build_view`. One benchmark in this session was measured through it before the mismatch was noticed.

The demo has a drift guard (`build_demo.py --check`); **the installed package has none.** A developer running `redd` from their home directory can silently exercise an older engine than the one under test, and the numbers will look plausible. Registered here; no guard built yet.

---

## Scoring

Each prediction is marked **hit**, **miss**, or **untestable** (data did not contain what was needed). A miss is not automatically a tool failure — the domain reasoning may have been wrong — and the two are distinguished in the notes.
