// Run the real engine inside real Pyodide and require it to agree,
// field for field, with the same engine under CPython.
//
//     python ci/expected.py > expected.json
//     node   ci/wasm_check.mjs expected.json
//
// WHY THIS EXISTS. `_mi_matrix` built its bincount index with
// .astype(np.int64). np.bincount casts to np.intp under the 'safe'
// rule, and np.intp is 64-bit on x86-64 but 32-bit on wasm32. So the
// upcast was free on the machine the tests ran on and an unconditional
// TypeError in every visitor's browser. 134 tests passed throughout.
//
// The lesson generalises past that one cast: a test suite constrains
// the platform it runs on and says nothing whatsoever about any other.
// The browser is not a deployment target for this product, it IS the
// product, so it gets a job of its own.

import { loadPyodide } from "pyodide";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, "..");

const expectedPath = process.argv[2];
if (!expectedPath) {
  console.error("usage: node ci/wasm_check.mjs <expected.json>");
  process.exit(2);
}
const expected = JSON.parse(fs.readFileSync(expectedPath, "utf8"));

const py = await loadPyodide();
await py.loadPackage("numpy");

// Same bytes both platforms read. Passed through the filesystem rather
// than as a string so the CSV parser is exercised identically.
py.FS.writeFile("/signal_audit.py",
  fs.readFileSync(path.join(ROOT, "signal_audit.py")));
py.globals.set("CSV",
  fs.readFileSync(path.join(ROOT, "demo", "prometheus_infra.csv"), "utf8"));

const raw = await py.runPythonAsync(`
import sys, json
sys.path.insert(0, "/")
import numpy as np
import signal_audit as SA

_r = SA.audit_text(CSV, label="prometheus_infra.csv")
_p = SA.report_payload(_r)

json.dumps({
    "platform": {"intp_bits": int(np.iinfo(np.intp).bits),
                 "numpy": np.__version__,
                 "engine": SA.__version__},
    "probe": {
        "effective_signals": _p["summary"]["effective_signals"],
        "metrics": _p["summary"]["metrics"],
        "rows": _p["summary"]["rows"],
        "archive_candidates": sorted(_p["archive_candidates"]),
        "nonlinear": sorted(
            ["%s~%s:%s" % (c["metric_a"], c["metric_b"], c["mi_vs_gaussian"])
             for c in _p["nonlinear_couplings"]]),
        "nonlinear_skipped": _p["nonlinear_skipped"],
        "identities": sorted(
            ["%s~%s:%s" % (i["metric_a"], i["metric_b"], i["r"])
             for i in _p["identity_pairs"]]),
        "grade": _p["assurance"]["grade"],
    },
    # The exports are generated in the engine, so they run here too. A
    # summary that raised only in the browser would reach the user as a
    # dead button rather than as an error anyone would report.
    "exports_ok": bool(SA.summary_markdown(_p)
                       and SA.blast_radius_worksheet(_r)),
})
`);

const got = JSON.parse(raw);
console.log(`wasm32 runtime: numpy ${got.platform.numpy}, engine ` +
            `${got.platform.engine}, np.intp is ` +
            `${got.platform.intp_bits}-bit`);

if (got.platform.intp_bits !== 32) {
  console.error(`\nFAIL: expected a 32-bit index width, got ` +
                `${got.platform.intp_bits}. This job only proves ` +
                `anything if it runs on wasm32; if Pyodide has gone ` +
                `64-bit, this check has quietly stopped testing what ` +
                `it claims to and must be re-derived, not relaxed.`);
  process.exit(1);
}
if (!got.exports_ok) {
  console.error("\nFAIL: summary_markdown or blast_radius_worksheet " +
                "produced nothing under wasm32.");
  process.exit(1);
}

const diffs = [];
for (const k of Object.keys(expected)) {
  const a = JSON.stringify(expected[k]), b = JSON.stringify(got.probe[k]);
  if (a !== b) diffs.push(`  ${k}\n    cpython: ${a}\n    wasm32 : ${b}`);
}

if (diffs.length) {
  console.error(`\nFAIL: ${diffs.length} field(s) differ between ` +
                `CPython and wasm32:\n${diffs.join("\n")}`);
  console.error(`\nThe engine must give the same answer on both. A ` +
                `divergence here means the browser has been reporting ` +
                `numbers the test suite never saw.`);
  process.exit(1);
}

console.log(`OK: ${Object.keys(expected).length} fields identical across ` +
            `CPython and wasm32 — ${got.probe.metrics} metrics, ` +
            `${got.probe.effective_signals} effective signals, ` +
            `${got.probe.nonlinear.length} nonlinear pair(s).`);
