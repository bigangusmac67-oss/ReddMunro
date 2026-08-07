"""
build_demo.py — assemble the static browser demo, and prove it is in sync.

The demo needs a COPY of the engine beside index.html, because the page
fetches it over HTTP and writes it into Pyodide's virtual filesystem. A
copy is a drift hazard: the day it falls behind, the demo silently runs
an old engine and every number on the landing page is wrong in a way
nobody notices.

So this script does not just copy. It verifies afterwards that the
copied engine produces byte-identical results to the source engine on a
known fixture, and refuses to report success otherwise.

    python build_demo.py            build and verify
    python build_demo.py --check    verify only, exit 1 if stale (for CI)
"""

import argparse
import filecmp
import json
import hashlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(HERE, "demo")
DATA = os.path.join(HERE, "data")


def source_path(name):
    """Locate a source asset.

    Corpora live in `data/`; code lives at the repo root. Both are
    checked so the build does not depend on which of the two a given
    asset happens to be, and so the move of the corpora into `data/`
    could not silently break the page.
    """
    for base in (HERE, DATA):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return os.path.join(DATA, name)      # report the intended path


ASSETS = [("signal_audit.py", "the engine, unmodified"),
          ("demo_dashboard.csv", "sample file for the 'try an example' link"),
          # Real telemetry from a public Prometheus instance. The page runs
          # this automatically on load so a first-time visitor sees a real
          # finding on real infrastructure data before being asked to export
          # anything of their own. Exporting a dashboard to CSV is twenty
          # minutes of tedium, and asking for it before showing a result is
          # where the visitor is lost.
          ("prometheus_infra.csv", "auto-run sample: real infra telemetry")]
RENAME = {"demo_dashboard.csv": "sample_dashboard.csv"}

# Domain lexicons are a THIRD asset class, added when the browser gained a
# lens switcher. They are presentation data fetched by JavaScript and are
# never handed to Pyodide, so they cannot be covered by the payload
# equivalence check below — that check compares engine output, and a
# lexicon by design changes no engine output. They get their own guard:
# byte-comparison against source, plus a generated index that must list
# exactly what is present. A lexicon that silently failed to copy would
# leave the dropdown empty with no error, which is the quiet kind of
# broken this build script exists to prevent.
DOMAIN_SRC = os.path.join(HERE, "cli", "domains")
DOMAIN_DST = os.path.join(DEMO, "domains")


def _clean_pycache():
    """Remove demo/__pycache__ if we can, and shrug if we cannot.

    The equivalence check imports the copied engine, which writes
    bytecode. It is suppressed with PYTHONDONTWRITEBYTECODE below, so
    this only clears leftovers from earlier runs. Deletion is
    best-effort on purpose: this directory is frequently on a
    cloud-synced volume (OneDrive, Dropbox) where unlink can fail with
    EPERM even though the build is otherwise fine. Failing a build over
    a stale cache directory would be a worse outcome than leaving it.
    """
    cache = os.path.join(DEMO, "__pycache__")
    if not os.path.isdir(cache):
        return
    try:
        shutil.rmtree(cache)
    except OSError as exc:
        print(f"  [note] could not remove demo/__pycache__ ({exc.strerror});"
              f" harmless — exclude it at deploy time")


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def domain_files():
    if not os.path.isdir(DOMAIN_SRC):
        return []
    return sorted(f for f in os.listdir(DOMAIN_SRC) if f.endswith(".json"))


def check_domains():
    """Byte-compare lexicons, and verify the generated index matches."""
    stale = []
    names = domain_files()
    for f in names:
        src = os.path.join(DOMAIN_SRC, f)
        dst = os.path.join(DOMAIN_DST, f)
        if not os.path.exists(dst):
            stale.append(f"domains/{f} missing")
        elif not filecmp.cmp(src, dst, shallow=False):
            stale.append(f"domains/{f} differs from source "
                         f"({sha(src)} vs {sha(dst)})")
    idx = os.path.join(DOMAIN_DST, "index.json")
    expect = sorted(f[:-5] for f in names)
    if not os.path.exists(idx):
        if names:
            stale.append("domains/index.json missing — the lens dropdown "
                         "would be silently empty")
    else:
        with open(idx, encoding="utf-8") as fh:
            got = json.load(fh).get("domains", [])
        if sorted(got) != expect:
            stale.append(f"domains/index.json lists {sorted(got)} but "
                         f"{expect} are present")
    # a lexicon copied into demo/ that is NOT in source is also drift
    if os.path.isdir(DOMAIN_DST):
        extra = sorted(set(f for f in os.listdir(DOMAIN_DST)
                           if f.endswith(".json") and f != "index.json")
                       - set(names))
        for f in extra:
            stale.append(f"domains/{f} is not in cli/domains — orphaned copy")
    return stale


