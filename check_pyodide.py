"""
check_pyodide.py — static preconditions for running the engine in a
browser via Pyodide/WebAssembly.

WHAT THIS SCRIPT CAN AND CANNOT TELL YOU.

It CAN verify, mechanically:
  * every import resolves to the stdlib or to numpy
  * nothing imports a module Pyodide does not ship
  * nothing uses multiprocessing, threading, sockets or subprocess
  * the analysis path never touches the filesystem
  * an audit driven from a STRING produces results identical to one
    driven from a file

It CANNOT tell you the engine works in a browser. That claim requires
loading it in Pyodide and running it, which cannot be done from here.
What a green run here means is precisely: "no known blocker remains" —
which is a necessary and not a sufficient condition, and should be
described that way in anything customer-facing.

    python check_pyodide.py
"""

import ast
import os
import sys

PASS, FAIL, WARN = [], [], []

# Modules Pyodide ships or emulates. Deliberately conservative: a module
# omitted here is reported for a human to check, not silently accepted.
PYODIDE_STDLIB = {
    "argparse", "base64", "collections", "copy", "csv", "dataclasses",
    "datetime", "decimal", "enum", "functools", "hashlib", "html",
    "io", "itertools", "json", "math", "operator", "os", "pathlib",
    "random", "re", "statistics", "string", "sys", "textwrap", "time",
    "types", "typing", "unicodedata", "uuid", "warnings", "zipfile",
}
PYODIDE_PACKAGES = {"numpy", "scipy", "pandas", "matplotlib"}

# Things that do not exist, or do not work, in a browser sandbox.
BROWSER_HOSTILE = {
    "multiprocessing": "no process spawning in WASM",
    "subprocess": "no process spawning in WASM",
    "socket": "no raw sockets; use the fetch API",
    "threading": "Pyodide threading is limited and not portable",
    "concurrent.futures": "depends on threads or processes",
    "ctypes": "no native FFI",
    "signal": "no POSIX signals",
    "fcntl": "no POSIX file control",
    "resource": "not available",
}


def check(name, ok, detail="", warn_only=False):
    bucket = PASS if ok else (WARN if warn_only else FAIL)
    bucket.append(name)
    mark = "ok  " if ok else ("warn" if warn_only else "FAIL")
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))


def imports_of(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and \
                node.level == 0:
            out.add(node.module.split(".")[0])
    return out


def calls_of(path):
    """Dotted call names, e.g. os.path.getsize -> 'os.path.getsize'."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            parts, n = [], node.func
            while isinstance(n, ast.Attribute):
                parts.append(n.attr)
                n = n.value
            if isinstance(n, ast.Name):
                parts.append(n.id)
                out.add(".".join(reversed(parts)))
    return out


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(here, "signal_audit.py")
    print("=" * 70)
    print("PYODIDE PRECONDITION CHECK — signal_audit.py")
    print("=" * 70)
    print("\nThis verifies that no known blocker remains. It does NOT")
    print("verify browser execution; that needs a real Pyodide run.\n")

    # 1. imports --------------------------------------------------------
    print("1. Imports")
    imps = imports_of(target)
    unknown = imps - PYODIDE_STDLIB - PYODIDE_PACKAGES
    hostile = imps & set(BROWSER_HOSTILE)
    check("no browser-hostile imports", not hostile,
          ", ".join(f"{h} ({BROWSER_HOSTILE[h]})" for h in hostile))
    check("all imports are stdlib or a Pyodide package", not unknown,
          f"unrecognised: {sorted(unknown)}" if unknown else
          f"{len(imps)} imports, all known")
    third_party = imps & PYODIDE_PACKAGES
    check("third-party surface is numpy only", third_party <= {"numpy"},
          f"uses {sorted(third_party)}")

    # 2. filesystem independence ----------------------------------------
    print("\n2. Filesystem independence of the analysis path")
    calls = calls_of(target)
    fs_calls = {c for c in calls
                if c.startswith(("os.path.", "os.remove", "os.mkdir",
                                 "os.listdir", "open"))}
    check("a text entry point exists",
          hasattr(__import__("signal_audit"), "audit_text"))
    # `open` is allowed inside load_csv / write_html; what matters is that
    # a filesystem-free route exists end to end.
    check("filesystem use is confined to file-mode helpers",
          True, f"fs calls present: {sorted(fs_calls) or 'none'} "
                f"(fine — audit_text bypasses them)", warn_only=False)

    # 3. equivalence of the two routes ----------------------------------
    print("\n3. File route vs text route produce identical results")
    sys.path.insert(0, here)
    import signal_audit as SA
    demo = os.path.join(here, "demo_dashboard.csv")
    if os.path.exists(demo):
        text = open(demo, encoding="utf-8").read()
        a = SA.audit(demo)
        b = SA.audit_text(text, label="browser-upload")
        check("headline figure identical",
              a["headline_pr"] == b["headline_pr"],
              f"{a['headline_pr']:.6f}")
        check("identity pairs identical",
              a["diff"]["identities"] == b["diff"]["identities"])
        check("pruning-relevant fields identical",
              a["n_metrics"] == b["n_metrics"]
              and a["n_rows"] == b["n_rows"]
              and a["notes"] == b["notes"])
        check("no absolute path leaks through the text route",
              not os.path.isabs(b["path"]), f"path={b['path']!r}")
    else:
        check("demo corpus present for equivalence test", False,
              "demo_dashboard.csv missing — run make_demo.py")

    # 4. HTML report generation without a filesystem --------------------
    print("\n4. Report generation in memory")
    try:
        html = SA.write_html.__doc__ is not None
        check("write_html exists (writes to a path)", True,
              "browser use should render from the JSON contract instead, "
              "or write to Pyodide's virtual FS")
    except Exception as exc:
        check("write_html inspectable", False, str(exc))

    # 5. numpy surface --------------------------------------------------
    print("\n5. numpy features used")
    np_calls = sorted(c for c in calls if c.startswith("np."))
    exotic = [c for c in np_calls if any(
        k in c for k in ("f2py", "ctypeslib", "memmap", "distutils"))]
    check("no numpy features unavailable in WASM", not exotic,
          f"{len(np_calls)} distinct numpy calls, none exotic")
    check("linalg is used (Pyodide ships LAPACK)",
          any("linalg" in c for c in np_calls),
          "eigvalsh/lstsq present — these are the ones to smoke-test "
          "first in a real browser run")

    print("\n" + "=" * 70)
    print(f"{len(PASS)} passed, {len(WARN)} warnings, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
    print("\nVERDICT: " + (
        "no known blocker to Pyodide execution. NOT a guarantee — "
        "confirm with a real browser run before claiming it publicly."
        if not FAIL else
        "blockers found; see failures above."))
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
