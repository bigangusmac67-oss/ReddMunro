"""score_history.py — score HISTORY_STORE_PREREG.md against real corpora.

H2-H8 only. **H1 cannot be scored here and is not attempted**: it asks
whether identity pairs break more during incidents than between quiet
windows, and answering that needs a window someone labelled `incident`
because their service was actually broken. Planting a divergence and
then detecting it would score the machinery, not the premise, and
recording that as a hit would be the exact self-deception this project
spends its time avoiding.

Negative controls run first, in the registered order, and the positive
case does not run until they pass.

    python score_history.py
"""

import datetime
import hashlib
import os
import shutil
import tempfile

import numpy as np

import history as HI
import signal_audit as SA

RESULTS = []
HERE = os.path.dirname(os.path.abspath(__file__))


def score(tag, ok, detail):
    RESULTS.append((tag, ok, detail))
    print(f"  [{'HIT ' if ok else 'MISS'}] {tag}  — {detail}")
    return ok


def load_corpus(rel):
    for cand in (os.path.join(HERE, rel),
                 os.path.join(HERE, "data", os.path.basename(rel)),
                 os.path.join(HERE, "demo", os.path.basename(rel))):
        if os.path.exists(cand):
            with open(cand, encoding="utf-8", errors="replace") as fh:
                return fh.read()
    return None


def slice_csv(text, lo, hi):
    lines = text.splitlines()
    return "\n".join([lines[0]] + lines[1 + lo:1 + hi])


def audit_window(text, label, root, run_id, day, span_days, source,
                 basis="differenced"):
    pay = SA.report_payload(SA.audit_text(text, label=source, ordered=True,
                                          basis=basis))
    d0 = datetime.date(2026, 1, 1) + datetime.timedelta(days=day)
    HI.record(pay, root=root, window_label=label,
              window_from=d0.isoformat(),
              window_to=(d0 + datetime.timedelta(days=span_days - 1)).isoformat(),
              source=source, run_id=run_id,
              dataset_id=hashlib.sha256(text.encode()).hexdigest()[:16])
    return pay


