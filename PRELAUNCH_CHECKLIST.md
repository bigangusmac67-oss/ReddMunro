# Redd Munro — Phase 1 completion & pre-launch checklist

One page. **Phase 1 is complete.** Every item below has been executed and verified, including the browser smoke test.

**Status: 267/267 tests green.** Engine 130 · Backend 108 · Schemas 17 · Pyodide preconditions 12.

---

## 1 · Browser smoke test ☑ PASSED

Verified 31/07/2026 against `sample_dashboard.csv`. Every figure matched the CLI exactly, **zero console errors**:

| Field | Expected | Observed |
|---|---|---|
| Headline | `17 metrics → 4.3 signals` · 74.5% redundant | ✅ match |
| Evidence grade | **B** · actionable | ✅ match |
| Failure catalogue | 6 checks run · 3 fired | ✅ match |
| Identity pairs | 3 | ✅ match |
| Archive candidates | 4 | ✅ match |

`np.linalg.eigvalsh` and `np.linalg.lstsq` — the two LAPACK calls with the largest WASM surface — both executed cleanly. **"Runs in the browser" is now a tested fact, not a design property.**

☐ **Browser and version string still outstanding.** The hand-off message carried an unfilled placeholder, so no browser is recorded. Send it and it goes in here; recording a guess in the document that exists to prevent guessing would defeat the point.

☐ Network-tab zero-trust check — confirm no request after initial asset load, if not already done.

**To re-run** (do this on any new browser you want to claim support for):

```bash
cd signal-audit          # the folder name is unchanged
python build_demo.py
cd demo && python3 -m http.server 8000     # then open http://localhost:8000
```

---

## 2 · PyPI publication

**☑ Distribution name confirmed** — `redd` was taken, `redd-munro` is free. The command stays `redd`: `[project.scripts]` is a separate namespace, so `pip install redd-munro` installs a `redd` executable.

☐ **Reserve `reddmunro` defensively** (no hyphen) so the obvious typo does not become someone else's package. Names are free and first-come.

```bash
twine upload dist/*                      # redd-munro

# then, for the defensive reservation, in a scratch copy:
#   set name = "signal-audit" in pyproject.toml
#   python -m build && twine upload dist/*
# Keep the primary pyproject.toml untouched — the scratch copy exists
# only to hold the name.
```

```bash
cd signal-audit          # the folder name is unchanged
python -m pip install --upgrade build twine

rm -rf dist build *.egg-info             # never ship a stale artefact
python -m build                          # → dist/*.whl + dist/*.tar.gz   ✓ verified
twine check dist/*                       # ✓ both PASSED
```

**☐ Clean-venv install test — do not skip.** A wheel that imports in your dev environment but not a fresh one is the classic packaging failure, and it costs a version number to fix after release.

```bash
python -m venv /tmp/sa-test
/tmp/sa-test/bin/pip install dist/*.whl
REDD_ALLOW_INSTALLED=1 /tmp/sa-test/bin/redd --version    # → redd 0.1.0
REDD_ALLOW_INSTALLED=1 /tmp/sa-test/bin/redd run demo_dashboard.csv
/tmp/sa-test/bin/python -c "import signal_audit; print(signal_audit.__version__)"
```

```bash
twine upload --repository testpypi dist/*         # rehearse first
pip install --index-url https://test.pypi.org/simple/ redd-munro

twine upload dist/*                               # live
```

**Notes.** Wheel contains exactly `signal_audit.py`, `signal_audit_cli.py` and the `redd` entry point — verified by inspection. `numpy>=1.21` is the only runtime dependency; FastAPI/uvicorn sit under `[project.optional-dependencies].server`. PyPI refuses re-uploads of an existing version, so bump `pyproject.toml` before every release.

---

## 3 · Deploy `demo/` as static assets

```bash
python build_demo.py                     # ALWAYS run before deploying
```

Deploy the contents of `demo/`. Exclude `__pycache__` (build cleans it best-effort; on OneDrive/Dropbox volumes deletion can fail with EPERM and it says so).

| File | Purpose |
|---|---|
| `index.html` | The page. Source, not a build artefact. |
| `signal_audit.py` | Engine copy — **generated**, never edit here |
| `sample_dashboard.csv` | Drag-and-drop example |
| `domains/*.json` | Lens lexicons — **generated**, never edit here |
| `domains/index.json` | Lens manifest — **generated**; the browser cannot list a directory |
| `.nojekyll` | GitHub Pages: serve files verbatim |
| `vercel.json` | Vercel: force static, correct MIME on the `.py` |

**GitHub Pages**
```bash
git subtree push --prefix signal-audit/demo origin gh-pages
```
`.nojekyll` is required — without it Jekyll may mangle the directory.

**Vercel**
```bash
cd demo && vercel --prod
```
`vercel.json` sets `framework: null` and pins `Content-Type: text/plain` on `signal_audit.py`. **Without it some hosts treat a root-level `.py` as a serverless function rather than a static asset** — the single most likely deploy failure.

**Netlify / Cloudflare Pages:** publish directory `demo/`, no build command.

☐ **Add to CI** so the demo can never silently run a stale engine:
```bash
python build_demo.py --check             # exit 1 if drifted
```
Guard verified by deliberately corrupting the copy and confirming it caught it.

---

## 4 · Ship / don't-ship

**Shipping — Starter tier, complete:** local CLI (`run`, `prune`, `--html`, `--json`, `--worksheet`), browser sandbox, self-contained HTML reports, 267-test suite, failure catalogue in `REAL_DASHBOARDS.md`.

**Not shipping — Pro/Enterprise, deferred by design:** the backend is functionally complete but **must not take money yet** — auth is a stub, there is no persistence, and `InMemoryJobStore` breaks behind more than one worker. All three raise or warn rather than failing quietly.

**Say this accurately in the deck:**

| Claim | Reality |
|---|---|
| Trend confound, derived-aggregate trap, noise-hallucination guard | **Shipped and tested** |
| Scale confound (cross-sections) | **Not handled.** Ratio-normalise before running — see README |
| Rolling-average blind spot | **Not detected.** Two designs built and withdrawn — renders as `○ not checked` |
| Subset-sum aggregates | **Shipped and tested.** Exact sums over non-negative columns; approximate sums deliberately rejected |
| M1–M10 (windowing, phase shift, baseline wander) | Failure modes we **designed around**, not detectors we run |
| Three-way synergy | Tested and **deferred** — found nothing pairwise missed |
| "Runs in the browser" | **Verified** — exact figures matched, no console errors |
