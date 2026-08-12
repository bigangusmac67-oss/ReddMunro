"""score_binning.py — score BINNING_PREREG.md.

Controls first: continuous data must be bit-identical, and the noise
controls must stay silent, before anything about tied data is measured.

    python score_binning.py
"""

import numpy as np

import signal_audit as SA

RESULTS = []


def score(tag, ok, detail):
    RESULTS.append((tag, ok, detail))
    print(f"  [{'HIT ' if ok else 'MISS'}] {tag}  — {detail}")
    return ok


def old_discretise(M, bins):
    """The previous implementation, kept here to compare against."""
    out = np.empty(M.shape, dtype=np.int16)
    edges = np.linspace(0, 1, bins + 1)[1:-1]
    for j in range(M.shape[1]):
        q = np.quantile(M[:, j], edges)
        out[:, j] = np.digitize(M[:, j], q)
    return out


def fixtures(n=2000, seed=0):
    r = np.random.default_rng(seed)
    return {
        "gaussian": r.standard_normal((n, 1)),
        "lognormal": r.lognormal(0, 1, (n, 1)),
        "uniform": r.uniform(0, 1, (n, 1)),
        "poisson4": r.poisson(4, (n, 1)).astype(float),
        "status_codes": r.choice([200, 301, 404, 500], size=(n, 1),
                                 p=[.85, .05, .07, .03]).astype(float),
        "pod_count": r.integers(1, 6, (n, 1)).astype(float),
    }


def floor_of(x, bins=12, reps=6, seed=0):
    """Bias floor: mean pairwise MI over independently shuffled columns."""
    r = np.random.default_rng(seed)
    X = np.column_stack([x[:, 0], x[:, 0]])
    vals = []
    for _ in range(reps):
        S = np.column_stack([r.permutation(X[:, 0]), r.permutation(X[:, 0])])
        D = SA._discretise(S, bins)
        vals.append(SA._mi_matrix(D, 2)[(0, 1)])
    return float(np.mean(vals))


