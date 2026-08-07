"""
triadic_validation.py — is three-way structure measurable on real
dashboards, and does it find anything the pairwise audit missed?

The pairwise auditor is blind to SYNERGY: information carried jointly
by three metrics that is present in none of the three pairs. The
estimator for it already exists in the research engine
(`triadic.py`, interaction information over equal-occupancy bins).
This script asks whether it is usable at the sample sizes real
dashboards actually have — which is the question that decides whether
the extension ships.

THE SCALING PROBLEM, stated plainly. A three-way table has bins^3
cells. Holding ~5 samples per cell means bins ~ (n/5)^(1/3), so:

    n =   500 rows  ->  4 bins  ->   64 cells
    n = 1,000 rows  ->  5 bins  ->  125 cells
    n = 8,000 rows  -> 10 bins  -> 1000 cells

Resolution grows as the CUBE ROOT of history. Doubling your data buys
26% more bins. That is the central fact and it is why this needed
testing on real corpora rather than assuming.

Four questions:
  Q1  Is the estimator's bias floor low enough that real structure
      clears it at these n?
  Q2  Does bin count change the verdict? (unswept-parameter discipline)
  Q3  Does any synergistic triple involve pairs the pairwise audit
      called unrelated? That is the only thing three-way analysis can
      offer that two-way cannot.
  Q4  What does it cost? C(N,3) grows fast enough to matter for tiering.

    python triadic_validation.py
"""

import itertools
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "Constraint Framework Engine"))

import signal_audit as SA

try:
    import triadic as TRI
except ImportError:
    raise SystemExit(
        "triadic.py not importable. It lives in the research engine "
        "folder ('Constraint Framework Engine'); this script adds that "
        "to sys.path relative to itself.")

CORPORA = [
    ("ACT air quality (hourly)", "act_air_quality.csv", ["datetime"]),
    ("NYC COVID dashboard (daily)", "nyc_covid_dashboard.csv", []),
    ("MTA subway OTP (monthly)", "mta_subway_otp.csv", ["month"]),
    ("Synthetic demo dashboard", "demo_dashboard.csv", []),
]


def load(path, ignore):
    names, M, _ = SA.load_csv(path, ignore=ignore)
    return names, M


def null_threshold(M, bins, reps=3, q=0.95, seed=0):
    """Detection threshold from the null distribution of |II|.

    NOT `shuffle_baseline()`. That returns the MEAN |II| over shuffled
    data, which is a scale reference and not a threshold: under the null
    roughly half of all triples exceed their own mean, so using it as a
    cutoff yields a ~50% false-positive rate. Measured directly — on a
    synthetic dashboard, column-shuffled noise produced 319 "synergistic"
    triples of 680 against the real data's 23.

    Returns the q-th quantile of |II| under column shuffling, together
    with the number of false positives to EXPECT in the negative tail
    from multiple comparisons alone: with T triples and a two-tailed
    quantile q, that is T * (1-q) / 2.
    """
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(reps):
        S = np.column_stack([rng.permutation(M[:, j])
                             for j in range(M.shape[1])])
        Tn = TRI.Triadic(S, bins=bins)
        vals += [abs(v) for v, *_ in Tn.all_triples()]
    return float(np.quantile(vals, q))


def pairwise_weak(M, names, thresh=0.3):
    """Pairs the pairwise audit would call unrelated."""
    C = SA._safe_corr(M)
    return {frozenset((i, j))
            for i, j in itertools.combinations(range(len(names)), 2)
            if abs(C[i, j]) < thresh}


