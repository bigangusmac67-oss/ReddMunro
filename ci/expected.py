"""Audit a fixed corpus under CPython and print the result as JSON.

Half of a cross-platform equality check. `ci/wasm_check.mjs` runs the
same engine on the same bytes inside real Pyodide and compares. Neither
number is hardcoded, so a legitimate change to the engine moves both
together and the check stays meaningful instead of becoming a chore.

What it is actually defending: an int64 index that `np.bincount`
accepted on x86-64 and rejected on wasm32, which broke every audit on
the live site while the whole test suite passed. See MI_SCALING_PREREG.md.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import signal_audit as SA          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, os.pardir, "demo", "prometheus_infra.csv")


def probe():
    """The same shape both platforms must agree on."""
    with open(CORPUS, encoding="utf-8") as f:
        res = SA.audit_text(f.read(), label="prometheus_infra.csv")
    p = SA.report_payload(res)
    return {
        "effective_signals": p["summary"]["effective_signals"],
        "metrics": p["summary"]["metrics"],
        "rows": p["summary"]["rows"],
        "archive_candidates": sorted(p["archive_candidates"]),
        # The MI scan is the code path that broke. Include it explicitly
        # so a platform that silently skipped it cannot pass by matching
        # only the headline.
        "nonlinear": sorted(
            [f"{c['metric_a']}~{c['metric_b']}:{c['mi_vs_gaussian']}"
             for c in p["nonlinear_couplings"]]),
        "nonlinear_skipped": p["nonlinear_skipped"],
        "identities": sorted(
            [f"{i['metric_a']}~{i['metric_b']}:{i['r']}"
             for i in p["identity_pairs"]]),
        "grade": p["assurance"]["grade"],
    }


if __name__ == "__main__":
    print(json.dumps(probe(), sort_keys=True, indent=2))