def check_only():
    stale = []
    for src_name, _ in ASSETS:
        src = source_path(src_name)
        dst = os.path.join(DEMO, RENAME.get(src_name, src_name))
        if not os.path.exists(dst):
            stale.append(f"{dst} missing")
        elif not filecmp.cmp(src, dst, shallow=False):
            stale.append(f"{os.path.basename(dst)} differs from source "
                         f"({sha(src)} vs {sha(dst)})")
    stale.extend(check_domains())
    return stale


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="verify only; exit 1 if the demo is stale")
    a = ap.parse_args()

    print("=" * 66)
    print("DEMO BUILD" + ("  (check only)" if a.check else ""))
    print("=" * 66)

    if a.check:
        stale = check_only()
        if stale:
            print("\n  STALE — the demo would run an old engine:")
            for s in stale:
                print(f"    · {s}")
            print("\n  Fix: python build_demo.py")
            return 1
        print("\n  demo assets match source")
        return 0

    os.makedirs(DEMO, exist_ok=True)

    _clean_pycache()
    print()
    for src_name, why in ASSETS:
        src = source_path(src_name)
        if not os.path.exists(src):
            print(f"  [skip] {src_name} not found — {why}")
            continue
        dst = os.path.join(DEMO, RENAME.get(src_name, src_name))
        shutil.copy2(src, dst)
        print(f"  copied {src_name:<22} -> demo/{os.path.basename(dst)}"
              f"   sha {sha(dst)}")

    # ---- domain lexicons -------------------------------------------
    names = domain_files()
    if names:
        os.makedirs(DOMAIN_DST, exist_ok=True)
        for f in names:
            shutil.copy2(os.path.join(DOMAIN_SRC, f),
                         os.path.join(DOMAIN_DST, f))
            print(f"  copied cli/domains/{f:<14} -> demo/domains/{f}"
                  f"   sha {sha(os.path.join(DOMAIN_DST, f))}")
        # Generated, not hand-maintained: the browser cannot list a
        # directory over HTTP, so it needs a manifest, and a manifest
        # written by hand is a manifest that goes stale.
        with open(os.path.join(DOMAIN_DST, "index.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"domains": sorted(f[:-5] for f in names)}, fh, indent=1)
        print(f"  generated demo/domains/index.json  "
              f"({', '.join(sorted(f[:-5] for f in names))})")
        # Best-effort, for the same reason _clean_pycache is: this
        # directory is frequently on a cloud-synced volume (OneDrive,
        # Dropbox) where unlink fails with EPERM. The first version of
        # this loop called os.remove bare and crashed the whole build
        # with a traceback on exactly that. An orphan is not harmless
        # like a stale __pycache__ — it is a lexicon the browser will
        # never load, because index.json is generated from SOURCE and
        # will not list it — but crashing the build is the wrong way to
        # say so. `--check` keeps flagging it until it is gone.
        for f in sorted(set(os.listdir(DOMAIN_DST))
                        - set(names) - {"index.json"}):
            if not f.endswith(".json"):
                continue
            try:
                os.remove(os.path.join(DOMAIN_DST, f))
                print(f"  removed orphaned demo/domains/{f}")
            except OSError as exc:
                print(f"  [note] could not remove orphaned "
                      f"demo/domains/{f} ({exc.strerror}); it is not in "
                      f"index.json so the browser ignores it — delete it "
                      f"by hand to clear --check")
    else:
        print("  [note] no cli/domains/*.json — lens dropdown will be empty")

    # ---- launch assets ---------------------------------------------
    # These are SOURCE files that live in demo/, not copies of anything,
    # so they are checked for presence rather than compared. A missing
    # og.png costs nothing at build time and everything the first time
    # the link is pasted into Slack.
    launch = {"favicon.svg": "tab icon",
              "og.png": "social card image (1200x630)",
              "robots.txt": "crawler policy",
              "sitemap.xml": "sitemap",
              "404.html": "not-found page",
              "_headers": "Cloudflare/Netlify header rules",
              "vercel.json": "Vercel static config"}
    missing = [f for f in launch if not os.path.exists(os.path.join(DEMO, f))]
    if missing:
        for f in sorted(missing):
            print(f"  [warn] demo/{f} missing — {launch[f]}")
    else:
        print(f"  launch assets present ({len(launch)})")

    # Placeholders must not reach production. CI enforces this too, but
    # failing here means it is caught before anyone runs a deploy.
    ph = []
    for f in ("index.html", "robots.txt", "sitemap.xml"):
        fp = os.path.join(DEMO, f)
        if os.path.exists(fp):
            with open(fp, encoding="utf-8") as fh:
                if "REPLACE-WITH-YOUR" in fh.read():
                    ph.append(f)
    if ph:
        print(f"  [note] deployment placeholders still in: {', '.join(ph)}")
        print(f"         fine for local use; see DEPLOY.md before going live")

    index = os.path.join(DEMO, "index.html")
    if not os.path.exists(index):
        print("\n  ERROR: demo/index.html is missing. It is source, not a "
              "build artefact — restore it from version control.")
        return 1

    # ---- verify the copy behaves identically ------------------------
    print("\n  verifying the copied engine matches the source engine")
    sample = os.path.join(DEMO, "sample_dashboard.csv")
    if not os.path.exists(sample):
        print("  [warn] no sample file; skipping equivalence check")
        return 0

    code = (
        "import sys, json; sys.path.insert(0, {d!r});"
        "import signal_audit as SA;"
        "print(json.dumps(SA.report_payload("
        "SA.audit_text(open({s!r}).read(), label='x')), sort_keys=True))"
    )
    outs = {}
    for label, root in (("source", HERE), ("demo", DEMO)):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [sys.executable, "-c", code.format(d=root, s=sample)],
            capture_output=True, text=True, cwd=root, env=env)
        if proc.returncode != 0:
            print(f"  FAILED to run the {label} engine:\n{proc.stderr[:400]}")
            return 1
        outs[label] = proc.stdout.strip()

    if outs["source"] == outs["demo"]:
        print(f"  identical payloads  sha {hashlib.sha256(outs['demo'].encode()).hexdigest()[:12]}")
    else:
        print("  MISMATCH — the demo engine produces different results "
              "from the source engine.")
        return 1

    _clean_pycache()

    print("\n  demo/ is ready to deploy as static files:")
    for f in sorted(os.listdir(DEMO)):
        print(f"    {f}")
    print("\n  Serve it:  cd demo && python3 -m http.server 8000")
    print("  Then open: http://localhost:8000")
    print("  (file:// will not work — fetch() needs an HTTP origin)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