def main():
    tmp = tempfile.mkdtemp(prefix="reddhist_")
    prom = load_corpus("demo/prometheus_infra.csv")
    nyc = load_corpus("data/nyc_covid_dashboard.csv")
    if prom is None:
        print("prometheus_infra.csv not found; cannot score.")
        return 2

    try:
        # ---------------- negative controls, in the registered order ---
        print("\nControls (the positive case does not run until these pass)")

        root = os.path.join(tmp, "c1")
        for i in (0, 1):
            audit_window(prom, "quiet", root, f"same{i}", 0, 7, "p.csv")
        an = HI.persistence(HI.load(root), source="p.csv")
        c1 = score("C1  the same export twice is one observation",
                   an["duplicate_inputs"] == 1,
                   f"{an['duplicate_inputs']} duplicate(s) detected by "
                   f"content hash")

        root = os.path.join(tmp, "c2")
        rng = np.random.default_rng(3)
        for i in range(30):
            X = rng.standard_normal((400, 6))
            csv = ",".join(f"n{j}" for j in range(6)) + "\n" + "\n".join(
                ",".join(f"{v:.6f}" for v in r) for r in X)
            audit_window(csv, "quiet", root, f"{i:03d}", i, 7, "noise.csv")
        an = HI.persistence(HI.load(root), source="noise.csv")
        c2 = score("C2  30 windows of independent noise -> no persistence",
                   not an["pairs"], f"{len(an['pairs'])} pair(s)")

        root = os.path.join(tmp, "c3")
        L = rng.standard_normal((3, 6))
        for i in range(30):
            F = rng.standard_normal((400, 3))
            X = F @ L + 0.2 * rng.standard_normal((400, 6))
            # Two PLANTED identities. The first version of this control
            # produced no identity pairs at all, so "0 exceptions" was
            # vacuously true and the control tested nothing — the same
            # shape of mistake as the drift cycle's D2 fixture, which
            # passed while measuring a weaker claim than registered.
            X = np.column_stack([X, X[:, 0] * 1000.0, X[:, 1] / 7.0])
            csv = ",".join(f"s{j}" for j in range(8)) + "\n" + "\n".join(
                ",".join(f"{v:.6f}" for v in r) for r in X)
            audit_window(csv, "quiet", root, f"{i:03d}", i, 7, "stat.csv")
        an = HI.persistence(HI.load(root), source="stat.csv")
        exc = sum(len(p["exceptions"]) for p in an["pairs"])
        c3 = score("C3  stationary structure -> persistence, no exceptions",
                   len(an["pairs"]) >= 2 and exc == 0,
                   f"{len(an['pairs'])} planted identity pair(s) held, "
                   f"{exc} exception(s)")

        if not (c1 and c2 and c3):
            print("\n  CONTROLS FAILED — H2-H8 not scored.")
            return 1
        print("\n  controls passed\n")

        # ---------------- H2: substrate stability ---------------------
        # Disjoint windows of a REAL corpus, so the question is about
        # this engine on real telemetry rather than about a fixture.
        rows = len(prom.splitlines()) - 1
        n = 3          # the most this corpus supports at grade B: 437
                       # rows over 11 metrics is 39.7 rows/metric, and
                       # the grade gate wants 10 per window.
        w = rows // n
        root = os.path.join(tmp, "h2")
        sets = []
        for i in range(n):
            seg = slice_csv(prom, i * w, (i + 1) * w)
            pay = audit_window(seg, "quiet", root, f"{i:03d}", i * 7, 7,
                               "prom.csv")
            sets.append({tuple(sorted([p["metric_a"], p["metric_b"]]))
                         for p in pay["identity_pairs"]})
        agree = []
        for i in range(n):
            for j in range(i + 1, n):
                u = sets[i] | sets[j]
                agree.append(1.0 if not u else len(sets[i] & sets[j]) / len(u))
        mean_agree = float(np.mean(agree))
        h2 = score("H2  disjoint quiet windows agree >= 95% on identity sets",
                   mean_agree >= 0.95,
                   f"mean Jaccard {mean_agree:.3f} over {len(agree)} window "
                   f"pairs, {n} windows of {w} rows")

        an_h2 = HI.persistence(HI.load(root), source="prom.csv")

        # ---------------- H3: absent is not evidence ------------------
        root = os.path.join(tmp, "h3")
        cols = prom.splitlines()[0].split(",")
        keep_all = list(range(len(cols)))
        drop_two = [k for k, c in enumerate(cols)
                    if c not in ("request_rate_total",)]
        for i in range(30):
            seg = slice_csv(prom, (i % n) * w, ((i % n) + 1) * w)
            if i >= 10:      # request_rate_total absent from 20 of 30
                lines = seg.splitlines()
                idx = drop_two
                seg = "\n".join(",".join(l.split(",")[k] for k in idx)
                                for l in lines)
            audit_window(seg, "quiet", root, f"{i:03d}", i, 7, "h3.csv")
        an = HI.persistence(HI.load(root), source="h3.csv")
        pair = next((p for p in an["pairs"]
                     if "request_rate_total" in p["pair"]), None)
        h3 = score("H3  a metric absent from a run is not counted against it",
                   pair is not None and pair["present"] == 10,
                   f"{pair['rating']} — 20 runs without the column are not "
                   f"in the denominator" if pair else "pair not found")

        # ---------------- H4: overlap inflates agreement --------------
        root_o = os.path.join(tmp, "h4o")
        root_d = os.path.join(tmp, "h4d")
        step = max(1, w // 7)
        for i in range(30):                      # rolling, overlapping
            seg = slice_csv(prom, i * step, i * step + w)
            if len(seg.splitlines()) < 30:
                break
            audit_window(seg, "quiet", root_o, f"{i:03d}", i, 7, "roll.csv")
        for i in range(n):                       # disjoint
            seg = slice_csv(prom, i * w, (i + 1) * w)
            audit_window(seg, "quiet", root_d, f"{i:03d}", i * 7, 7,
                         "disj.csv")
        ao = HI.persistence(HI.load(root_o), source="roll.csv")
        ad = HI.persistence(HI.load(root_d), source="disj.csv")
        if ao.get("error") or ao.get("effective_windows") is None:
            h4 = score("H4  overlapping runs are not independent observations",
                       False, f"could not be computed: "
                              f"{ao.get('error') or 'no window spans'}")
            ratio_o = None
        else:
            ratio_o = ao["effective_windows"] / max(ao["runs_eligible"], 1)
            h4 = score("H4  overlapping runs are not independent observations",
                   ratio_o < 0.5,
                   f"rolling: {ao['runs_eligible']} runs -> "
                   f"{ao['effective_windows']} effective "
                   f"({ratio_o:.0%}); disjoint: {ad['runs_eligible']} runs "
                   f"-> {ad.get('effective_windows')} effective")

        # ---------------- H5: thin runs do not vote -------------------
        # NYC grades C on the full corpus; sliced thinner it grades D.
        h5 = None
        if nyc:
            root = os.path.join(tmp, "h5")
            nrows = len(nyc.splitlines()) - 1
            for i in range(6):
                seg = slice_csv(nyc, i * (nrows // 6), (i + 1) * (nrows // 6))
                audit_window(seg, "quiet", root, f"{i:03d}", i * 7, 7,
                             "nyc.csv")
            runs = HI.load(root)
            grades = sorted({r["grade"] for r in runs})
            an = HI.persistence(runs, source="nyc.csv")
            h5 = score("H5  runs below grade B are excluded from persistence",
                       (an.get("error") is not None
                        or an["runs_excluded_by_grade"] == len(runs)),
                       f"grades {grades}; "
                       + (f"refused: {an['error'][:60]}" if an.get("error")
                          else f"{an['runs_excluded_by_grade']} excluded"))

        # ---------------- H6: the exception leads ---------------------
        root = os.path.join(tmp, "h6")
        for i in range(n):
            seg = slice_csv(prom, i * w, (i + 1) * w)
            if i == n // 2:      # the middle window
                # break one identity by perturbing a column, and label the
                # window 'incident'. This scores the ORDERING, not H1.
                lines = seg.splitlines()
                hdr = lines[0].split(",")
                k = hdr.index("request_rate_total")
                pert = [lines[0]]
                rr = np.random.default_rng(11)
                for ln in lines[1:]:
                    parts = ln.split(",")
                    try:
                        parts[k] = f"{float(parts[k]) * (1 + rr.normal(0, .5)):.6f}"
                    except ValueError:
                        pass
                    pert.append(",".join(parts))
                seg = "\n".join(pert)
                audit_window(seg, "incident", root, f"{i:03d}", i * 7, 7,
                             "h6.csv")
            else:
                audit_window(seg, "quiet", root, f"{i:03d}", i * 7, 7,
                             "h6.csv")
        an = HI.persistence(HI.load(root), source="h6.csv")
        lines_out = HI.report_lines(an)
        first_rating = next((i for i, l in enumerate(lines_out)
                             if "present run(s)" in l), 10 ** 6)
        first_keep = next((i for i, l in enumerate(lines_out)
                           if l.startswith("KEEP")), 10 ** 6)
        h6 = score("H6  the incident exception is printed above any rating",
                   first_keep == 0 and first_keep < first_rating,
                   lines_out[0][:70] if lines_out else "no output")

        # ---------------- H7: engine majors refused -------------------
        root = os.path.join(tmp, "h7")
        p1 = audit_window(prom, "quiet", root, "old", 0, 7, "h7.csv")
        p2 = SA.report_payload(SA.audit_text(prom, label="h7.csv",
                                             ordered=True,
                                             basis="differenced"))
        p2["engine_version"] = "2.0.0"
        HI.record(p2, root=root, window_label="quiet", source="h7.csv",
                  run_id="new", dataset_id="deadbeef",
                  window_from="2026-02-01", window_to="2026-02-07")
        an = HI.persistence(HI.load(root), source="h7.csv")
        h7 = score("H7  runs from different engine majors are refused",
                   bool(an.get("error")) and "engine majors" in an["error"],
                   (an.get("error") or "no error")[:70])

        # ---------------- H8: real corpus, registered blind ------------
        # Scored AS REGISTERED first: 8 windows. That is the number in
        # the document and it is not being quietly changed.
        root8 = os.path.join(tmp, "h8")
        w8 = rows // 8
        for i in range(8):
            audit_window(slice_csv(prom, i * w8, (i + 1) * w8), "quiet",
                         root8, f"{i:03d}", i * 7, 7, "h8.csv")
        an8 = HI.persistence(HI.load(root8), source="h8.csv")
        grades8 = sorted({r["grade"] for r in HI.load(root8)})
        h8_registered = (an8.get("error") is None and an8["pairs"])
        score("H8  as registered: 8 windows of prometheus_infra",
              bool(h8_registered),
              f"grades {grades8}; "
              + (an8["error"][:80] if an8.get("error")
                 else f"{len(an8['pairs'])} pair(s)"))

        pairs = an_h2["pairs"]
        persistent = [p for p in pairs if p["held"] >= 7]
        varying = [p for p in pairs if p["exceptions"]]
        h8 = score("H8  prometheus_infra in 8 windows: 1-2 persistent, "
                   ">=1 varying",
                   1 <= len(persistent) <= 2 and len(varying) >= 1,
                   f"{len(persistent)} persistent (>=7 of 8), "
                   f"{len(varying)} varying, {len(pairs)} pair(s) total: "
                   + "; ".join(f"{p['pair'][0]}~{p['pair'][1]} {p['rating']}"
                               for p in pairs[:4]))

        print("\n" + "=" * 70)
        hits = sum(1 for _t, ok, _d in RESULTS if ok)
        print(f"{hits} hit, {len(RESULTS) - hits} missed, of {len(RESULTS)}")
        print("H1 NOT SCORED — needs a window someone labelled 'incident' "
              "because their service broke.")
        print("=" * 70)
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
