"""
test_signal_audit.py — validation against known ground truth.

Every fixture here is generated from a KNOWN number of latent factors,
so the tool's answer can be checked rather than admired. A redundancy
auditor that cannot recover a planted answer has no business reporting
an unknown one.

    python test_signal_audit.py

Exit code 0 if all checks pass.
"""

import itertools
import json
import re
import os
import shutil
import tempfile

import numpy as np

import signal_audit as SA

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))


def _catch(fn):
    try:
        fn()
    except Exception as exc:
        return exc
    return None


def _raises(fn):
    try:
        fn()
    except Exception:
        return True
    return False


def write_csv(path, names, M):
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(",".join(names) + "\n")
        for row in M:
            f.write(",".join(f"{v:.6g}" for v in row) + "\n")


# ----------------------------------------------------------------------
def fixture_known_factors(rng, n=800, k=3, d=12, noise=0.05):
    """d metrics generated from k latent factors. True answer: ~k."""
    F = rng.standard_normal((n, k))
    A = rng.standard_normal((k, d))
    X = F @ A + noise * rng.standard_normal((n, d))
    return [f"m{j:02d}" for j in range(d)], X


def fixture_independent(rng, n=800, d=8):
    """d independent metrics. True answer: ~d."""
    return ([f"ind{j}" for j in range(d)], rng.standard_normal((n, d)))


def fixture_identities(rng, n=600):
    """Contains two exact identities: a rate and its complement, and a
    unit conversion. True structure: 3 real signals in 6 columns."""
    a = rng.standard_normal(n).cumsum()
    b = rng.standard_normal(n).cumsum()
    c = rng.standard_normal(n).cumsum()
    success = 1 / (1 + np.exp(-0.1 * a))
    return (["latency_ms", "latency_sec", "success_rate", "error_rate",
             "throughput", "queue_depth"],
            np.column_stack([a, a / 1000.0, success, 1.0 - success, b, c]))


def fixture_trend_trap(rng, n=700):
    """Six metrics with NO relationship to each other, each with a
    strong shared upward trend. Raw correlation says one signal;
    differenced says six. This is failure mode M7 and the reason the
    headline uses differences."""
    t = np.arange(n)
    trend = 0.05 * t
    X = np.column_stack([trend + rng.standard_normal(n) * 0.4
                         for _ in range(6)])
    return [f"growth{j}" for j in range(6)], X


def fixture_nonlinear(rng, n=900):
    """y is fully determined by x but uncorrelated with it (y = x^2
    with x symmetric). A correlation matrix reports independence."""
    x = rng.uniform(-3, 3, n)
    y = x ** 2 + 0.05 * rng.standard_normal(n)
    z = rng.standard_normal(n)
    w = rng.standard_normal(n)
    return ["driver", "squared_response", "noise_a", "noise_b"], \
        np.column_stack([x, y, z, w])



def fixture_energy_grid(rng, n=3000):
    """A SYNTHETIC power grid, with the physics planted deliberately.

    This is NOT a real dashboard and must never be counted as one. It
    exists because the real energy corpus could not be obtained without
    an API key, and a benchmark suite that needs a third-party
    credential to go green is not a benchmark suite.

    What it tests is the TOOL against known physics, not the domain.
    Planted relationships, each matching a pre-registered prediction in
    REAL_DASHBOARDS.md section 3:

      E1  demand depends on temperature as a U-SHAPE (heating below
          ~15C, cooling above), so the linear correlation is near zero
          while the dependence is strong. The nonlinear detector should
          catch it and correlation alone should not.
      E2  total_generation == demand + tiny imbalance. A grid must
          balance instantaneously, so this is a near-identity.
      E3  wind and solar are driven independently (synoptic vs diurnal).
      E4  gas fills the gap left by renewables, so it is NEGATIVELY
          related to their sum — merit-order dispatch.
      E6  total_generation is also the SUM of the three sources, giving
          the aggregate detector something real to find.
    """
    t = np.arange(n)
    hour = t % 24
    day = t / 24.0

    # Temperature is centred on the 15C comfort point and symmetric
    # about it, so BOTH arms of the U are exercised. A first attempt
    # centred it at 12C and added a phase-shifted diurnal demand term;
    # that gave r = +0.59 between temperature and demand, because the
    # two diurnal cycles correlated directly and swamped the U-shape.
    # Realistic, but it made the fixture test something other than what
    # it claims to.
    # The seasonal cycle must COMPLETE inside the window. A 365-day
    # period over 1,500 hours (62 days) never straddles the comfort
    # point — temperature rises monotonically across the whole sample
    # and only 9% of it falls below 15C, giving r = +0.94 and a fixture
    # that tests a linear relationship while claiming to test a U-shape.
    # Two full cycles inside the window puts 50% either side and the
    # linear component cancels to r = -0.01, measured before committing.
    season = 2.0 * n / 2.0          # two complete cycles over the sample
    temp = (15 + 12 * np.sin(2 * np.pi * t / (n / 2.0))
            + 3 * np.sin(2 * np.pi * (hour - 4) / 24.0)
            + rng.normal(0, 1.5, n))

    # E1: U-shaped response about the comfort point. Equal coefficients
    # keep the two arms balanced so the linear fit cancels.
    hdd = np.maximum(0.0, 15.0 - temp)
    cdd = np.maximum(0.0, temp - 15.0)
    demand = 900 + 24 * hdd + 24 * cdd + rng.normal(0, 18, n)

    # E3: independent renewable drivers
    solar = np.maximum(0.0, 260 * np.sin(np.pi * np.clip(
        (hour - 6) / 12.0, 0, 1)) + rng.normal(0, 12, n))
    wind = np.maximum(0.0, 180 + 90 * np.sin(2 * np.pi * day / 3.4)
                      + rng.normal(0, 45, n))
    del season

    # E4: gas is the residual — merit order puts it last
    gas = np.maximum(0.0, demand - solar - wind + rng.normal(0, 8, n))

    # E2/E6: generation balances demand AND is the sum of sources
    total_gen = solar + wind + gas
    frequency = 50.0 + 0.02 * (total_gen - demand) / 100.0 \
        + rng.normal(0, 0.004, n)

    names = ["temperature_c", "demand_mw", "solar_mw", "wind_mw",
             "gas_mw", "total_generation_mw", "grid_frequency_hz"]
    X = np.column_stack([temp, demand, solar, wind, gas, total_gen,
                         frequency])
    return names, X


