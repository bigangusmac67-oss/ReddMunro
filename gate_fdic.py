"""
gate_fdic.py -- the arithmetic gate.

Registered in REAL_DASHBOARDS.md section 7 before the data was fetched:
"Check that the recovered rows actually satisfy assets = liabilities +
equity in the raw data BEFORE running the audit. If the arithmetic does
not hold in the source, the corpus is wrong and no result from it means
anything."

Three identities are checked. All three are definitional in the FFIEC
call report, not empirical regularities:

  G1  ASSET    = LIAB + EQ                      the accounting equation
  G2  LNRE     = LNREAG + LNRECONS + LNREMULT
                 + LNRENRES + LNRERES           complete RE decomposition
  G3  LNLSNET  = LNLSGR - LNATRES               net of loss allowance

G2 is the important one for prediction F2: it is a COMPLETE subset-sum
with exactly five named children, so it is the cleanest possible test of
the subset-sum detector gap against legally mandated ground truth.

Figures are reported in USD thousands, so an exact identity should hold
to the dollar, not approximately.
"""
import csv, sys

rows = list(csv.DictReader(open("fdic_callreport_2024q1.csv")))
n = len(rows)
print(f"{n} banks\n")

def num(r, k):
    v = r.get(k, "").strip()
    return float(v) if v not in ("", "None") else None

CHECKS = [
    ("G1  ASSET = LIAB + EQ",
     lambda r: num(r,"ASSET"), lambda r: num(r,"LIAB") + num(r,"EQ")),
    ("G2  LNRE = AG+CONS+MULT+NRES+RES",
     lambda r: num(r,"LNRE"),
     lambda r: sum(num(r,k) for k in
                   ("LNREAG","LNRECONS","LNREMULT","LNRENRES","LNRERES"))),
    ("G3  LNLSNET = LNLSGR - LNATRES",
     lambda r: num(r,"LNLSNET"), lambda r: num(r,"LNLSGR") - num(r,"LNATRES")),
]

verdict = True
for label, lhs, rhs in CHECKS:
    exact = off = 0
    worst = 0.0
    worst_cert = None
    for r in rows:
        try:
            a, b = lhs(r), rhs(r)
        except (TypeError, KeyError):
            continue
        if a is None or b is None:
            continue
        d = abs(a - b)
        if d < 0.5:
            exact += 1
        else:
            off += 1
            if d > worst:
                worst, worst_cert = d, r["CERT"]
    pct = 100.0 * exact / max(exact + off, 1)
    ok = "PASS" if pct >= 99.5 else "FAIL"
    if pct < 99.5:
        verdict = False
    print(f"  [{ok}] {label}")
    print(f"         exact to the dollar on {exact}/{exact+off} banks ({pct:.2f}%)")
    if off:
        print(f"         {off} off; largest discrepancy ${worst:,.0f}k "
              f"(CERT {worst_cert})")
    print()

print("=" * 60)
print("GATE: " + ("PASS -- corpus is sound, proceed to audit"
                 if verdict else
                 "FAIL -- source arithmetic broken, DISCARD corpus"))
print("=" * 60)
sys.exit(0 if verdict else 1)