def main():
    print("\nControls (nothing about tied data is measured until these pass)")
    fx = fixtures()

    # --- B2: continuous data must not move -----------------------------
    same = []
    for name in ("gaussian", "lognormal", "uniform"):
        a = old_discretise(fx[name], 12)
        b = SA._discretise(fx[name], 12)
        same.append((name, np.array_equal(a, b)))
    b2 = score("B2  continuous data is bit-identical to the old binning",
               all(ok for _n, ok in same),
               ", ".join(f"{n}:{'same' if ok else 'MOVED'}" for n, ok in same))

    # --- noise controls -------------------------------------------------
    r = np.random.default_rng(5)
    quiet = []
    for name, X in (("uniform", r.uniform(0, 1, (2000, 12))),
                    ("lognormal", r.lognormal(0, 1, (2000, 12))),
                    ("poisson", r.poisson(4, (2000, 12)).astype(float))):
        names = [f"m{i}" for i in range(12)]
        quiet.append((name, len(SA.nonlinear_pairs(X, names))))
    c_noise = score("C   independent noise still yields zero couplings",
                    all(k == 0 for _n, k in quiet),
                    ", ".join(f"{n}:{k}" for n, k in quiet))

    # The fix shrinks the bin count on tied columns, which shrinks the
    # bias floor. A smaller floor is easier to clear, so status-code
    # columns are the place a false positive would appear. Checked
    # explicitly, because "no couplings on Poisson" does not cover a
    # column with four distinct values and 85% of the mass on one.
    codes = np.column_stack([
        r.choice([200, 301, 404, 500], 2000, p=[.85, .05, .07, .03]),
        r.choice([200, 500], 2000, p=[.9, .1]),
        r.integers(1, 6, 2000),
        r.integers(0, 3, 2000),
    ]).astype(float)
    n_codes = len(SA.nonlinear_pairs(codes, ["status", "up", "pods", "retries"]))
    c_codes = score("C   independent STATUS-CODE columns yield no couplings",
                    n_codes == 0,
                    f"{n_codes} — a smaller floor is easier to clear, so "
                    f"this is where a false positive would show")

    x = r.uniform(-3, 3, 2000)
    mono = np.column_stack([x, x ** 3, np.exp(x)])
    c_mono = score("C   monotone transforms stay gated out by |r|",
                   len(SA.nonlinear_pairs(mono, ["x", "x3", "ex"])) == 0,
                   "high MI, high |r| — must not be reported")

    if not (b2 and c_noise and c_mono and c_codes):
        print("\n  CONTROLS FAILED — tied-data predictions not scored.")
        return 1
    print("\n  controls passed\n")

    # --- B1: no empty bins ---------------------------------------------
    rep_new, rep_old = [], []
    for name in ("poisson4", "status_codes", "pod_count"):
        SA._discretise(fx[name], 12, report=rep_new)
        d = old_discretise(fx[name], 12)
        c = np.bincount(d[:, 0], minlength=int(d.max()) + 1)
        rep_old.append((name, int((c == 0).sum())))
    b1 = score("B1  no empty bins on tied data",
               all(r0["empty_bins"] == 0 for r0 in rep_new),
               "; ".join(
                   f"{n}: was {o} empty -> now {r0['empty_bins']} "
                   f"({r0['bins_used']} bins from {r0['distinct_values']} "
                   f"distinct values)"
                   for (n, o), r0 in zip(rep_old, rep_new)))

    # --- B3: does the floor actually move? ------------------------------
    # Scored by comparing OLD and NEW on the SAME fixture. The first
    # version compared this fixture's floor against 0.0290 from the MI
    # cycle — a different fixture, different n, different seed — and
    # called the difference a movement. That was not a measurement.
    def floor_with(disc, x, bins=12, reps=6, seed=0):
        rr = np.random.default_rng(seed)
        vals = []
        for _ in range(reps):
            S = np.column_stack([rr.permutation(x[:, 0]),
                                 rr.permutation(x[:, 0])])
            vals.append(SA._mi_matrix(disc(S, bins), 2)[(0, 1)])
        return float(np.mean(vals))

    moved = {}
    for name in ("poisson4", "status_codes", "pod_count"):
        o = floor_with(old_discretise, fx[name])
        n_ = floor_with(SA._discretise, fx[name])
        moved[name] = (o, n_)
    b3 = score("B3  the bias floor for tied data moves",
               any(abs(o - n_) > 1e-12 for o, n_ in moved.values()),
               "; ".join(f"{k}: {o:.6f} -> {n_:.6f}"
                         for k, (o, n_) in moved.items())
               + "  — empty bins contribute 0*log(0), so collapsing them "
                 "cannot change MI")

    # --- B4: occupancy is still unequal, and reported --------------------
    p = rep_new[0]
    b4 = score("B4  occupancy stays unequal on tied data, and is reported",
               p["occupancy_max"] > p["occupancy_min"] * 1.5 and p["tied"],
               f"poisson4 occupancy {p['occupancy_min']}-{p['occupancy_max']} "
               f"across {p['bins_used']} bins, tied={p['tied']}")

    # --- B5: detection survives ------------------------------------------
    xs = r.uniform(-3, 3, 2000)
    planted = np.column_stack([xs, xs ** 2 + 0.05 * r.standard_normal(2000),
                               r.standard_normal(2000)])
    found = SA.nonlinear_pairs(planted, ["driver", "sq", "noise"])
    b5 = score("B5  the planted y = x^2 pair is still detected",
               any({"driver", "sq"} == {a, b} for _rt, a, b, _r, _mi in found),
               f"{len(found)} pair(s) reported")

    # --- B7: cross-dataset spread ---------------------------------------
    floors = {n: floor_of(fx[n]) for n in
              ("gaussian", "lognormal", "uniform", "poisson4",
               "status_codes", "pod_count")}
    lo, hi = min(floors.values()), max(floors.values())
    spread = (hi - lo) / hi if hi else 0.0
    b7 = score("B7  cross-dataset floor spread < 25% (was 68%)",
               spread < 0.25,
               "; ".join(f"{k}={v:.4f}" for k, v in floors.items())
               + f"  spread {spread:.0%}")

    print("\n" + "=" * 70)
    hits = sum(1 for _t, ok, _d in RESULTS if ok)
    print(f"{hits} hit, {len(RESULTS) - hits} missed, of {len(RESULTS)}")
    print("B6 and B8 are scored against the real corpora — see below.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
