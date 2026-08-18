# START HERE — state of Redd Munro

**Written 14/08/2026.** For whoever picks this up next, including a
future assistant with folder access and no conversation history. Read
this file first; nothing else is required before you can work.

Supersedes the 31/07/2026 "Phase 1 hand-off", which described the
project as pre-publication and is now wrong in almost every particular.

---

## 1 · The one-sentence goal

**Find out whether measuring redundancy in telemetry changes what anyone
actually does about it.**

The tool reads a wide CSV — one column per metric, one row per timestamp
— and reports how much independent information it carries, which columns
restate one another, and which are candidates to archive, with the
evidence for each and the reasons not to.

The tool is built. **The question is not answered**, because nobody has
run it on a dashboard they would be paged for. Everything below is
either in service of answering that, or is a limitation recorded so it
cannot be discovered later by someone else.

## 2 · Authority hierarchy — if two documents disagree

| Rank | Document | Role |
|---|---|---|
| 1 | `*_PREREG.md` | **Authoritative.** Predictions registered before data, scored afterwards, misses included. |
| 2 | `SAFETY_BOUNDARIES.md` | The constraints no feature may violate. Condition 3 — evidence, never clearance — has vetoed real proposals. |
| 3 | `README.md` § Engine boundaries | What the tool cannot do. Thirteen entries. |
| 4 | This file | Orientation and current state. |
| 5 | `demo/index.html` | Public summary. Shorter than the README by design, never contradicting it. |

**A pre-registration outranks a report, the README and the site.** If the
site says something the register does not support, the site is wrong.

## 3 · Where things are, right now

| Surface | State |
|---|---|
| PyPI | **`redd-munro` 0.1.1 live** |
| Repo version | **0.1.2 — built, tested, NOT released** |
| GitHub | `bigangusmac67-oss/ReddMunro`, public, Apache 2.0 |
| Site | `reddmunro.com`, Cloudflare Worker, static assets from `demo/` |
| Tests | **496** — engine 357, backend 108, schemas 17, scale 14 (as of 14/08; re-derive with §10) |
| Cohort | **not started.** Zero teams contacted. |

**0.1.2 must be released before anyone is contacted.** It fixes a crash
on redirected output and an import failure on Python 3.8/3.9 — both are
first-command failures that would end an evaluation before it started.

## 4 · What has been established

**The engine works and has been caught being wrong on purpose.** Six
scored pre-registrations, four with published misses. The most useful
results were misses: `BINNING_PREREG` B3 disproved the previous cycle's
own diagnosis, and `MI_SCALING` cost a change that was abandoned as its
stopping rule required.

**It runs in a browser, verified.** CPython and Pyodide reproduce each
other field for field in CI. Added after an `int64` index that was free
on x86-64 raised `TypeError` for every visitor for five days while 134
tests passed — none on wasm32.

**Zero egress is asserted, not claimed.** Sockets are blocked at runtime
and five CLI paths run through the block, with a positive control proving
the block is armed.

**The full workflow exists**: audit → worksheet → reference scan →
shadow board → cost → routing → history. The reference scan produces the
CONFLICT flag, which is the best output the tool has: a metric the
arithmetic says ARCHIVE that a paging alert depends on.

**The engine is substrate-independent; the product is not.** Five of six
validation corpora are not telemetry — COVID counts, air quality, bank
call reports, transit punctuality, language models. The strongest
methodological result (scale confound, 2.5 → 14.8 effective signals) came
from bank balance sheets. SRE-specific work lives entirely in the shell:
`refgraph`, the CONFLICT flag, the worksheet's attestation columns,
`routing`, `shadow`. **The shell is the moat, not the engine.**

## 5 · What is NOT established

- **H1 — the central premise — is unscored.** Whether metrics that look
  identical come apart during incidents has never been tested, here or
  anywhere. It needs a window someone labelled `incident`.
- **CF3, the controlled-flat hypothesis, is unscored** and needs the same
  window.
- **A1–A3 on adoption are unscored.** No team has been contacted.
- **`CONTINUOUS_DIFF_SPEC.md` is unscored.**
- **Nobody has used this.** Every number anywhere is from public data.

## 6 · Known defects, disclosed not fixed

