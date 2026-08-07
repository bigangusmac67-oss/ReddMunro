# Zero-server browser demo

Runs the audit engine entirely in the visitor's browser via Pyodide. The CSV
is read by the page, passed to Python as a string through `audit_text()`, and
never transmitted. There is no upload endpoint in this directory.

## Serving it

Any static host. Three files must sit together:

```
index.html          the page
signal_audit.py     the engine, unmodified — copy it, do not fork it
sample_dashboard.csv  optional, for a "try an example" link
```

```
cd demo && python3 -m http.server 8000
```

`file://` will not work: `fetch('signal_audit.py')` needs an HTTP origin.

## Keeping the engine in sync

`signal_audit.py` here is a **copy**. It must be refreshed whenever the engine
changes, or the demo silently runs an old version:

```
cp ../signal_audit.py .
```

`build_demo.py` does this, and verifies afterwards that the copy produces
byte-identical results to the source engine. The page fetches the source and
writes it to Pyodide's virtual filesystem, so it imports as a normal module —
the demo runs exactly the code that passes CI, not a browser-specific fork.

Note that **this file is itself served**, at `/README.md`, along with
everything else in this directory. Nothing here is private, by design.

## What is verified, and what is not

`check_pyodide.py` verifies the preconditions statically: stdlib-and-numpy
imports only, nothing browser-hostile, and a filesystem-free entry point whose
results are bit-identical to the file route. The payload contract the page
reads is asserted in the engine test suite (section 9).

**Browser execution VERIFIED 31/07/2026.** A real Pyodide run against
`sample_dashboard.csv` reproduced the CLI figures exactly — 17 metrics → 4.3
signals, 74.5% redundant, grade B, 6 checks run / 3 fired — with zero console
errors. Both `np.linalg.eigvalsh` and `np.linalg.lstsq` executed cleanly under
WebAssembly.

The specific browser and version were not recorded, so no individual browser is
listed as supported. Re-run the steps above on any browser you intend to claim.

## First-load cost

Pyodide plus numpy is roughly 10 MB, cached after the first visit. The page
says so while loading rather than showing an unexplained spinner.
