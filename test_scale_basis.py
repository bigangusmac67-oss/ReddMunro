"""
test_scale_basis.py -- does the ratio basis recover truth, or closure?

Predictions C1-C9 were written to SCALE_BASIS_PREREG.md before this file
existed. Read that first; the ordering is the evidence.

The question is narrow and decision-relevant: compositional bias is real
and textbook, but does it move the numbers THIS engine thresholds on? If
it does not, plain ratios ship. If it does, a centred-log-ratio transform
becomes mandatory.
"""
import numpy as np
import signal_audit as SA

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def cross_section(rng, n=2000, d=40, k=4, mode="incomplete", noise=0.05):
    """Synthetic entity cross-section with a KNOWN factor count.

    dollar view carries k+1 quantities (k shapes + size);
    ratio view carries k (size divided out).

    Two construction errors were made here and caught by the verification
    block before anything was scored. Both are recorded because the
    corrected fixture only makes sense against them:

    1. Shares were built as exp(F @ L), which is NONLINEAR in the
       factors. The engine measures LINEAR correlation, so a rank-k
       structure in log space presents as rank > k in the space actually
       being measured -- the ratio view read 8.26 against a planted k=4,
       and that gap was nonlinearity, not closure. Shares are now linear
       in the factors, so the planted rank is the rank the tool sees.

    2. The closure variants were generated with k=0, intending "no shape
       structure". That yields shares that are IDENTICAL across entities,
       not random compositions -- there is no composition to close over.
       Every column became size/d, correlating at exactly +1.0, and the
       measured "closure bias" of +1.0000 was that artefact rather than
       any property of compositional data. Random compositions now have
       their own mode.

    This is the third fixture in this project built wrong on the first
    attempt (the energy fixture twice, now this one). The verification
    block is not ceremony.
    """
    size = np.exp(rng.normal(0, 2.5, n))          # ~5 orders of magnitude
    if mode == "degenerate":
        shares = np.tile(rng.uniform(.5, 1.5, d), (n, 1))
    elif mode == "random_composition":
        # independent shares, normalised: the ONLY structure is the sum
        # constraint, which is exactly what closure is about
        shares = np.abs(rng.normal(1.0, 0.3, (n, d))) + 0.05
        shares = shares / shares.sum(axis=1, keepdims=True)
        return size[:, None] * shares, size
    else:
        # LINEAR in the factors, so planted rank == rank the engine sees
        F = rng.normal(0, 1, (n, k))
        L = rng.normal(0, 1, (k, d))
        shares = 1.0 + 0.3 * (F @ L) / np.sqrt(k)
        shares = np.clip(shares, 0.05, None)
    if mode == "complete":
        shares = shares / shares.sum(axis=1, keepdims=True)
    X = size[:, None] * shares
    if noise:
        X = X * np.exp(rng.normal(0, noise, X.shape))
    return X, size


def pr(M):
    """Participation ratio of the correlation spectrum, engine-identical."""
    C = np.corrcoef(M, rowvar=False)
    C = np.nan_to_num(C, nan=0.0)
    w = np.linalg.eigvalsh(C)
    w = np.clip(w, 0, None)
    return float(w.sum() ** 2 / np.sum(w ** 2))


def mean_offdiag_r(M):
    C = np.corrcoef(M, rowvar=False)
    C = np.nan_to_num(C, nan=0.0)
    iu = np.triu_indices_from(C, k=1)
    return float(C[iu].mean()), float(np.abs(C[iu]).max())