The sharpest is the **controlled-flat archive hazard**: a metric held
near-constant by a working control loop loses its unique variance and is
offered for archiving — and it is the channel that moves first when the
loop fails. Measured 0.90 → 0.0000 as the loop tightens.
`CONTROLLED_FLAT_PREREG.md` registers a detector; **none is built**, and
that is deliberate — a fix that has not been scored is the false
confidence this project exists to refuse.

Twelve further limits are in `README.md` § Engine boundaries.

## 7 · The recurring failure class

Every significant bug found in this project has one shape: **something
succeeds while doing nothing, or reports a number that is not about what
it claims.**

`--worksheet` accepted on `run` and silently ignored · a duplicate header
overwriting a column with no note · 134 tests passing on a platform none
ran on · a negative control that was vacuously true · four documentation
counts drifting from what they counted · a test swallowing two cases via
`SystemExit` · output crashing only when redirected.

**This is the same phenomenon the product detects** — a panel that looks
informative while restating another. When reviewing anything here, ask
first: *could this pass while measuring nothing?*

## 8 · Immediate next steps, in order

1. **Release 0.1.2.** Build, `twine check`, clean-venv install from
   *outside* the source tree, upload, then tag. Watch the new 3.8 CI job
   — if it fails, **raise `requires-python`**, do not delete the job.
2. **Deploy `demo/`** — new boundary card, corrected counts, new
   `og.png`. Re-scrape the social card afterwards.
3. **Two twenty-minute checks nobody has done**: the site on a phone, and
   the CLI against a 200-column CSV (the MI scan is O(N²); silence for
   minutes reads as a hang).
4. **Contact ten teams** using `OUTREACH.md` verbatim. Record every send
   on the day it goes out, including the ones that never reply.
5. **Stop building until a reply arrives.** `ADOPTION_PREREG.md` A1
   predicts the current build queue targets the wrong problem.

## 9 · Standing rules

- **Never add a number to a document without a way to check it.** Test
  section 29 asserts the docs' stated counts against their contents.
- **Declarations, never inference.** `--basis`, `--ordered`, and any
  future channel tagging are declared by the user. The engine does not
  guess delimiters, pivots, row order or units.
- **Lenses cannot reach the mathematics.** Asserted in the suite.
- **The tool never applies anything.** It writes files a human applies.
- **Nothing is uploaded, ever.** No network code, asserted at runtime.
- **Published misses stay published.** Removing a scored miss because it
  is embarrassing would destroy the only thing here that is hard to copy.

## 10 · How to re-derive every number in this file

```bash
cd signal-audit
REDD_REQUIRE_EXTRAS=1 python test_signal_audit.py   # engine
python backend/test_backend.py                     # backend
python backend/test_schemas.py                     # schemas
python test_scale_basis.py                         # scale basis
python build_demo.py --check                       # demo not stale
python score_drift.py ; python score_history.py ; python score_binning.py
grep -c '^- \*\*' README.md                        # boundary entries
ls *_PREREG.md                                     # 8: 6 scored, 2 open
```

`REDD_REQUIRE_EXTRAS=1` turns a missing optional dependency into a
failure rather than a skip. CI sets it. Without it the reference-scan
tests skip and the suite still exits 0 — which is the point of the flag.

## 11 · File map

```
signal-audit/
├── signal_audit.py          engine — numpy only, no presentation
├── signal_audit_cli.py      the `redd` command
├── refgraph.py              PromQL + Grafana reference scan  [SRE-specific]
├── cardinality.py routing.py shadow.py history.py            [SRE-specific]
├── redd.py                  shim so `python -m redd` works
├── *_PREREG.md              8 registrations — the authority
├── SAFETY_BOUNDARIES.md     constraints no feature may violate
├── OUTREACH.md              the cohort note, reply shape, record sheet
├── REAL_DASHBOARDS.md       corpora and the failure catalogue
├── demo/                    the site. index.html is SOURCE; the rest is
│                            generated by build_demo.py — never edit there
├── data/                    corpora
└── backend/                 hosted API — complete, must not take money
```

**The parent programme** (`../Constraint Framework Engine/`) is where the
falsification method came from. It asks a *dynamical* question — is this
system approaching a transition. This asks a *structural* one — how many
distinct things is this table measuring. Concepts transfer only when they
survive being stripped to something checkable on a static table; a
thermodynamic proposal was assessed on 14/08 and reduced to one
forty-line empirical claim with no physics left in it. See
`CONTROLLED_FLAT_PREREG.md` § "Where this came from".