def analyse(label, names, M, max_metrics=20):
    d0 = len(names)
    if d0 > max_metrics:
        # C(53,3) = 23,426 triples; cap for runtime and report the cap
        keep = list(range(max_metrics))
        names = [names[i] for i in keep]
        M = M[:, keep]
    n, d = len(M), len(names)
    triples = d * (d - 1) * (d - 2) // 6

    print(f"\n{'=' * 72}")
    print(f"{label}")
    print(f"{'=' * 72}")
    print(f"  {n} rows x {d} metrics"
          + (f"  (capped from {d0}; C({d0},3) = "
             f"{d0*(d0-1)*(d0-2)//6:,} triples)" if d0 > d else "")
          + f"   C({d},3) = {triples:,} triples")

    # Q1 — bias floor vs observed structure -----------------------------
    t0 = time.time()
    T = TRI.Triadic(M)
    floor = T.shuffle_baseline()
    trips = T.all_triples()
    elapsed = time.time() - t0

    ii = np.array([v for v, *_ in trips])
    syn = int((ii < -floor).sum())
    red = int((ii > floor).sum())
    below = len(trips) - syn - red

    print(f"\n  Q1  estimator resolution")
    print(f"      bins {T.bins} -> {T.bins**3:,} cells, "
          f"{n / T.bins**3:.1f} samples per cell")
    print(f"      bias floor |II| = {floor:.4f}")
    print(f"      |II| observed:  median {np.median(np.abs(ii)):.4f}   "
          f"max {np.abs(ii).max():.4f}")
    print(f"      synergistic above floor: {syn:,}/{len(trips):,} "
          f"({100*syn/max(1,len(trips)):.0f}%)")
    print(f"      redundant   above floor: {red:,}/{len(trips):,} "
          f"({100*red/max(1,len(trips)):.0f}%)")
    print(f"      below floor (not evidence): {below:,} "
          f"({100*below/max(1,len(trips)):.0f}%)")
    usable = np.abs(ii).max() > 2 * floor
    print(f"      -> {'USABLE' if usable else 'NOT USABLE'}: strongest "
          f"signal is {np.abs(ii).max()/max(floor,1e-9):.1f}x the floor")

    # Q2 — does bin count change the verdict? ---------------------------
    print(f"\n  Q2  sensitivity to bin count (unswept-parameter check)")
    print(f"      {'bins':>5}{'samples/cell':>14}{'floor':>10}"
          f"{'synergistic':>13}{'top |II|':>10}")
    stable = []
    for b in (3, 4, 5, 6, 8):
        if n / b**3 < 1.0:
            print(f"      {b:>5}{n/b**3:>14.2f}{'':>10}"
                  f"{'skipped — under 1 sample per cell':>36}")
            continue
        Tb = TRI.Triadic(M, bins=b)
        fb = Tb.shuffle_baseline()
        tb = Tb.all_triples()
        iib = np.array([v for v, *_ in tb])
        sb = int((iib < -fb).sum())
        stable.append(sb / max(1, len(tb)))
        print(f"      {b:>5}{n/b**3:>14.1f}{fb:>10.4f}"
              f"{sb:>10,}/{len(tb):<6}{np.abs(iib).max():>10.3f}")
    if len(stable) >= 2:
        spread = max(stable) - min(stable)
        verdict = ("STABLE" if spread < 0.15 else
                   "UNSTABLE — the verdict depends on an unswept parameter")
        print(f"      synergistic fraction varies {min(stable):.0%}"
              f"-{max(stable):.0%} across bin counts ({verdict})")

    # Q3 — does it find anything pairwise missed? -----------------------
    thr = null_threshold(M, T.bins)
    expected_fp = len(trips) * 0.05 / 2.0
    strict = int((ii < -thr).sum())
    print(f"\n  Q3  synergy against a PROPER threshold")
    print(f"      null 95th-pct |II| = {thr:.4f} "
          f"({thr/max(floor,1e-9):.1f}x the mean floor)")
    print(f"      synergistic below -threshold: {strict}")
    print(f"      expected from chance alone:   {expected_fp:.1f} "
          f"({len(trips):,} triples x 2.5% negative tail)")
    ratio = strict / max(expected_fp, 1e-9)
    print(f"      -> {ratio:.1f}x chance "
          + ("— evidence of real synergy" if ratio >= 2
             else "— NOT distinguishable from noise"))

    print(f"\n      triples synergistic despite all three pairs weak "
          f"(|r| < 0.3)")
    weak = pairwise_weak(M, names)
    hidden = [(v, i, j, k) for v, i, j, k in trips
              if v < -thr
              and frozenset((i, j)) in weak
              and frozenset((i, k)) in weak
              and frozenset((j, k)) in weak]
    if hidden:
        print(f"      {len(hidden)} found — this is structure the "
              f"pairwise audit cannot see")
        for v, i, j, k in hidden[:5]:
            print(f"        II={v:+.4f}  {names[i][:18]:<20}"
                  f"{names[j][:18]:<20}{names[k][:18]}")
    else:
        print(f"      none — every synergistic triple already contains a "
              f"correlated pair,\n      so three-way analysis adds nothing "
              f"here beyond what pairwise found")

    # Q4 — cost ---------------------------------------------------------
    print(f"\n  Q4  cost: {elapsed:.2f}s for {len(trips):,} triples "
          f"({1000*elapsed/max(1,len(trips)):.2f} ms each)")
    for m in (25, 50, 150):
        t = m * (m - 1) * (m - 2) // 6
        print(f"      extrapolated N={m:<4} -> {t:>9,} triples "
              f"~ {t * elapsed / max(1, len(trips)):>7.1f}s")

    return dict(label=label, n=n, d=d, bins=T.bins, floor=floor,
                syn=syn, hidden=len(hidden), usable=usable,
                seconds=elapsed, triples=len(trips))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    results = []
    print("TRIADIC VALIDATION — three-way structure on real dashboards")
    for label, fn, ig in CORPORA:
        path = os.path.join(here, fn)
        if not os.path.exists(path):
            print(f"\n[skip] {label}: {fn} not present")
            continue
        try:
            names, M = load(path, ig)
            results.append(analyse(label, names, M))
        except Exception as exc:
            print(f"\n[fail] {label}: {type(exc).__name__}: {exc}")

    print(f"\n{'=' * 72}")
    print("SUMMARY")
    print(f"{'=' * 72}")
    print(f"  {'corpus':<32}{'rows':>7}{'bins':>6}{'s/cell':>8}"
          f"{'usable':>8}{'hidden':>8}")
    for r in results:
        print(f"  {r['label'][:30]:<32}{r['n']:>7}{r['bins']:>6}"
              f"{r['n']/r['bins']**3:>8.1f}"
              f"{'yes' if r['usable'] else 'NO':>8}{r['hidden']:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