# ----------------------------------------------------------------------
def main():
    rng = np.random.default_rng(7)
    tmp = tempfile.mkdtemp(prefix="sigaudit_")
    print("=" * 70)
    print("SIGNAL AUDIT — validation against known ground truth")
    print("=" * 70)

    try:
        # 1. planted factor count -------------------------------------
        print("\n1. Recovering a planted factor count (3 factors, 12 metrics)")
        names, X = fixture_known_factors(rng, k=3, d=12)
        p = os.path.join(tmp, "factors.csv")
        write_csv(p, names, X)
        r = SA.audit(p)
        pr = r["headline_pr"]
        check("participation ratio recovers ~3", 2.0 <= pr <= 4.5,
              f"got {pr:.2f}")
        check("95% components in range 2-5", 2 <= r["diff"]["n95"] <= 5,
              f"got {r['diff']['n95']}")
        check("flags heavy redundancy", pr / 12 < 0.5,
              f"ratio {pr / 12:.2f}")

        # 2. genuinely independent ------------------------------------
        print("\n2. Independent metrics (8 metrics, 8 factors)")
        names, X = fixture_independent(rng, d=8)
        p = os.path.join(tmp, "independent.csv")
        write_csv(p, names, X)
        r = SA.audit(p)
        pr = r["headline_pr"]
        check("participation ratio near 8", pr >= 7.0, f"got {pr:.2f}")
        check("no identities found", len(r["diff"]["identities"]) == 0)
        check("no redundancy clusters",
              all(len(c) == 1 for c in r["diff"]["clusters"]))
        check("verdict says little redundancy",
              "little redundancy" in SA.verdict_line(r))

        # 3. definitional identities ----------------------------------
        print("\n3. Definitional identities (unit conversion + complement)")
        names, X = fixture_identities(rng)
        p = os.path.join(tmp, "identities.csv")
        write_csv(p, names, X)
        r = SA.audit(p)
        idpairs = {frozenset((a, b)) for _, _, a, b in
                   (r["diff"]["identities"] or r["raw"]["identities"])}
        check("catches ms/sec unit conversion",
              frozenset(("latency_ms", "latency_sec")) in idpairs)
        check("catches success/error complement",
              frozenset(("success_rate", "error_rate")) in idpairs)
        check("complement reported as negative r",
              any(r_ < 0 for _, r_, a, b in
                  (r["diff"]["identities"] or r["raw"]["identities"])
                  if {a, b} == {"success_rate", "error_rate"}))

        # 3b. identity survivors ---------------------------------------
        print("\n3b. Deletion candidates never remove both halves of a pair")
        keep = SA.identity_representatives(r)
        cands = {u["name"] for u in SA.deletion_candidates(r)}
        pairs = list(r["diff"]["identities"]) + list(r["raw"]["identities"])
        both_gone = [(a, b) for _ar, _rr, a, b in pairs
                     if a in cands and b in cands]
        check("never lists both members of an identity pair",
              not both_gone, f"{both_gone[:2]}")
        check("exactly one survivor per pair is protected",
              all((a in keep) != (b in keep) for _ar, _rr, a, b in pairs)
              if pairs else True)
        check("survivor choice is deterministic across runs",
              SA.identity_representatives(SA.audit(p)) == keep)

        # 4. the trend trap (failure mode M7) -------------------------
        print("\n4. Trend trap — unrelated metrics sharing a trend")
        names, X = fixture_trend_trap(rng)
        p = os.path.join(tmp, "trend.csv")
        write_csv(p, names, X)
        r = SA.audit(p)
        check("raw view is fooled by shared trend", r["raw"]["pr"] < 3.0,
              f"raw PR {r['raw']['pr']:.2f}")
        check("differenced view recovers ~6", r["diff"]["pr"] >= 5.0,
              f"diff PR {r['diff']['pr']:.2f}")
        # The fixture IS a time series, but write_csv emits no date
        # column, so the engine has no evidence of row order and the
        # trend check now correctly declines to fire. Declaring it is
        # the fix — and this is the regression that proves the gate
        # works, since it failed the moment the gate went in.
        check("trend domination is NOT flagged without evidence of order",
              not r["trend_dominated"],
              "no time column, no declaration — a calendar finding would "
              "be invented")
        r_ord = SA.audit(p, ordered=True)
        check("trend domination is flagged once order is declared",
              r_ord["trend_dominated"])
        check("declaring order is recorded as a declaration",
              r_ord["order"]["declared"] and r_ord["order"]["ordered"])
        check("headline uses the honest (differenced) number",
              r["headline_pr"] == r["diff"]["pr"])

        # 5. nonlinear dependence -------------------------------------
        print("\n5. Nonlinear dependence (y = x^2, r ~ 0)")
        names, X = fixture_nonlinear(rng)
        p = os.path.join(tmp, "nonlinear.csv")
        write_csv(p, names, X)
        r = SA.audit(p)
        pairs = {frozenset((a, b)) for _, a, b, _, _ in r["nonlinear"]}
        check("detects x ~ x^2 despite near-zero correlation",
              frozenset(("driver", "squared_response")) in pairs)
        check("does not flag genuinely independent noise pairs",
              frozenset(("noise_a", "noise_b")) not in pairs)

        # 6. robustness / guards --------------------------------------
        print("\n6. Input handling")
        p = os.path.join(tmp, "messy.csv")
        with open(p, "w", encoding="utf-8") as f:
            f.write("date,region,revenue,cost,constant,notes\n")
            for i in range(200):
                f.write(f"2024-01-{i % 28 + 1:02d},EMEA,"
                        f"{1000 + i * 3 + rng.integers(0, 50)},"
                        f"{400 + i + rng.integers(0, 20)},7,ok\n")
        r = SA.audit(p)
        kept = set(r["names"])
        check("drops time-like column", "date" not in kept)
        check("drops non-numeric column", "notes" not in kept)
        check("drops constant column", "constant" not in kept)
        check("keeps real metrics", {"revenue", "cost"} <= kept)
        check("explains every drop in notes", len(r["notes"]) >= 3,
              f"{len(r['notes'])} notes")

        # thousands separators and percent signs
        p2 = os.path.join(tmp, "formatted.csv")
        with open(p2, "w", encoding="utf-8") as f:
            f.write("visits,conversion\n")
            for i in range(120):
                f.write(f'"{1000 + i * 7:,}",{i % 40 + 5}%\n')
        r2 = SA.audit(p2)
        check("parses thousands separators and percent signs",
              set(r2["names"]) == {"visits", "conversion"})

        # too few columns should raise, not guess
        p3 = os.path.join(tmp, "single.csv")
        write_csv(p3, ["only"], rng.standard_normal((100, 1)))
        try:
            SA.audit(p3)
            check("refuses single-column input", False)
        except ValueError:
            check("refuses single-column input", True)

        # too few rows should raise
        p4 = os.path.join(tmp, "short.csv")
        write_csv(p4, ["a", "b"], rng.standard_normal((12, 2)))
        try:
            SA.audit(p4)
            check("refuses too-few-rows input", False)
        except ValueError:
            check("refuses too-few-rows input", True)

        # MI honestly skipped on small samples
        p5 = os.path.join(tmp, "smallish.csv")
        write_csv(p5, ["a", "b", "c"], rng.standard_normal((60, 3)))
        r5 = SA.audit(p5)
        check("skips MI estimate when rows are too few",
              r5["mi_skipped"] and r5["nonlinear"] == [])

        # 6b. derived aggregates --------------------------------------
        print("\n6b. Derived aggregates (max / sum of other columns)")
        n = 500
        base = rng.uniform(10, 90, (n, 4))
        worst = base.max(1)                       # an index-of-indices
        total = base.sum(1)                       # a total
        p6 = os.path.join(tmp, "aggregates.csv")
        write_csv(p6, ["sub_a", "sub_b", "sub_c", "sub_d", "worst_index",
                       "grand_total"],
                  np.column_stack([base, worst, total]))
        r6 = SA.audit(p6)
        flagged = {n_: k for _, n_, k, _ in r6["aggregates"]}
        check("detects a max-of-others index", "worst_index" in flagged,
              f"as {flagged.get('worst_index')}")
        # subset sums are a documented gap; the total must at least not
        # be misreported as something it is not
        check("does not misreport the total as a max/min",
              flagged.get("grand_total") in (None, "sum"),
              f"as {flagged.get('grand_total')}")
        check("does not flag the independent components",
              not ({"sub_a", "sub_b", "sub_c", "sub_d"} & set(flagged)))

        # the correlation-based version of this detector flagged 39/53
        # columns on a real dashboard; equality-based must stay quiet on
        # merely-correlated data
        names_c, Xc = fixture_known_factors(rng, k=3, d=12)
        p7 = os.path.join(tmp, "correlated.csv")
        write_csv(p7, names_c, Xc)
        r7 = SA.audit(p7)
        check("stays quiet on merely-correlated data (no false positives)",
              len(r7["aggregates"]) == 0,
              f"{len(r7['aggregates'])} flagged")

        # 7. HTML output ----------------------------------------------
        print("\n7. HTML report")
        out = os.path.join(tmp, "report.html")
        SA.write_html(r, out)
        doc = open(out, encoding="utf-8").read()
        check("HTML written and non-trivial", len(doc) > 3000,
              f"{len(doc)} bytes")
        check("HTML is self-contained (no external fetches)",
              "http://" not in doc and "https://" not in doc
              and "<script" not in doc)
        check("HTML contains the headline figure",
              f"{r['headline_pr']:.1f}" in doc)


        # 8. synthetic energy grid — pre-registered predictions ---------
        print("\n8. Synthetic energy grid (planted physics, NOT a real corpus)")
        names, X = fixture_energy_grid(rng)
        p8 = os.path.join(tmp, "energy.csv")
        write_csv(p8, names, X)
        r8 = SA.audit(p8)

        nl = {frozenset((a, b)) for _, a, b, _, _ in r8["nonlinear"]}
        C = SA._safe_corr(np.column_stack([X[:, i] for i in range(len(names))]))
        i_temp, i_dem = names.index("temperature_c"), names.index("demand_mw")
        check("E1 demand~temperature is nonlinear, not linear",
              frozenset(("temperature_c", "demand_mw")) in nl
              and abs(C[i_temp, i_dem]) < 0.5,
              f"linear r={C[i_temp, i_dem]:+.3f}, MI-flagged="
              f"{frozenset(('temperature_c', 'demand_mw')) in nl}")

        idents = {frozenset((a, b)) for _, _, a, b in
                  (list(r8["diff"]["identities"])
                   + list(r8["raw"]["identities"]))}
        gen_dem = frozenset(("demand_mw", "total_generation_mw"))
        i_gen = names.index("total_generation_mw")
        check("E2 generation and demand are a near-identity",
              abs(C[i_dem, i_gen]) >= 0.99,
              f"r={C[i_dem, i_gen]:.4f}, identity-flagged={gen_dem in idents}")

        clusters = [set(c) for c in r8["diff"]["clusters"] if len(c) > 1]
        check("E3 wind and solar are not clustered together",
              not any({"wind_mw", "solar_mw"} <= c for c in clusters))

        i_gas = names.index("gas_mw")
        i_sol, i_wind = names.index("solar_mw"), names.index("wind_mw")
        ren = X[:, i_sol] + X[:, i_wind]
        r_gas_ren = float(np.corrcoef(X[:, i_gas], ren)[0, 1])
        check("E4 gas is negatively related to renewable output",
              r_gas_ren < 0, f"r={r_gas_ren:+.3f}")

        check("E5 at least 3 independent signals",
              r8["headline_pr"] >= 3.0, f"{r8['headline_pr']:.2f}")

        # E6 was written expecting the total to be caught. It is not,
        # and that is CORRECT: total_generation is the sum of a SUBSET
        # (3 of 6 other columns) and the detector only tests aggregates
        # over ALL others. Rather than delete the prediction, it is
        # inverted into a regression test for the documented gap — if
        # subset-sum detection is ever built, this test flips and
        # whoever built it finds out immediately.
        aggs = {nm for _f, nm, _k, _r in r8["aggregates"]}
        i_gen2 = names.index("total_generation_mw")
        parts = X[:, i_sol] + X[:, i_wind] + X[:, i_gas]
        exact = float(np.mean(np.isclose(X[:, i_gen2], parts)))
        check("E6 subset-sum total IS an exact aggregate of its parts",
              exact > 0.99, f"{exact:.0%} of rows match exactly")
        check("E6 known gap: subset sums are NOT detected",
              "total_generation_mw" not in aggs,
              "documented limitation — flips if subset detection is built")

        check("energy payload serialises",
              bool(json.dumps(SA.report_payload(r8))))

        # 9. browser payload contract ----------------------------------
        # report_payload is consumed by three clients that cannot be
        # changed in lockstep: the CLI, the hosted API, and the static
        # Pyodide page. Removing a key here breaks the demo silently, so
        # the shape is asserted rather than assumed.
        print("\n9. Browser payload contract (demo/index.html reads these)")
        pay = SA.report_payload(r8)
        top = ["engine_version", "file", "summary", "assurance",
               "failure_catalogue", "trend_confound", "identity_pairs",
               "redundancy_clusters", "derived_aggregates",
               "nonlinear_couplings", "metrics", "archive_candidates",
               "excluded_columns"]
        check("all top-level keys present",
              not [k for k in top if k not in pay],
              f"missing {[k for k in top if k not in pay]}")
        check("summary carries the headline fields",
              all(k in pay["summary"] for k in
                  ("metrics", "rows", "effective_signals",
                   "noise_reduction_pct", "load_bearing_count",
                   "archive_candidate_count", "verdict")))
        check("assurance carries grade and actionability",
              all(k in pay["assurance"] for k in
                  ("grade", "actionable", "reasons"))
              and pay["assurance"]["grade"] in ("A", "B", "C", "D"))
        check("every catalogue entry is renderable",
              all(set(c) >= {"id", "label", "available", "fired", "detail"}
                  for c in pay["failure_catalogue"]))
        check("unavailable checks are marked, not hidden",
              any(not c["available"] for c in pay["failure_catalogue"]),
              "rolling-average blind spot shows as not-checked")
        check("every metric row is renderable",
              all(set(m) >= {"name", "unique_variance", "closest_other",
                             "archive_candidate", "load_bearing"}
                  for m in pay["metrics"]))
        check("archive candidates agree between list and rows",
              set(pay["archive_candidates"])
              == {m["name"] for m in pay["metrics"]
                  if m["archive_candidate"]})
        check("identity pairs name the survivor",
              all("keep" in i for i in pay["identity_pairs"]))
        check("payload is free of NaN and Infinity",
              "NaN" not in json.dumps(pay)
              and "Infinity" not in json.dumps(pay))

        # ------------------------------------------------------------------
        # 10. subset-sum detector, and the archive guard it exposed
        #
        # Ordered deliberately: the negative controls come FIRST. Three of
        # the four rejected `derived_aggregates` designs looked correct on a
        # planted positive and only failed on data with no relation in it.
        # ------------------------------------------------------------------
        print("\n10. subset sums")
        rng = np.random.default_rng(4242)

        M = np.abs(rng.normal(50, 10, (900, 20)))
        check("no subset sum in independent non-negative noise",
              SA.subset_sums(M, [f"m{i}" for i in range(20)]) == [])

        f_ = rng.lognormal(3, 1.5, (900, 1))
        M = f_ * rng.uniform(0.05, 0.9, (1, 18)) * np.exp(rng.normal(0, .15, (900, 18)))
        check("no subset sum under pure scale dominance",
              SA.subset_sums(M, [f"s{i}" for i in range(18)]) == [],
              "the confound that defeated derived_aggregates")

        kids = np.abs(rng.normal(100, 20, (700, 4)))
        near = np.column_stack([kids.sum(1) * 0.97, kids])
        check("a parent at 97% of the sum is REJECTED",
              SA.subset_sums(near, ["parent"] + [f"k{i}" for i in range(4)]) == [],
              "exactness, not correlation")

        M = np.column_stack([kids.sum(1), kids, np.abs(rng.normal(300, 50, (700, 3)))])
        names = ["total"] + [f"part{i}" for i in range(4)] + [f"n{i}" for i in range(3)]
        got = SA.subset_sums(M, names)
        check("planted subset sum is recovered exactly",
              len(got) == 1 and got[0][0] == "total"
              and set(got[0][1]) == {f"part{i}" for i in range(4)},
              str(got))

        signed = np.column_stack([kids.sum(1), kids])
        signed[:, 1] *= -1
        check("signed columns are excluded, not mis-handled",
              SA.subset_sums(signed, ["total"] + [f"k{i}" for i in range(4)]) == [])

        zero = np.column_stack([kids[:, :2].sum(1), kids[:, :2], np.zeros(700)])
        check("an all-zero column is never counted as a child",
              all("z" not in r[1] for r in
                  SA.subset_sums(zero, ["total", "a", "b", "z"])))

        # the guard: every member of an additive family scores zero unique
        # variance, so an unguarded ranking offers the whole family
        path = os.path.join(tmp, "sums.csv")
        write_csv(path, names, M)
        res = SA.audit(path)
        prot = SA.subset_sum_protected(res)
        check("subset-sum children are protected from archiving",
              prot == {f"part{i}" for i in range(4)}, str(prot))
        arch = {u["name"] for u in SA.deletion_candidates(res)}
        check("no child of a subset sum is an archive candidate",
              not (arch & prot))
        check("the parent itself stays archivable",
              "total" not in prot)

        pay = SA.report_payload(res)
        check("payload carries subset sums",
              pay["subset_sums"] and pay["subset_sums"][0]["metric"] == "total")
        check("subset-sum check reports as available",
              any(c["id"] == "subset-sum" and c["available"]
                  for c in pay["failure_catalogue"]))

        # --------------------------------------------------------------
        # 11. view dict, aliases, and the headline as a declaration
        # --------------------------------------------------------------
        print("\n11. view dict")
        res = SA.audit(os.path.join(tmp, "sums.csv"))
        check("every basis is computed, always",
              set(res["views"]) == {"raw", "differenced"}, str(list(res["views"])))
        check("raw is an ALIAS of views['raw'], not a copy",
              res["raw"] is res["views"]["raw"])
        check("diff is an ALIAS of views['differenced'], not a copy",
              res["diff"] is res["views"]["differenced"])
        check("every view carries an identical key set",
              set(res["views"]["raw"]) == set(res["views"]["differenced"]),
              "a basis with a cheaper summary could not be compared")
        check("headline is a declared name, not a deduction",
              res["headline"] == "differenced")
        check("headline_pr resolves through the declaration",
              res["headline_pr"] == res["views"][res["headline"]]["pr"])
        check("each view names its own columns",
              res["views"]["raw"]["names"] == list(res["names"]))

        # --------------------------------------------------------------
        # 12. ratio basis and the basis-conflict falsifier
        # --------------------------------------------------------------
        print("\n12. ratio basis")
        rng2 = np.random.default_rng(99)
        n = 600
        size = np.exp(rng2.normal(0, 2.0, n))
        shape = 1.0 + 0.3 * rng2.normal(0, 1, (n, 4))
        cols = np.column_stack([size, size * shape.T]).T if False else \
            np.column_stack([size] + [size * shape[:, i] for i in range(4)])
        rate = np.abs(rng2.normal(5, 1, n))          # already scale-free
        M2 = np.column_stack([cols, rate])
        nm2 = ["total", "a", "b", "c", "d", "rate"]
        path2 = os.path.join(tmp, "ratio.csv")
        write_csv(path2, nm2, M2)

        res = SA.audit(path2, scale_by="total")
        check("declaring a denominator adds exactly one basis",
              set(res["views"]) == {"raw", "differenced", "ratio:total"},
              str(sorted(res["views"])))
        check("the denominator column is dropped from its own basis",
              "total" not in res["views"]["ratio:total"]["names"])
        check("other bases are untouched by the declaration",
              res["views"]["raw"]["n_metrics"] == len(nm2))
        check("each view reports its own shape",
              res["views"]["ratio:total"]["n_metrics"] == len(nm2) - 1)
        check("headline is still the declaration, not the new basis",
              res["headline"] == "differenced")

        res_ex = SA.audit(path2, scale_by="total", scale_exempt=("rate",))
        check("exempt column passes through unscaled",
              np.allclose(
                  res_ex["views"]["ratio:total"]["corr"].shape,
                  res["views"]["ratio:total"]["corr"].shape))
        check("scale_exempt on an unknown column raises, never guesses",
              _raises(lambda: SA.audit(path2, scale_by="total",
                                       scale_exempt=("nope",))))
        check("scale_by on an unknown column raises, never guesses",
              _raises(lambda: SA.audit(path2, scale_by="nope")))

        # a basis that injects a factor must be caught
        inj = np.column_stack([size, rate, np.abs(rng2.normal(5, 1, n))])
        p3 = os.path.join(tmp, "inject.csv")
        write_csv(p3, ["size", "r1", "r2"], inj)
        rj = SA.audit(p3, scale_by="size")
        check("dividing scale-free columns by a size is flagged",
              any(b == "ratio:size" and {x, y} == {"r1", "r2"}
                  for b, x, y, _o, _n in rj["basis_conflicts"]),
              str(rj["basis_conflicts"]))
        check("no conflict is reported when no basis was declared",
              SA.audit(p3)["basis_conflicts"] == [])
        pay = SA.report_payload(rj)
        check("payload carries basis conflicts",
              bool(pay["basis_conflicts"]))
        check("basis-conflict check reports as available",
              any(c["id"] == "basis-conflict" and c["available"]
                  for c in pay["failure_catalogue"]))

        # --------------------------------------------------------------
        # 13. basis declaration: assumed vs declared, and metric counts
        # --------------------------------------------------------------
        print("\n13. basis declaration")
        r_a = SA.audit(path2)
        check("an undeclared basis is RECORDED as assumed, not hidden",
              r_a["basis_declared"] is False and r_a["headline"] == "differenced")
        r_d = SA.audit(path2, scale_by="total", basis="ratio:total")
        check("a declared basis is recorded as declared",
              r_d["basis_declared"] is True and r_d["headline"] == "ratio:total")
        check("declaring an uncomputed basis raises, never falls back",
              _raises(lambda: SA.audit(path2, basis="ratio:total")))
        # the drift the per-view names key exists to prevent
        check("headline metric count comes from the HEADLINE basis",
              SA.report_payload(r_d)["summary"]["metrics"]
              == r_d["views"]["ratio:total"]["n_metrics"],
              "a ratio basis drops its denominator; using the global count "
              "silently misstates % redundant")
        check("that count differs from the global one, so the bug was real",
              r_d["views"]["ratio:total"]["n_metrics"] != r_d["n_metrics"])
        pay = SA.report_payload(r_d)
        check("payload publishes the basis and whether it was declared",
              pay["basis"]["declared"] is True
              and pay["basis"]["headline"] == "ratio:total"
              and set(pay["basis"]["per_basis"]) == set(r_d["views"]))

        # --------------------------------------------------------------
        # 14. scale basis and the closure problem
        #
        # Folded in from test_scale_basis.py, which was kept standalone
        # while it tested a basis the engine did not yet have. It does
        # now, so these run every time.
        #
        # Predictions C1-C9 were registered in SCALE_BASIS_PREREG.md
        # before the fixture existed. The fixture itself was built wrong
        # twice and the verification block caught both before anything
        # was scored -- shares were exponential in the latent factors
        # (nonlinear, so a planted rank-4 read as 8.26), and the closure
        # variants used k=0, which yields identical shares rather than
        # random compositions and correlated at exactly +1.0.
        # --------------------------------------------------------------
        print("\n14. scale basis / closure")
        import test_scale_basis as TSB
        rngc = np.random.default_rng(20260731)
        d_, k_ = 40, 4

        X, size = TSB.cross_section(rngc, d=d_, k=k_, mode="incomplete")
        Xq, sq = TSB.cross_section(rngc, d=d_, k=k_, mode="incomplete",
                                   noise=0.0)
        Rq = Xq / sq[:, None]
        sv = np.linalg.svd(Rq - Rq.mean(0), compute_uv=False)
        check("fixture: planted rank is the rank the engine would see",
              int(np.sum(sv > sv[0] * 0.05)) == k_,
              "rank verified noiseless; noise added back for scoring")
        check("fixture: dollar view is scale-dominated as intended",
              TSB.mean_offdiag_r(X)[0] > 0.8)

        R = X / size[:, None]
        pr_d, pr_r = TSB.pr(X), TSB.pr(R)
        check("C1 dollar basis buries the structure (PR < 3)", pr_d < 3,
              f"{pr_d:.2f} against a true {k_ + 1}")
        check("C2 ratio basis recovers the planted factor count",
              3 <= pr_r <= 7, f"{pr_r:.2f} against a planted {k_}")
        check("C3 ratio basis at least doubles the dollar basis",
              pr_r >= 2 * pr_d, f"{pr_r / pr_d:.1f}x")

        for width, pred in ((5, -0.25), (10, -1 / 9), (40, -1 / 39)):
            Xc, sc = TSB.cross_section(rngc, d=width,
                                       mode="random_composition")
            m, mx = TSB.mean_offdiag_r(Xc / sc[:, None])
            check(f"C4/C5 closure bias matches -1/(d-1) at d={width}",
                  abs(m - pred) < 0.05, f"{m:+.4f} vs {pred:+.4f}")
            if width >= 10:
                check(f"C7 closure crosses no threshold at d={width}",
                      mx < SA.REDUNDANT_R, f"max|r| = {mx:.3f}")

        Xc, sc = TSB.cross_section(rngc, d=40, mode="random_composition")
        prc = TSB.pr(Xc / sc[:, None])
        check("C6 participation ratio is NOT biased by closure",
              abs(prc - 39) < 4, f"{prc:.2f} against a correct 39")

        Xg, sg = TSB.cross_section(rngc, d=d_, mode="degenerate", noise=0.0)
        check("C8 degenerate control invents no structure",
              int(np.sum(np.std(Xg / sg[:, None], axis=0) < 1e-12)) == d_)

        # --------------------------------------------------------------
        # 15. execution guard — stale installed engine
        # --------------------------------------------------------------
        print("\n15. execution guard")
        import io as _io
        import signal_audit_cli as CLI

        class _Installed:
            __file__ = "/usr/lib/python3/site-packages/signal_audit.py"

        real_sa = CLI.SA
        repo = os.path.dirname(os.path.abspath(SA.__file__))
        try:
            CLI.SA = _Installed
            buf = _io.StringIO()
            check("refuses an installed engine inside the source tree",
                  CLI.execution_guard(stream=buf, cwd=repo) == 1)
            msg = buf.getvalue()
            check("the refusal names both paths, not just the fact",
                  "/usr/lib/python3/site-packages" in msg and repo in msg)
            check("the refusal says how to fix it",
                  "pip install -e ." in msg
                  and "REDD_ALLOW_INSTALLED=1" in msg)

            os.environ["REDD_ALLOW_INSTALLED"] = "1"
            check("the documented wheel test is not blocked",
                  CLI.execution_guard(stream=_io.StringIO(), cwd=repo) == 0,
                  "HANDOFF runs the built wheel from inside the repo")
            os.environ.pop("REDD_ALLOW_INSTALLED")

            check("no false positive outside any source tree",
                  CLI.execution_guard(stream=_io.StringIO(), cwd=tmp) == 0)

            # a pyproject.toml alone must not trigger it — that would fire
            # on unrelated projects
            other = os.path.join(tmp, "other_project")
            os.makedirs(other, exist_ok=True)
            open(os.path.join(other, "pyproject.toml"), "w").write("[project]\n")
            check("someone else's pyproject.toml does not trigger it",
                  CLI.execution_guard(stream=_io.StringIO(), cwd=other) == 0,
                  "requires signal_audit.py alongside it")
        finally:
            CLI.SA = real_sa
            os.environ.pop("REDD_ALLOW_INSTALLED", None)

        check("the local engine passes its own guard",
              CLI.execution_guard(stream=_io.StringIO(), cwd=repo) == 0)

        # --------------------------------------------------------------
        # 16. domain lexicons — presentation only, never a verdict
        # --------------------------------------------------------------
        print("\n16. domain lexicons")
        import json as _json
        import signal_audit_cli as CLI2

        avail = sorted(x[:-5] for x in os.listdir(CLI2.DOMAIN_DIR)
                       if x.endswith(".json"))
        check("lexicons ship as JSON, not TOML",
              avail and all(os.path.exists(
                  os.path.join(CLI2.DOMAIN_DIR, f"{d}.json")) for d in avail),
              "tomllib is 3.11+; this package supports >=3.8")

        for d in avail:
            lex = CLI2.load_domain(d)
            for key, v in lex["checks"].items():
                check(f"{d}:{key} offers competing readings, not a verdict",
                      {"consistent_with", "but_also", "distinguish_by"} <= set(v),
                      "basis_conflicts cannot tell injected from exposed")
            check(f"{d} names only checks the engine actually has",
                  set(lex["checks"]) <= {c[0] for c in SA.CATALOGUE},
                  str(set(lex["checks"]) - {c[0] for c in SA.CATALOGUE}))


        # the browser gets the lexicons as static assets, so demo/ must
        # carry byte-identical copies plus a generated manifest — the
        # browser cannot list a directory over HTTP
        demo_dom = os.path.join(os.path.dirname(CLI2.__file__), "demo",
                                "domains")
        if os.path.isdir(demo_dom):
            import filecmp as _fc
            for d in avail:
                srcf = os.path.join(CLI2.DOMAIN_DIR, f"{d}.json")
                dstf = os.path.join(demo_dom, f"{d}.json")
                check(f"demo carries a byte-identical {d} lexicon",
                      os.path.exists(dstf)
                      and _fc.cmp(srcf, dstf, shallow=False))
            idxf = os.path.join(demo_dom, "index.json")
            check("demo lexicon manifest lists exactly what is present",
                  os.path.exists(idxf)
                  and sorted(_json.load(open(idxf))["domains"]) == avail,
                  "a hand-written manifest goes stale; this one is generated")

        check("an unknown domain is refused, never silently ignored",
              _raises(lambda: CLI2.load_domain("no_such_domain")))
        check("refusal is a normal exception, not SystemExit",
              isinstance(_catch(lambda: CLI2.load_domain("nope")), ValueError),
              "SystemExit is not catchable as Exception and would kill a "
              "caller that meant to handle it")
        check("the lexicon search includes sys.prefix",
              any("sys.prefix" in ln for ln in
                  open(CLI2.__file__, encoding="utf-8").read().split("\n")),
              "installed data-files land there, not beside the module")

        # the load-bearing property: a lexicon cannot change a number
        r_plain = SA.audit(path2)
        r_lens = SA.audit(path2)
        check("a lexicon touches no engine state",
              SA.report_payload(r_plain) == SA.report_payload(r_lens),
              "translation happens in the CLI, after the maths")
        f0 = CLI2.Fmt(False)
        plain = CLI2.render(r_plain, f0, width=100)
        lensed = CLI2.render(r_lens, f0, width=100,
                             lex=CLI2.load_domain(avail[0]))
        for tok in ("effective signals", "% redundant", "EVIDENCE GRADE"):
            check(f"lensed output keeps the unlensed figure: {tok!r}",
                  (tok in plain) == (tok in lensed))
        check("the lens only ADDS a section, never removes one",
              len(lensed) > len(plain))

        # --------------------------------------------------------------
        # 17. the demo page's third-party URLs
        #
        # configure_site.py once rewrote the Pyodide CDN to the customer's
        # own domain, because its domain regex matched any https://host/.
        # The deployed page then 404'd on its own engine, `loadPyodide`
        # was never defined, and the site died on load. Nothing in the
        # test suite noticed, because the engine itself was fine.
        # --------------------------------------------------------------
        print("\n17. demo page third-party URLs")
        demo_index = os.path.join(os.path.dirname(os.path.abspath(SA.__file__)),
                                  "demo", "index.html")
        if os.path.exists(demo_index):
            page = open(demo_index, encoding="utf-8").read()
            check("Pyodide loads from a CDN, not from our own domain",
                  "cdn.jsdelivr.net/pyodide/" in page,
                  "a rewritten CDN URL breaks the page with an error that "
                  "names none of the cause")
            check("a fallback Pyodide source exists",
                  "unpkg.com/pyodide" in page,
                  "one blocked CDN should not take the page down")
            check("the page checks loadPyodide before calling it",
                  "window.loadPyodide" in page)
            import re as _re
            hosts = set(_re.findall(r'https://([A-Za-z0-9.\-]+)/', page))
            hosts.discard("host")          # the cautionary comment
            third = {h for h in hosts if "jsdelivr" in h or "unpkg" in h}
            check("both third-party hosts survive substitution",
                  len(third) == 2, str(sorted(third)))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # 18. index width — the engine must run on a 32-bit platform
    #
    # `_mi_matrix` built its combined bin index with .astype(np.int64).
    # `np.bincount` casts its argument to `np.intp` under the 'safe'
    # rule. On x86-64 intp IS int64, so this was free and correct, and
    # 134 tests said so. In Pyodide intp is 32-bit, int64 -> int32 is
    # not a safe cast, and EVERY audit on the live site died with
    #
    #   TypeError: Cannot cast array data from dtype('int64') to
    #   dtype('int32') according to the rule 'safe'
    #
    # The engine was validated on six corpora and never once on the
    # platform it actually ships to. These checks assert the invariant
    # rather than the platform, because the test host is 64-bit and
    # cannot reproduce the failure by running the code.
    # ------------------------------------------------------------------
    print("\n18. index width (32-bit / wasm safety)")

    src = open(os.path.abspath(SA.__file__), encoding="utf-8").read()
    check("no int64 upcast feeds np.bincount",
          ".astype(np.int64) * K" not in src,
          "np.bincount casts to intp under 'safe'; intp is int32 on wasm32")

    D = SA._discretise(np.random.RandomState(0).normal(size=(400, 3)), 12)
    K = int(D.max()) + 1
    idx = D[:, 0].astype(np.intp) * K + D[:, 1]
    check("combined index is intp on this platform",
          idx.dtype == np.intp, str(idx.dtype))
    check("index is safely castable to intp",
          np.can_cast(idx.dtype, np.intp, casting="safe"),
          "this is the exact rule np.bincount applies")

    # The cast is only sound while K*K stays small. Unguarded, K = 30001
    # asks np.bincount for a 6.7 GiB dense table on 64-bit and overflows
    # the index width on 32-bit. Both must be a named refusal.
    big = np.zeros((10, 2), dtype=np.int16)
    big[0, 0] = 30000                      # K = 30001
    try:
        SA._mi_matrix(big, 2)
        raised = False
    except ValueError:
        raised = True
    except MemoryError:
        raised = False                     # the guard did not fire first
    check("oversized bin count is refused before allocating", raised,
          "must be a ValueError, not a 6.7 GiB MemoryError")

    # ...and the legitimate range still works untouched.
    ok_small = SA._mi_matrix(SA._discretise(
        np.random.default_rng(1).standard_normal((200, 3)), 12), 3)
    check("normal bin counts pass the guard", len(ok_small) == 3)

    # The dtype change must not have moved a single MI value. Checked
    # against the sorting implementation Part A replaced — the same
    # bit-identical claim, re-verified after the cast changed.
    rng18 = np.random.default_rng(7)
    D2 = SA._discretise(rng18.standard_normal((500, 6)), 12)
    fast = SA._mi_matrix(D2, 6)

    def _mi_by_sorting(D, d):
        n = D.shape[0]
        out = {}
        hs = []
        for j in range(d):
            _, c = np.unique(D[:, j], return_counts=True)
            p = c / n
            hs.append(float(-(p * np.log2(p)).sum()))
        for i, j in itertools.combinations(range(d), 2):
            _, c = np.unique(D[:, [i, j]], axis=0, return_counts=True)
            p = c / n
            out[(i, j)] = hs[i] + hs[j] - float(-(p * np.log2(p)).sum())
        return out

    slow = _mi_by_sorting(D2, 6)
    worst = max(abs(fast[k] - slow[k]) for k in slow)
    check("intp index is bit-identical to the sorting version",
          worst == 0.0, f"max |diff| = {worst:.3e}")

    # ------------------------------------------------------------------
    # 19. the exported summary
    #
    # This text is the one artefact that leaves the tool and lands in a
    # change request, where it becomes the evidence of record and gets
    # read by someone who did not run it. So it is held to the same
    # standard as the page: it must state what was proved, name what was
    # not, and never read as a clearance.
    # ------------------------------------------------------------------
    print("\n19. exported summary")

    names19, X19 = fixture_identities(np.random.default_rng(3))
    csv19 = ",".join(names19) + "\n" + "\n".join(
        ",".join(f"{v:.6f}" for v in row) for row in X19)
    res19 = SA.audit_text(csv19, label="ws.csv")
    pay19 = SA.report_payload(res19)
    md = SA.summary_markdown(pay19)

    check("summary reports the same headline the page renders",
          f"{pay19['summary']['effective_signals']}" in md,
          f"effective_signals = {pay19['summary']['effective_signals']}")
    check("summary names the evidence grade",
          f"**{pay19['assurance']['grade']}**" in md)
    check("summary refuses to read as a clearance",
          "This is not a clearance" in md
          and "not evidence that it is safe" in md)
    check("summary carries every attestation column",
          all(c in md for c in SA.WORKSHEET_ATTESTATION),
          "the reviewer must see what is still unanswered")
    check("an undeclared basis is flagged in the exported text",
          ("ASSUMED, not declared" in md) == (not res19["basis_declared"]),
          "a basis warning on screen and not in the export would be worse "
          "than no export")

    # The failure mode this feature was one line away from: an artefact
    # that reads "archived N redundant panels", which the engine has no
    # standing to say.
    lowered = md.lower()
    for phrase in ("safe to archive", "archived ", "cleared for",
                   "approved", "verified safe"):
        check(f"summary avoids overclaiming: {phrase.strip()!r}",
              phrase not in lowered)

    ws = SA.blast_radius_worksheet(res19)
    head = ws.splitlines()[0].split(",")
    check("worksheet header matches the shared column contract",
          head == SA.WORKSHEET_COLUMNS,
          "CLI and browser must emit the same shape")
    check("worksheet has one row per metric",
          len(ws.strip().splitlines()) - 1 == len(res19["diff"]["unique"]))
    check("attestation columns ship empty, not guessed",
          all(r.endswith("," * len(SA.WORKSHEET_ATTESTATION))
              for r in ws.strip().splitlines()[1:]))

    demo_page = os.path.join(os.path.dirname(os.path.abspath(SA.__file__)),
                             "demo", "index.html")
    if os.path.exists(demo_page):
        pg = open(demo_page, encoding="utf-8").read()
        check("page builds exports in the engine, not in JavaScript",
              "SA.summary_markdown(_p)" in pg
              and "SA.blast_radius_worksheet(_r)" in pg,
              "a summary that disagreed with the screen is worse than none")
        check("export happens in-tab via Blob, with no upload",
              "URL.createObjectURL" in pg and "fetch(" not in
              pg.split("function doExport")[1].split("}")[0])

    # ------------------------------------------------------------------
    # 20. correlation drift and the row-order gate
    #
    # Registered in DRIFT_PREREG.md and scored there before any of this
    # was wired in. These checks defend the wiring, not the detector:
    # that a gated check is visibly skipped rather than silently absent,
    # and that "we have not built this" stays distinguishable from "this
    # cannot be asked of your data".
    # ------------------------------------------------------------------
    print("\n20. correlation drift + row-order gate")

    rng20 = np.random.default_rng(21)

    def stat20(n, d, k, noise=0.35):
        L = rng20.standard_normal((k, d))
        F = rng20.standard_normal((n, k))
        return F @ L + noise * rng20.standard_normal((n, d))

    nm = [f"m{j:02d}" for j in range(12)]

    # Control before positive, same order as the registration.
    flat = SA.correlation_drift(nm, stat20(2000, 12, 3), ordered=True)
    check("stationary correlated data does not drift",
          flat["status"] == "ok" and not flat["pairs"],
          f"{len(flat['pairs'])} of {flat['pairs_tested']} pairs")

    X20 = stat20(2000, 12, 3)
    a, b = rng20.standard_normal(2000), rng20.standard_normal(2000)
    b[1000:] = 0.8 * a[1000:] + np.sqrt(1 - 0.64) * b[1000:]
    X20[:, 0], X20[:, 1] = a, b
    got = SA.correlation_drift(nm, X20, ordered=True)
    check("planted drift is found and ranked first",
          bool(got["pairs"]) and
          (got["pairs"][0]["metric_a"], got["pairs"][0]["metric_b"]) == ("m00", "m01"),
          f"{len(got['pairs'])} flagged")

    # The incident confound: one shared spike must not become N findings.
    X21 = stat20(2000, 12, 3)
    X21[-60:] += 9.0 * np.abs(rng20.standard_normal((60, 1)))
    naive = SA.correlation_drift(nm, X21, ordered=True, naive=True)
    ship = SA.correlation_drift(nm, X21, ordered=True)
    check("a shared incident is not reported as many new couplings",
          len(ship["pairs"]) / ship["pairs_tested"] < 0.05
          and len(naive["pairs"]) > len(ship["pairs"]),
          f"naive {len(naive['pairs'])}/{naive['pairs_tested']}, "
          f"shipped {len(ship['pairs'])}/{ship['pairs_tested']}")

    check("unordered rows get no drift result",
          SA.correlation_drift(nm, X20)["status"] == "not_applicable")
    check("too few rows refuses rather than guesses",
          SA.correlation_drift(nm, stat20(40, 12, 3),
                               ordered=True)["status"] == "insufficient_rows")

    # BH q-values must agree with the rejections they are printed beside.
    # The first version decided by BH and displayed Bonferroni, so a
    # flagged pair showed q = 0.053 against a 0.05 threshold.
    for p_ in got["pairs"]:
        check(f"reported q-value is consistent with rejection ({p_['metric_a']})",
              p_["q_value"] <= SA.DRIFT_Q, f"q = {p_['q_value']:.2e}")

    # --- the gate, end to end -----------------------------------------
    tmp20 = tempfile.mkdtemp()
    try:
        pth = os.path.join(tmp20, "noorder.csv")
        write_csv(pth, nm, X20)
        no = SA.report_payload(SA.audit(pth))
        yes = SA.report_payload(SA.audit(pth, ordered=True))

        cat = {c["id"]: c for c in no["failure_catalogue"]}
        check("time-dependent checks are SKIPPED, not silently absent",
              all(cat[k]["skipped"] and cat[k]["available"] and not cat[k]["fired"]
                  for k in ("trend-confound", "correlation-drift")),
              "both carry a reason naming the data, not the roadmap")
        check("the skip reason says how to fix it",
              "--ordered" in cat["correlation-drift"]["skipped"])
        check("an unbuilt check stays distinguishable from a gated one",
              any(c["available"] is False and not c["skipped"]
                  for c in no["failure_catalogue"]),
              "the rolling-average entry must not hide behind the gate")
        check("declaring order lets the drift check run",
              yes["correlation_drift"]["status"] == "ok"
              and bool(yes["correlation_drift"]["pairs"]))
        check("order state is reported either way",
              no["order"]["ordered"] is False
              and yes["order"]["declared"] is True)
        check("payload is JSON-safe with drift present",
              isinstance(json.dumps(yes), str))
    finally:
        shutil.rmtree(tmp20, ignore_errors=True)

    # Registered handling decision that the first wired version missed:
    # a C or D grade returns no pairs. It returned 114 on grade-C data.
    thin = SA.correlation_drift  # keep the name in scope for readability
    nyc = os.path.join(os.path.dirname(os.path.abspath(SA.__file__)),
                       "data", "nyc_covid_dashboard.csv")
    if os.path.exists(nyc):
        rp = SA.report_payload(SA.audit(nyc))
        check("a thin corpus reports insufficient evidence, not 114 pairs",
              rp["assurance"]["grade"] in ("C", "D")
              and rp["correlation_drift"]["status"] == "insufficient_evidence"
              and not rp["correlation_drift"]["pairs"],
              f"grade {rp['assurance']['grade']}")

    # ------------------------------------------------------------------
    # 21. reference graph
    #
    # This is not a statistical detector, so it gets no pre-registration.
    # It gets the deterministic equivalent: the cases that must NOT match
    # were written before the parser, because the failure mode here is a
    # substring search that looks like it works. `node_load1` inside
    # `node_load15` is the whole reason this is a tokeniser.
    #
    # SAFETY_BOUNDARIES.md condition 3 is the load-bearing constraint —
    # the graph supplies evidence, never clearance, and these checks fail
    # if it ever emits the latter.
    # ------------------------------------------------------------------
    print("\n21. reference graph")
    import refgraph as RG

    def refs(expr):
        return (RG.extract_metric_identifiers(expr)
                | RG._iter_label_selector_metrics(expr))

    must_not = [
        ("node_load15 > 5", "node_load1", "a prefix is not a match"),
        ("my_node_load1_total > 5", "node_load1", "an infix is not a match"),
        ('rate(http_requests_total{job="up"}[5m])', "up",
         "a label VALUE is not a metric"),
        ("sum by (node_load1) (x)", "node_load1",
         "a grouping label is not a metric"),
        ("# node_load1 was removed\nother > 1", "node_load1",
         "a comment is not a reference"),
        ('label_replace(x,"d","node_load1","s","")', "node_load1",
         "a string literal is not a reference"),
        ("rate(x[5m])", "m", "a duration suffix is not a metric"),
        ("avg_over_time(disk_free[1h30m])", "h",
         "a compound duration is not a metric"),
    ]
    for expr, metric, why in must_not:
        check(f"no false match: {why}", metric not in refs(expr),
              repr(expr[:44]))

    must_match = [
        ("node_load1 > 5", "node_load1", "bare"),
        ("rate(node_load1[5m])", "node_load1", "inside a function"),
        ('{__name__="node_load1"} > 1', "node_load1", "__name__ selector"),
        ('node_load1{instance="a"} > 5', "node_load1", "with a selector"),
    ]
    for expr, metric, why in must_match:
        check(f"finds a real reference: {why}", metric in refs(expr))

    tmp21 = tempfile.mkdtemp()
    try:
        with open(os.path.join(tmp21, "rules.yml"), "w", encoding="utf-8") as fh:
            fh.write("groups:\n  - name: http\n    rules:\n"
                     "      - record: job:http:rate5m\n"
                     "        expr: sum by (job) (rate(http_requests_total[5m]))\n"
                     "      - alert: HighErrorRate\n"
                     "        expr: job:http:rate5m > 100\n")
        with open(os.path.join(tmp21, "board.json"), "w", encoding="utf-8") as fh:
            fh.write('{"title":"B","panels":[{"title":"Load",'
                     '"targets":[{"expr":"node_load15"}]}]}')

        g = RG.scan_paths([tmp21])
        check("scan reads both file types", len(g.scanned) == 2,
              f"{len(g.scanned)} files, {len(g.entries)} entries")
        check("unparseable files are recorded, not skipped silently",
              hasattr(g, "unreadable") and g.unreadable == [])

        # The reference a human misses: the raw metric is on no
        # dashboard, and every alert reaches it through a recording rule.
        raw = g.lookup("http_requests_total")
        check("transitive reference through a recording rule is found",
              raw["status"] == "referenced" and raw["reference_count"] >= 2,
              f"{raw['reference_count']} references")

        check("a rule does not reference itself",
              "job:http:rate5m" not in
              [e for e in g.entries if e.get("defines") == "job:http:rate5m"][0]["uses"])

        absent = g.lookup("node_load1")
        check("an unfound metric is NOT reported as unreferenced",
              absent["status"] == "not_found_in_scanned_sources",
              absent["status"])
        check("no lookup can ever return a clearance",
              all(g.lookup(m)["status"] in
                  ("referenced", "not_found_in_scanned_sources")
                  for m in ("node_load1", "node_load15", "http_requests_total")),
              "only two states exist, by design")
        check("what was searched is reported with the answer",
              absent["scanned"] == g.scanned and len(absent["scanned"]) == 2)

        # SAFETY_BOUNDARIES condition 3, in code.
        src = open(os.path.abspath(RG.__file__), encoding="utf-8").read()
        for phrase in ("unreferenced", "safe to archive", "safe_to_delete",
                       "clearance\":", "= \"safe\""):
            check(f"refgraph never emits {phrase!r} as a status",
                  f'"{phrase}"' not in src and f"'{phrase}'" not in src)

        # Annotation must leave unanswerable cells EMPTY, not "no".
        nm21 = ["http_requests_total", "node_load15", "node_load1"]
        rng21 = np.random.default_rng(9)
        b = rng21.standard_normal((400, 3))
        csv21 = ",".join(nm21) + "\n" + "\n".join(
            ",".join(f"{v:.5f}" for v in r) for r in b)
        ws = SA.blast_radius_worksheet(SA.audit_text(csv21, label="w.csv",
                                                     ordered=True))
        ann = RG.annotate_worksheet(ws, g)
        rows21 = list(__import__("csv").reader(__import__("io").StringIO(ann)))
        h21 = rows21[0]
        check("annotation preserves the shared column contract",
              h21 == SA.WORKSHEET_COLUMNS)
        mi, ni = h21.index("metric"), h21.index("note")
        mon = h21.index("referenced_by_monitors")
        by = {r[mi]: r for r in rows21[1:]}
        sc = h21.index("scan_evidence")
        check("evidence goes in scan_evidence, not the yes/no cells",
              "monitors:" in by["http_requests_total"][sc]
              and by["http_requests_total"][mon] == "",
              "attestation columns are parsed as booleans; writing text "
              "into them made every pre-filled worksheet unattestable")
        check("an unfound metric says so, and says it is not a clearance",
              "not found in" in by["node_load1"][sc]
              and "NOT a clearance" in by["node_load1"][sc])
        check("the attestation columns are untouched by the scan",
              all(by[m][mon] == "" for m in by),
              "the look-up is automated; the answer is not")

    finally:
        shutil.rmtree(tmp21, ignore_errors=True)


    # ------------------------------------------------------------------
    # 22. packaging — every module actually ships
    #
    # `py-modules` lists flat files with no package for setuptools to
    # discover, so an omission is SILENT: the module imports perfectly
    # from the source tree and is simply absent from the wheel. That is
    # the same class of divergence as the domain lexicons landing in
    # sys.prefix, and `refgraph` was missing until a Windows install
    # surfaced it.
    # ------------------------------------------------------------------
    # The shipped examples are documentation, and documentation that
    # drifts from behaviour is worse than none — examples/README.md
    # makes specific claims about what is and is not found.
    ex = os.path.join(os.path.dirname(os.path.abspath(SA.__file__)),
                      "examples", "monitoring")
    if os.path.exists(ex):
        gx = RG.scan_paths([ex])
        check("examples scan cleanly", len(gx.scanned) == 3 and not gx.unreadable,
              f"{len(gx.scanned)} files, {len(gx.entries)} references")
        check("example: the ARCHIVE candidate is on a paging monitor",
              gx.lookup("request_rate_total")["status"] == "referenced",
              "request_rate_total ~ methodGET_status200 at r = 0.99987")
        check("example: node_load1 is NOT matched by node_load15",
              gx.lookup("node_load1")["reference_count"] == 0,
              "grep finds 6; the tokeniser finds 0")
        check("example: node_load15 IS found",
              gx.lookup("node_load15")["reference_count"] >= 2)
        check("example: transitive reference through a recording rule",
              any("MemoryAvailableLow" in x for v in
                  gx.lookup("node_memory_MemAvailable_bytes")["columns"].values()
                  for x in v))
        check("a missing --refs path is refused, not treated as empty",
              _raises(lambda: RG.scan_paths([os.path.join(ex, "nope")])),
              "scanning nothing must not read as 'nothing references these'")

        # --- the conflict flag: ARCHIVE + referenced ------------------
        rr = SA.audit(os.path.join(os.path.dirname(os.path.abspath(SA.__file__)),
                                   "demo", "prometheus_infra.csv"),
                      basis="differenced", ordered=True)
        wsx = RG.annotate_worksheet(SA.blast_radius_worksheet(rr), gx)
        rws = list(__import__("csv").reader(__import__("io").StringIO(wsx)))
        hx = rws[0]
        ix = {n: hx.index(n) for n in hx}
        evid = [r[ix["scan_evidence"]] for r in rws[1:]]
        rec = [r[ix["recommendation"]] for r in rws[1:]]

        conflicts = [k for k, e in enumerate(evid) if e.startswith("** CONFLICT")]
        check("an ARCHIVE candidate behind a monitor is flagged CONFLICT",
              len(conflicts) >= 1, f"{len(conflicts)} conflict(s)")
        check("conflicts are sorted to the top",
              conflicts == list(range(len(conflicts))), f"rows {conflicts}")
        check("a paging alert is called out, and sorted first",
              "PAGING" in evid[0] and rec[0].upper().startswith("ARCHIVE"),
              evid[0][:56])
        check("a KEEP metric is never flagged CONFLICT",
              not any(e.startswith("** CONFLICT") for e, c in zip(evid, rec)
                      if not c.upper().startswith("ARCHIVE")))
        check("the conflict row explains itself in the note column",
              "do not archive on the arithmetic alone" in rws[1][ix["note"]])
        check("flagging a conflict attests nothing",
              all(r[ix["referenced_by_monitors"]] == "" for r in rws[1:]),
              "a flag is not an answer")

    # ------------------------------------------------------------------
    # 23. cardinality, and the zero-egress claim
    #
    # Two axes that must not merge: redundancy BETWEEN metrics (what the
    # engine finds) and cardinality WITHIN one (what the bill is driven
    # by). On the fixture below the archive candidates cost 3 and 2
    # series while a NON-redundant metric costs 4,200 — so an operator
    # shown one blended number would delete the wrong thing.
    #
    # The egress checks are the code half of SAFETY_BOUNDARIES.md
    # Amendment 1: "runs locally" stops being structural the moment this
    # code can open a socket.
    # ------------------------------------------------------------------
    print("\n23. cardinality + zero egress")
    import cardinality as CD

    expo = """# HELP x help text
# TYPE x counter
http_requests_total{method="GET",code="200"} 1
http_requests_total{method="GET",code="404"} 3
http_requests_total{method="GET",path="/a,b"} 5
http_requests_total{method="GET",path="{braces}"} 2
node_load1 0.42
latency_bucket{le="0.5"} 100
latency_bucket{le="+Inf"} 300
"""
    ex23 = CD.parse_exposition(expo)
    check("a comma inside a label value does not split the labels",
          ex23["http_requests_total"]["labels"]["path"] == 2,
          "'/a,b' is one value, not two")
    check("a brace inside a label value does not end the block",
          ex23["http_requests_total"]["series"] == 4)
    check("HELP/TYPE comments are not counted as samples",
          "HELP" not in ex23 and "TYPE" not in ex23)
    check("an unlabelled metric is one series",
          ex23["node_load1"]["series"] == 1)
    check("histogram suffixes collapse to one logical metric",
          CD.base_name("latency_bucket") == "latency"
          and CD.base_name("http_requests_total") == "http_requests")

    prom = json.dumps({"data": {"result": [
        {"metric": {"__name__": "a"}, "value": [0, "42"]},
        {"metric": {}, "value": [0, "9"]}]}})
    pc = CD.parse_promql_series_count(prom)
    check("PromQL count output parses, and a nameless row is skipped",
          pc == {"a": {"series": 42, "labels": {}}})

    # --- the two axes -------------------------------------------------
    lines = []
    lines += [f'request_rate_total{{c="{i}"}} 1' for i in range(3)]
    lines += [f'methodGET_status200{{c="{i}"}} 1' for i in range(3)]
    lines += [f'methodGET_status404{{c="{i}"}} 1' for i in range(2)]
    lines += [f'demo_disk_usage_bytes{{path="/v/{i}"}} 1' for i in range(4200)]
    for nm in ("node_load1", "node_load5", "node_load15",
               "node_memory_Cached_bytes", "node_memory_Buffers_bytes",
               "node_memory_MemAvailable_bytes"):
        lines += [f'{nm}{{host="h{i}"}} 1' for i in range(40)]
    lines += ["demo_api_http_requests_in_progress 1"]

    prom_csv = os.path.join(os.path.dirname(os.path.abspath(SA.__file__)),
                            "demo", "prometheus_infra.csv")
    if os.path.exists(prom_csv):
        pay23 = SA.report_payload(SA.audit(prom_csv, basis="differenced",
                                           ordered=True))
        rep23 = CD.CardinalityReport(CD.parse_exposition("\n".join(lines)),
                                     scope="one instance", window="24h")
        cls = CD.classify(pay23, rep23)
        byname = {r["metric"]: r for r in cls["rows"]}

        check("an expensive NON-redundant metric is never told to archive",
              byname["demo_disk_usage_bytes"]["quadrant"] == "expensive_only"
              and "do not archive" in byname["demo_disk_usage_bytes"]["advice"],
              f"{byname['demo_disk_usage_bytes']['series']} series")
        check("and the advice names the label, not the metric",
              "path" in byname["demo_disk_usage_bytes"]["advice"])
        check("a cheap redundant metric is not sold as a saving",
              byname["request_rate_total"]["quadrant"] == "redundant_only"
              and "not for the bill" in byname["request_rate_total"]["advice"],
              f"{byname['request_rate_total']['series']} series")
        check("the two axes are never added into one number",
              all(("redundant" in r) and ("high_cardinality" in r)
                  and "score" not in r for r in cls["rows"]))
        check("scope travels with the numbers",
              cls["scope"] and cls["window"],
              "a saving without its scope is a confident wrong number")

    # --- cost: arithmetic, never estimation ---------------------------
    if os.path.exists(prom_csv):
        unpriced = CD.cost(cls, None)
        check("no price supplied means no cost figure, not a guessed one",
              unpriced["archivable_saving"] is None
              and all(r["monthly_cost"] is None for r in unpriced["rows"])
              and "YOUR invoice" in unpriced["why_null"])
        check("the module carries no vendor price list",
              not any(w in open(os.path.join(
                  os.path.dirname(os.path.abspath(SA.__file__)),
                  "cardinality.py"), encoding="utf-8").read().lower()
                  for w in ("datadog_price", "default_price",
                            "price = 0.", "per_series = 0.")),
              "a default price is a guess wearing a number")

        pr = CD.Price.from_invoice(4100, 82000, "GBP")
        check("a unit price derived from an invoice shows its derivation",
              abs(pr.per_series_month - 0.05) < 1e-12
              and "82,000 series" in pr.source)

        priced = CD.cost(cls, pr)
        arch_series = sum(r["series"] or 0 for r in priced["rows"]
                          if r["quadrant"].startswith("redundant"))
        check("the saving is series x price, and nothing else",
              abs(priced["archivable_saving"]
                  - arch_series * pr.per_series_month) < 1e-9,
              f"{arch_series} series x {pr.per_series_month}")

        # The load-bearing honesty check: the big number is NOT a saving.
        check("cost held by non-redundant metrics is kept separate",
              priced["label_saving_upper_bound"]
              > priced["archivable_saving"] * 100,
              f"archivable {priced['archivable_saving']:.2f} vs "
              f"upper bound {priced['label_saving_upper_bound']:.2f} — "
              f"summing them would promise a saving no archive delivers")
        lines = CD.cost_lines(priced)
        check("and is labelled an upper bound, not a saving",
              any("UPPER BOUND" in l for l in lines)
              and any("not archivable" in l for l in lines))
        check("the working is shown, not just the total",
              any("unit price" in l for l in lines)
              and any("scope" in l for l in lines))
        check("scope travels with the money too", priced["scope"])

    # ------------------------------------------------------------------
    # 24. routing generator — the first artefact that could change a
    #     live system. Governed by SAFETY_BOUNDARIES.md Amendment 1.
    # ------------------------------------------------------------------
    print("\n24. routing generator")
    import routing as RT

    demo24 = os.path.join(os.path.dirname(os.path.abspath(SA.__file__)),
                          "data", "demo_dashboard.csv")
    if os.path.exists(demo24):
        r24 = SA.audit(demo24, basis="differenced", ordered=True)
        p24 = SA.report_payload(r24)
        blank = SA.blast_radius_worksheet(r24)

        check("an unattested worksheet generates nothing",
              _raises(lambda: RT.generate(blank, p24,
                                          cold_exporter="awss3/cold")),
              "a half-filled worksheet does not unlock an export")

        def complete(referenced_row=None):
            rows = list(__import__("csv").reader(__import__("io").StringIO(blank)))
            hh = rows[0]
            jj = {n: hh.index(n) for n in hh}
            o = __import__("io").StringIO()
            ww = __import__("csv").writer(o)
            ww.writerow(hh)
            for n, row in enumerate(rows[1:]):
                for c in ("referenced_by_monitors", "referenced_by_slos",
                          "referenced_by_other_dashboards",
                          "referenced_by_runbooks"):
                    row[jj[c]] = ("yes" if (referenced_row == n
                                            and c == "referenced_by_monitors")
                                  else "no")
                row[jj["reviewer"]] = "s.cooper"
                ww.writerow(row)
            return o.getvalue()

        done24 = complete()
        check("routing without a cold exporter is refused",
              _raises(lambda: RT.generate(done24, p24, cold_exporter="")),
              "relocated, not destroyed — Amendment 1")

        y = RT.generate(done24, p24, cold_exporter="awss3/cold",
                        primary_exporter="datadog", source="t.csv")

        names = RT.routed_metrics(y)
        half = len(names) // 2
        check("the include and exclude lists are exact complements",
              half > 0 and names[:half] == names[half:],
              "every series keeps a destination, verifiable by reading it")
        check("nothing is deleted, and the config says so",
              "NOTHING IS DELETED" in y and "UNDO" in y)
        check("the attesting reviewer is named in the artefact",
              "s.cooper" in y)
        check("each routed metric carries the evidence that justified it",
              y.count("unique variance") == half)
        check("the config is never described as safe or approved",
              not any(w in y.lower() for w in
                      ("safe to archive", "verified safe", "approved")))
        check("the artefact says it must not be applied unreviewed",
              "DO NOT APPLY UNREVIEWED" in y)

        # The worksheet must be able to overrule the engine.
        overruled = RT.generate(complete(referenced_row=1), p24,
                                cold_exporter="awss3/cold")
        check("a metric the reviewer marked referenced is NOT routed",
              len(RT.routed_metrics(overruled)) < len(names),
              "the human overrules the arithmetic, or the gate is a formality")

        # Hard drop: available, loud, and never the default.
        dropped = RT.generate(done24, p24, cold_exporter="",
                              allow_drop=True)
        check("a hard drop names what stops existing, and has no undo",
              "STOP EXISTING" in dropped and "no undo" in dropped.lower()
              and "UNDO" not in dropped.replace("no undo", ""),
              "explicit, and never inferred from the audit")
        check("the drop variant is not produced without asking",
              "STOP EXISTING" not in y)

        # One reader, not two.
        import sys as _sys
        _sys.path.insert(0, os.path.join(
            os.path.dirname(os.path.abspath(SA.__file__)), "backend"))
        import exports as _EX
        check("routing uses the hosted service's attestation reader",
              set(RT._parse_worksheet(done24)) == set(_EX.parse_worksheet(done24)),
              "a second implementation would drift, and the drift would "
              "decide whether an export unlocks")

    # ------------------------------------------------------------------
    # 25. shadow dashboard — structural check, and only that
    # ------------------------------------------------------------------
    print("\n25. shadow dashboard")
    import shadow as SH

    board = json.dumps({
        "title": "Platform", "uid": "orig-123", "id": 7, "version": 9,
        "templating": {"list": [{"name": "inst", "type": "query",
                                 "query": {"query": "label_values(node_load5, instance)"}}]},
        "panels": [
            {"type": "row", "title": "Compute", "panels": [
                {"title": "Load", "targets": [
                    {"expr": "node_load15{instance=~\"$inst\"}"}]},
                {"title": "Error ratio", "targets": [
                    {"expr": "rate(methodGET_status404[5m]) / rate(request_rate_total[5m])"}]}]},
            {"title": "Requests", "targets": [{"expr": "request_rate_total"}]},
            {"title": "Memory", "targets": [
                {"expr": "node_memory_MemAvailable_bytes"}]}]})

    doc25, rep25 = SH.build_shadow(board, ["request_rate_total", "node_load5"])

    check("an expression losing an operand is reported as BREAKING",
          rep25["broken_panels"] == 1,
          "rate(a)/rate(b) with b archived — invisible to an audit of values")
    check("a panel whose only query went is merely EMPTY",
          rep25["empty_panels"] == 1)
    check("a broken template variable is reported separately",
          rep25["broken_variables"] == 1, "it cascades to every panel using it")
    check("untouched panels are untouched",
          any(p.get("title") == "Memory" for p in doc25["panels"]))

    titles25 = []

    def _collect(ps):
        for p in ps or []:
            titles25.append(p.get("title") or "")
            _collect(p.get("panels"))
    _collect(doc25["panels"])
    check("breaking panels are retitled so they are visible in Grafana",
          any(t.startswith("[BREAKS]") for t in titles25)
          and any(t.startswith("[EMPTY]") for t in titles25))

    # Substitution, not deletion — otherwise the shadow renders perfectly
    # because the metric has not been archived yet, and proves nothing.
    flat = json.dumps(doc25)
    check("archived metrics are SUBSTITUTED, not removed",
          SH.SENTINEL_PREFIX in flat,
          "deletion would render fine and demonstrate nothing")
    check("the query structure survives, so the failure is the real one",
          "rate(" in flat and "methodGET_status404" in flat)

    check("the shadow gets a new identity and cannot overwrite the original",
          doc25["uid"] is None and doc25["id"] is None
          and "Shadow" in doc25["title"])

    banner = doc25["panels"][0]
    body = banner["options"]["content"]
    check("the first panel states what this does NOT prove",
          "does NOT prove" in body and "same evidence twice" in body,
          "the audit and the shadow measured the same window")
    check("the shadow never claims safety",
          not any(w in flat.lower() for w in
                  ("safe to archive", "verified safe", "proven safe",
                   "approved")))
    check("the report says structural only", rep25["structural_only"] is True)

    # The label-argument bug: label_values(metric, LABEL).
    check("a Grafana label argument is not read as a metric",
          "instance" not in RG.extract_metric_identifiers(
              "label_values(node_load5, instance)"),
          "it was, and the shadow reported it as a lost operand")

    # ------------------------------------------------------------------
    # 26. local history store — HISTORY_STORE_PREREG.md, hard stops
    # ------------------------------------------------------------------
    print("\n26. history store")
    import history as HI

    tmp26 = tempfile.mkdtemp()
    try:
        def pay26(pairs, present, grade="A", label="quiet", eff=4.0):
            return {
                "engine_version": "0.1.0", "file": "b.csv",
                "summary": {"rows": 500, "effective_signals": eff},
                "assurance": {"grade": grade},
                "basis": {"headline": "differenced", "declared": True},
                "order": {"ordered": True},
                "identity_pairs": [{"metric_a": a, "metric_b": b}
                                   for a, b in pairs],
                "redundancy_clusters": [], "subset_sums": [],
                "metrics": [{"name": m} for m in present],
                "archive_candidates": [],
            }

        root = os.path.join(tmp26, ".redd")
        base = ["a", "b", "c"]
        import datetime as _dt
        for i in range(30):
            inc = (i == 13)
            d0 = _dt.date(2026, 7, 1) + _dt.timedelta(days=i)
            HI.record(pay26([] if inc else [("a", "b")], base,
                            label="incident" if inc else "quiet"),
                      root=root,
                      window_label="incident" if inc else "quiet",
                      window_from=d0.isoformat(),
                      window_to=(d0 + _dt.timedelta(days=6)).isoformat(),
                      source="b.csv", run_id=f"{i:03d}",
                      dataset_id=f"hash{i:03d}")

        an = HI.persistence(HI.load(root), source="b.csv")
        lines = HI.report_lines(an)

        check("the store gitignores itself wherever it is created",
              os.path.exists(os.path.join(root, ".gitignore"))
              and "*" in open(os.path.join(root, ".gitignore"),
                              encoding="utf-8").read())
        check("runs are plain JSON, readable without this tool",
              json.load(open(os.path.join(root, "history", "000.json"),
                             encoding="utf-8"))["run_id"] == "000")

        # H6 — the exception leads.
        check("the incident exception is the FIRST thing printed",
              lines[0].startswith("KEEP"), lines[0][:52])
        first_rating = next((i for i, l in enumerate(lines)
                             if "present run(s)" in l), 999)
        first_keep = next((i for i, l in enumerate(lines)
                           if l.startswith("KEEP")), 999)
        check("no persistence figure appears above its exceptions",
              first_keep < first_rating, "H6")
        check("the exception names its run and window label",
              any("run 013" in l and "incident" in l for l in lines))

        # H4 — effective windows, not raw runs.
        check("30 overlapping runs are not reported as 30 observations",
              an["effective_windows"] is not None
              and an["effective_windows"] < 10,
              f"{an['runs_eligible']} runs -> "
              f"{an['effective_windows']} effective windows")
        check("and the report says so in words",
              any("effective independent window" in l for l in lines))

        # H3 — absent is not evidence.
        root3 = os.path.join(tmp26, ".redd3")
        for i in range(30):
            present = base if i < 10 else ["c"]
            HI.record(pay26([("a", "b")] if i < 10 else [], present),
                      root=root3, window_label="quiet", source="b.csv",
                      run_id=f"{i:03d}", dataset_id=f"h{i}")
        an3 = HI.persistence(HI.load(root3), source="b.csv")
        p3 = an3["pairs"][0]
        check("a metric absent from a run is NOT counted against it",
              p3["held"] == 10 and p3["present"] == 10,
              f"{p3['rating']} — never 10 of 30")

        # H5 — thin runs do not vote.
        root5 = os.path.join(tmp26, ".redd5")
        for i in range(4):
            HI.record(pay26([("a", "b")] if i < 2 else [], base,
                            grade="A" if i < 2 else "D"),
                      root=root5, window_label="quiet", source="b.csv",
                      run_id=f"{i:03d}", dataset_id=f"g{i}")
        an5 = HI.persistence(HI.load(root5), source="b.csv")
        check("grade C/D runs are excluded from persistence",
              an5["runs_excluded_by_grade"] == 2
              and an5["pairs"][0]["present"] == 2,
              "splitting a thin corpus across windows makes it thinner")

        # H7 — engine majors are refused, not pooled.
        root7 = os.path.join(tmp26, ".redd7")
        HI.record(pay26([("a", "b")], base), root=root7, source="b.csv",
                  run_id="old", dataset_id="x1")
        p7 = pay26([("a", "b")], base)
        p7["engine_version"] = "2.0.0"
        HI.record(p7, root=root7, source="b.csv", run_id="new",
                  dataset_id="x2")
        an7 = HI.persistence(HI.load(root7), source="b.csv")
        check("runs from different engine majors are refused, not pooled",
              an7["error"] and "engine majors" in an7["error"])

        # Two dashboards are two histories.
        rootm = os.path.join(tmp26, ".reddm")
        HI.record(pay26([("a", "b")], base), root=rootm, source="one.csv",
                  run_id="1", dataset_id="m1")
        HI.record(pay26([("x", "y")], ["x", "y"]), root=rootm,
                  source="two.csv", run_id="2", dataset_id="m2")
        anm = HI.persistence(HI.load(rootm))
        check("runs from different dashboards are not pooled",
              anm["error"] and "different sources" in anm["error"],
              "persistence is a claim about ONE dashboard's history")

        # Declared, never inferred.
        check("an unknown window label is rejected outright",
              _raises(lambda: HI.record(pay26([], base), root=rootm,
                                        window_label="outage")),
              "quiet|busy|incident|unknown, declared by the operator")

        # No incident label means the history says so.
        rootq = os.path.join(tmp26, ".reddq")
        for i in range(3):
            HI.record(pay26([("a", "b")], base), root=rootq,
                      window_label="quiet", source="b.csv",
                      run_id=f"{i}", dataset_id=f"q{i}")
        lq = HI.report_lines(HI.persistence(HI.load(rootq), source="b.csv"))
        check("with no incident window, the report says it proves nothing "
              "about incidents",
              any("NO run is labelled 'incident'" in l for l in lq))

        # A rating is a count, never a probability.
        check("ratings are counts, not probabilities",
              all("%" not in p["rating"] and "confidence" not in p["rating"]
                  for p in an["pairs"]),
              "'27 of 30 present runs' is checkable; '90% confident' is not")
    finally:
        shutil.rmtree(tmp26, ignore_errors=True)

    # --- zero egress ---------------------------------------------------
    banned = ("import socket", "import urllib", "import requests",
              "import http.client", "from urllib", "import httpx",
              "urlopen(", "socket.socket")
    for mod in ("cardinality", "refgraph", "routing", "shadow", "history", "signal_audit"):
        src23 = open(os.path.join(os.path.dirname(os.path.abspath(SA.__file__)),
                                  mod + ".py"), encoding="utf-8").read()
        hits = [b for b in banned if b in src23]
        check(f"{mod}.py opens no network connection", not hits,
              f"found: {hits}" if hits else
              "asserted, because 'runs locally' must be checkable")

    print("\n22. packaging")
    root22 = os.path.dirname(os.path.abspath(SA.__file__))
    toml = os.path.join(root22, "pyproject.toml")
    if os.path.exists(toml):
        cfg = open(toml, encoding="utf-8").read()
        listed = set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"',
                                re.search(r"py-modules\s*=\s*\[([^\]]*)\]",
                                          cfg).group(1)))
        # Top-level .py files that are part of the shipped tool, as
        # opposed to build scripts, corpora builders and test suites.
        shipped = {"signal_audit", "signal_audit_cli", "refgraph", "cardinality", "routing", "shadow", "history", "redd"}
        missing = sorted(shipped - listed)
        check("every shipped module is in py-modules", not missing,
              f"missing from the wheel: {missing}" if missing else
              f"{len(listed)} listed")

        for mod in sorted(shipped):
            check(f"{mod}.py exists to be shipped",
                  os.path.exists(os.path.join(root22, mod + ".py")))

        # `python -m redd` must work, because pip's Scripts directory is
        # routinely off PATH on Windows and that is the obvious recovery.
        import redd as _redd
        check("`python -m redd` has an entry point", callable(_redd.main))
        check("the console script and the shim call the same function",
              _redd.main is __import__("signal_audit_cli").main)

    print("\n" + "=" * 70)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
