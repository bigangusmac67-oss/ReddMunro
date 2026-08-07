"""score_drift.py — score DRIFT_PREREG.md, controls first.

Run order is fixed by the registration and enforced here: D3 (the planted
positive) does not execute until D1, D2 and the two extra controls have
passed. Three of the four rejected `derived_aggregates` designs looked
correct on a planted positive and failed only on data with no relation in
it, which is why this is a gate in code rather than a note in a document.

    python score_drift.py
"""

import numpy as np

import signal_audit as SA

RESULTS = []


def record(tag, ok, detail):
    RESULTS.append((tag, ok, detail))
    print(f"  [{'HIT ' if ok else 'MISS'}] {tag}  — {detail}")
    return ok


def stationary(n, d, k, rng, loading=None, noise=0.35):
    """d metrics from k latent factors, loadings FIXED for the whole run.
    Correlation is strong and constant: the thing that must not fire."""
    L = rng.standard_normal((k, d)) if loading is None else loading
    F = rng.standard_normal((n, k))
    return F @ L + noise * rng.standard_normal((n, d)), L


def mean_abs_r(X):
    C = np.corrcoef(X, rowvar=False)
    return float(np.mean(np.abs(C[np.triu_indices(X.shape[1], 1)])))


def names_for(d):
    return [f"m{j:02d}" for j in range(d)]


# ---------------------------------------------------------------- D1
print("\nD1  independent noise -> zero pairs")
rng = np.random.default_rng(11)
X = rng.standard_normal((2000, 20))
r1 = SA.correlation_drift(names_for(20), X, ordered=True)
d1 = record("D1", r1["status"] == "ok" and len(r1["pairs"]) == 0,
            f"{len(r1['pairs'])} flagged of {r1.get('pairs_tested')} tested")

# ---------------------------------------------------------------- D2
print("\nD2  stationary correlated structure -> zero pairs")
X2, _ = stationary(2000, 12, 2, np.random.default_rng(12), noise=0.12)
mar = mean_abs_r(X2)
r2 = SA.correlation_drift(names_for(12), X2, ordered=True)
# The registration specified mean |r| > 0.6. A fixture that misses that
# has not tested the prediction, however green it looks — so the spec is
# asserted, not just printed. The first version came in at 0.419.
d2 = record("D2", r2["status"] == "ok" and len(r2["pairs"]) == 0 and mar > 0.6,
            f"mean |r| = {mar:.3f} (registered: > 0.6), "
            f"{len(r2['pairs'])} flagged")

# ------------------------------------------------- control 3: incident
print("\nC3  stationary + shared incident in the last 3% -> near zero")
X3, _ = stationary(2000, 12, 3, np.random.default_rng(13))
spike = int(0.03 * len(X3))
X3[-spike:] += 9.0 * np.abs(np.random.default_rng(99).standard_normal((spike, 1)))
r3 = SA.correlation_drift(names_for(12), X3, ordered=True)
r3n = SA.correlation_drift(names_for(12), X3, ordered=True, naive=True)
tested = r3["pairs_tested"]
frac_shipped = len(r3["pairs"]) / tested
frac_naive = len(r3n["pairs"]) / tested
c3 = record("C3", frac_shipped < 0.05,
            f"shipped {frac_shipped:.1%} vs naive {frac_naive:.1%} of {tested} pairs")

# ------------------------------------------- control 4: variance shift
print("\nC4  variance x10 on one metric, relationships unchanged -> zero")
X4, _ = stationary(2000, 10, 3, np.random.default_rng(14))
X4[1000:, 0] *= 10.0        # r is scale-invariant; this must not fire
r4 = SA.correlation_drift(names_for(10), X4, ordered=True)
c4 = record("C4", len(r4["pairs"]) == 0, f"{len(r4['pairs'])} flagged")

if not all([d1, d2, c3, c4]):
    print("\n  CONTROLS FAILED — the positive case is not run. "
          "A detector that fires on data with no drift in it cannot be "
          "credited for finding drift that was planted.")
    raise SystemExit(1)

print("\n  controls passed; proceeding to the positive case")

# ---------------------------------------------------------------- D3
print("\nD3  planted drift is detected AND ranked first")


def planted(n, d, rng, r_target=0.8):
    X, L = stationary(n, d, 3, rng)
    half = n // 2
    a = rng.standard_normal(n)
    b = rng.standard_normal(n)
    b[half:] = r_target * a[half:] + np.sqrt(1 - r_target ** 2) * b[half:]
    X[:, 0], X[:, 1] = a, b          # independent, then coupled
    return X


X5 = planted(2000, 12, np.random.default_rng(15))
r5 = SA.correlation_drift(names_for(12), X5, ordered=True)
top = (r5["pairs"][0]["metric_a"], r5["pairs"][0]["metric_b"]) if r5["pairs"] else None
d3 = record("D3", top == ("m00", "m01"),
            f"top pair {top}, {len(r5['pairs'])} flagged, "
            f"r {r5['pairs'][0]['r_first']:+.3f} -> {r5['pairs'][0]['r_second']:+.3f}"
            if r5["pairs"] else "nothing flagged")

# ---------------------------------------------------------------- D4
print("\nD4  naive >40% of pairs, shipped <5%  (the load-bearing one)")
d4 = record("D4", frac_naive > 0.40 and frac_shipped < 0.05,
            f"naive {frac_naive:.1%}, shipped {frac_shipped:.1%}")

# ---------------------------------------------------------------- D5
print("\nD5  false-positive rate <= 0.05 over 200 draws")
fp = 0
tot = 0
for s in range(200):
    g = np.random.default_rng(1000 + s)
    rr = SA.correlation_drift(names_for(20), g.standard_normal((2000, 20)),
                              ordered=True)
    fp += len(rr["pairs"])
    tot += rr["pairs_tested"]
rate = fp / tot
d5 = record("D5", rate <= 0.05,
            f"{fp} false positives in {tot} tests = {rate:.5f}")

# ---------------------------------------------------------------- D6
print("\nD6  small files refuse rather than name the wrong pair")
d6_ok = True
detail = []
for n in (2000, 500, 120):
    rr = SA.correlation_drift(names_for(12), planted(n, 12, np.random.default_rng(16)),
                              ordered=True)
    if rr["status"] != "ok":
        detail.append(f"n={n}: {rr['status']}")
        continue
    got = [(p["metric_a"], p["metric_b"]) for p in rr["pairs"]]
    wrong = [g for g in got if g != ("m00", "m01")]
    detail.append(f"n={n}: {len(got)} flagged, {len(wrong)} wrong")
    if wrong:
        d6_ok = False
d6 = record("D6", d6_ok, "; ".join(detail))

# ---------------------------------------------------------------- D7
print("\nD7  entity-indexed data gets no drift result")
ent = SA.correlation_drift(names_for(8), np.random.default_rng(17)
                           .standard_normal((1915, 8)))     # ordered defaults False
d7 = record("D7", ent["status"] == "not_applicable" and not ent["pairs"],
            f"status={ent['status']}")

print("\n" + "=" * 66)
hits = sum(1 for _, ok, _ in RESULTS if ok)
print(f"{hits} hit, {len(RESULTS) - hits} missed, of {len(RESULTS)} scored here")
print("D8 (no existing figure moves) and D9 (real corpus) run separately.")
print("=" * 66)
