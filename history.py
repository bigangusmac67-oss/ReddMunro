"""history.py — what did this dashboard look like across many windows?

Registered in HISTORY_STORE_PREREG.md before any of this was written.

THE POINT IS THE EXCEPTION
==========================
A single audit measures correlation over one window. If that window was
quiet, a pair at r = 0.9998 tells you they agreed while nothing was
happening — which is exactly when a redundant-looking metric looks
redundant. The one you keep it for is the incident.

So this does not report confidence. It reports:

    request_rate_total ~ methodGET_status200
      identical in 29 of 30 runs where both were present
      EXCEPT run 2026-07-14 (window labelled 'incident') -> KEEP

Twenty-nine confirmations are one finding repeated. **The exception is
the information**, and it is printed first, always. A store that led with
the 29 would be a confidence-inflation machine handing the archive
recommendation authority it has not earned.

FOUR THINGS THIS REFUSES TO DO
==============================
1. Infer the window label. A deploy, a batch job and an outage look
   similar in aggregate, and a classifier deciding what the tool reports
   is the failure the basis declaration exists to prevent. Declared, or
   `unknown`.

2. Count absent as evidence. A metric missing from a run — not in that
   export, added last month — is not a metric that failed to be
   redundant. The denominator is runs where it was PRESENT, and the
   denominator is printed.

3. Pretend overlapping runs are independent. Thirty daily runs on a
   7-day window share six-sevenths of their data; "29 of 30" reads as
   thirty observations and is nearer four. Effective windows are
   computed where spans were declared, and where they were not this
   says so rather than implying breadth it cannot support.

4. Change the recommendation. The store reports; the engine decides. A
   rating that silently reweighted the audit would make the result
   depend on files a reviewer cannot see in the output.

STORAGE
=======
One plain JSON file per run under `.redd/history/`, readable and
diffable without this tool — anything needing our code to inspect
becomes a thing the operator has to trust. The directory carries its own
`.gitignore` containing `*`, so it is ignored whatever the surrounding
repository is configured to do. It holds the operator's metric names,
which is the first thing this project writes to disk persistently.
"""

import datetime
import glob
import json
import os

__all__ = ["record", "load", "persistence", "report_lines",
           "HISTORY_DIR", "WINDOW_LABELS"]

HISTORY_DIR = os.path.join(".redd", "history")
WINDOW_LABELS = ("quiet", "busy", "incident", "unknown")

# Runs this thin do not get a vote. Splitting evidence across windows is
# already demanding; a grade the rest of the report calls marginal is not
# good enough to count toward a persistence claim.
ELIGIBLE_GRADES = ("A", "B")