def main():
    rng = np.random.default_rng(20260731)
    d, k = 40, 4

    print("=" * 72)
    print("FIXTURE VERIFICATION — before any prediction is scored")
    print("=" * 72)
    X, size = cross_section(rng, d=d, k=k, mode="incomplete")
    R = X / size[:, None]
    # Rank is a property of the CONSTRUCTION, so it is verified on a
    # noiseless draw. Checking it on the noisy realisation conflates the
    # planted factors with measurement noise -- the first attempt read
    # rank 12 for a planted 4 purely because noise singular values clear
    # a 5% threshold. Noise is then added back for the actual scoring,
    # where it legitimately lifts the participation ratio.
    Xq, sq = cross_section(rng, d=d, k=k, mode="incomplete", noise=0.0)
    Rq = Xq / sq[:, None]
    sv = np.linalg.svd(Rq - Rq.mean(0), compute_uv=False)
    rank_k = int(np.sum(sv > sv[0] * 0.05))
    check("size spans >= 4 orders of magnitude",
          size.max() / size.min() > 1e4, f"{size.max()/size.min():.3g}x")
    check("share matrix has the planted rank in LINEAR space",
          rank_k == k, f"rank {rank_k}, planted {k}")
    Xc, _ = cross_section(rng, d=d, k=k, mode="complete")
    check("complete variant sums to the total on every row",
          np.allclose((Xc / Xc.sum(1, keepdims=True)).sum(1), 1.0))
    mdollar, _ = mean_offdiag_r(X)
    check("dollar view is scale-dominated as intended",
          mdollar > 0.8, f"mean pairwise r = {mdollar:.3f}")

    print()
    print("=" * 72)
    print("SCORING C1-C9")
    print("=" * 72)

    pr_d, pr_r = pr(X), pr(R)
    print(f"\n  incomplete  d={d} k={k}:  dollar PR = {pr_d:.2f}   "
          f"ratio PR = {pr_r:.2f}   (truth: {k+1} / {k})")
    check("C1  dollar-view PR < 3", pr_d < 3, f"{pr_d:.2f}")
    check("C2  ratio-view PR recovers k=4 within [3,7]",
          3 <= pr_r <= 7, f"{pr_r:.2f}")
    check("C3  ratio view >= 2x dollar view",
          pr_r >= 2 * pr_d, f"{pr_r/pr_d:.1f}x")

    print()
    for width in (5, 10, 40):
        Xc, sc = cross_section(rng, d=width, mode="random_composition")
        Rc = Xc / sc[:, None]
        m, mx = mean_offdiag_r(Rc)
        expect = -1.0 / (width - 1)
        print(f"  complete partition d={width:<3} mean pairwise r = {m:+.4f}"
              f"   theory -1/(d-1) = {expect:+.4f}   max|r| = {mx:.3f}")
        if width == 40:
            check("C4  closure bias matches -1/(d-1) at d=40",
                  abs(m - expect) < 0.05, f"{m:+.4f} vs {expect:+.4f}")
        if width == 5:
            check("C5  closure bias is ~-0.25 at d=5",
                  abs(m - expect) < 0.05, f"{m:+.4f} vs {expect:+.4f}")
        if width >= 10:
            check(f"C7  no pair crosses 0.90 at d={width}", mx < 0.90,
                  f"max|r| = {mx:.3f}")

    Xc, sc = cross_section(rng, d=40, mode="random_composition")
    Rc = Xc / sc[:, None]
    pr_c = pr(Rc)
    check("C6  PR not biased by closure (pure composition -> ~d-1)",
          abs(pr_c - (40 - 1)) < 4, f"PR = {pr_c:.2f}, d-1 = 39")

    Xg, sg = cross_section(rng, d=d, k=0, mode="degenerate", noise=0.0)
    Rg = Xg / sg[:, None]
    const = np.sum(np.std(Rg, axis=0) < 1e-12)
    check("C8  degenerate control: ratio columns are constant",
          const == d, f"{const}/{d} columns constant -> loader drops them")

    names = [f"c{i}" for i in range(6)]
    kids = np.abs(rng.normal(100, 20, (800, 4)))
    tot = kids.sum(1)
    sz = np.exp(rng.normal(0, 2.0, 800))
    M = np.column_stack([tot, kids, np.abs(rng.normal(50, 5, 800))]) * sz[:, None]
    names = ["total"] + [f"p{i}" for i in range(4)] + ["other"]
    got_d = SA.subset_sums(M, names)
    got_r = SA.subset_sums(M / sz[:, None], names)
    check("C9  subset sums survive ratio normalisation",
          bool(got_d) and bool(got_r)
          and set(got_d[0][1]) == set(got_r[0][1]),
          f"dollar {len(got_d)}, ratio {len(got_r)}")

    print("\n" + "=" * 72)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
