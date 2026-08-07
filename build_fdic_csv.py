"""
build_fdic_csv.py -- assemble an FDIC call-report CROSS-SECTION.

Source: FDIC BankFind Suite REST API, /api/financials, REPDTE=20240331.

Two constraints shaped this, and both are the same constraint that bit the
Prometheus run:

  1. The fetch path caps a response at roughly 64KB and cuts it MID-ROW.
     Every page therefore ends in a partial line. This parser drops any row
     whose field count does not match the header, rather than trusting it.
  2. The request URL has a length cap of roughly 14 field names. The 39
     metrics here were therefore pulled as four separate field groups and
     are joined on CERT.

The join is an INNER join. A bank survives only if it appears in every
group, so the recovered row count is the intersection, not the union.

ROWS ARE BANKS, NOT TIME. This file is deliberately NOT a time series.
Consecutive rows are unrelated institutions, so first differences across
rows are meaningless -- see prediction F0 in REAL_DASHBOARDS.md. Read the
RAW column of the audit, not the differenced headline.
"""

import csv
import glob
import os

SRC = ("/sessions/peaceful-happy-knuth/mnt/.claude/projects/"
       "C--Users-shaun-AppData-Roaming-Claude-local-agent-mode-sessions-"
       "634f597b-911a-4863-9929-bbf5916a718e-a780dba2-364e-4a52-a342-"
       "b1965f4fa6ed-local-1c3d725e-2705-4233-a736-3415a4d1eb82-outputs/"
       "9d019725-894b-4e86-9a28-6dd7b4add57a/tool-results")

OUT = ("/sessions/peaceful-happy-knuth/mnt/THE CONSTRAINT PROJECT/"
       "signal-audit/fdic_callreport_2024q1.csv")

# Only files whose fetch URL mentions the financials endpoint.
WANT = "banks.data.fdic.gov/api/financials"

# The four intended field groups, each identified by a field unique to it.
# Exploratory probe fetches also landed in this directory; without this
# filter they join as extra groups and, because they were pulled at a
# single offset, they cap the inner join at their own row count. That is
# exactly what happened on the first build: a stray 12-field probe held
# the corpus down. Match on signature rather than field count so the
# reason a page is accepted is visible.
SIGNATURES = ("DEPDOM", "LNREAG", "LNMUNI", "NIMY")


def parse(path):
    """Return (fieldnames, {cert: {field: value}}) from one saved page."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    lines = raw.split("\n")

    # The saved file starts with fetch metadata (url, redirect, content-type,
    # blank). The CSV header is the first line beginning with a quote.
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith('"') and "CERT" in ln:
            start = i
            break
    if start is None:
        return None, {}

    header = next(csv.reader([lines[start]]))
    n = len(header)
    out = {}
    dropped = 0
    for ln in lines[start + 1:]:
        if not ln.strip():
            continue
        try:
            row = next(csv.reader([ln]))
        except Exception:
            dropped += 1
            continue
        if len(row) != n:          # truncated tail row -- discard
            dropped += 1
            continue
        rec = dict(zip(header, row))
        cert = rec.get("CERT", "").strip()
        if not cert:
            dropped += 1
            continue
        out[cert] = rec
    return header, out, dropped


def main():
    files = sorted(glob.glob(os.path.join(SRC, "mcp-workspace-web_fetch-*.txt")))
    groups = {}          # frozenset(fields) -> {cert: rec}
    print("scanning saved pages")
    for p in files:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            head = f.readline()
        if WANT not in head:
            continue
        header, recs, dropped = parse(p)
        if not header:
            continue
        sig = [x for x in SIGNATURES if x in header]
        if len(sig) != 1:
            print(f"  {os.path.basename(p)[-17:-4]}  skipped "
                  f"(not one of the four intended groups)")
            continue
        key = sig[0]
        groups.setdefault(key, {}).update(recs)
        print(f"  {os.path.basename(p)[-17:-4]}  {len(recs):>4} rows kept, "
              f"{dropped} dropped (truncated tail)  fields={len(header)}")

    if not groups:
        print("no usable pages found")
        return 1

    # Inner join across field groups on CERT.
    certs = None
    for recs in groups.values():
        s = set(recs)
        certs = s if certs is None else (certs & s)
    certs = sorted(certs, key=lambda c: int(c))
    print(f"\n  {len(groups)} field groups")
    print(f"  {len(certs)} banks present in EVERY group (inner join)")

    # Assemble columns. CERT and ID are identifiers, not metrics.
    skip = {"CERT", "ID"}
    cols = []
    for recs in groups.values():
        allf = set()
        for r in recs.values():
            allf |= set(r)
        for f in sorted(allf):
            if f not in skip and f not in cols:
                cols.append(f)
    cols.sort()

    rows = []
    for c in certs:
        row = {"CERT": c}
        ok = True
        for recs in groups.values():
            r = recs.get(c)
            if r is None:
                ok = False
                break
            for f, v in r.items():
                if f not in skip:
                    row[f] = v
        if ok:
            rows.append(row)

    # Drop any column that is entirely blank, and any row with a blank cell.
    live = [f for f in cols
            if any(str(r.get(f, "")).strip() not in ("", "None") for r in rows)]
    dead = [f for f in cols if f not in live]
    if dead:
        print(f"  dropped {len(dead)} all-blank column(s): {', '.join(dead)}")

    clean = [r for r in rows
             if all(str(r.get(f, "")).strip() not in ("", "None") for f in live)]
    print(f"  {len(rows) - len(clean)} row(s) dropped for blank cells")
    print(f"\n  FINAL: {len(clean)} banks x {len(live)} metrics"
          f"   ({len(clean)/len(live):.1f} rows per metric)")

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["CERT"] + live)
        for r in clean:
            w.writerow([r["CERT"]] + [r[f] for f in live])
    print(f"  wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