def _ensure_store(root):
    """Create the store, and make it ignore itself."""
    d = os.path.join(root, "history")
    fresh = not os.path.isdir(d)
    os.makedirs(d, exist_ok=True)
    gi = os.path.join(root, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as fh:
            fh.write("# Redd Munro's local history store.\n"
                     "# These files contain YOUR metric names. Ignored here\n"
                     "# rather than relying on the surrounding repository,\n"
                     "# so the store is ignored wherever it is created.\n"
                     "*\n")
    return d, fresh


def record(payload, root=".redd", window_label="unknown", window_from=None,
           window_to=None, source=None, run_id=None, dataset_id=None):
    """Append one run to the store. Returns (run_dict, path, was_fresh).

    Only SET-VALUED findings are stored. Per CONTINUOUS_DIFF_SPEC.md the
    sets are the stable substrate and the scalars drift, so counting
    scalar agreement across runs would be counting sampling noise.
    `effective_signals` is kept for reference and is never counted.
    """
    if window_label not in WINDOW_LABELS:
        raise ValueError(f"window label must be one of {WINDOW_LABELS}; "
                         f"got {window_label!r}. It is declared, never "
                         f"inferred — a classifier guessing 'incident' "
                         f"from a variance spike would decide what this "
                         f"tool reports.")
    d, fresh = _ensure_store(root)
    now = datetime.datetime.now(datetime.timezone.utc)
    rid = run_id or now.strftime("%Y%m%dT%H%M%SZ")

    basis = payload.get("basis") or {}
    run = {
        "run_id": rid,
        "recorded_at": now.isoformat(),
        "engine_version": payload.get("engine_version"),
        "source": source or payload.get("file"),
        # Content hash of the INPUT, supplied by the caller. The only
        # sound basis for deciding two runs are the same observation.
        # The first version keyed this on the FINDINGS, which collapsed
        # thirty genuinely different windows into two because twenty-nine
        # of them agreed — discarding exactly the evidence the store
        # exists to accumulate.
        "dataset_id": dataset_id,
        "window": {"label": window_label, "from": window_from,
                   "to": window_to, "declared_by": "operator",
                   "rows": (payload.get("summary") or {}).get("rows")},
        "basis": basis.get("headline"),
        "basis_declared": bool(basis.get("declared")),
        "ordered": bool((payload.get("order") or {}).get("ordered")),
        "grade": (payload.get("assurance") or {}).get("grade"),
        "identity_pairs": sorted(
            sorted([p["metric_a"], p["metric_b"]])
            for p in payload.get("identity_pairs", [])),
        "clusters": [sorted(c["metrics"])
                     for c in payload.get("redundancy_clusters", [])],
        "subset_sums": sorted(
            [s["metric"], sorted(s["children"])]
            for s in payload.get("subset_sums", [])),
        "metrics_present": sorted(m["name"] for m in payload.get("metrics", [])),
        "archive_candidates": sorted(payload.get("archive_candidates") or []),
        # reference only, never counted
        "effective_signals": (payload.get("summary") or {}).get("effective_signals"),
    }
    path = os.path.join(d, f"{rid}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(run, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return run, path, fresh


def load(root=".redd"):
    """Every recorded run, oldest first."""
    d = os.path.join(root, "history")
    out = []
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            with open(p, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except (OSError, ValueError):
            continue
    return out


def _major(v):
    return (v or "0").split(".")[0]


def _effective_windows(runs):
    """Independent windows, not raw runs.

    Unique wall-clock coverage divided by the mean window length. Thirty
    daily runs of a 7-day window spanning 36 days gives 36/7 = 5.1, not
    30 — which is the number a reader would otherwise take away.

    Returns (value, reason). `None` where spans were not declared: the
    honest answer is that overlap is unknown, not that there is none.
    """
    spans = []
    for r in runs:
        a, b = (r.get("window") or {}).get("from"), (r.get("window") or {}).get("to")
        if not a or not b:
            return None, ("window spans were not declared, so overlap "
                          "cannot be computed — these runs may share most "
                          "of their data")
        try:
            fa = datetime.date.fromisoformat(str(a)[:10])
            fb = datetime.date.fromisoformat(str(b)[:10])
        except ValueError:
            return None, "window spans are not ISO dates; overlap unknown"
        if fb < fa:
            fa, fb = fb, fa
        spans.append((fa, fb))
    if not spans:
        return None, "no runs"
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    covered = sum((b - a).days + 1 for a, b in merged)
    mean_len = sum((b - a).days + 1 for a, b in spans) / len(spans)
    if mean_len <= 0:
        return None, "zero-length windows"
    return round(covered / mean_len, 2), None


def persistence(runs, source=None, eligible_grades=ELIGIBLE_GRADES):
    """Which identity pairs held across windows, and where they did not.

    Refuses to pool runs from different engine majors, different bases
    or different row-order declarations — a count over inconsistent
    definitions is worse than no count.
    """
    if not runs:
        return {"error": "no runs recorded", "pairs": []}

    # A pair's persistence is about ONE dashboard's history. The first
    # version pooled every run in the store, so two unrelated exports
    # produced a single merged history in which each contributed pairs
    # the other had never contained — and "1 of 1 present run" looked
    # like a finding rather than an artefact of mixing.
    sources = sorted({r.get("source") for r in runs})
    if source is not None:
        runs = [r for r in runs if r.get("source") == source]
        if not runs:
            return {"error": f"no runs recorded for {source!r}. "
                             f"Recorded: {sources}", "pairs": []}
    elif len(sources) > 1:
        return {"error": (f"the store holds runs from {len(sources)} "
                          f"different sources {sources} and they will not "
                          f"be pooled — persistence is a claim about one "
                          f"dashboard's history. Pass source=... to pick "
                          f"one."),
                "pairs": [], "sources": sources}

    majors = {_major(r.get("engine_version")) for r in runs}
    if len(majors) > 1:
        return {"error": (f"runs span engine majors {sorted(majors)} and "
                          f"will not be pooled — findings from different "
                          f"engine versions may not be comparable"),
                "pairs": []}
    bases = {r.get("basis") for r in runs}
    if len(bases) > 1:
        return {"error": f"runs span bases {sorted(map(str, bases))}; "
                         f"refusing to pool", "pairs": []}
    if len({r.get("ordered") for r in runs}) > 1:
        return {"error": "runs disagree about whether rows are ordered; "
                         "refusing to pool", "pairs": []}

    eligible = [r for r in runs if r.get("grade") in eligible_grades]
    excluded = [r for r in runs if r.get("grade") not in eligible_grades]
    if not eligible:
        return {"error": (f"all {len(runs)} run(s) graded outside "
                          f"{eligible_grades}; splitting a thin corpus "
                          f"across windows makes it thinner"),
                "pairs": []}

    # The same input audited twice is one observation. Keyed on the
    # content hash and NOTHING else: two windows that happen to produce
    # the same findings are two observations, and treating them as one
    # would throw away the agreement that is being counted.
    ids = [r.get("dataset_id") for r in eligible if r.get("dataset_id")]
    duplicates = len(ids) - len(set(ids))
    dedup_possible = len(ids) == len(eligible)

    labels = {}
    for r in eligible:
        labels[(r.get("window") or {}).get("label", "unknown")] = \
            labels.get((r.get("window") or {}).get("label", "unknown"), 0) + 1

    all_pairs = set()
    for r in eligible:
        for p in r["identity_pairs"]:
            all_pairs.add(tuple(sorted(p)))

    rows = []
    for pair in sorted(all_pairs):
        a, b = pair
        present, held, exceptions = 0, 0, []
        for r in eligible:
            roster = set(r["metrics_present"])
            if a not in roster or b not in roster:
                continue            # ABSENT is not evidence against it
            present += 1
            held_here = {tuple(sorted(x)) for x in r["identity_pairs"]}
            if pair in held_here:
                held += 1
            else:
                exceptions.append({
                    "run_id": r["run_id"],
                    "label": (r.get("window") or {}).get("label", "unknown"),
                    "from": (r.get("window") or {}).get("from"),
                    "to": (r.get("window") or {}).get("to"),
                })
        incident_exc = [e for e in exceptions if e["label"] == "incident"]
        rows.append({
            "pair": list(pair),
            "held": held,
            "present": present,
            "exceptions": exceptions,
            "incident_exceptions": incident_exc,
            # A count, never a probability. "27 of 30 present runs" is
            # checkable; "90% confidence" implies a model we do not have.
            "rating": f"{held} of {present} present run(s)",
        })

    # EXCEPTIONS FIRST. Incident exceptions above all, then any
    # exception, then the pairs that merely confirmed.
    rows.sort(key=lambda x: (not x["incident_exceptions"],
                             not x["exceptions"],
                             -x["present"], x["pair"]))

    eff, eff_reason = _effective_windows(eligible)
    return {
        "error": None,
        "source": sources[0] if len(sources) == 1 else source,
        "runs_total": len(runs),
        "runs_eligible": len(eligible),
        "runs_excluded_by_grade": len(excluded),
        "duplicate_inputs": duplicates,
        "duplicate_check_complete": dedup_possible,
        "effective_windows": eff,
        "effective_windows_unknown_because": eff_reason,
        "labels": labels,
        "has_incident_window": labels.get("incident", 0) > 0,
        "pairs": rows,
    }


def report_lines(an):
    """Exceptions first. H6 of the registration, in code."""
    if an.get("error"):
        return [f"history: {an['error']}"]
    out = []

    flagged = [p for p in an["pairs"] if p["incident_exceptions"]]
    other = [p for p in an["pairs"] if p["exceptions"] and not p["incident_exceptions"]]
    stable = [p for p in an["pairs"] if not p["exceptions"]]

    if flagged:
        out.append("KEEP — diverged during a window you labelled 'incident':")
        for p in flagged:
            a, b = p["pair"]
            out.append(f"  {a} ~ {b}")
            out.append(f"    identical in {p['rating']}, EXCEPT:")
            for e in p["incident_exceptions"]:
                span = f" ({e['from']}..{e['to']})" if e.get("from") else ""
                out.append(f"      run {e['run_id']}{span} — labelled "
                           f"'{e['label']}'")
            out.append("    That divergence is the reason this metric "
                       "exists. Do not archive it on the other runs.")
    if other:
        out.append("")
        out.append("Diverged in some windows, none labelled 'incident':")
        for p in other:
            a, b = p["pair"]
            out.append(f"  {a} ~ {b}  —  {p['rating']}, "
                       f"{len(p['exceptions'])} exception(s)")
    if stable:
        out.append("")
        out.append(f"Held in every present run ({len(stable)} pair(s)):")
        for p in stable:
            out.append(f"  {p['pair'][0]} ~ {p['pair'][1]}  —  {p['rating']}")

    out.append("")
    eff = an["effective_windows"]
    if eff is None:
        out.append(f"{an['runs_eligible']} eligible run(s); EFFECTIVE "
                   f"INDEPENDENT WINDOWS UNKNOWN — "
                   f"{an['effective_windows_unknown_because']}.")
    else:
        out.append(f"{an['runs_eligible']} eligible run(s), but only "
                   f"{eff} effective independent window(s) — overlapping "
                   f"runs share most of their data and are not separate "
                   f"observations.")
    if an["runs_excluded_by_grade"]:
        out.append(f"{an['runs_excluded_by_grade']} run(s) excluded: grade "
                   f"below {ELIGIBLE_GRADES[-1]}.")
    if an["duplicate_inputs"]:
        out.append(f"{an['duplicate_inputs']} run(s) audited an input already "
                   f"recorded — the same observation, not a confirmation.")
    elif not an.get("duplicate_check_complete"):
        out.append("Some runs carry no dataset id, so a repeat audit of the "
                   "same export cannot be told from a new window.")
    if not an["has_incident_window"]:
        out.append("NO run is labelled 'incident'. This history therefore "
                   "contains no evidence about incidents, and says only "
                   "that these pairs were stable across the windows given. "
                   "Label one with --window-label incident to test the "
                   "thing that actually matters.")
    return out
