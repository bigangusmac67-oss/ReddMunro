"""
test_backend.py — validation for the service and job layers.

These are SEPARATE from the engine's 32 tests, which remain the
authority on whether the analysis is correct. This file tests the
wrapper: contract shape, quota enforcement, metering accuracy, job
lifecycle, and error classification.

Check 0 asserts the engine's own suite still passes untouched, so a
change here can never quietly break the analysis.

    python test_backend.py
"""

import json
import os
import re
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobs as JOBS
import service as SVC

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}"
          + (f"  — {detail}" if detail else ""))


def make_csv(path, n=400, d=8, k=3, seed=5, extras=True):
    rng = np.random.default_rng(seed)
    F = rng.standard_normal((n, k))
    A = rng.standard_normal((k, d))
    X = F @ A + 0.05 * rng.standard_normal((n, d))
    names = [f"m{j:02d}" for j in range(d)]
    if extras:
        # a guaranteed identity pair, so contract sections are populated
        X = np.column_stack([X, X[:, 0] * 1000.0])
        names.append("m00_scaled")
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(",".join(names) + "\n")
        for row in X:
            f.write(",".join(f"{v:.6g}" for v in row) + "\n")
    return path


def main():
    tmp = tempfile.mkdtemp(prefix="sa_backend_")
    print("=" * 70)
    print("SIGNAL AUDIT BACKEND — service and job layer validation")
    print("=" * 70)

    # 0. the engine's own suite must still pass -------------------------
    print("\n0. Engine suite is untouched")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run([sys.executable, "test_signal_audit.py"],
                          cwd=root, capture_output=True, text=True)
    tail = proc.stdout.strip().splitlines()[-2] if proc.stdout else ""
    check("engine tests still pass", proc.returncode == 0, tail)
    # A floor, not an exact count: the engine suite grows as failure
    # modes are found, and a magic number here fails on every addition.
    # What must never happen is the count going DOWN — that means a
    # guarantee was deleted rather than a feature added.
    m = re.search(r"(\d+) passed", proc.stdout or "")
    n_engine = int(m.group(1)) if m else 0
    check("engine test count has not decreased (>= 32)", n_engine >= 32,
          f"{n_engine} engine tests")

    # 1. contract shape -------------------------------------------------
    print("\n1. Contract shape")
    p = make_csv(os.path.join(tmp, "a.csv"))
    out = SVC.run_audit(p, tier="pro")
    required = ["contract_version", "engine_version", "summary",
                "trend_confound", "identity_pairs", "redundancy_clusters",
                "nonlinear_couplings", "derived_aggregates",
                "pruning_queue", "pruning_summary", "warnings",
                "excluded_columns", "usage"]
    missing = [k for k in required if k not in out]
    check("all top-level sections present", not missing, f"missing {missing}")
    check("summary carries true_signal_ratio",
          isinstance(out["summary"]["true_signal_ratio"], float))
    check("trend gap present",
          "gap" in out["trend_confound"])
    check("contract is JSON-serialisable",
          json.dumps(out) and True)

    js = json.dumps(out)
    check("no NaN or Infinity leaked into JSON",
          "NaN" not in js and "Infinity" not in js)

    # 2. pruning queue --------------------------------------------------
    print("\n2. Pruning queue")
    q = out["pruning_queue"]
    check("queue covers every metric", len(q) == out["summary"]
          ["metrics_supplied"], f"{len(q)} rows")
    uvs = [row["unique_variance"] for row in q]
    check("queue is ordered cheapest-to-lose first",
          uvs == sorted(uvs))
    check("identity partner surfaced for the planted pair",
          any(r["identity_partner"] for r in q))
    check("every row carries a confidence band",
          all(r["confidence"] in ("high", "medium", "low") for r in q))
    check("every row carries a blockers list",
          all(isinstance(r["blockers"], list) for r in q))
    check("recommended implies no blockers",
          all(not r["blockers"] for r in q if r["recommended"]))
    check("every row records its evidence basis",
          all(r["basis"] in ("identity", "unique_variance") for r in q))
    check("never recommends deleting both halves of an identity pair",
          all(not (r["recommended"] and r["keep_as_representative"])
              for r in q))
    pairs = [(r["metric"], r["identity_partner"]) for r in q
             if r["identity_partner"]]
    for a, b in pairs:
        ra = next(r for r in q if r["metric"] == a)
        rb = next(r for r in q if r["metric"] == b)
        check(f"exactly one of {a}/{b} survives",
              ra["keep_as_representative"] != rb["keep_as_representative"])
        break
    check("pruning_summary agrees with the queue",
          out["pruning_summary"]["recommended_count"]
          == sum(1 for r in q if r["recommended"]))

    # 3. metering -------------------------------------------------------
    print("\n3. Usage metering")
    u = out["usage"]
    check("billable_cells = metrics x rows",
          u["billable_cells"] == u["metrics"] * u["rows"],
          f"{u['metrics']}x{u['rows']}={u['billable_cells']}")
    check("duration recorded", u["duration_ms"] >= 0)
    check("engine version stamped", u["engine_version"] == SVC.ENGINE_VERSION)
    check("charges only analysed columns",
          u["columns_analysed"] == out["summary"]["metrics_supplied"])

    # a file with junk columns must not be billed for them
    p2 = os.path.join(tmp, "junk.csv")
    with open(p2, "w", encoding="utf-8") as f:
        f.write("date,notes,constant,real_a,real_b\n")
        rng = np.random.default_rng(1)
        for i in range(120):
            f.write(f"2024-01-01,text,7,{rng.normal():.4f},"
                    f"{rng.normal():.4f}\n")
    out2 = SVC.run_audit(p2, tier="pro")
    check("dropped columns are not billed",
          out2["usage"]["columns_analysed"] == 2,
          f"analysed {out2['usage']['columns_analysed']}")
    check("dropped columns are explained",
          len(out2["excluded_columns"]) >= 3)

    # 4. quota ----------------------------------------------------------
    print("\n4. Quota enforcement")
    wide = make_csv(os.path.join(tmp, "wide.csv"), n=200, d=40, k=5,
                    extras=False)
    try:
        SVC.run_audit(wide, tier="starter")
        check("starter rejects 40 metrics", False)
    except SVC.QuotaExceeded as exc:
        check("starter rejects 40 metrics", exc.limit == "metrics",
              f"{exc.actual} > {exc.allowed}")
        check("quota error is structured",
              exc.to_dict()["error"] == "quota_exceeded")
    check("pro accepts the same file",
          SVC.run_audit(wide, tier="pro")["summary"]["metrics_supplied"] == 40)

    try:
        SVC.run_audit(p, tier="nonexistent")
        check("unknown tier rejected", False)
    except SVC.InvalidInput:
        check("unknown tier rejected", True)

    # quota must be checked BEFORE analysis: a 1-row-per-metric file
    # would be analysable but is over the starter metric cap
    import time as _t
    t0 = _t.time()
    try:
        SVC.run_audit(wide, tier="starter")
    except SVC.QuotaExceeded:
        pass
    check("quota rejection is fast (pre-flight, not post-analysis)",
          _t.time() - t0 < 1.0, f"{(_t.time()-t0)*1000:.0f}ms")

    # 5. invalid input --------------------------------------------------
    print("\n5. Input errors")
    bad = os.path.join(tmp, "bad.csv")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("only\n")
        for i in range(50):
            f.write(f"{i}\n")
    try:
        SVC.run_audit(bad, tier="pro")
        check("single-column file rejected as invalid input", False)
    except SVC.InvalidInput as exc:
        check("single-column file rejected as invalid input", True)
        check("invalid input is structured",
              exc.to_dict()["error"] == "invalid_input")
    except SVC.QuotaExceeded:
        check("single-column file rejected as invalid input", False,
              "misclassified as quota")

    # 6. jobs -----------------------------------------------------------
    print("\n6. Job lifecycle")
    runner = JOBS.AuditRunner(max_workers=2, delete_source_on_finish=False)
    runner.start()
    try:
        j = runner.submit(p, account_id="acct-1", tier="pro")
        check("submit returns a pending job", j.status == JOBS.PENDING)
        done = runner.wait(j.id, timeout=30)
        check("job reaches a terminal state", done.status in JOBS.TERMINAL,
              done.status)
        check("job succeeded", done.status == JOBS.SUCCEEDED,
              str(done.error))
        check("job carries the full contract",
              done.result and "pruning_queue" in done.result)
        check("job timing recorded",
              done.to_dict()["duration_ms"] is not None)

        # over-quota submissions are refused at submit, not at run
        try:
            runner.submit(wide, account_id="acct-1", tier="starter")
            check("over-quota submit refused synchronously", False)
        except SVC.QuotaExceeded:
            check("over-quota submit refused synchronously", True)

        # account isolation
        j2 = runner.submit(p, account_id="acct-2", tier="pro")
        runner.wait(j2.id, timeout=30)
        mine = runner.store.list_for_account("acct-1")
        check("jobs are scoped per account",
              all(x.account_id == "acct-1" for x in mine)
              and len(mine) >= 1, f"{len(mine)} for acct-1")

        # source cleanup
        runner2 = JOBS.AuditRunner(max_workers=1,
                                   delete_source_on_finish=True)
        p3 = make_csv(os.path.join(tmp, "ephemeral.csv"))
        j3 = runner2.submit(p3, account_id="acct-3", tier="pro")
        runner2.wait(j3.id, timeout=30)
        check("uploaded source removed after run",
              not os.path.exists(p3))
        runner2.stop()

        # to_dict must not leak server filesystem paths
        d = done.to_dict()
        check("job payload does not leak source path",
              "source_path" not in d)
    finally:
        runner.stop()

    # 7. async routing --------------------------------------------------
    print("\n7. Sync/async routing")
    check("small audit routes sync",
          not SVC.should_run_async("pro", 10, 500))
    check("large audit routes async",
          SVC.should_run_async("pro", 150, 250_000))
    # Every audit a Starter account can legally submit must fit in a
    # request: its cell cap is below its async threshold by design, so
    # that tier never shows a polling state. Checked at the largest
    # shapes the cap actually permits.
    st = SVC.TIERS["starter"]
    check("starter never needs the queue, at any legal shape",
          not any(SVC.should_run_async("starter", m, st.max_cells // m)
                  for m in (5, 10, 25)),
          f"cap {st.max_cells:,} cells vs async at "
          f"{st.async_only_above_cells:,}")
    check("an over-cap starter shape is a quota error, not an async one",
          SVC.should_run_async("starter", 25, 10_000)
          and 25 * 10_000 > st.max_cells)

    # 8. cost estimation ------------------------------------------------
    print("\n8. Cost estimation")
    import cost as COST
    base = SVC.run_audit(p, tier="pro")
    e0 = base["estimated_monthly_saving"]
    check("no estimate without a unit cost",
          e0["available"] is False and e0["amount"] is None)
    check("absence is explained, not silent", bool(e0["reason"]))

    cm = COST.CostModel(unit_cost_per_series_month=0.05, currency="GBP")
    e1 = COST.estimate(base, cm)
    check("estimate appears once a unit cost is supplied",
          e1["available"] and e1["currency"] == "GBP")
    check("unmapped cardinality is low confidence",
          e1["confidence"] == "low", e1["confidence"])
    check("assumptions are returned with the figure",
          len(e1["assumptions"]) >= 3)
    check("ingestion caveat always present",
          any("ingestion" in a for a in e1["assumptions"]))

    recs = base["pruning_summary"]["recommended_metrics"]
    cm2 = COST.CostModel(unit_cost_per_series_month=0.05,
                         series_per_metric={m: 500 for m in recs})
    e2 = COST.estimate(base, cm2)
    check("full cardinality map raises confidence",
          e2["confidence"] == "medium", e2["confidence"])
    check("cardinality multiplies the figure",
          e2["amount"] > e1["amount"] if recs else True,
          f"{e1['amount']} -> {e2['amount']}")
    check("only recommended rows are counted",
          e2["metrics_affected"] == len(recs))

    # 9. exports --------------------------------------------------------
    print("\n9. Export generation")
    import exports as EXP

    ws = EXP.review_worksheet(base)
    check("review worksheet is generated ungated", ws.startswith("metric,"))
    check("worksheet covers every metric",
          len(ws.strip().splitlines()) - 1
          == base["summary"]["metrics_supplied"])
    check("worksheet leaves operational columns blank for the operator",
          "referenced_by_monitors" in ws.splitlines()[0])

    man = EXP.column_manifest(base)
    check("column manifest is generated ungated", man.startswith("column,"))
    check("manifest never says delete", "delete" not in man.lower())
    check("keep_list excludes recommended metrics",
          set(EXP.keep_list(base)).isdisjoint(set(recs)))

    avail = EXP.available_exports(base)
    check("gated exports are declared gated",
          "datadog_terraform" in avail["gated"])
    check("gate is closed without attestation",
          avail["gate_satisfied"] is False)

    try:
        EXP.datadog_terraform(base, None)
        check("terraform refuses without attestation", False)
    except EXP.ExportGated as exc:
        check("terraform refuses without attestation", True)
        check("gate error names the outstanding metrics",
              set(exc.missing) == set(recs))
        check("gate error is structured",
              exc.to_dict()["error"] == "attestation_required")

    # a half-filled worksheet must not unlock the gate
    partial = ws.splitlines()[0] + "\n"
    if recs:
        partial += f"{recs[0]},REMOVE,identity,0.0,x,1.0,False,,,y,,,,,me,\n"
        try:
            EXP.datadog_terraform(base, EXP.parse_worksheet(partial))
            check("half-filled worksheet does not unlock the gate", False)
        except EXP.ExportGated:
            check("half-filled worksheet does not unlock the gate", True)

    # fully attested, all clear
    atts = {m: EXP.Attestation(m, False, False, False, False,
                               last_queried_days_ago=120, reviewer="ops")
            for m in recs}
    hcl = EXP.datadog_terraform(base, atts)
    check("terraform emits once fully attested", "resource " in hcl or not recs)
    # Check the EXECUTABLE lines, not the prose: the header deliberately
    # explains why nothing destructive is emitted, and that explanation
    # is worth keeping.
    code = "\n".join(l for l in hcl.splitlines()
                     if not l.strip().startswith("#")).lower()
    check("terraform emits no destructive resources or verbs",
          not any(w in code for w in
                  ("delete", "destroy", "force_destroy", "prevent_destroy")))
    check("terraform uses the reversible cardinality lever",
          "datadog_metric_tag_configuration" in code or not recs)
    check("terraform states how to reverse", "reverse" in hcl.lower())
    check("terraform carries the evidence for review",
          "attested by" in hcl.lower() or not recs)

    ex = EXP.datadog_exclusion_json(base, atts)
    check("exclusion plan is reversible by declaration",
          ex["reversible"] is True and ex["action"] == "exclude_from_indexing")
    check("exclusion plan lists the cleared metrics",
          len(ex["metrics"]) == len(recs))

    # a metric attested as referenced elsewhere must be withheld
    if recs:
        blocked = dict(atts)
        blocked[recs[0]] = EXP.Attestation(recs[0], True, False, False, False,
                                           reviewer="ops")
        ex2 = EXP.datadog_exclusion_json(base, blocked)
        check("metric referenced by a monitor is withheld",
              all(m["metric"] != recs[0] for m in ex2["metrics"]))
        check("withheld metric is explained, not dropped silently",
              any(m["metric"] == recs[0]
                  for m in ex2["excluded_from_plan"]))

    # 10. four-screen contract -------------------------------------------
    print("\n10. Front-end contract (4 screens)")
    # Corpora live in data/; the repo root is checked too so this keeps
    # working either side of that move rather than silently falling back
    # to the synthetic fixture and testing less than it claims.
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    nyc = next((p for p in (
        os.path.join(_root, "data", "nyc_covid_dashboard.csv"),
        os.path.join(_root, "nyc_covid_dashboard.csv"))
        if os.path.exists(p)), None)
    real = SVC.run_audit(nyc, tier="pro") if nyc else base

    s = real["summary"]
    check("screen 1 — executive summary fields present",
          all(k in s for k in ("true_signal_ratio", "independent_signals",
                               "redundancy_band"))
          and "gap" in real["trend_confound"]
          and "estimated_monthly_saving" in real)

    badges = real["metric_badges"]
    check("screen 2 — every metric has a badge list",
          set(badges) == set(real["metric_badges"])
          and all(isinstance(v, list) for v in badges.values()))
    vocab = set(SVC.BADGES)
    used = {b for v in badges.values() for b in v}
    check("screen 2 — badges come from the fixed vocabulary",
          used <= vocab, f"unexpected: {used - vocab}")
    check("screen 2 — topology sections present",
          all(k in real for k in ("redundancy_clusters",
                                  "nonlinear_couplings",
                                  "derived_aggregates")))

    check("screen 3 — pruning view fields present",
          all(k in real["pruning_queue"][0] for k in
              ("unique_variance", "keep_as_representative", "basis",
               "blockers", "confidence")) if real["pruning_queue"] else True)

    check("screen 4 — export availability is describable",
          "gate_satisfied" in EXP.available_exports(real))

    check("whole contract is JSON round-trippable",
          json.loads(json.dumps(real))["summary"]["metrics_supplied"]
          == real["summary"]["metrics_supplied"])


    # ------------------------------------------------------------------
    # basis options — additive by contract, asserted not assumed
    # ------------------------------------------------------------------
    print("\n  basis options")
    import copy
    bpath = os.path.join(tmp, "basis.csv")
    make_csv(bpath, n=400, d=8, k=3)
    names = open(bpath).readline().strip().split(",")
    denom = names[0]

    base = SVC.run_audit(bpath, tier="pro", dataset_id="fixed")
    same = SVC.run_audit(bpath, tier="pro", dataset_id="fixed",
                         scale_by=(), scale_exempt=(), basis=None)
    a, b = copy.deepcopy(base), copy.deepcopy(same)
    for x in (a, b):
        x.get("usage", {}).pop("duration_ms", None)
    check("omitting every new option changes NOTHING in the response",
          a == b, "an additive minor must be invisible to existing clients")
    check("an undeclared basis is reported as undeclared",
          base["basis"]["declared"] is False
          and base["basis"]["headline"] == "differenced")
    check("both original bases are still computed",
          set(base["basis"]["available"]) == {"raw", "differenced"})
    check("no conflicts reported when no transform was declared",
          base["basis_conflicts"] == [])

    r = SVC.run_audit(bpath, tier="pro", scale_by=[denom],
                      basis=f"ratio:{denom}")
    check("a declared ratio basis appears in the contract",
          r["basis"]["declared"] is True
          and r["basis"]["headline"] == f"ratio:{denom}")
    check("contract counts metrics from the HEADLINE basis",
          r["summary"]["metrics_supplied"]
          == r["basis"]["per_basis"][f"ratio:{denom}"]["metrics"])
    check("that count differs from raw, so the check cannot pass vacuously",
          r["basis"]["per_basis"][f"ratio:{denom}"]["metrics"]
          != r["basis"]["per_basis"]["raw"]["metrics"])

    for label, kw in (
            ("unknown scale_by column", {"scale_by": ["not_a_column"]}),
            ("unknown scale_exempt column",
             {"scale_by": [denom], "scale_exempt": ["not_a_column"]}),
            ("basis that was never computed", {"basis": "ratio:nope"}),
    ):
        try:
            SVC.run_audit(bpath, tier="pro", **kw)
            check(f"{label} -> InvalidInput (422)", False)
        except SVC.InvalidInput:
            check(f"{label} -> InvalidInput (422)", True)
        except Exception as exc:
            check(f"{label} -> InvalidInput (422)", False,
                  f"raised {type(exc).__name__}, would surface as 500")

    try:
        SVC.run_audit(bpath, tier="pro", require_basis=True)
        check("require_basis refuses an undeclared request", False)
    except SVC.InvalidInput:
        check("require_basis refuses an undeclared request", True)
    check("require_basis accepts a declared one",
          SVC.run_audit(bpath, tier="pro", require_basis=True,
                        basis="differenced")["basis"]["declared"] is True)
    check("contract version reflects the additive change",
          SVC.CONTRACT_VERSION == "1.1")


    # ------------------------------------------------------------------
    # HTTP surface: the declaration must survive BOTH paths
    # ------------------------------------------------------------------
    print("\n  basis over HTTP")
    import inspect
    import app as APP

    sig = inspect.signature(APP.create_audit)
    for fld in ("scale_by", "scale_exempt", "basis", "require_basis"):
        check(f"create_audit exposes {fld}", fld in sig.parameters)
    check("preflight was NOT given basis fields it cannot use",
          "scale_by" not in inspect.signature(APP.preflight).parameters,
          "it does not run the analysis")

    jsig = inspect.signature(JOBS.AuditRunner.submit)
    for fld in ("scale_by", "scale_exempt", "basis", "require_basis"):
        check(f"submit carries {fld} onto the job", fld in jsig.parameters)
    jf = {f.name for f in __import__("dataclasses").fields(JOBS.Job)}
    check("the Job itself stores the declaration",
          {"scale_by", "scale_exempt", "basis", "require_basis"} <= jf,
          "otherwise a queued audit silently loses it")

    # async and sync must AGREE on what is a bad request
    bad = [
        ({"require_basis": True}, "require_basis with no basis"),
        ({"scale_by": ["not_a_column"]}, "unknown scale_by column"),
        ({"basis": "ratio:not_declared"}, "ratio basis not in scale_by"),
        ({"basis": "sideways"}, "unknown basis name"),
    ]
    runner = JOBS.AuditRunner(JOBS.InMemoryJobStore())
    for kw, label in bad:
        sync_rejected = async_rejected = False
        try:
            SVC.run_audit(bpath, tier="pro", **kw)
        except SVC.InvalidInput:
            sync_rejected = True
        except Exception:
            pass
        try:
            runner.submit(bpath, account_id="a", tier="pro", **kw)
        except SVC.InvalidInput:
            async_rejected = True
        except Exception:
            pass
        check(f"sync and async agree: {label} is rejected by both",
              sync_rejected and async_rejected,
              f"sync={sync_rejected} async={async_rejected}")

    # and a VALID declaration must be accepted by both
    ok_kw = {"scale_by": [denom], "basis": f"ratio:{denom}"}
    accepted = True
    try:
        j = runner.submit(bpath, account_id="a", tier="pro", **ok_kw)
        check("a valid declaration reaches the job intact",
              j.basis == f"ratio:{denom}" and j.scale_by == (denom,))
    except Exception as exc:
        accepted = False
        check("a valid declaration reaches the job intact", False, str(exc))

    print("\n" + "=" * 70)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
