# Phase 1 hand-off

Status as of 31/07/2026 — **Phase 1 complete and frozen.** Written to be read by someone who was not here.

---

## 1. Browser smoke test — PASSED

Verified 31/07/2026. Every figure matched the CLI exactly against `sample_dashboard.csv`, with zero console errors: `17 metrics → 4.3 signals`, 74.5% redundant, evidence grade B, 6 checks run / 3 fired, 3 identity pairs, 4 archive candidates.

Both LAPACK-backed calls — `np.linalg.eigvalsh` (participation ratio) and `np.linalg.lstsq` (unique variance) — executed cleanly under WebAssembly. These were the two most likely failure points and neither surfaced a problem.

**"Runs in the browser" is now a tested fact rather than a design property**, and may be claimed publicly.

Outstanding: the browser and version string were not supplied (the hand-off message carried an unfilled placeholder), so no specific browser is recorded as supported. Any claim of the form "works in X" still needs that string.

To re-verify on a new browser:

```bash
cd signal-audit          # the folder name is unchanged
python build_demo.py
cd demo && python3 -m http.server 8000     # open http://localhost:8000
```

---

## 2. PyPI publication

Verified locally in a clean directory — both artefacts build and `twine check` passes.

```bash
cd signal-audit          # the folder name is unchanged
python -m pip install --upgrade build twine

rm -rf dist build *.egg-info          # never ship a stale artefact
python -m build                       # -> dist/*.whl and dist/*.tar.gz
twine check dist/*                    # metadata validity
```

**Before uploading, run this once:**

```bash
python -m venv /tmp/v && /tmp/v/bin/pip install dist/*.whl
# the execution guard refuses an installed engine inside the source
# tree; this test is the one legitimate exception, so declare it
REDD_ALLOW_INSTALLED=1 /tmp/v/bin/redd run demo_dashboard.csv
```

A wheel that imports in your dev environment but not a clean one is the classic packaging failure, and it costs a version number to fix after release.

```bash
twine upload --repository testpypi dist/*     # rehearse
pip install --index-url https://test.pypi.org/simple/ redd-munro

twine upload dist/*                           # live
```

**Notes.**

- The wheel contains exactly two modules — `signal_audit.py` and `signal_audit_cli.py` — plus the `redd` console entry point (distribution: `redd-munro`). Verified by inspecting the built wheel.
- `numpy>=1.21` is the only runtime dependency. That is a deliberate constraint, not an accident: it is what makes the Pyodide demo possible and the container small. Adding a dependency costs the browser demo.
- FastAPI, uvicorn and python-multipart are under `[project.optional-dependencies].server` and are not installed by `pip install redd-munro`.
- **`redd` was taken on PyPI; the distribution is `redd-munro`, confirmed free.** The command a user types is `redd` regardless — `[project.scripts]` is a separate namespace from the distribution name, so `pip install redd-munro` puts a `redd` executable on the path. Small known risk: if the existing PyPI `redd` package also ships a `redd` console script, a user with both installed gets a clash. Worth checking what that package is before release.
- Version is `0.1.0` in `pyproject.toml`. PyPI refuses re-uploads of an existing version, so bump before every release.

---

## 3. Asset inventory

### Engine — `signal_audit.py`
Single module, numpy only, ~1,000 lines. Public surface:

| Function | Purpose |
|---|---|
| `audit(path)` / `audit_text(csv)` | The analysis. Text route is bit-identical and needs no filesystem. |
| `report_payload(res)` | The JSON contract. One shape for CLI, API and browser. |
| `assurance(res)` | Evidence grade A–D, separate from the finding. |
| `deletion_candidates(res)` | Archive candidates, identity survivors AND subset-sum children excluded. |
| `identity_representatives(res)` | Which half of an identity pair survives. |
| `subset_sums(M, names)` | Columns that are the exact sum of a named subset of others. |
| `build_view(names, X)` | Every per-basis figure. Basis-agnostic by design. |
| `ratio_basis(names, M, denom, exempt)` | The ÷denominator transform. Returns (names, X, notes). |
| `basis_conflicts(res)` | Pairs a transform made MORE correlated — injected or exposed. |
| `subset_sum_protected(res)` | Children of any detected sum — never archivable. |
| `blast_radius_worksheet(res)` | The safety worksheet, shared column shape with the service. |
| `write_html(res, path)` | Self-contained report, no external requests. |
| `CATALOGUE` | The failure-mode list the CLI and payload both render. |

### CLI — `signal_audit_cli.py`
`redd run` and `redd prune`, with `--html`, `--json`, `--worksheet`, `--ignore`, `--top`, `--quiet`, `--no-fail`, and the basis flags `--scale-by` (repeatable), `--scale-exempt` (repeatable), `--basis`, `--strict-basis`, `--domain`. Exit codes: `0` clean, `1` heavy redundancy, `2` input error — so it gates CI. Colour degrades when piped. Zero presentation logic in the engine, zero analysis in the CLI.

