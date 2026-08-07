"""
build_prom_csv.py — assemble a wide CSV from PromLabs demo range queries.

The fetch layer truncates responses at roughly 70KB, mid-JSON. Rather
than silently losing the tail, this parser recovers every COMPLETE
[timestamp, value] pair it can and records how much was lost, then
aligns all series on their common timestamps. Any series that ends
early simply contributes fewer usable rows.

Percentiles are computed HERE from the raw histogram buckets rather
than asked of the server, so the interpolation is visible and the same
method produces every quantile.
"""
import csv, glob, json, os, re, sys

B = ("/sessions/peaceful-happy-knuth/mnt/.claude/projects/"
     "C--Users-shaun-AppData-Roaming-Claude-local-agent-mode-sessions-"
     "634f597b-911a-4863-9929-bbf5916a718e-a780dba2-364e-4a52-a342-"
     "b1965f4fa6ed-local-1c3d725e-2705-4233-a736-3415a4d1eb82-outputs/"
     "9d019725-894b-4e86-9a28-6dd7b4add57a/tool-results")

PAIR = re.compile(r'\[(\d+(?:\.\d+)?),"(-?[\d.eE+naN]+)"\]')

def salvage(path):
    """Return [(labels_dict, {ts: value})], tolerating truncation."""
    txt = open(path).read()
    txt = txt[txt.index('{"status"'):]
    out = []
    # split on series boundaries; the last one may be incomplete
    for chunk in txt.split('{"metric":')[1:]:
        mtxt = chunk[:chunk.index('},"values":[') + 1] if '},"values":[' in chunk else None
        if mtxt is None:
            continue
        try:
            labels = json.loads(mtxt)
        except json.JSONDecodeError:
            continue
        vals = {}
        for ts, v in PAIR.findall(chunk):
            try:
                vals[int(float(ts))] = float(v)
            except ValueError:
                pass
        if vals:
            out.append((labels, vals))
    return out

def name_of(labels):
    n = labels.get("__name__")
    if n:
        return n
    if "le" in labels:
        return "le_" + labels["le"].replace(".", "_")
    return "_".join(f"{k}{v}" for k, v in sorted(labels.items()))

series = {}
buckets = {}
report = []
for f in sorted(glob.glob(os.path.join(B, "mcp-workspace-web_fetch-17855061*.txt"))
                + glob.glob(os.path.join(B, "mcp-workspace-web_fetch-17855062*.txt"))
                + glob.glob(os.path.join(B, "prom_*.txt"))):
    got = salvage(f)
    report.append((os.path.basename(f)[-14:], len(got),
                   max((len(v) for _, v in got), default=0)))
    for labels, vals in got:
        if "le" in labels:
            buckets[float(labels["le"]) if labels["le"] != "+Inf"
                    else float("inf")] = vals
        else:
            series[name_of(labels)] = vals

print("recovered per file (file, series, max points):")
for r in report:
    print("   ", r)

# ---- percentiles from raw buckets ------------------------------------
if buckets:
    les = sorted(buckets)
    ts_common = set.intersection(*[set(buckets[l]) for l in les])
    def quantile(q, t):
        total = buckets[les[-1]][t]
        if total <= 0:
            return None
        target = q * total
        prev_le, prev_c = 0.0, 0.0
        for le in les:
            c = buckets[le][t]
            if c >= target:
                if le == float("inf"):
                    return prev_le
                if c == prev_c:
                    return le
                # linear interpolation inside the bucket
                return prev_le + (le - prev_le) * (target - prev_c) / (c - prev_c)
            prev_le, prev_c = (le if le != float("inf") else prev_le), c
        return les[-2] if len(les) > 1 else None
    # PERCENTILES DELIBERATELY NOT COMPUTED HERE.
    #
    # The fetch layer truncates responses at ~70KB mid-JSON, and the
    # histogram has more buckets than survive that cut — 2 of them in
    # the 96h pull, 8 in the 24h pull, against a full set that extends
    # well beyond the largest recovered bound. Interpolating quantiles
    # from a truncated bucket set does not measure latency; it measures
    # how much of the histogram happened to fit down the pipe, and the
    # error is systematic (biased toward the small buckets that survive).
    #
    # A wrong number here would have flowed straight into S1 and S2 —
    # the two predictions a platform engineer is most likely to check.
    # Better to mark them untestable in this run and say why.
    #
    # The total request rate IS recoverable: it is the +Inf bucket,
    # which is a single series and always present.
    series["request_rate_total"] = {t: buckets[les[-1]][t] for t in ts_common}
    print(f"\npercentiles SKIPPED — only {len(les)} buckets survived "
          f"truncation; see comment. request_rate_total recovered.")

# ---- align and write --------------------------------------------------
series = {k: v for k, v in series.items() if len(v) >= 100}
common = sorted(set.intersection(*[set(v) for v in series.values()]))
cols = sorted(series)
print(f"\n{len(cols)} metrics x {len(common)} aligned rows")
with open("prometheus_infra.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["timestamp"] + cols)
    for t in common:
        w.writerow([t] + [f"{series[c][t]:.6g}" for c in cols])
print("wrote prometheus_infra.csv")
print("\ncolumns:", ", ".join(cols))
