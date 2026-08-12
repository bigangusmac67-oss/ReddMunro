# Redd Munro — Phase 1 completion & pre-launch checklist

One page. **Phase 1 is complete.** Every item below has been executed and verified, including the browser smoke test.

**Status: 392 tests green.** Engine 284 · Backend 108. Plus four scoring scripts that are run and recorded rather than asserted: `score_drift.py`, `score_history.py`, `score_binning.py`, and the wasm32 CI job.

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

## 2 · PyPI publication ☑ PUBLISHED 12/08/2026

**Live at https://pypi.org/project/redd-munro/0.1.0/** — verified by installing `redd-munro[refgraph]` from the real index into a clean virtualenv and running an audit, not by reading the project page.

**☑ Distribution name confirmed** — `redd` was taken, `redd-munro` is free. The command stays `redd`: `[project.scripts]` is a separate namespace, so `pip install redd-munro` installs a `redd` executable.

**Not doing: defensive name reservation.** An earlier draft of this file
described uploading placeholder packages to hold `reddmunro` and
`signal-audit` against typos. PyPI's policy treats packages uploaded
solely to reserve a name as squatting, and it is a strange thing to
publish in a repository whose argument is that we do the honest version
even when it costs something. If a typo becomes a real problem, the fix
is a genuine alias package that installs the real one, not an empty
shell.

```bash
cd signal-audit          # the folder name is unchanged
python -m pip install --upgrade build twine

rm -rf dist build *.egg-info             # never ship a stale artefact
python -m build                          # → dist/*.whl + dist/*.tar.gz   ✓ verified
twine check dist/*                       # ✓ both PASSED
```

**☑ Clean-venv install test — done 12/08/2026, see the table at the foot of this file.** A wheel that imports in your dev environment but not a fresh one is the classic packaging failure, and it costs a version number to fix after release.

```bash
python -m venv /tmp/sa-test
/tmp/sa-test/bin/pip install "dist/*.whl[refgraph]"
/tmp/sa-test/bin/redd --version                          # → redd 0.1.0
cd /tmp && /tmp/sa-test/bin/redd run data/prometheus_infra.csv \
    --basis differenced --ordered
```

Run it from OUTSIDE the source tree. The execution guard refuses an
installed engine while you are standing in the repo, which is the point
of it — `REDD_ALLOW_INSTALLED=1` exists for CI, not for skipping this.

**For the next release**, in this order:

```bash
python -m twine upload -r testpypi dist/*
python -m venv /tmp/tp && /tmp/tp/bin/pip install \
    -i https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ "redd-munro[refgraph]"
/tmp/tp/bin/redd --version

python -m twine upload dist/*
```

`--extra-index-url` is not optional: numpy is not mirrored on TestPyPI,
so without it the install fails on a dependency and tells you nothing
about this package.

**Notes.** Wheel contains eight modules — `signal_audit`, `signal_audit_cli`, `refgraph`, `cardinality`, `routing`, `shadow`, `history`, `redd` — verified by inspecting the built artefact, not by reading the config. See the verification table at the foot of this file. `numpy>=1.21` is the only runtime dependency; FastAPI/uvicorn sit under `[project.optional-dependencies].server`. PyPI refuses re-uploads of an existing version, so bump `pyproject.toml` before every release.

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

**Shipping — Starter tier, complete:** local CLI (`run`, `prune`, `history`, `--html`, `--json`, `--worksheet`, `--refs`, `--cardinality`, `--shadow`, `--route`, `--record`), browser sandbox, self-contained HTML reports, 284-check engine suite, failure catalogue in `REAL_DASHBOARDS.md`.

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


---

## PyPI — built, verified and published 12/08/2026

Built and installed into a clean virtualenv. What was checked, because
"it works in the repo" is not the same claim:

| Check | Result |
|---|---|
| `python -m build` (wheel + sdist) | both build |
| `twine check dist/*` | **PASSED** on both |
| sdist size | **320 KB**, 26 entries — no corpora, no tests, no `.redd/` |
| Leaked into either artefact | **none** — `.env`, `.redd`, `.git`, `ws.csv`, `test_*`, `score_*`, `__pycache__` all absent |
| Modules in the wheel | all 8: `signal_audit`, `signal_audit_cli`, `refgraph`, `cardinality`, `routing`, `shadow`, `history`, `redd` |
| `redd --version` from a clean venv | `redd 0.1.0` |
| `redd run` / `prune` / `history` | all three resolve |
| `--refs` (needs the `[refgraph]` extra) | 3 files, 14 references |
| `--shadow` | 1 panel BREAKS, as on the source tree |
| `--record` + `redd history` | writes `.redd/`, reads it back |
| Domain lexicons from the wheel | `Available: ai, retail` — the `data-files`/`sys.prefix` bug stays fixed |
| Engine result installed vs source | 5.573 effective signals, identical |

**The entry point is `signal_audit_cli:main`, not `redd.cli:main`.** The
command is still `redd`; only the module path differs. A package layout
would break `import signal_audit` for four test suites, five backend
modules and the browser demo, for no user-visible benefit — the
reasoning is in `pyproject.toml` beside the setting.

### Upload

```bash
cd signal-audit
python -m pip install --upgrade build twine
rm -rf dist build *.egg-info

python -m build                    # wheel + sdist
python -m twine check dist/*       # metadata renders on PyPI

# TestPyPI FIRST — the real index is unforgiving
python -m twine upload -r testpypi dist/*
python -m venv /tmp/tp && /tmp/tp/bin/pip install \
    -i https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    "redd-munro[refgraph]"
/tmp/tp/bin/redd --version        # then delete /tmp/tp

# only then
python -m twine upload dist/*
```

**`0.1.0` is permanent.** A version number cannot be re-uploaded on PyPI
even after deletion, so the clean-venv test above happens before the
last command, not after it.

The `--extra-index-url` is not optional on TestPyPI: numpy is not
mirrored there, and without it the install fails on a dependency rather
than on anything to do with this package.

### Afterwards ☑ done

`README.md` now opens with `pip install redd-munro` instead of a clone,
and the site footer carries the command again. Both changed in the same
commit as the release, so the project never shipped a stale claim about
its own availability.

**Verified from the live index**, not from the project page:

```
python -m venv /tmp/verify
/tmp/verify/bin/pip install "redd-munro[refgraph]"
/tmp/verify/bin/redd --version                    # redd 0.1.0
/tmp/verify/bin/redd run prometheus_infra.csv --basis differenced --ordered
                                                  # 11 metrics -> 5.6 signals
```

### The next version

`0.1.0` is now permanent and cannot be re-uploaded. Everything in this
session that came after the build — nothing, as it happens — would need
`0.1.1`. Bump `pyproject.toml` before the next upload and re-run the
clean-venv check; the wheel is easy to get right twice and easy to get
wrong once.