### Backend — `backend/`
| File | Role |
|---|---|
| `service.py` | Contract, metering, tiers, pruning queue. All policy lives here. |
| `jobs.py` | Async execution. `InMemoryJobStore` + a `RedisJobStore` stub that refuses to run. |
| `exports.py` | Worksheet, manifest, reversible Terraform, exclusion JSON. Gated. |
| `cost.py` | Savings estimation. Returns null without a customer-supplied unit cost. |
| `schemas.py` | Pydantic models. Closed badge enum. |
| `app.py` | FastAPI. 12 documented endpoints, `openapi.json` generated. `POST /v1/audits` carries the basis declaration as form fields (`scale_by`, `scale_exempt`, `basis`, `require_basis`) on **both** the sync and async paths. |

### Browser demo — `demo/`
`index.html` (source) + engine copy + sample CSV + **`domains/` lexicons and a generated `index.json` manifest**. Dark cockpit UI, a domain-lens dropdown, and a collapsible About panel. The lexicons are fetched by JavaScript and **never enter Pyodide** — switching lens re-renders from the cached payload, so the engine cannot be reached by a naming layer and the numbers cannot move. `build_demo.py` assembles everything and verifies the engine copy produces byte-identical payloads; the lexicons get their own guard (byte-compare, manifest must match source, orphaned copies flagged) because a payload check cannot cover data that by design changes no payload. `--check` fails CI on any of it. All four drift modes tested by deliberately breaking them.

### Test corpus and the moat
| Suite | Count | What it guards |
|---|---|---|
| `test_signal_audit.py` | 130 | The analysis, against planted ground truth |
| `backend/test_backend.py` | 108 | Contract, quota, metering, jobs, exports |
| `backend/test_schemas.py` | 17 | Models match what the service actually emits |
| `check_pyodide.py` | 12 | Browser preconditions |
| **Total** | **267** | `test_scale_basis.py` is now folded into the engine suite |

`REAL_DASHBOARDS.md` is the commercial asset: six real corpora (NYC COVID 53×455, ACT air quality 12×1,094, MTA subway 23×49, Prometheus 11×437, FDIC call reports 39×1,915, Open LLM Leaderboard 15×541), predictions registered before fetching, every rejected detector design recorded with the evidence that killed it, and — from §8 — one closed repair cycle showing a disclosed gap being shut.

---

## 4. Is Phase 1 feature-complete?

**Starter tier: yes, fully verified.** Local CLI, browser sandbox, HTML reports, self-contained test suite. Everything the free tier promises exists, is tested, and the browser path is confirmed working.

**Pro tier: functionally complete, not deployable.** The blast-radius worksheet and reversible Terraform exporter both work and are gated correctly. But three things must be built before it takes money:

1. **Auth is a stub.** `resolve_account` returns a fixed dev account with Pro limits. Set `SIGNAL_AUDIT_REQUIRE_AUTH=1` and it refuses rather than permits — but it must be replaced, not configured.
2. **No persistence.** Jobs and results live in memory. A restart loses everything; `/v1/usage` sums job memory rather than a billing store.
3. **`InMemoryJobStore` is single-worker only.** Behind two workers, a client polling the wrong one gets 404 for a running job. `RedisJobStore` raises with that explanation rather than half-working.

**Enterprise tier: not started.** Live connectors, alert/SLO dependency mapping and CI drift governance are all Phase 2+.

### Known gaps, carried forward deliberately

