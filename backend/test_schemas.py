"""
test_schemas.py — the models must describe what the service actually
emits, not what we intended it to emit.

This is the test that keeps `schemas.py` honest. It runs a REAL audit
through `service.build_contract` and validates the output against the
Pydantic models. A field added to the service but not the model, or a
type that drifts, fails here rather than silently disappearing from the
generated client and then from the frontend.

It also generates the OpenAPI document and checks the endpoints and the
badge enum survived the round trip.

    python test_schemas.py
"""

import json
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import schemas as S
import service as SVC

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}"
          + (f"  — {detail}" if detail else ""))


def make_csv(path, n=420, d=9, k=3, seed=11):
    rng = np.random.default_rng(seed)
    F = rng.standard_normal((n, k))
    A = rng.standard_normal((k, d))
    X = F @ A + 0.04 * rng.standard_normal((n, d))
    names = [f"m{j:02d}" for j in range(d)]
    X = np.column_stack([X, X[:, 0] * 1000.0])       # identity pair
    names.append("m00_scaled")
    base = rng.uniform(5, 95, (n, 3))
    X = np.column_stack([X, base, base.max(1)])      # max-aggregate
    names += ["sub_a", "sub_b", "sub_c", "worst_of"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        f.write(",".join(names) + "\n")
        for row in X:
            f.write(",".join(f"{v:.6g}" for v in row) + "\n")
    return path


def main():
    tmp = tempfile.mkdtemp(prefix="sa_schema_")
    print("=" * 70)
    print("SCHEMA CONFORMANCE — models vs what the service emits")
    print("=" * 70)

    # 1. real contract validates against the model ----------------------
    print("\n1. Live contract validates")
    p = make_csv(os.path.join(tmp, "rich.csv"))
    raw = SVC.run_audit(p, tier="pro")
    try:
        model = S.AuditContract.model_validate(raw)
        check("real audit output validates against AuditContract", True)
    except Exception as exc:
        check("real audit output validates against AuditContract", False,
              str(exc)[:300])
        model = None

    if model:
        # no silent loss: every service key must exist on the model
        missing = [k for k in raw if k not in model.model_dump()]
        check("model covers every field the service emits", not missing,
              f"unmodelled: {missing}")
        # round trip must not change the headline
        rt = model.model_dump()
        check("headline survives the round trip",
              rt["summary"]["true_signal_ratio"]
              == raw["summary"]["true_signal_ratio"])
        check("pruning queue length preserved",
              len(rt["pruning_queue"]) == len(raw["pruning_queue"]))

    # 2. the fixture actually exercises the interesting sections --------
    print("\n2. Fixture exercises the sections that matter")
    check("identity pairs present", len(raw["identity_pairs"]) >= 1,
          f"{len(raw['identity_pairs'])}")
    check("derived aggregate present", len(raw["derived_aggregates"]) >= 1,
          f"{len(raw['derived_aggregates'])}")
    check("badges assigned", any(raw["metric_badges"].values()))

    # 3. badge vocabulary is closed and matches the service -------------
    print("\n3. Badge vocabulary")
    model_badges = {b.value for b in S.Badge}
    service_badges = set(SVC.BADGES)
    check("schema and service agree on the badge vocabulary",
          model_badges == service_badges,
          f"schema-only {model_badges - service_badges}, "
          f"service-only {service_badges - model_badges}")
    used = {b for v in raw["metric_badges"].values() for b in v}
    check("emitted badges are all in the vocabulary",
          used <= model_badges, f"unexpected {used - model_badges}")

    # 4. cost model round trip ------------------------------------------
    print("\n4. Cost model")
    cm = S.CostModelIn(unit_cost_per_series_month=0.05, currency="GBP",
                       series_per_metric={"m00_scaled": 300})
    import cost as COST
    est = COST.estimate(raw, COST.model_from_request(cm.model_dump()))
    try:
        S.CostEstimate.model_validate(est)
        check("cost estimate validates against CostEstimate", True)
    except Exception as exc:
        check("cost estimate validates against CostEstimate", False,
              str(exc)[:200])
    check("null estimate also validates",
          S.CostEstimate.model_validate(
              COST.estimate(raw, None)).available is False)

    # 5. OpenAPI document generates -------------------------------------
    print("\n5. OpenAPI generation")
    try:
        import app as APP
        spec = APP.api.openapi()
        check("OpenAPI document generates", bool(spec))
        paths = set(spec.get("paths", {}))
        expected = {"/v1/health", "/v1/tiers", "/v1/audits",
                    "/v1/audits/preflight", "/v1/audits/{job_id}",
                    "/v1/usage",
                    "/v1/audits/{job_id}/exports",
                    "/v1/audits/{job_id}/exports/review-worksheet",
                    "/v1/audits/{job_id}/exports/column-manifest",
                    "/v1/audits/{job_id}/exports/terraform",
                    "/v1/audits/{job_id}/exports/datadog-exclusions",
                    "/v1/audits/{job_id}/cost-estimate"}
        missing_paths = expected - paths
        check("every endpoint is documented", not missing_paths,
              f"missing {sorted(missing_paths)}" if missing_paths
              else f"{len(paths)} paths")
        comps = spec.get("components", {}).get("schemas", {})
        check("AuditContract is in the components", "AuditContract" in comps)
        check("Badge enum survived into the spec",
              "Badge" in comps and
              set(comps["Badge"].get("enum", [])) == model_badges)
        check("error responses are documented",
              any("402" in str(op.get("responses", {}))
                  for path in spec["paths"].values()
                  for op in path.values() if isinstance(op, dict)),
              "402 quota response present")
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "openapi.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2)
        check("openapi.json written for the client generator",
              os.path.getsize(out) > 5000,
              f"{os.path.getsize(out):,} bytes")
    except ImportError as exc:
        check("FastAPI available for OpenAPI generation", False, str(exc))

    print("\n" + "=" * 70)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