| Gap | Status |
|---|---|
| **Nonlinear/MI scan scaling** | **CLOSED — 63× on lossless changes only.** Scan at 200 metrics 108.7s → 1.72s; full `audit()` 12.8s. Two changes, both required to alter no figure: joint entropy by `bincount` instead of a per-pair sort (verified bit-identical), and skipping MI for pairs the \|r\| < 0.5 gate discards anyway. Reported pairs are identical on all six corpora. The contested optimisation — sampling the bias floor — was **abandoned when its pre-registered precondition failed**, and turned out to be unnecessary. See `MI_SCALING_PREREG.md`. |
| **Stale installed package** | **CLOSED.** `execution_guard()` in the CLI refuses to run when you are standing in the source tree but the engine resolved from elsewhere, naming both paths and the fix. Fails closed. `REDD_ALLOW_INSTALLED=1` is the one escape hatch, needed because this document's own wheel test runs the installed binary from inside the repo — a guard without it would have been routed around on day one. |
| Rolling-average blind spot | **Not detected.** Two designs built and withdrawn — one flagged independent random walks, the other produced 476 false positives while missing the confirmed real case. Renders as `○ not checked`. |
| Subset-sum aggregates | **CLOSED — shipped and tested.** `subset_sums` detects a column that is the exact sum of a named subset of others. Recovers NYC `death_count` = its five boroughs, and FFIEC `LNRE` = its five named children on 1,915 banks. Restrictions: exact sums only (a relation holding on 96% of rows is rejected), non-negative columns only, skipped above 250 columns. Building it exposed a **catastrophic archive bug** — see below. |
| Additive-family archive guard | **Fixed.** Every member of an additive family is perfectly predictable from the others, so the unguarded ranking offered all six members of `LNRE = 5 children` for deletion at once, and both `LIAB` and `EQ`. `subset_sum_protected` now protects the children and archives only the parent. Same failure class as the identity-pair bug. |
| **Trend check fires on data with no time axis** | **CLOSED.** Row order is now a declaration (`--ordered` / `--not-ordered`), assumed from a time-like header column and reported as ASSUMED when nobody stated it. `TIME_DEPENDENT` lists the checks that consult the gate, so the trend check and correlation drift cannot drift apart. On `llm_leaderboard.csv` the trend gap is -0.503, just past the -0.5 firing threshold: it fired before and does not now, while `nyc_covid` at -3.467 still does. A gated check reports `- not applicable` with a reason, which is deliberately distinct from `o not implemented` — a check that could not run is not a check that passed, and the CLI rendered it as `clear` until a test caught that. |
| **Subset-MEAN aggregates undetected** | **Open, found by the AI corpus.** `derived_aggregates` tests max/min/mean over ALL other columns; `subset_sums` finds exact SUMS over a subset. Neither finds a MEAN over a subset. The Open LLM Leaderboard's `Average` column is exactly the mean of six of fourteen others — r = 1.00000000, zero deviation on 541 of 541 models — and nothing flagged it. Same hole `subset_sums` closed, in its averaging form. |
| **Equal-occupancy binning on tied data** | **Partly closed, and the repair disproved the original diagnosis.** Duplicate quantile edges are now collapsed and labels compacted, so tied columns get honest bin counts and are reported as `tied` with their occupancy. **This moves no number**: empty bins contribute `0·log 0`, so removing them cannot change MI — verified bit-identical on all five corpora. The real defect is what `BINNING_PREREG.md` B7 exposed instead: the bias floor spans **0.0004 to 0.0454** across fixtures, a hundredfold range driven by tie structure, while `nonlinear_pairs` computes **one floor per dataset**. So tied columns are systematically LESS likely to be flagged than continuous ones in the same audit — under-detection, the safer direction, but undocumented until now. A per-column floor is the next repair and needs its own registration. 31 tied columns exist across the shipped corpora. |
| **Derived-aggregate + clustering under scale dominance** | **Open.** Distinct from the scale confound itself: even with a correct ratio basis available, running the dollar basis makes `derived_aggregates` flag 28 of 39 metrics (21 as `max`, including `LIAB`, which is not the maximum of anything) and single-linkage clustering chain 24 of 39 into one unusable group. The four-attempt aggregate fix was validated on time series and does not hold cross-sectionally. No repair attempted. |
| M1–M10 artifacts | Failure modes the engine is **designed around**, not detectors. Windowing errors, phase shifts, baseline wander and definitional discontinuities are not checked. Describe them that way in the deck. |
| Three-way structure | Tested and **deferred** with reasons: found nothing pairwise missed, resolution scales as the cube root of history, cost is C(N,3). |
| Real energy corpus | Not obtained. Replaced by an explicitly synthetic fixture that tests the tool, not the domain. |
| **Entity-indexed (cross-sectional) input** | **Partly closed.** The basis is now a DECLARATION: `--basis raw\|differenced\|ratio:COL`, carried into the payload as `basis.declared`, printed on the headline as `[differenced · ASSUMED]` when nobody chose, and refusable with `--strict-basis` / `require_basis`. What is still absent, deliberately, is INFERENCE — nothing tries to detect whether rows are ordered, because a classifier that guesses wrong reproduces the original silent failure with more machinery. The measurement that motivated this stands: differenced 2.47 vs raw 2.52 on 1,915 banks, so the invalid operation returns nearly the valid answer and no divergence flags it. |
| **Scale confound** | **CLOSED — cure shipped, must be declared.** `--scale-by COL` (repeatable) adds a ratio basis; `--scale-exempt COL` protects columns that are already scale-free, which is not optional politeness — dividing a rate by a size took `ROA`~`ROE` from +0.60 to +0.98, past the clustering threshold. Validated 9/9 against a synthetic cross-section with a known factor count (`SCALE_BASIS_PREREG.md`), including the compositional closure problem: closure matches −1/(d−1) to four decimals, does **not** bias the participation ratio, and crosses no threshold at d ≥ 10 — so plain ratios ship and CLR is not needed. `basis_conflicts` reports any pair a transform inflated. Still true: under the confound the derived-aggregate detector flagged 28 of 39 metrics and single-linkage chained 24 of 39, and **neither of those detectors was fixed** — only the basis that avoids provoking them. |

None of these block Phase 1. All are documented where someone will find them rather than discovering them in front of a customer.
