"""
signal_audit.py — how many independent signals does your dashboard
actually have?

Point it at a CSV whose columns are metrics sampled over time. It
answers four questions:

  1. How many independent signals are really in there?
     (participation ratio of the correlation spectrum, plus the number
     of principal components needed for 95% of variance)
  2. Which columns are DEFINITIONAL IDENTITIES of each other — pairs at
     |r| >= 0.999 that are the same number wearing two names?
  3. Which columns are REDUNDANT — highly correlated clusters where one
     representative would carry nearly all the information?
  4. Which columns are LOAD-BEARING — carrying variation nothing else
     in the set carries?

Why this exists. A 40-metric dashboard driven by four underlying
things gives its owner the feeling of forty-fold coverage and the
reality of four. The measurement that motivated this tool found eleven
recorded variables collapsing to ~1.9 independent signals, with four of
them exact identities of others.

METHOD, and its two honest caveats.

  Trend confound. Two metrics that both grow over time correlate near
  1.0 from shared trend alone, with no relationship between them
  whatsoever. Business and infrastructure metrics almost all trend.
  This tool therefore computes EVERY result twice — on the raw series
  and on first differences (period-over-period change) — and reports
  both. Where they disagree, the differenced answer is the one about
  the metrics and the raw answer is mostly about the calendar. The
  headline verdict uses the differenced view for this reason. This is
  not a refinement; ignoring it is the single most common way an
  audit like this produces a confident wrong answer.

  Correlation is second-order. Two metrics can be uncorrelated and
  still statistically dependent (one driving the other's volatility,
  say). With enough rows this tool also runs a mutual-information
  check that catches monotone-invisible dependence, and flags pairs
  whose empirical dependence far exceeds what their correlation
  implies. Below ~200 rows that estimator is unreliable and is skipped
  rather than reported badly.

Dependencies: numpy only. Python 3.8+.

    python signal_audit.py metrics.csv
    python signal_audit.py metrics.csv --html report.html
    python signal_audit.py metrics.csv --ignore date,region --min-rows 50
"""

import argparse
import csv
import html as _html
import io
import itertools
import math
import os
import sys

import numpy as np

__version__ = "0.1.1"

# Thresholds. Stated as constants because they are judgement calls, not
# facts, and anyone using this should be able to see and change them.
IDENTITY_R = 0.999      # |r| at or above this: same quantity, two names
REDUNDANT_R = 0.90      # |r| at or above this: one carries the other
MI_RATIO_FLAG = 3.0     # empirical MI this many x the Gaussian
SUM_TOL = 0.005         # subset sums must close to within 0.5% ...
SUM_FRAC = 0.99         # ... on at least this share of rows
SUM_MIN_SHARE = 0.01    # each child must be >= 1% of the parent
                        # equivalent: dependence correlation cannot see
MIN_ROWS_MI = 200       # below this the MI estimator is not trustworthy
MIN_ROWS = 30           # below this, refuse the whole audit

TIME_LIKE = {"time", "t", "date", "datetime", "timestamp", "index",
             "step", "period", "day", "week", "month", "year", "epoch"}


# ----------------------------------------------------------------------
# loading
# ----------------------------------------------------------------------
def load_csv(path, ignore=(), max_rows=None):
    """Read numeric columns from a CSV FILE. Returns (names, matrix, notes)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        return load_csv_text(f.read(), ignore=ignore, max_rows=max_rows,
                             label=path)


def _shape_hint(header, body, raw_header):
    """Name the likely CAUSE when a file yields too few numeric columns.

    The refusal was already accurate — "found 1 usable numeric column" is
    true — and accurate was not enough. `ADOPTION_PREREG.md` A1 predicts
    that teams stall getting an export into shape, not at the worksheet,
    so this message is the one a new user is most likely to meet and the
    last thing they read before giving up.

    Each branch names a specific shape and what to do about it. Nothing
    here transforms the data: guessing a delimiter or pivoting silently
    would make the tool audit a file the user did not hand it, which is
    the failure this whole engine is built to refuse. It says what it
    thinks the file is; the fix stays the user's.
    """
    hints = []

    # Semicolon or tab delimited: the whole header arrived as one field.
    if len(raw_header) == 1 and any(d in raw_header[0] for d in ";\t|"):
        d = next(d for d in ";\t|" if d in raw_header[0])
        shown = {";": "semicolon", "\t": "tab", "|": "pipe"}[d]
        hints.append(
            f"This looks {shown}-delimited, not comma-delimited — the whole "
            f"header parsed as a single column. Excel writes this in some "
            f"locales. Re-export as comma-separated, or convert first.")

    # Long / narrow: one label column and one value column, many repeats.
    low = [h.lower() for h in header]
    label_like = {"metric", "name", "series", "__name__", "measurement",
                  "field", "key", "label"}
    value_like = {"value", "val", "v", "y", "reading", "sample"}
    if (len(header) <= 4 and any(h in label_like for h in low)
            and any(h in value_like for h in low)):
        lab = next(h for h in header if h.lower() in label_like)
        val = next(h for h in header if h.lower() in value_like)
        distinct = len({r[low.index(lab.lower())]
                        for r in body[:500] if len(r) > low.index(lab.lower())})
        hints.append(
            f"This looks like LONG format: one row per (time, metric, "
            f"value), with {distinct} distinct value(s) in '{lab}'. This "
            f"engine needs WIDE format — one COLUMN per metric, one ROW "
            f"per timestamp. Pivot '{lab}' into columns and '{val}' into "
            f"the cells. In pandas:\n"
            f"    df.pivot(index=<time col>, columns='{lab}', "
            f"values='{val}').to_csv('wide.csv')")

    # Too few rows is a row problem, and saying "0 usable columns" blames
    # the columns for it.
    if 0 < len(body) < MIN_ROWS:
        hints.append(
            f"The file has {len(body)} data row(s). The audit needs at "
            f"least {MIN_ROWS}, and about 10 rows per metric for a usable "
            f"evidence grade — every column here was dropped for having "
            f"too few values, not for being non-numeric.")

    return ("\n\n  " + "\n\n  ".join(hints)) if hints else ""


def load_csv_text(text, ignore=(), max_rows=None, label="<input>"):
    """Read numeric columns from CSV TEXT. Returns (names, matrix, notes).

    Exists so the engine can run where there is no filesystem — a
    browser via Pyodide, a lambda handed a request body, a notebook
    holding a string. `load_csv` is a thin wrapper over this, so both
    paths share one parser and cannot drift apart.

    Columns that are non-numeric, constant, time-like by name, or
    explicitly ignored are dropped, and each drop is reported rather
    than done silently — a column vanishing without explanation is how
    an audit quietly becomes an audit of something else.
    """
    rows = list(csv.reader(io.StringIO(text)))
    path = label
    if len(rows) < 2:
        raise ValueError(f"{path}: need a header row and at least one data row")

    header = [h.strip() for h in rows[0]]
    body = rows[1:]
    if max_rows:
        body = body[:max_rows]

    ignore_l = {s.strip().lower() for s in ignore}
    cols, notes = {}, []
    for j, name in enumerate(header):
        if not name:
            continue
        low = name.lower()
        if low in ignore_l:
            notes.append(f"'{name}' — ignored by request")
            continue
        if low in TIME_LIKE:
            notes.append(f"'{name}' — dropped, looks like a time index")
            continue
        vals, bad = [], 0
        for r in body:
            v = r[j].strip() if j < len(r) else ""
            if v == "":
                vals.append(np.nan)
                continue
            try:
                vals.append(float(v.replace(",", "").replace("%", "")))
            except ValueError:
                bad += 1
                vals.append(np.nan)
        arr = np.array(vals, dtype=float)
        finite = np.isfinite(arr)
        if finite.sum() < MIN_ROWS:
            notes.append(f"'{name}' — dropped, only {int(finite.sum())} "
                         f"numeric values")
            continue
        if bad > 0.5 * len(arr):
            notes.append(f"'{name}' — dropped, mostly non-numeric")
            continue
        if np.nanstd(arr) < 1e-12:
            notes.append(f"'{name}' — dropped, constant "
                         f"(value {np.nanmax(arr):g})")
            continue

        # DUPLICATE HEADER NAMES. `cols` is keyed by name, so a repeated
        # header used to overwrite the earlier column: three columns in,
        # two metrics out, nothing in `notes`, and a headline counting a
        # number of metrics the file did not have. The docstring above
        # promises the opposite, and this was the one case that broke it.
        #
        # Kept rather than refused, and disambiguated rather than
        # merged, because a dashboard that exports the same panel twice
        # is REDUNDANCY — the thing this tool exists to find. Collapsing
        # the pair silently destroyed the finding and the evidence for
        # it at the same time. The suffix names the column position, so
        # it points at something checkable in the source file.
        key = name
        if key in cols:
            key = f"{name} (col {j + 1})"
            notes.append(f"'{name}' — appears more than once in the "
                         f"header; kept as '{key}'. Identical duplicates "
                         f"will be reported as an identity pair, which "
                         f"is a finding, not an error.")
        cols[key] = arr

    if len(cols) < 2:
        raise ValueError(
            f"{path}: found {len(cols)} usable numeric column(s); need at "
            f"least 2.\n" + "\n".join("  " + n for n in notes)
            + _shape_hint(header, body, rows[0]))

    names = list(cols)
    M = np.column_stack([cols[n] for n in names])
    # listwise deletion: every metric must be present in a row for that
    # row to inform a correlation between any two of them
    keep = np.all(np.isfinite(M), axis=1)
    dropped = int((~keep).sum())
    if dropped:
        notes.append(f"{dropped} row(s) dropped for missing values "
                     f"({100 * dropped / len(M):.0f}% of the file)")
    M = M[keep]
    if len(M) < MIN_ROWS:
        raise ValueError(f"{path}: only {len(M)} complete rows after "
                         f"removing rows with gaps; need {MIN_ROWS}")
    return names, M, notes


# ----------------------------------------------------------------------
# core measures
# ----------------------------------------------------------------------
def _safe_corr(M):
    """Correlation matrix that tolerates zero-variance columns.

    A column can be constant in the raw data (caught at load) or become
    constant after differencing (a perfectly linear ramp). numpy emits
    a divide warning and returns nan; we substitute an identity row so
    the column is reported as related to nothing rather than crashing
    the audit.
    """
    M = np.asarray(M, dtype=float)
    sd = M.std(0)
    dead = sd < 1e-12
    if dead.any():
        M = M.copy()
        M[:, dead] += np.linspace(0, 1e-9, len(M))[:, None]
    with np.errstate(invalid="ignore", divide="ignore"):
        C = np.corrcoef(M.T)
    C = np.nan_to_num(C, nan=0.0)
    np.fill_diagonal(C, 1.0)
    return C


def participation_ratio(M):
    """Effective number of independent signals.

    PR = (sum lambda)^2 / sum(lambda^2) over the correlation-matrix
    eigenvalues. Equals d when all d signals are independent and equal
    variance; equals 1 when they are all the same signal. Unlike a
    variance-threshold count it is continuous, so it does not jump
    when a component sits at the cutoff.
    """
    C = _safe_corr(M)
    ev = np.linalg.eigvalsh(C)
    ev = np.clip(ev, 0.0, None)
    s = ev.sum()
    if s <= 0:
        return float("nan")
    return float(s * s / np.square(ev).sum())


def components_for(M, frac=0.95):
    """Principal components needed to reach `frac` of total variance."""
    C = _safe_corr(M)
    ev = np.sort(np.clip(np.linalg.eigvalsh(C), 0.0, None))[::-1]
    if ev.sum() <= 0:
        return len(ev)
    return int(np.searchsorted(np.cumsum(ev) / ev.sum(), frac) + 1)


def differenced(M):
    """First differences. Removes shared trend, which is the dominant
    source of spurious correlation in metrics that all grow."""
    return np.diff(M, axis=0)


def pairs_above(M, names, thresh):
    """[(|r|, r, name_i, name_j)] for pairs at or above |r| = thresh."""
    C = _safe_corr(M)
    out = []
    for i, j in itertools.combinations(range(len(names)), 2):
        r = float(C[i, j])
        if np.isfinite(r) and abs(r) >= thresh:
            out.append((abs(r), r, names[i], names[j]))
    out.sort(reverse=True)
    return out


def cluster(names, M, thresh=REDUNDANT_R):
    """Group metrics into redundancy clusters by single-linkage on
    |r| >= thresh. Returns [[name, ...], ...], largest first.

    Single linkage deliberately: if A tracks B and B tracks C, then a
    dashboard showing all three is showing one thing three times even
    when A and C are not directly correlated.
    """
    C = _safe_corr(M)
    d = len(names)
    parent = list(range(d))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in itertools.combinations(range(d), 2):
        if np.isfinite(C[i, j]) and abs(C[i, j]) >= thresh:
            a, b = find(i), find(j)
            if a != b:
                parent[a] = b

    groups = {}
    for i in range(d):
        groups.setdefault(find(i), []).append(names[i])
    out = sorted(groups.values(), key=len, reverse=True)
    return out


def uniqueness(M, names):
    """Per-metric: fraction of its variance NOT explained by the best
    single other metric, and not explained by all the others together.

    'unique' is 1 - R^2 from regressing this metric on every other,
    which is the honest measure of what would be lost by deleting it.
    'best_partner' names the single metric that predicts it best.

    R^2 is ADJUSTED for the number of predictors. With many metrics and
    few rows, ordinary R^2 rises toward 1 for arithmetic reasons alone —
    52 predictors will fit 227 points of noise fairly well — which would
    make every metric look redundant and the whole audit look decisive
    when it is merely overfitted. The adjustment penalises predictor
    count; where rows are scarce relative to metrics the caller is also
    warned (see `crowded`).
    """
    d = len(names)
    Z = (M - M.mean(0)) / np.where(M.std(0) < 1e-12, 1.0, M.std(0))
    C = _safe_corr(M)
    out = []
    for i in range(d):
        others = [j for j in range(d) if j != i]
        # best single predictor
        rs = [(abs(C[i, j]), j) for j in others if np.isfinite(C[i, j])]
        best_r, best_j = max(rs) if rs else (0.0, None)
        # multiple regression R^2 on all others (least squares)
        A = Z[:, others]
        y = Z[:, i]
        try:
            coef, *_ = np.linalg.lstsq(A, y, rcond=None)
            resid = y - A @ coef
            ss_tot = float(np.dot(y, y))
            r2 = 1.0 - float(np.dot(resid, resid)) / ss_tot if ss_tot > 0 \
                else 0.0
            # adjusted R^2: penalise predictor count so that a metric is
            # not declared redundant merely because there were enough
            # predictors to fit it
            n_obs, p = len(y), len(others)
            if n_obs - p - 1 > 0:
                r2 = 1.0 - (1.0 - r2) * (n_obs - 1) / (n_obs - p - 1)
        except np.linalg.LinAlgError:
            r2 = float("nan")
        out.append(dict(name=names[i],
                        unique=min(1.0, max(0.0, 1.0 - r2))
                        if np.isfinite(r2) else float("nan"),
                        best_partner=names[best_j] if best_j is not None
                        else "—",
                        best_r=best_r))
    return out


def derived_aggregates(M, names, match_frac=0.50, tol=0.02):
    """Metrics that are a rowwise max / min / mean of the others.

    Dashboards are full of these — an overall index that is the worst of
    its components, a total that is the sum of its parts, an average
    across regions. They are fully determined by other columns and yet
    can be almost linearly UNPREDICTABLE from them, because which
    component is currently the maximum keeps switching. The regression
    behind `uniqueness` therefore reports them as highly load-bearing,
    which is exactly backwards.

    This was found on a real air-quality dashboard: `aqi_site` is the
    rowwise max of four sub-indices (75.7% exact match, r = 0.99 against
    that max), and the tool ranked it the single most load-bearing
    metric on the board at 56% unique variance.

    Aggregation is tried on BOTH the raw values and z-scored values,
    because neither alone is sufficient:

      * raw — correct when the columns already share a scale, which is
        the usual case for an index built out of sub-indices. This is
        the one that catches `aqi_site`.
      * z-scored — correct when the columns are in different units and
        a raw max would simply track whichever column has the largest
        numbers.

    Z-scoring alone MISSES the aqi_site case (r falls from 0.99 to
    0.45), because standardising each sub-index separately changes
    which one is largest in any given row and so destroys the very
    relationship being looked for. That failure is why both are tried.

    The test is near-EQUALITY, not correlation. Correlation was tried
    first and was useless: in any redundant dataset the rowwise mean of
    the other columns is simply the common factor, so nearly every
    metric correlates with it. That version flagged 39 of 53 columns on
    a real public-health dashboard and 13 of 17 on a synthetic one —
    detecting redundancy in general rather than derivation in
    particular. Requiring the metric to actually EQUAL the aggregate on
    a majority of rows separates the two.

    For max and min the aggregate is taken over the DOMINATED SET — the
    columns this metric is never below (for a max) or never above (for
    a min) — rather than over all other columns. Aggregating over all
    others is fragile: it fails as soon as a second aggregate is
    present, because the two pollute each other's comparison set. A
    synthetic table containing both a max-index and a grand total
    defeated the all-others version entirely, while the real dashboard
    that motivated the detector passed only because its index happened
    to be numerically largest.

    Mean is still taken over all other columns, so only whole-set means
    are caught. SUBSET sums and means are not detected at all — see the
    README's limitations; a real dashboard's citywide total was found
    to be the sum of its five borough columns (r = 1.00000, exact on
    56.5% of rows) and this function does not flag it.

    A hit is a prompt to check the column's definition, not proof.
    """
    d = len(names)
    if d < 3:
        return []
    sd = M.std(0)
    Z = (M - M.mean(0)) / np.where(sd < 1e-12, 1.0, sd)
    corr = _safe_corr(M)
    out = []
    for i in range(d):
        others = [j for j in range(d) if j != i]
        best = None
        for X in (M, Z):
            x = X[:, i]
            scale = np.std(x)
            if scale < 1e-12:
                continue
            eps = tol * scale

            # max: columns x is never meaningfully below, and which x
            # actually attains on most rows
            for label, sign in (("max", 1.0), ("min", -1.0)):
                dom = [j for j in others
                       if np.mean(sign * (x - X[:, j]) >= -eps) >= 0.99]
                if not dom:
                    continue
                A = X[:, dom]
                gaps = np.abs(A - x[:, None])
                attained = np.min(gaps, axis=1) <= eps
                frac = float(np.mean(attained))
                if frac < match_frac:
                    continue
                # A genuine max/min AGGREGATE is attained by different
                # columns at different times — that switching is what
                # makes it linearly unpredictable in the first place.
                # A metric that is merely a near-duplicate of one other
                # column is also "attained" on every row, but always by
                # the SAME column, and it is already reported as an
                # identity. Requiring the attaining column to vary
                # separates the two; without this, z-scoring makes every
                # near-identity look like a max-aggregate.
                who = np.argmin(gaps[attained], axis=1)
                if len(who) == 0:
                    continue
                counts = np.bincount(who, minlength=len(dom))
                if (counts > 0).sum() < 2:
                    continue
                # ...and it must not simply BE one of them. A near-
                # duplicate is attained on every row too, by the same
                # partner, and belongs in the identities table instead.
                # A share cap was tried here and rejected the real case:
                # aqi_site is attained by three columns but PM2.5
                # dominates 97% of the time, which is normal for a
                # worst-of index in a period with one dominant driver.
                if np.nanmax(np.abs(corr[i, dom])) >= IDENTITY_R:
                    continue
                if best is None or frac > best[0]:
                    agg = A.max(1) if sign > 0 else A.min(1)
                    best = (frac, names[i], label,
                            float(np.corrcoef(x, agg)[0, 1]))

            # whole-set mean only
            A = X[:, others]
            agg = A.mean(1)
            frac = float(np.mean(np.abs(x - agg) <= eps))
            if frac >= match_frac and (best is None or frac > best[0]):
                best = (frac, names[i], "mean",
                        float(np.corrcoef(x, agg)[0, 1]))
        if best:
            out.append(best)
    out.sort(reverse=True)
    return out


# ----------------------------------------------------------------------
# mutual information — dependence that correlation cannot see
# ----------------------------------------------------------------------

def subset_sums(M, names, tol=SUM_TOL, min_frac=SUM_FRAC,
                min_share=SUM_MIN_SHARE, max_children=12, restarts=3,
                max_cols=250):
    """Columns that are the exact SUM of a named subset of other columns.

    This closes the gap `derived_aggregates` leaves. That check tests max
    / min / mean over ALL other columns; a total that sums a NAMED SUBSET
    is invisible to it. The gap was documented against two cases: NYC's
    citywide death count, which is exactly its five borough columns added
    up, and total real-estate loans in FFIEC bank call reports, which is
    the sum of exactly five named children on 99.79% of 1,915 banks.
    Neither was flagged before this existed.

    Why this is not a combinatorial search. There are 2**(N-1) subsets
    per candidate parent -- at N=39 that is 2.7e11. But the relation has
    to hold on EVERY ROW, which turns it into arithmetic:

      * If P is the sum of non-negative children then every child is <= P
        on every row, and after removing some children each remaining
        child is <= the RESIDUAL. Dominance prunes the candidates at
        every step, and the initial dominance set is computed once.
      * Take the largest dominated column first. Real children are large
        relative to what is left; unrelated columns that happen to fit
        are small and are only considered once the residual is nearly
        closed, where `min_share` rejects them.

    So: peel the residual greedily, and require it to close to zero.

    Non-negativity is REQUIRED, and checked rather than assumed.
    Dominance is meaningless for signed data -- a negative child makes
    the parent smaller than its own component -- so any column carrying a
    negative value is excluded as both parent and child. On an income
    statement that excludes most of the sheet. A stated restriction, not
    an oversight.

    Exactness, not correlation, is the test. A parent that is 97% of the
    sum of its children is REJECTED: correlation there is ~1.0, and
    accepting it would re-introduce the failure mode that killed three
    earlier `derived_aggregates` designs. Worked example from the
    corpus: NYC `case_count` correlates with its five boroughs at
    r = 0.99999998 but is exact on only 56% of rows -- there are cases
    with no borough assigned -- and is correctly NOT reported.
    `hospitalized_count` reaches 96% and is also rejected, being under
    `min_frac`. Only `death_count`, exact on 99.56%, is returned.

    Greedy can pick wrong and strand a real relation, so the opening
    choice is restarted over the `restarts` largest dominated columns.
    Past that, a failure is reported as "not found". Missing a real sum
    is far cheaper than inventing one.

    Cost is O(N^2 * rows) for the dominance pass, so it is skipped above
    `max_cols` columns rather than silently taking minutes.

    Returns [(parent, [children], worst_relative_residual), ...].
    """
    n_rows, n_cols = M.shape
    if n_cols > max_cols or n_cols < 3:
        return []

    nonneg = np.all(M >= 0, axis=0)
    scale = np.median(np.abs(M), axis=0)
    # A column that is ~zero everywhere fits inside any residual and
    # explains nothing.
    alive = nonneg & (scale > 0)
    live = [i for i in range(n_cols) if alive[i]]
    if len(live) < 3:
        return []

    out = []
    for p in live:
        P = M[:, p]
        pscale = scale[p]
        band = tol * np.maximum(np.abs(P), pscale)

        # Only columns the parent already dominates can ever be children.
        # One vectorised pass, reused by every restart.
        others = [c for c in live if c != p]
        if not others:
            continue
        sub = M[:, others]
        dom0 = np.mean(sub <= (P + band)[:, None], axis=0) >= min_frac
        cands = [c for c, ok in zip(others, dom0) if ok]
        if len(cands) < 2:
            continue
        cands.sort(key=lambda c: -scale[c])

        best = None
        for start in range(min(restarts, len(cands))):
            resid = P.copy()
            used = []
            forced = cands[start]
            while len(used) < max_children:
                if forced is not None:
                    pick, forced = forced, None
                else:
                    free = [c for c in cands if c not in used]
                    if not free:
                        break
                    ok = np.mean(M[:, free] <= (resid + band)[:, None],
                                 axis=0) >= min_frac
                    fits = [c for c, f in zip(free, ok) if f]
                    if not fits:
                        break
                    pick = fits[0]           # cands is already scale-sorted
                resid = resid - M[:, pick]
                used.append(pick)
                if np.mean(np.abs(resid) <= band) >= min_frac:
                    if len(used) >= 2:
                        shares = [scale[c] / pscale for c in used]
                        if min(shares) >= min_share:
                            worst = float(np.max(
                                np.abs(resid) /
                                np.maximum(np.abs(P), pscale)))
                            cand = (names[p], [names[c] for c in used], worst)
                            if best is None or len(cand[1]) < len(best[1]):
                                best = cand
                    break
            if best is not None:
                break
        if best is not None:
            out.append(best)

    out.sort(key=lambda t: (-len(t[1]), t[0]))
    return out


def _bins_for(n, order=2, per_cell=5.0):
    return max(2, min(12, int((n / per_cell) ** (1.0 / order))))


def _discretise(M, bins, report=None):
    """Near-equal-occupancy binning. Monotone-invariant, so a log-scaled
    metric and its raw form give identical results.

    TIED DATA. Quantile edges on a column with few distinct values land
    repeatedly on the SAME value, and every bin between two identical
    edges is empty by construction. Measured on Poisson(4) at 12 bins:
    four bins empty, occupancy 19-229 instead of a flat 100, and the MI
    bias floor 61% below its continuous value — an artefact of the empty
    bins, not a property of the data.

    That matters because integer counters are what telemetry is made of:
    status codes, pod counts, retries, queue depths. So duplicate edges
    are collapsed before digitising. Fewer bins, none of them empty.

    WHAT THIS DOES NOT DO, because it cannot: equalise occupancy. If 30%
    of a column is the integer 4, no binning puts less than 30% of the
    mass wherever 4 lands. A fix claiming otherwise would be lying, so
    columns that could not be equalised are REPORTED as tied instead.
    """
    out = np.empty(M.shape, dtype=np.int16)
    edges = np.linspace(0, 1, bins + 1)[1:-1]
    for j in range(M.shape[1]):
        col = M[:, j]
        q = np.quantile(col, edges)
        # Collapse repeated edges. On continuous data `np.unique` is a
        # no-op and the result is bit-identical to the previous version;
        # on tied data it is the whole fix.
        qu = np.unique(q)
        lab = np.digitize(col, qu)
        # `digitize` still leaves bin 0 empty whenever the column's
        # minimum equals the first edge — which is the common case once
        # duplicates are collapsed, because the first edge IS the modal
        # value. Compacting to consecutive labels removes the last empty
        # bin by construction rather than by argument. On continuous
        # data the labels are already consecutive, so this is a no-op
        # and B2's bit-identity holds.
        _, lab = np.unique(lab, return_inverse=True)
        out[:, j] = lab
        if report is not None:
            k_used = int(out[:, j].max()) + 1
            counts = np.bincount(out[:, j], minlength=k_used)
            report.append({
                "column": j,
                "distinct_values": int(np.unique(col).size),
                "bins_requested": bins,
                "bins_used": k_used,
                "empty_bins": int((counts == 0).sum()),
                # A column is TIED when the edges had to be collapsed:
                # the requested resolution is not available in the data.
                "tied": bool(len(qu) < len(q)),
                "occupancy_min": int(counts[counts > 0].min()) if k_used else 0,
                "occupancy_max": int(counts.max()) if k_used else 0,
            })
    return out


def _mi_matrix(D, d, pairs=None):
    """Pairwise mutual information over an already-discretised matrix.

    Joint counts come from `np.bincount` on a combined index rather than
    `np.unique(axis=0)`, which sorts n rows for every pair. Every column
    is already an int bin label in [0, K), so a pair collapses to
    `i * K + j` and is counted in one pass. Verified bit-identical to the
    sorting version on gaussian, lognormal, integer-count and heavily
    tied data — not "within tolerance", identical.

    `pairs` restricts the computation. `nonlinear_pairs` only ever
    reports pairs with |r| < 0.5, so MI for the rest was computed and
    discarded; passing the surviving pairs is lossless by construction.

    NOTE ON THE DTYPE. The combined index is built in `np.intp`, not
    `np.int64`. `np.bincount` casts its input to `intp` under the
    'safe' rule, and **`intp` is 32-bit on wasm32** — so an explicit
    int64 upcast, which is free on x86-64, raises

        TypeError: Cannot cast array data from dtype('int64') to
        dtype('int32') according to the rule 'safe'

    inside Pyodide. It did, on the live site, on every audit. The test
    suite could not see it because CPython on x86-64 is the only
    platform it runs on; the browser is the platform every visitor
    uses. `intp` is correct on both by construction.
    """
    n = D.shape[0]
    K = int(D.max()) + 1 if D.size else 1

    # The combined index reaches K*K - 1 and `bincount` allocates a
    # dense array of that length. `_bins_for` caps K at 12, so this is
    # ~143 cells — but the bound is asserted rather than assumed. A
    # hand-built D with K = 30001 asks for a 6.7 GiB allocation on
    # 64-bit and blows the index width on 32-bit; both should be a
    # named refusal rather than a MemoryError or a wrapped index.
    MAX_K = 4096                            # 16.7M cells, ~134 MB
    if K > MAX_K or K * K > np.iinfo(np.intp).max:
        raise ValueError(
            f"bin count {K} is out of range for the joint-count table "
            f"(max {MAX_K}); _bins_for caps this at 12, so a value this "
            f"large means D was not produced by _discretise")

    hs = []
    for j in range(d):
        c = np.bincount(D[:, j], minlength=K)
        p = c[c > 0] / n
        hs.append(float(-(p * np.log2(p)).sum()))
    out = {}
    it = pairs if pairs is not None else itertools.combinations(range(d), 2)
    for i, j in it:
        joint = np.bincount(D[:, i].astype(np.intp) * K + D[:, j],
                            minlength=K * K)
        p = joint[joint > 0] / n
        hij = float(-(p * np.log2(p)).sum())
        out[(i, j)] = hs[i] + hs[j] - hij
    return out


def _mi_bias_floor(D, d, reps=6, seed=0):
    """Mean pairwise MI over column-independently shuffled data.

    Finite-sample MI is biased upward: two genuinely independent
    columns still score above zero, and the bias grows with bin count
    and shrinks with sample size. Shuffling destroys any real
    dependence while preserving n, bins and the marginals, so what
    remains is the estimator's own noise. Anything at or below this
    floor is not evidence of dependence. Omitting this step makes the
    tool report independent metrics as related — which it did, until a
    test with planted independent noise caught it.

    A REJECTED OPTIMISATION, recorded here because it is the obvious
    one. This floor is 86% of the scan's cost, and the tempting saving
    is to estimate it from a sample of pairs or cache it by (n, bins),
    on the theory that equal-occupancy binning makes it data-
    independent. That theory was pre-registered as M3 in
    MI_SCALING_PREREG.md and MEASURED FALSE: floors across six datasets
    at identical n, d and bins spread 68%. Equal-occupancy binning
    cannot equalise data with fewer distinct values than bins —
    Poisson(4) left 4 of 12 bins empty and the floor fell from 0.0746
    to 0.0290. The registered rule was that M3 failing abandons that
    work, and it does. The floor is still computed over every pair.
    Parts A and B alone gave 63x, which cleared the speed target with
    room, so the contested optimisation was not needed.
    """
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(reps):
        S = np.column_stack([rng.permutation(D[:, j]) for j in range(d)])
        vals.extend(_mi_matrix(S, d).values())
    return float(np.mean(vals)) if vals else 0.0


# ---------------------------------------------------------------------
# Correlation drift. Registered in DRIFT_PREREG.md before this was written.
# ---------------------------------------------------------------------
DRIFT_MIN_ROWS = 30     # per window; below this the check refuses
DRIFT_Q = 0.05          # Benjamini-Hochberg false discovery rate
DRIFT_MAX_INFLATION = 3.0   # observed/theoretical spread past which the
                            # file is too non-stationary to trust


def _bh(p, q):
    """Benjamini-Hochberg step-up. Returns (rejected mask, q-values).

    Bonferroni over 1,770 pairs would suppress real findings along with
    the false ones; FDR controls the proportion of reported pairs that
    are wrong, which is the quantity an operator actually cares about
    when handed a list.

    The q-values are returned rather than a Bonferroni p*m, because the
    first version reported the latter beside a BH rejection — so a
    flagged pair displayed an adjusted p of 0.053 next to a threshold
    of 0.05 and looked like a bug in the rejection rule. Reporting one
    correction while deciding by another is its own small dishonesty.
    """
    p = np.asarray(p, dtype=float)
    m = p.size
    if m == 0:
        return np.zeros(0, dtype=bool), np.zeros(0)
    order = np.argsort(p)
    ranked = p[order]
    scaled = ranked * m / np.arange(1, m + 1)
    # monotone from the top: q_(i) = min over k >= i
    qvals_sorted = np.minimum.accumulate(scaled[::-1])[::-1]
    qvals = np.empty(m)
    qvals[order] = np.clip(qvals_sorted, 0.0, 1.0)
    rejected = np.zeros(m, dtype=bool)
    passed = ranked <= q * (np.arange(1, m + 1) / m)
    if passed.any():
        rejected[order[:int(np.max(np.where(passed)[0])) + 1]] = True
    return rejected, qvals


def _corr_of(M):
    C = np.corrcoef(M, rowvar=False)
    return np.atleast_2d(np.nan_to_num(C, nan=0.0))


def correlation_drift(names, M, ordered=False, q=DRIFT_Q,
                      min_rows=DRIFT_MIN_ROWS, naive=False):
    """Pairs whose correlation changed between the first and second half.

    Registered in DRIFT_PREREG.md, negative controls first. Returns a
    dict with `status` and `pairs`; `pairs` is empty unless status is
    "ok".

    THE THREE THINGS THIS HAS TO GET RIGHT, and why each is here:

    1. ORDER. Splitting rows into halves asserts a before and an after.
       On entity-indexed data — one row per bank, per model, per host —
       there is no such thing, and "the first half of the banks" is not
       a period. The check refuses unless the caller declares the rows
       ordered. The trend check does not consult this and fired on 541
       language models; see AI_EVAL_PREREG.md finding 1.

    2. SAMPLE SIZE. Correlation on n/2 rows is noisier than on n, so an
       absolute threshold on |dr| is wrong at every size but the one it
       was tuned at. Fisher z (arctanh r) is approximately normal with
       SE 1/sqrt(n-3), so the difference of two windows has a known SE
       and the statistic is scale-free by construction.

    3. THE SHARED INCIDENT. During an outage every metric moves at once
       and EVERY pair's correlation rises together. A naive difference
       reports hundreds of new couplings for one event. So the
       statistic is centred on the MEDIAN shift across all pairs: a
       genuine drift in a few pairs barely moves the median, while an
       incident that moves everything moves it a lot and is subtracted
       out. `naive=True` disables this and exists only so the
       registered comparison in D4 can be measured rather than asserted.
    """
    n_all, d = M.shape
    if not ordered:
        return {"status": "not_applicable",
                "reason": "rows are not declared to be in time order; "
                          "a before and an after would be invented",
                "pairs": []}
    if d < 2:
        return {"status": "not_applicable",
                "reason": "fewer than two metrics", "pairs": []}

    cut = n_all // 2
    A, B = M[:cut], M[cut:]
    n1, n2 = A.shape[0], B.shape[0]
    if min(n1, n2) < min_rows:
        return {"status": "insufficient_rows",
                "reason": f"{min(n1, n2)} rows per window; {min_rows} needed "
                          f"before a change can be told from sampling noise",
                "pairs": []}

    # Constant columns within a window make correlation undefined; they
    # are excluded rather than silently returned as 0, because "no
    # relationship" and "no variation to have a relationship with" are
    # different statements.
    live = [j for j in range(d)
            if np.std(A[:, j]) > 0 and np.std(B[:, j]) > 0]
    if len(live) < 2:
        return {"status": "not_applicable",
                "reason": "fewer than two metrics vary in both windows",
                "pairs": []}
    dropped = [names[j] for j in range(d) if j not in live]

    CA, CB = _corr_of(A[:, live]), _corr_of(B[:, live])
    iu = np.triu_indices(len(live), k=1)
    rA, rB = CA[iu], CB[iu]

    clip = 1.0 - 1e-9
    dz = np.arctanh(np.clip(rB, -clip, clip)) - np.arctanh(np.clip(rA, -clip, clip))
    se = float(np.sqrt(1.0 / max(n1 - 3, 1) + 1.0 / max(n2 - 3, 1)))

    centre = 0.0 if naive else float(np.median(dz))
    resid = dz - centre

    # Robust spread, for two purposes: as a floor on the denominator so
    # the test is conservative when the data is messier than the normal
    # theory assumes, and as a report on HOW much messier.
    mad = float(np.median(np.abs(resid - np.median(resid)))) * 1.4826
    scale = se if naive else max(se, mad)
    inflation = (mad / se) if se > 0 else 0.0

    stat = resid / scale
    # two-sided normal tail without scipy: numpy only, by design
    p = np.array([math.erfc(abs(s) / math.sqrt(2.0)) for s in stat])
    rej, qvals = _bh(p, q)

    pairs = []
    for k in np.where(rej)[0]:
        i, j = iu[0][k], iu[1][k]
        pairs.append({
            "metric_a": names[live[i]], "metric_b": names[live[j]],
            "r_first": float(rA[k]), "r_second": float(rB[k]),
            "delta_r": float(rB[k] - rA[k]),
            "z": float(stat[k]), "q_value": float(qvals[k]),
            "direction": "coupled" if abs(rB[k]) > abs(rA[k]) else "decoupled",
        })
    pairs.sort(key=lambda x: -abs(x["z"]))

    return {
        "status": "ok",
        "pairs": pairs,
        "rows_per_window": [n1, n2],
        "pairs_tested": int(len(p)),
        "shared_shift": float(centre),
        "se_theoretical": se,
        "spread_inflation": float(inflation),
        "excluded": dropped,
        # A file whose pair-to-pair spread far exceeds the normal theory
        # is not stationary enough for this test to mean what it says.
        # Reported rather than silently absorbed into the threshold.
        "unstable": bool(inflation > DRIFT_MAX_INFLATION),
    }


def nonlinear_pairs(M, names, top=6):
    """Pairs whose empirical mutual information greatly exceeds what
    their linear correlation would imply — dependence a correlation
    matrix reports as absent.

    Returns [] when there are too few rows to estimate MI honestly.
    """
    n, d = len(M), len(names)
    if n < MIN_ROWS_MI:
        return []
    bins = _bins_for(n, 2)
    D = _discretise(M, bins)
    C = _safe_corr(M)
    # Only |r| < 0.5 pairs can pass the gate below, so only they need
    # MI. Lossless: the gate is unchanged, and the reported set was
    # verified pair-for-pair and order-for-order against the previous
    # implementation on all six corpora.
    cand = [(i, j) for i, j in itertools.combinations(range(d), 2)
            if np.isfinite(C[i, j]) and abs(C[i, j]) < 0.5]
    mis = _mi_matrix(D, d, pairs=cand)
    floor = _mi_bias_floor(D, d)

    out = []
    for (i, j), mi in mis.items():
        r = float(C[i, j])
        if not np.isfinite(r):
            continue
        mi_gauss = -0.5 * math.log2(max(1.0 - r * r, 1e-12))
        # measure dependence in EXCESS of the estimator's own noise
        excess = mi - floor
        ratio = excess / mi_gauss if mi_gauss > 1e-9 else float("inf")
        # interesting only when correlation is LOW, dependence is HIGH,
        # and the dependence clears the bias floor by a clear margin
        if abs(r) < 0.5 and excess > floor and ratio >= MI_RATIO_FLAG \
                and excess > 0.05:
            out.append((ratio, names[i], names[j], r, mi))
    out.sort(reverse=True)
    return out[:top]


# ----------------------------------------------------------------------
# the audit
# ----------------------------------------------------------------------
def audit(path, ignore=(), max_rows=None, scale_by=(),
          scale_exempt=(), basis=None, ordered=None):
    """Audit a CSV file."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        text = f.read()
    names, M, notes = load_csv_text(text, ignore=ignore, max_rows=max_rows,
                                    label=path)
    return _audit_core(names, M, notes, os.path.abspath(path),
                       os.path.basename(path), scale_by=scale_by,
                       scale_exempt=scale_exempt, basis=basis,
                       ordered=ordered, evidence=header_is_ordered(text))


def audit_text(text, ignore=(), max_rows=None, label="<input>",
               scale_by=(), scale_exempt=(), basis=None, ordered=None):
    """Audit CSV text. No filesystem required — for Pyodide, serverless
    handlers, and anywhere the data arrives as a string."""
    names, M, notes = load_csv_text(text, ignore=ignore, max_rows=max_rows,
                                    label=label)
    return _audit_core(names, M, notes, label, label, scale_by=scale_by,
                       scale_exempt=scale_exempt, basis=basis,
                       ordered=ordered, evidence=header_is_ordered(text))


def build_view(names, X):
    """Every per-basis figure, for one transformed matrix.

    One function so that every basis is measured identically. A basis
    that got a cheaper summary than the others could not be compared
    with them, and comparison between bases is the entire point -- the
    trend confound is visible only as raw-against-differenced, and the
    scale confound only as dollars-against-ratios.

    Computing this per basis was profiled before being made general:
    at 60 metrics x 2000 rows it costs 0.23s, against 9.54s for the
    one-time nonlinear/MI scan, and retains ~28KB. The cost of an extra
    basis is roughly 2% of a run, so there is no reason to economise
    here and every reason not to.
    """
    return dict(pr=participation_ratio(X), n95=components_for(X),
                identities=pairs_above(X, names, IDENTITY_R),
                redundant=pairs_above(X, names, REDUNDANT_R),
                clusters=cluster(names, X), unique=uniqueness(X, names),
                corr=_safe_corr(X), names=list(names),
                n_metrics=len(names), n_rows=len(X))



def ratio_basis(names, M, denom, exempt=()):
    """Divide every column by one declared column. Returns (names, X, notes).

    Deliberately NOT part of `build_view`. Transforms produce a
    (names, matrix) pair; `build_view` measures whatever it is handed,
    identically, for every basis. The moment the measuring function knows
    which basis it is looking at, bases stop being comparable by
    construction -- and comparison between bases is the only way either
    confound is visible.

    The denominator is a DECLARATION. Nothing here inspects the data to
    decide whether a column looks like a good scaling variable.

    `exempt` names columns that are ALREADY scale-free and must pass
    through untouched. This is not a convenience. Dividing a column that
    carries no size by a size INJECTS a 1/size factor -- manufacturing
    the very confound this basis exists to remove. Measured on the FDIC
    corpus before the parameter existed:

        r(ROA, ROE)          +0.6008  ->  +0.9811   past the 0.90 cluster
        r(ROA, NIMY)         +0.1133  ->  -0.3847   sign flipped
        r(NIMY/ASSET, 1/ASSET)        ->  +0.8292   the injected factor

    ROA, ROE and NIMY are rates; ASSET is a size. The transform made two
    weakly-related profitability measures look near-identical. Note that
    this crosses the 0.90 threshold that SCALE_BASIS_PREREG C7 certified
    closure would never reach -- C7 was about closure and remains true;
    this is a different defect and was found by checking a semantic
    question the closure fixture was never designed to ask.

    Exemption is a declaration because the alternative is inference:
    guessing which columns are "already ratios" from their values is the
    classifier trap this engine refuses everywhere else. Columns left
    unexempted ARE divided, and any that end up strongly correlated with
    1/denominator are reported in the notes so the mistake announces
    itself.

    Four things happen to columns, and each is reported rather than done
    quietly:

    1. The denominator itself becomes exactly 1.0 and is DROPPED. Not
       tidiness -- a constant column makes np.corrcoef divide by a zero
       standard deviation, and the NaN propagates into the spectrum.
    2. Any other column that was exactly proportional to the denominator
       also becomes constant and is dropped. That is INFORMATIVE: such a
       column carried nothing but scale, which is the finding, so it is
       named in the notes rather than silently removed.
    3. Rows where the denominator is zero, negative or non-finite are
       dropped, because the quotient is undefined. A scaling variable is
       a size, and a size is positive.
    4. The resulting view therefore has FEWER metrics and possibly fewer
       rows than the raw view. Both are recorded per view, because a
       participation ratio of 14.8 over 38 metrics and one of 14.8 over
       39 are different claims about redundancy.

    Row-dropping is confined to THIS basis. Declaring a denominator must
    never change what another basis reports -- if it did, the
    declaration would be altering evidence rather than selecting a
    lens.
    """
    notes = []
    if denom not in names:
        raise ValueError(
            f"scale_by column {denom!r} is not in the data. "
            f"Available: {', '.join(names)}")
    j = names.index(denom)
    d = M[:, j]

    good = np.isfinite(d) & (d > 0)
    if not good.all():
        notes.append(f"ratio:{denom} — dropped {int((~good).sum())} row(s) "
                     f"where {denom} was zero, negative or missing")
    X = M[good] / M[good, j][:, None]

    # exempt columns are restored to their untransformed values
    exempt = set(exempt)
    missing = exempt - set(names)
    if missing:
        raise ValueError(f"scale_exempt names unknown column(s): "
                         f"{', '.join(sorted(missing))}")
    for i, nm in enumerate(names):
        if nm in exempt:
            X[:, i] = M[good, i]
    if exempt:
        notes.append(f"ratio:{denom} — {', '.join(sorted(exempt))} left "
                     f"unscaled (declared already scale-free)")

    keep, dropped_const = [], []
    for i, nm in enumerate(names):
        if i == j:
            continue
        if np.std(X[:, i]) < 1e-12:
            dropped_const.append(nm)
        else:
            keep.append(i)

    # falsifier: a transformed column that tracks 1/denominator was
    # probably already scale-free and should have been exempted
    inv = 1.0 / M[good, j]
    suspect = []
    for i in keep:
        if names[i] in exempt:
            continue
        sd = np.std(X[:, i])
        if sd > 0 and abs(np.corrcoef(X[:, i], inv)[0, 1]) >= 0.70:
            suspect.append(names[i])
    if suspect:
        notes.append(
            f"ratio:{denom} — WARNING: {', '.join(suspect)} now track "
            f"1/{denom} at |r| >= 0.70. Dividing an already scale-free "
            f"column by a size injects a size factor. Declare them in "
            f"scale_exempt if they were already rates.")
    if dropped_const:
        notes.append(f"ratio:{denom} — {', '.join(dropped_const)} became "
                     f"constant, i.e. exactly proportional to {denom}: "
                     f"carried scale and nothing else")
    notes.append(f"ratio:{denom} — {denom} itself dropped (it is 1.0 "
                 f"by construction in this basis)")
    return [names[i] for i in keep], X[:, keep], notes



def basis_conflicts(res, reference="raw", jump=0.25, floor=REDUNDANT_R):
    """Pairs a transform made MORE correlated. The falsifier for a basis.

    Every basis is supposed to REMOVE a shared factor. A pair that comes
    out substantially more correlated than it went in means the
    transform did something to that pair, and the operator should know
    which pair and by how much.

    IT CANNOT TELL YOU WHICH OF TWO THINGS HAPPENED, and claiming
    otherwise would be the overreach this catalogue exists to prevent.
    Both were observed on the same corpus:

      INJECTED, and a defect. Dividing ROA and ROE by ASSET -- a size
      neither of them carries -- took r from +0.6008 to +0.9811, past
      the 0.90 clustering threshold, inviting the tool to call two
      different profitability measures redundant. Nothing real was
      found; a size factor was manufactured.

      REVEALED, and a genuine finding. With ROA correctly exempted,
      NETINC/ASSET went from r = +0.0555 against ROA to +0.9997 --
      because NETINC/ASSET *is* ROA, definitionally. The basis exposed a
      duplicate the dollar view had hidden. Same signature, opposite
      meaning. Likewise INTINC/ASSET against NIMY at +0.9470.

    The numbers alone do not separate these. Only someone who knows what
    the metrics mean can, which is the standing limit on this engine.

    This exists because the narrower check inside `ratio_basis` -- does a
    transformed column track 1/denominator -- caught only one of three
    real cases on the FDIC corpus. It flagged NIMY at r = 0.83 against
    1/ASSET, and missed ROA and ROE entirely, because their injected
    factor showed up not against the denominator but against EACH OTHER:
    r(ROA, ROE) went from +0.6008 raw to +0.9811 after both were divided
    by a size neither of them carried, crossing the 0.90 clustering
    threshold and inviting the tool to call two different profitability
    measures redundant.

    A pair-level comparison catches both shapes, because an injected
    common factor is by definition shared.

    Reported, never acted on. This does not disqualify a basis or switch
    the headline -- it hands the operator the specific pairs and lets
    them decide which of the two readings applies. A detector that
    silently re-routed on its own findings would be the classifier trap
    this engine refuses everywhere else.

    A narrower check inside `ratio_basis` -- does a transformed column
    track 1/denominator -- is kept alongside this one because it catches
    a case this misses, and vice versa. On FDIC it flagged NIMY at
    r = 0.83 against 1/ASSET while missing ROA and ROE entirely, whose
    injected factor appeared only against each other.
    """
    out = []
    if reference not in res["views"]:
        return out
    ref = res["views"][reference]
    ref_idx = {n: i for i, n in enumerate(ref["names"])}
    for basis, v in res["views"].items():
        if basis == reference:
            continue
        idx = {n: i for i, n in enumerate(v["names"])}
        shared = [n for n in v["names"] if n in ref_idx]
        for a in range(len(shared)):
            for b in range(a + 1, len(shared)):
                x, y = shared[a], shared[b]
                r_new = float(v["corr"][idx[x], idx[y]])
                r_old = float(ref["corr"][ref_idx[x], ref_idx[y]])
                if not (np.isfinite(r_new) and np.isfinite(r_old)):
                    continue
                if abs(r_new) >= floor and abs(r_new) - abs(r_old) >= jump:
                    out.append((basis, x, y, r_old, r_new))
    out.sort(key=lambda t: -(abs(t[4]) - abs(t[3])))
    return out


def header_is_ordered(text):
    """Does this file's header carry a time index?

    The only evidence of row order available without asking. Every
    time-series corpus in this project has one — `timestamp`, `date`,
    `datetime`, `month` — and both entity-indexed corpora have none:
    FDIC starts at `BKPREM`, the leaderboard at `Average`. So this is
    a usable signal, and it is the same TIME_LIKE set the loader
    already drops columns by, rather than a second opinion that could
    disagree with the first.

    It is EVIDENCE, not a declaration. Whatever it returns is reported
    as ASSUMED unless a caller states otherwise, for the same reason
    the basis is: an inference presented as a fact is how a tool hands
    someone a confident wrong answer.
    """
    try:
        first = next(csv.reader(io.StringIO(text)))
    except StopIteration:
        return False
    return any(h.strip().lower() in TIME_LIKE for h in first)


def _order_state(ordered, evidence):
    """Resolve the row-order question into something reportable.

    Time-dependent checks — the trend confound and correlation drift —
    are meaningless without row order. `AI_EVAL_PREREG.md` finding 1
    records the trend check firing on 541 language models and naming
    shared CALENDAR movement in data that has no calendar. That defect
    is closed here, and the drift check consults the same gate rather
    than growing a second one that could drift out of step.
    """
    if ordered is None:
        return {"ordered": bool(evidence), "declared": False,
                "evidence": ("a time-like column is present"
                             if evidence else
                             "no time-like column in the header")}
    return {"ordered": bool(ordered), "declared": True,
            "evidence": "declared by the caller"}


def _audit_core(names, M, notes, path, file, scale_by=(),
                scale_exempt=(), basis=None, ordered=None, evidence=False):
    d = len(names)

    # Every basis is computed, always. Nothing here branches on a
    # property of the data: the transforms are fixed, and a declaration
    # later selects which one is the HEADLINE. Inference about which
    # basis suits the data would make output depend on classifier
    # behaviour, and therefore on classifier version -- which is exactly
    # the silent-wrong-answer failure this engine exists to expose.
    views = {
        "raw": build_view(names, M),
        "differenced": build_view(names, differenced(M)),
    }
    # One extra basis per declared denominator. Profiled at ~2% of a run
    # each, so there is no reason to make the caller pick just one --
    # platform telemetry has no single denominator the way a balance
    # sheet has total assets.
    if isinstance(scale_by, str):
        scale_by = (scale_by,)
    for denom in scale_by:
        rn, RX, rnotes = ratio_basis(names, M, denom,
                                     exempt=scale_exempt)
        views[f"ratio:{denom}"] = build_view(rn, RX)
        notes.extend(rnotes)

    res = dict(
        path=path, file=file,
        names=names, n_rows=len(M), n_metrics=d, notes=notes,
        views=views,
        # `raw` and `diff` are ALIASES of the same objects, not copies.
        # Kept so the refactor to a view dict does not move ~10 call
        # sites and the backend service in the same commit as a change
        # to the mathematics.
        raw=views["raw"],
        diff=views["differenced"],
        nonlinear=nonlinear_pairs(M, names),
        mi_skipped=len(M) < MIN_ROWS_MI,
        aggregates=derived_aggregates(M, names),
        subset_sums=subset_sums(M, names),
    )
    res["basis_conflicts"] = basis_conflicts(res)
    # Which basis the headline reports. A declaration, not a deduction.
    # `basis_declared` records whether anyone actually made that
    # declaration or whether the engine fell back. The fallback is a
    # real assumption -- that rows are ordered observations -- and F0
    # showed it fails SILENTLY on entity-indexed data, returning very
    # nearly the right number for the wrong reason. So the fact of
    # assuming is carried in the result and printed beside the headline,
    # not buried in a note.
    res["headline"] = basis if basis else "differenced"
    res["basis_declared"] = bool(basis)
    if res["headline"] not in res["views"]:
        raise ValueError(
            f"basis {res['headline']!r} was not computed. Available: "
            f"{', '.join(sorted(res['views']))}")
    res["headline_pr"] = res["views"][res["headline"]]["pr"]
    res["trend_gap"] = res["raw"]["pr"] - res["diff"]["pr"]
    # Gated on row order. The gap is still COMPUTED and reported — it is
    # a real difference between two views and hiding it would be its own
    # dishonesty — but it is not allowed to FIRE as a calendar finding
    # on data with no calendar.
    res["order"] = _order_state(ordered, evidence)
    res["trend_dominated"] = bool(
        res["order"]["ordered"]
        and np.isfinite(res["trend_gap"]) and res["trend_gap"] < -0.5)

    # rows per metric: below ~10 the per-metric regression is strained
    # even after the adjustment, and correlations themselves get noisy
    res["rows_per_metric"] = (len(M) - 1) / d if d else float("nan")
    res["crowded"] = res["rows_per_metric"] < 10

    # Correlation drift. Registered in DRIFT_PREREG.md, scored 8/9
    # before being wired in here. Computed on the differenced basis:
    # two metrics sharing a trend correlate in both windows regardless,
    # and the question is about their relationship, not their drift.
    #
    # GATED ON THE ASSURANCE GRADE, as registered. Splitting the file in
    # half halves the evidence for a check that already needs more of it
    # than the others, so a grade the rest of the report treats as
    # marginal is not good enough here. The first wired version returned
    # 114 pairs on a grade-C corpus before this gate was added — a
    # handling decision fixed in advance and then not implemented, which
    # is exactly what pre-registration is supposed to catch.
    grade = assurance(res)["grade"]
    if not res["order"]["ordered"]:
        res["drift"] = correlation_drift(names, M, ordered=False)
    elif grade in ("C", "D"):
        res["drift"] = {
            "status": "insufficient_evidence",
            "reason": (f"evidence grade {grade} — {res['rows_per_metric']:.1f} "
                       f"rows per metric, and splitting the file halves that "
                       f"again; drift needs more history than the other checks, "
                       f"not less"),
            "pairs": [], "grade": grade}
    else:
        res["drift"] = correlation_drift(
            names, np.diff(M, axis=0) if len(M) > 1 else M, ordered=True)
    return res


def verdict_line(res):
    """One sentence a person can act on."""
    pr = res["headline_pr"]
    d = res["views"][res["headline"]]["n_metrics"]
    ratio = pr / d if d else float("nan")
    if ratio >= 0.8:
        return (f"{d} metrics carrying about {pr:.1f} independent signals — "
                f"little redundancy. This set is close to fully informative.")
    if ratio >= 0.5:
        return (f"{d} metrics carrying about {pr:.1f} independent signals — "
                f"moderate redundancy. Some consolidation is possible.")
    return (f"{d} metrics carrying about {pr:.1f} independent signals — "
            f"heavy redundancy. Most of this set restates a small number "
            f"of underlying quantities.")




# ATTEMPTED AND WITHDRAWN: smoothing-pair detection.
#
# The goal was to flag a metric plotted alongside its own rolling
# average (`cases` and `cases_7day_avg`), because differencing breaks
# that relationship and the headline therefore UNDERSTATES redundancy on
# any dashboard carrying both. Two designs were built and both failed on
# real data:
#
#   1. High raw |r| + low differenced |r|. That is also the signature of
#      two unrelated metrics sharing a trend, which the headline already
#      handles. Flagged `arr` and `support_tickets` — independent random
#      walks — as a smoothing pair.
#   2. The above plus a ROUGHNESS discriminator, sd(diff(x))/sd(x), on
#      the theory that a rolling average moves less period-to-period
#      than its source. It does — but it is also smoother than every
#      other rough metric on the board, so the test does not isolate the
#      pair. Produced 476 false positives on a 53-metric dashboard while
#      missing the one confirmed real case.
#
# The real case (ozone 1-hour vs 8-hour average) sits at raw r = 0.749
# because averaging a strongly diurnal signal genuinely decorrelates it,
# while the false positives sit at 0.93. No threshold separates them.
#
# What would work: name-based pairing (`x` against `x_7day_avg`,
# `x_p99`, `x_5m`) combined with the statistical signature, or a lagged
# cross-correlation showing one series leads the other by the window
# length. Both are real work, and the second is the only one that is a
# measurement rather than a guess about labels.
#
# Until then this remains a DOCUMENTED LIMITATION, not a shipped check.
# Claiming detection we do not have is the exact failure mode this
# product exists to prevent.



# Columns of the blast-radius worksheet. Defined here so the CLI and the
# hosted service emit the SAME shape and a worksheet completed against
# one can be uploaded to the other. The first block is filled by the
# engine; the second is what a human must answer before anything
# executable is generated.
# `scan_evidence` is MACHINE-supplied and belongs in the evidence block,
# not the attestation block. The first version of the reference-graph
# integration wrote its findings straight into `referenced_by_monitors`,
# which reads as a boolean — `parse_worksheet` rejected the text as
# unanswered, so a pre-filled worksheet could never unlock an export.
#
# Keeping them apart is also what SAFETY_BOUNDARIES.md condition 3
# requires: the scan reports where it looked and what it saw, and the
# human answers yes or no. Automating the look-up is the saving;
# automating the answer is the thing this product sells against.
# `tier` groups rows by WHAT WAS CHECKED, never by how risky the row is.
# "Tier 1 (Zero-Risk)" was proposed and refused: a tier is an extremely
# efficient way to smuggle a clearance into a document whose entire job
# is to withhold one. Condition 3 again — evidence, never clearance.
#
# The scope travels inside the cell rather than in a legend, because a
# legend is the first thing lost when a row is pasted into a ticket.
# With no reference scan run, tiers A and B say NO SCAN, because "nothing
# found" is vacuous when nothing was looked at.
TIER_A = "A - identity pair"
TIER_B = "B - high redundancy"
TIER_C = "C - conflict"
TIER_CONTEXT = ""          # KEEP rows: present for context, not a decision
TIER_UNSCANNED = "NO SCAN (re-run with --refs)"

WORKSHEET_EVIDENCE = ["metric", "tier", "recommendation", "unique_variance",
                      "duplicated_by", "identity_partner", "cluster_id",
                      "scan_evidence"]
WORKSHEET_ATTESTATION = ["referenced_by_monitors", "referenced_by_slos",
                         "referenced_by_other_dashboards",
                         "referenced_by_runbooks",
                         "last_queried_days_ago", "reviewer", "note"]
WORKSHEET_COLUMNS = WORKSHEET_EVIDENCE + WORKSHEET_ATTESTATION


def blast_radius_worksheet(res):
    """CSV an operator completes before any metric is archived.

    The engine can prove a metric carries no variance the others lack.
    It cannot see that the same metric is the sole condition on a paging
    monitor, an SLO error budget, or a compliance report — none of which
    appear in a CSV of values. Filling this in IS the safety check, and
    the completed file is what unlocks executable exports.

    Evidence columns are pre-filled so a reviewer can see WHY each
    metric is listed without leaving the file.
    """
    keep = identity_representatives(res)
    cands = {u["name"] for u in deletion_candidates(res)}
    partner = {}
    for _ar, _r, a, b in (list(res["diff"]["identities"])
                          + list(res["raw"]["identities"])):
        partner.setdefault(a, b)
        partner.setdefault(b, a)
    cluster_of = {}
    for i, c in enumerate(res["diff"]["clusters"]):
        if len(c) > 1:
            for nm in c:
                cluster_of[nm] = i

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(WORKSHEET_COLUMNS)

    # Rows are grouped so a reviewer meets the same number of decisions
    # in a smaller number of kinds. Tier C cannot be assigned here — a
    # conflict needs a reference scan — so `annotate_worksheet` upgrades
    # rows to C and rewrites the scope text. Until then every tier
    # carries NO SCAN, which is the honest state of a worksheet produced
    # without --refs.
    rows = []
    for u in sorted(res["diff"]["unique"], key=lambda x: x["unique"]):
        nm = u["name"]
        rec = ("ARCHIVE" if nm in cands
               else "KEEP (identity survivor)" if nm in keep
               else "KEEP")
        if nm not in cands:
            tier = TIER_CONTEXT
        elif partner.get(nm):
            tier = f"{TIER_A} - {TIER_UNSCANNED}"
        else:
            tier = f"{TIER_B} - {TIER_UNSCANNED}"
        rows.append(([nm, tier, rec, round(float(u["unique"]), 4),
                      u["best_partner"], partner.get(nm, ""),
                      cluster_of.get(nm, ""), ""]
                     + [""] * len(WORKSHEET_ATTESTATION)))

    for row in sorted(rows, key=lambda r: worksheet_sort_key(r[1])):
        w.writerow(row)
    return buf.getvalue()


def worksheet_sort_key(tier):
    """Order rows C, A, B, then context — decisions before background.

    Kept in one place because `annotate_worksheet` re-sorts after
    assigning tier C, and two orderings that disagree would silently
    depend on which ran last.
    """
    if tier.startswith(TIER_C):
        return 0
    if tier.startswith(TIER_A):
        return 1
    if tier.startswith(TIER_B):
        return 2
    return 3


def worksheet_tier_counts(csv_text):
    """{tier letter: count} for the summary line the CLI prints."""
    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows or "tier" not in rows[0]:
        return {}
    i = rows[0].index("tier")
    out = {}
    for r in rows[1:]:
        if i < len(r) and r[i]:
            out[r[i][:1]] = out.get(r[i][:1], 0) + 1
    return out


def summary_markdown(payload):
    """A pull-request or ticket body, built from `report_payload`.

    Takes the PAYLOAD rather than the result object so that the text
    cannot disagree with what the caller rendered — same numbers, same
    rounding, one source. CLI, hosted API and browser all emit byte
    identical output for the same audit.

    ON WHAT THIS DELIBERATELY WILL NOT SAY. The obvious version of this
    feature writes "archived 14 redundant panels based on an r = 0.998
    correlation audit" and is exactly the sentence this tool exists to
    stop someone writing. The engine proves a metric carries no
    variation the others already carry. Whether the metric is the sole
    condition on a paging rule is not in a CSV of values and never will
    be. So the summary states what was proved, names what was not, and
    closes by saying it is not a clearance — because the moment this
    text lands in a change request it becomes the evidence of record,
    and it will be read by someone who did not run it.
    """
    s = payload["summary"]
    a = payload["assurance"]
    b = payload.get("basis") or {}
    L = []

    L.append(f"# Dashboard signal audit — {payload.get('file') or 'export'}")
    L.append("")
    eff, n = s.get("effective_signals"), s.get("metrics")
    pct = s.get("noise_reduction_pct")
    L.append(f"**{n} metrics collapse to {eff} effective signals"
             + (f" — {pct:.0f}% redundant.**" if pct is not None else ".**"))
    L.append("")

    declared = bool(b.get("declared"))
    basis_txt = f"`{b.get('headline', '?')}`" + (
        " (declared)" if declared else " — **ASSUMED, not declared**")
    fired = [c for c in payload["failure_catalogue"] if c["fired"]]
    avail = [c for c in payload["failure_catalogue"] if c["available"]]

    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| Basis | {basis_txt} |")
    L.append(f"| Evidence grade | **{a['grade']}** — "
             f"{a.get('rows_per_metric')} rows per metric |")
    L.append(f"| Rows | {s.get('rows')} |")
    L.append(f"| Checks | {len(avail)} run · {len(fired)} fired |")
    L.append(f"| Engine | redd-munro {payload.get('engine_version', '?')} |")
    L.append("")

    if not declared:
        L.append("> **The basis was not declared.** Raw, differenced and "
                 "per-unit views are all computed; the headline above is the "
                 "engine's default, not a statement about how this data "
                 "should be read. On entity-indexed data — one row per host, "
                 "per service, per account — the differenced view subtracts "
                 "two unrelated things and the number above is meaningless. "
                 "Re-run with an explicit basis before quoting it.")
        L.append("")

    if fired:
        L.append("## What fired")
        L.append("")
        for c in fired:
            det = f" — {c['detail']}" if c.get("detail") else ""
            L.append(f"- **{c['label']}**{det}")
        L.append("")

    unavail = [c for c in payload["failure_catalogue"] if not c["available"]]
    if unavail:
        L.append("## Not checked")
        L.append("")
        for c in unavail:
            L.append(f"- {c['label']} — not implemented; listed so its "
                     f"absence is visible rather than assumed")
        L.append("")

    cands = set(payload.get("archive_candidates") or [])
    if cands:
        L.append(f"## Archive candidates — {len(cands)}, none yet cleared")
        L.append("")
        L.append("| Metric | Unique variance | Closest other | r |")
        L.append("|---|---:|---|---:|")
        for m in payload["metrics"]:
            if m["name"] in cands:
                L.append(f"| `{m['name']}` | {m['unique_variance']} | "
                         f"`{m['closest_other']}` | {m['closest_r']} |")
        L.append("")

    if payload.get("subset_sums"):
        L.append("## Totals that are their parts")
        L.append("")
        for x in payload["subset_sums"]:
            L.append(f"- `{x['metric']}` = " +
                     " + ".join(f"`{k}`" for k in x["children"]))
        L.append("")
        L.append("Archive the parent, keep the parts. Every member of an "
                 "additive family is predictable from the others, so an "
                 "unguarded ranking offers the whole family at once.")
        L.append("")

    L.append("## This is not a clearance")
    L.append("")
    L.append("The engine proves one thing: that a metric carries no variation "
             "the others do not already carry. That is arithmetic and it is "
             "checkable. It **cannot** see whether a metric is the sole "
             "condition on a paging monitor, an SLO error budget, a "
             "compliance export or a runbook step — none of which appear in "
             "a CSV of values.")
    L.append("")
    L.append("Before anything is archived, complete the blast-radius "
             "worksheet exported alongside this file. It carries one row per "
             "metric with the evidence pre-filled and these columns for a "
             "human to answer:")
    L.append("")
    L.append("".join("- `%s`\n" % c for c in WORKSHEET_ATTESTATION).rstrip())
    L.append("")
    L.append("**Completing that worksheet is the safety check.** This summary "
             "is evidence that archiving is arithmetically possible, not "
             "evidence that it is safe.")
    L.append("")
    L.append("---")
    L.append("")
    L.append(f"Produced by [Redd Munro](https://reddmunro.com) "
             f"{payload.get('engine_version', '')} — the audit ran locally "
             f"and no data was uploaded.")
    return "\n".join(L) + "\n"


def assurance(res):
    """How far this audit's evidence can be trusted, A to D.

    Deliberately separate from the FINDING. "Your dashboard is 90%
    redundant" and "we had enough data to say so" are different claims,
    and collapsing them into one score would hide the second. The grade
    answers only: is there enough history here to act on the numbers?

    The binding constraint is rows per metric. The unique-variance
    figure comes from regressing each metric on every other, so with d
    metrics and n rows the regression has d-1 predictors and n samples;
    below ~10 samples per predictor it is strained even after the
    adjustment, and below ~5 the ordering is all that survives.
    """
    rpm = res.get("rows_per_metric", 0.0)
    reasons, caps = [], []

    if rpm >= 30:
        grade, note = "A", "ample history"
    elif rpm >= 10:
        grade, note = "B", "adequate history"
    elif rpm >= 5:
        grade, note = "C", "thin history"
    else:
        grade, note = "D", "insufficient history"
    reasons.append(f"{rpm:.1f} rows per metric ({note})")

    if res.get("mi_skipped"):
        caps.append("B")
        reasons.append(f"nonlinear check skipped ({res['n_rows']} rows, "
                       f"needs {MIN_ROWS_MI})")
    else:
        reasons.append("nonlinear dependence checked")

    if res.get("n_rows", 0) < MIN_ROWS * 2:
        caps.append("C")
        reasons.append("very short series")

    for cap in caps:
        if grade < cap:          # letters: A < B < C < D
            grade = cap
    return {"grade": grade, "rows_per_metric": rpm, "reasons": reasons,
            "actionable": grade in ("A", "B")}


# ----------------------------------------------------------------------
# The Failure Catalogue, as the terminal renders it.
#
# Every check the engine performs is listed here, INCLUDING the ones
# that did not fire. That is deliberate and it is the product: a naive
# correlation script produces the same top-line number, and the only
# visible difference is the list of ways it was prevented from lying.
# A customer who sees "6 checks run, 2 fired" understands what they are
# buying. One who sees only the 2 does not.
#
# Entries marked available=False are failure modes the engine is
# designed AROUND but does not detect. They are shown as not-checked
# rather than hidden, because implying coverage we lack is the exact
# thing this catalogue exists to prevent.
# Checks that mean nothing without row order. Listed once, so the gate
# cannot be applied to one and forgotten on the other.
TIME_DEPENDENT = {"trend-confound", "correlation-drift"}

CATALOGUE = [
    ("trend-confound", "Calendar trend confound", True,
     lambda r: r["trend_dominated"],
     lambda r: (f"raw view claims {r['raw']['pr']:.1f} signals, "
                f"differenced {r['diff']['pr']:.1f} — "
                f"{abs(r['trend_gap']):.1f} of the apparent structure is "
                f"shared calendar movement")),
    ("correlation-drift", "Relationship changed mid-file", True,
     lambda r: bool(r.get("drift", {}).get("pairs")),
     lambda r: (f"{len(r['drift']['pairs'])} pair(s) correlate differently "
                f"in the second half of this export than the first — "
                f"strongest: {r['drift']['pairs'][0]['metric_a']} ~ "
                f"{r['drift']['pairs'][0]['metric_b']}, r "
                f"{r['drift']['pairs'][0]['r_first']:+.3f} → "
                f"{r['drift']['pairs'][0]['r_second']:+.3f}")),
    ("identity", "Definitional identities", True,
     lambda r: bool(r["diff"]["identities"] or r["raw"]["identities"]),
     lambda r: (f"{len(r['diff']['identities'] or r['raw']['identities'])} "
                f"pair(s) are the same quantity under two names")),
    ("derived-aggregate", "Derived aggregate trap", True,
     lambda r: bool(r["aggregates"]),
     lambda r: (f"{len(r['aggregates'])} metric(s) are a max/min/mean of "
                f"others — linearly unpredictable, so naive scoring ranks "
                f"them as primary signals")),
    ("subset-sum", "Subset-sum aggregate", True,
     lambda r: bool(r["subset_sums"]),
     lambda r: (f"{len(r['subset_sums'])} metric(s) are the exact sum of a "
                f"named subset of other columns — fully redundant, and "
                f"invisible to the max/min/mean check")),
    ("basis-conflict", "Basis inflated a correlation", True,
     lambda r: bool(r["basis_conflicts"]),
     lambda r: (f"{len(r['basis_conflicts'])} pair(s) came out of a "
                f"transform MORE correlated than they went in — either a "
                f"factor was injected (a defect) or a real duplicate was "
                f"exposed (a finding); the numbers cannot tell which")),
    ("nonlinear", "Nonlinear coupling", True,
     lambda r: bool(r["nonlinear"]),
     lambda r: (f"{len(r['nonlinear'])} pair(s) are strongly dependent but "
                f"near-zero correlated — invisible to a correlation matrix")),
    ("noise-floor", "Noise hallucination guard", True,
     lambda r: False,
     lambda r: "mutual-information estimates cleared a shuffled null floor"),
    ("evidence", "Evidence sufficiency", True,
     lambda r: r["crowded"],
     lambda r: (f"only {r['rows_per_metric']:.1f} rows per metric — "
                f"unique-variance figures are strained")),
    ("rolling-average", "Rolling-average blind spot", False,
     lambda r: False,
     lambda r: "not detected; see REAL_DASHBOARDS.md"),
]


def _skip_reason(res, key):
    """Why a check did not run on this data, or None if it did.

    Only ever about the DATA. A check that is not implemented reports
    that through `available` instead, and the two must not merge: the
    demo page prints unbuilt checks specifically so their absence is
    visible, and a gated check quietly borrowing that slot would undo
    the point of printing it.
    """
    if key not in TIME_DEPENDENT:
        return None
    order = res.get("order") or {}
    if not order.get("ordered", True):
        if order.get("declared"):
            return ("rows were declared to be entities rather than a "
                    "timeline, so a before and an after do not exist")
        return (f"rows are not in time order — {order.get('evidence', '')}. "
                f"If they are consecutive observations, declare it with "
                f"--ordered and this check will run")
    if key == "correlation-drift":
        st = (res.get("drift") or {}).get("status")
        if st and st != "ok":
            return (res.get("drift") or {}).get("reason")
    return None


def report_payload(res):
    """The complete audit as plain JSON-safe data.

    Single source for every consumer: CLI `--json`, the hosted API, and
    the browser demo. Deliberately in the ENGINE rather than the CLI so
    a Pyodide page needs only this one module — the CLI carries terminal
    formatting a browser has no use for.

    Contains no numpy types, no NaN and no infinities, so
    `json.dumps(report_payload(...))` always succeeds.
    """
    # Metric count comes from the HEADLINE BASIS, not the global column
    # list. A ratio basis drops its own denominator, so the two differ,
    # and dividing the headline participation ratio by the global count
    # silently misstates "% redundant" — 14.78 over 39 reads 62%
    # redundant where the correct 38 gives 61%. Found the moment a third
    # basis existed; this is precisely the index drift the per-view
    # `names` key was added to prevent.
    n = res["views"][res["headline"]]["n_metrics"]
    pr = res["headline_pr"]
    grade = assurance(res)
    kept = deletion_candidates(res)
    ident = res["diff"]["identities"] or res["raw"]["identities"]

    def num(x, d=4):
        try:
            v = float(x)
        except (TypeError, ValueError):
            return None
        return round(v, d) if math.isfinite(v) else None

    return {
        "engine_version": __version__,
        "file": res["file"],
        "summary": {
            "metrics": n,
            "rows": res["n_rows"],
            "effective_signals": num(pr, 3),
            "true_signal_ratio": num(pr / n) if n else None,
            "noise_reduction_pct": num(100.0 * (1 - pr / n), 1) if n else None,
            "components_for_95pct": res["diff"]["n95"],
            "load_bearing_count": sum(1 for u in res["diff"]["unique"]
                                      if u["unique"] >= 0.30),
            "archive_candidate_count": len(kept),
            "verdict": verdict_line(res),
        },
        "assurance": {
            "grade": grade["grade"],
            "actionable": grade["actionable"],
            "rows_per_metric": num(grade["rows_per_metric"], 1),
            "reasons": grade["reasons"],
        },
        # `skipped` is separate from `available` on purpose. "We have not
        # built this" and "this cannot be asked of your data" are
        # different statements, and collapsing them would let a gated
        # check hide behind the unimplemented one.
        "failure_catalogue": [
            {"id": key, "label": label, "available": avail,
             "skipped": _skip_reason(res, key),
             "fired": bool(avail and not _skip_reason(res, key)
                           and fires(res)),
             "detail": (detail(res)
                        if (avail and not _skip_reason(res, key)
                            and fires(res)) else None)}
            for key, label, avail, fires, detail in CATALOGUE
        ],
        "order": dict(res.get("order", {})),
        "correlation_drift": {
            "status": res.get("drift", {}).get("status"),
            "reason": res.get("drift", {}).get("reason"),
            "pairs_tested": res.get("drift", {}).get("pairs_tested"),
            "rows_per_window": res.get("drift", {}).get("rows_per_window"),
            "shared_shift": num(res.get("drift", {}).get("shared_shift")),
            "spread_inflation": num(res.get("drift", {}).get("spread_inflation"), 2),
            "unstable": bool(res.get("drift", {}).get("unstable", False)),
            "pairs": [
                {"metric_a": p["metric_a"], "metric_b": p["metric_b"],
                 "r_first": num(p["r_first"]), "r_second": num(p["r_second"]),
                 "z": num(p["z"], 2), "q_value": num(p["q_value"], 6),
                 "direction": p["direction"]}
                for p in res.get("drift", {}).get("pairs", [])],
        },
        "trend_confound": {
            "raw": num(res["raw"]["pr"], 3),
            "differenced": num(res["diff"]["pr"], 3),
            "gap": num(res["trend_gap"], 3),
            "trend_dominated": bool(res["trend_dominated"]),
            "headline_basis": "differenced",
        },
        "identity_pairs": [
            {"metric_a": a, "metric_b": b, "r": num(r, 6),
             "keep": a if a in identity_representatives(res) else b}
            for _ar, r, a, b in ident],
        "redundancy_clusters": [
            {"cluster_id": i, "size": len(c), "metrics": c}
            for i, c in enumerate(res["diff"]["clusters"]) if len(c) > 1],
        "subset_sums": [
            {"metric": parent, "children": list(kids),
             "worst_relative_residual": num(worst, 6)}
            for parent, kids, worst in res["subset_sums"]],
        "basis": {
            "headline": res["headline"],
            "declared": bool(res["basis_declared"]),
            "available": sorted(res["views"]),
            "per_basis": {k: {"effective_signals": num(v["pr"], 3),
                              "metrics": v["n_metrics"], "rows": v["n_rows"]}
                          for k, v in res["views"].items()},
        },
        "basis_conflicts": [
            {"basis": b, "a": x, "b": y,
             "r_reference": num(ro, 4), "r_basis": num(rn, 4)}
            for b, x, y, ro, rn in res["basis_conflicts"]],
        "derived_aggregates": [
            {"metric": nm, "aggregate_of_others": k,
             "match_fraction": num(fr), "r": num(rr)}
            for fr, nm, k, rr in res["aggregates"]],
        "nonlinear_couplings": [
            {"metric_a": a, "metric_b": b, "r": num(r),
             "mi_vs_gaussian": num(rt, 2)}
            for rt, a, b, r, _mi in res["nonlinear"]],
        "nonlinear_skipped": bool(res["mi_skipped"]),
        "metrics": [
            {"name": u["name"], "unique_variance": num(u["unique"]),
             "closest_other": u["best_partner"],
             "closest_r": num(u["best_r"]),
             "archive_candidate": u["name"] in {k["name"] for k in kept},
             "load_bearing": u["unique"] >= 0.30}
            for u in sorted(res["diff"]["unique"], key=lambda x: x["unique"])],
        "archive_candidates": [u["name"] for u in kept],
        "excluded_columns": res["notes"],
    }


def identity_representatives(res):
    """Which member of each identity pair to KEEP.

    Two metrics at |r| >= IDENTITY_R are the same quantity twice, so one
    can go — but deleting BOTH destroys it. Any consumer that turns this
    analysis into a deletion list needs to know which one survives, and
    it must be the same answer every time or two runs over the same data
    will disagree.

    Lives here rather than in a caller because more than one consumer
    needs it (CLI, hosted service) and a duplicated copy would drift.
    Selection is `min(name)` — arbitrary but deterministic; there is no
    principled reason to prefer either member of an exact duplicate.

    Pairs are taken as the UNION of the raw and differenced views, not
    the usual differenced-first fallback. A pair can be an identity in
    levels and not in differences: `pm2_5` and `aqi_pm2_5` sit at
    r = 0.99992 raw but drop below the threshold once differenced,
    because differencing a deterministic-but-rounded transform injects
    noise. Reading only the differenced view would leave both members
    unprotected and put a real quantity on the deletion list. For a
    destructive decision, evidence from EITHER view protects.

    Returns the set of names to keep.
    """
    keep = set()
    pairs = list(res["diff"]["identities"]) + list(res["raw"]["identities"])
    for _ar, _r, a, b in pairs:
        if a not in keep and b not in keep:
            keep.add(min(a, b))
    return keep


def subset_sum_protected(res):
    """Children of any detected subset sum. These must NOT be archived.

    Building the subset-sum detector exposed a pre-existing and far worse
    defect than the one it was written to fix.

    In a family of k children plus their parent there is exactly ONE
    linear relation, so the family carries k independent quantities, not
    k+1. But every member is perfectly predictable from the others --
    the parent is the sum of the children, and each child is the parent
    minus its siblings. The unique-variance regression therefore scores
    ALL of them at zero and offers the whole family for deletion.

    Measured on the FDIC corpus before this guard existed: all SIX
    members of `LNRE = LNRERES + LNRENRES + LNREAG + LNRECONS +
    LNREMULT` were listed as archive candidates simultaneously. Acting
    on that list would have deleted an entire loan book. `LIAB` and `EQ`
    were likewise both offered, which would have dropped bank equity --
    the subject of capital regulation -- while keeping total assets.

    The bug was always there. It was invisible because nothing in the
    engine could see a sum. This is the same failure class as the
    identity-pair bug that once offered both halves of a duplicated
    metric, and it is guarded the same way: name the survivors
    explicitly rather than trusting the ranking.

    Rule: archive the PARENT, protect the CHILDREN. The parent is
    recomputable from the children and carries no detail they do not
    already hold; the reverse is false. That removes exactly one column
    per family, which is exactly the redundancy present.
    """
    prot = set()
    for _parent, kids, _worst in res.get("subset_sums", ()):
        prot.update(kids)
    return prot


def deletion_candidates(res, max_unique=0.02):
    """Metrics safe to drop, with identity survivors excluded.

    The ordering is by unique variance ascending — cheapest to lose
    first. Metrics that are the retained half of an identity pair are
    filtered out, which is the difference between a useful list and one
    that deletes a quantity outright. Children of a subset sum are
    filtered out for the same reason — see `subset_sum_protected`.
    """
    keep = identity_representatives(res) | subset_sum_protected(res)
    return [u for u in sorted(res["diff"]["unique"],
                              key=lambda x: x["unique"])
            if u["unique"] <= max_unique and u["name"] not in keep]


# ----------------------------------------------------------------------
# terminal report
# ----------------------------------------------------------------------
def print_report(res):
    W = 74
    p = print
    p("=" * W)
    p(f"SIGNAL AUDIT — {res['file']}")
    p("=" * W)
    p(f"{res['n_metrics']} metrics, {res['n_rows']} complete rows")
    for n in res["notes"]:
        p(f"  note: {n}")

    p(f"\nHOW MANY INDEPENDENT SIGNALS?")
    p(f"  {'':<26}{'raw':>12}{'differenced':>14}")
    p(f"  {'independent signals':<26}{res['raw']['pr']:>12.2f}"
      f"{res['diff']['pr']:>14.2f}")
    p(f"  {'components for 95% var':<26}{res['raw']['n95']:>12}"
      f"{res['diff']['n95']:>14}")
    p(f"\n  → {verdict_line(res)}")
    if res["trend_dominated"]:
        p(f"  ! Raw and differenced disagree by "
          f"{abs(res['trend_gap']):.1f} signals. Much of the raw "
          f"correlation\n    is shared trend, not shared behaviour. The "
          f"differenced column is the\n    one about your metrics.")

    ident = res["diff"]["identities"] or res["raw"]["identities"]
    p(f"\nDEFINITIONAL IDENTITIES  (|r| >= {IDENTITY_R})")
    if not ident:
        p("  none — no two metrics are the same number twice")
    for ar, r, a, b in ident[:12]:
        p(f"  {a[:28]:<30}{b[:28]:<30}r = {r:+.4f}")
    if len(ident) > 12:
        p(f"  ... and {len(ident) - 12} more")

    p(f"\nREDUNDANCY CLUSTERS  (single-linkage at |r| >= {REDUNDANT_R}, "
      f"differenced)")
    multi = [c for c in res["diff"]["clusters"] if len(c) > 1]
    if not multi:
        p("  none — no metric tracks another this closely")
    for i, c in enumerate(multi, 1):
        p(f"  cluster {i} ({len(c)} metrics): {', '.join(x[:22] for x in c)}")
    singles = [c[0] for c in res["diff"]["clusters"] if len(c) == 1]
    if singles:
        p(f"  standalone: {', '.join(x[:22] for x in singles)}")

    p(f"\nPER-METRIC — what would be lost by deleting it")
    if res["crowded"]:
        p(f"  ! Only {res['rows_per_metric']:.1f} rows per metric. Unique-"
          f"variance figures are adjusted for\n    predictor count but "
          f"remain strained at this ratio; treat the ordering as\n    "
          f"informative and the exact percentages as approximate. More "
          f"rows would fix it.")
    p(f"  {'metric':<28}{'unique var':>12}{'best predictor':>24}{'r':>8}")
    for u in sorted(res["diff"]["unique"], key=lambda x: -x["unique"]):
        p(f"  {u['name'][:26]:<28}{u['unique']:>11.0%}"
          f"{u['best_partner'][:22]:>24}{u['best_r']:>8.2f}")

    if res["aggregates"]:
        p(f"\nDERIVED AGGREGATES — a summary of the other columns")
        for frac, name, kind, r in res["aggregates"]:
            p(f"  {name[:28]:<30}equals the rowwise {kind:<5} of the "
              f"others on {frac:.0%} of rows")
        p(f"  These are determined by columns already on the dashboard, but "
          f"a max or min\n  is linearly unpredictable, so the unique-variance"
          f" figure above OVERSTATES\n  how load-bearing they are. Check the "
          f"definitions before trusting that column.")

    if res["mi_skipped"]:
        p(f"\nNONLINEAR DEPENDENCE: skipped ({res['n_rows']} rows; needs "
          f"{MIN_ROWS_MI}+)")
    elif res["nonlinear"]:
        p(f"\nNONLINEAR DEPENDENCE — related but barely correlated")
        p(f"  {'pair':<46}{'r':>8}{'MI x gaussian':>16}")
        for ratio, a, b, r, mi in res["nonlinear"]:
            p(f"  {a[:20] + ' ~ ' + b[:20]:<46}{r:>8.2f}{ratio:>15.1f}x")
        p(f"  These would look independent on a correlation matrix.")
    else:
        p(f"\nNONLINEAR DEPENDENCE: none detected above {MI_RATIO_FLAG}x")
    p("")


# ----------------------------------------------------------------------
# HTML report
# ----------------------------------------------------------------------
def _tail_sentence(res):
    """The judgement half of the verdict, sentence-cased."""
    v = verdict_line(res)
    tail = v.split("—", 1)[1].strip() if "—" in v else v
    return tail[:1].upper() + tail[1:] if tail else ""


def _heat(v):
    """Colour for a correlation cell."""
    a = min(1.0, abs(v))
    if v >= 0:
        return f"rgba(198,40,40,{0.06 + 0.84 * a:.3f})"
    return f"rgba(21,101,192,{0.06 + 0.84 * a:.3f})"


def write_html(res, out_path):
    e = _html.escape
    names = res["names"]
    C = res["diff"]["corr"]
    pr = res["headline_pr"]
    d = res["views"][res["headline"]]["n_metrics"]
    ratio = pr / d if d else 1.0
    tone = "#2e7d32" if ratio >= 0.8 else "#ef6c00" if ratio >= 0.5 \
        else "#c62828"

    def table_rows():
        out = []
        for i, a in enumerate(names):
            cells = "".join(
                f'<td style="background:{_heat(C[i][j])}" '
                f'title="{e(a)} ~ {e(names[j])}: r={C[i][j]:+.3f}">'
                f'{C[i][j]:+.2f}</td>' for j in range(len(names)))
            out.append(f'<tr><th class="rowh">{e(a)}</th>{cells}</tr>')
        return "\n".join(out)

    ident = res["diff"]["identities"] or res["raw"]["identities"]
    ident_html = "".join(
        f"<tr><td>{e(a)}</td><td>{e(b)}</td><td class='num'>{r:+.4f}</td></tr>"
        for _, r, a, b in ident[:20]) or \
        "<tr><td colspan=3 class='none'>None found</td></tr>"

    clusters = [c for c in res["diff"]["clusters"] if len(c) > 1]
    cluster_html = "".join(
        f"<li><b>{len(c)} metrics:</b> {e(', '.join(c))}</li>"
        for c in clusters) or "<li class='none'>No redundancy clusters</li>"

    uniq = sorted(res["diff"]["unique"], key=lambda x: -x["unique"])
    uniq_html = "".join(
        f"<tr><td>{e(u['name'])}</td>"
        f"<td class='num'>{u['unique']:.0%}</td>"
        f"<td><div class='bar'><i style='width:{max(1, u['unique'] * 100):.0f}%'>"
        f"</i></div></td>"
        f"<td>{e(u['best_partner'])}</td>"
        f"<td class='num'>{u['best_r']:.2f}</td></tr>" for u in uniq)

    if res["mi_skipped"]:
        nl_html = (f"<p class='none'>Skipped — {res['n_rows']} rows, "
                   f"needs {MIN_ROWS_MI}+ to estimate honestly.</p>")
    elif res["nonlinear"]:
        nl_html = ("<table><tr><th>Pair</th><th>r</th><th>MI vs gaussian"
                   "</th></tr>" + "".join(
                       f"<tr><td>{e(a)} ~ {e(b)}</td>"
                       f"<td class='num'>{r:+.2f}</td>"
                       f"<td class='num'>{ratio:.1f}×</td></tr>"
                       for ratio, a, b, r, mi in res["nonlinear"]) +
                   "</table><p class='sub'>These pairs are related but would "
                   "look independent on the correlation matrix above.</p>")
    else:
        nl_html = (f"<p class='none'>None detected above "
                   f"{MI_RATIO_FLAG}× the correlation-implied level.</p>")


    tag = ("declared" if res["basis_declared"] else "ASSUMED")
    basis_html = (f"<p class='sub'><b>Basis: {res['headline']} "
                  f"&middot; {tag}.</b> Every basis below is computed on "
                  f"every run; the headline is a choice, not a deduction. "
                  f"An undeclared basis means rows were treated as ordered "
                  f"observations.</p>")
    if len(res["views"]) > 2:
        basis_html += ("<table><tr><th>Basis</th><th>Effective signals</th>"
                       "<th>Metrics</th><th>Redundant</th></tr>" +
                       "".join(
                           f"<tr><td>{k}"
                           f"{' <b>&larr; headline</b>' if k == res['headline'] else ''}"
                           f"</td><td>{v['pr']:.2f}</td>"
                           f"<td>{v['n_metrics']}</td>"
                           f"<td>{100*(1-v['pr']/v['n_metrics']):.0f}%</td></tr>"
                           for k, v in res["views"].items()) + "</table>")
    if res["basis_conflicts"]:
        basis_html += ("<h2>Basis inflated a correlation</h2><table><tr>"
                       "<th>Basis</th><th>Pair</th><th>Was</th>"
                       "<th>Became</th></tr>" +
                       "".join(f"<tr><td>{b}</td><td>{x} ~ {y}</td>"
                               f"<td>{ro:+.3f}</td><td>{rn:+.3f}</td></tr>"
                               for b, x, y, ro, rn in res["basis_conflicts"])
                       + "</table><p class='sub'>A pair more correlated "
                         "after a transform than before it means the basis "
                         "either injected a shared factor (a defect) or "
                         "exposed a real duplicate (a finding). The numbers "
                         "cannot tell which; someone who knows what the "
                         "metrics mean can.</p>")

    sums_html = ""
    if res["subset_sums"]:
        sums_html = ("<h2>Subset sums</h2><table><tr><th>Metric</th>"
                     "<th>is exactly</th></tr>" +
                     "".join(f"<tr><td>{p_}</td><td>{' + '.join(k)}</td></tr>"
                             for p_, k, _w in res["subset_sums"]) +
                     "</table><p class='sub'>Each of these is the exact sum of "
                     "a named subset of the other columns, on at least 99% of "
                     "rows. Archive the parent and keep the parts: every "
                     "member of an additive family is predictable from the "
                     "others, so an unguarded ranking would offer all of them "
                     "for deletion at once. The parts are excluded from the "
                     "archive list for that reason.</p>")

    if res["aggregates"]:
        agg_html = ("<table><tr><th>Metric</th><th>Equals</th><th>Match</th>"
                    "</tr>" + "".join(
                        f"<tr><td>{e(n)}</td><td>rowwise {k} of the other "
                        f"columns</td><td class='num'>{f:.0%} of rows</td>"
                        f"</tr>" for f, n, k, r in res["aggregates"]) +
                    "</table><p class='sub'>These are determined by columns "
                    "already present, but a max or min is linearly "
                    "unpredictable — so the unique-variance table above "
                    "overstates how load-bearing they are.</p>")
    else:
        agg_html = "<p class='none'>None detected.</p>"

    crowd_html = ""
    if res["crowded"]:
        crowd_html = (
            f"<div class='warn'><b>Only {res['rows_per_metric']:.1f} rows "
            f"per metric.</b> Unique-variance figures below are adjusted "
            f"for predictor count, but at this ratio they remain strained: "
            f"read the ordering as informative and the exact percentages "
            f"as approximate. More history would resolve it.</div>")

    trend_html = ""
    if res["trend_dominated"]:
        trend_html = (
            f"<div class='warn'><b>Shared trend is inflating the raw "
            f"numbers.</b> Raw and differenced disagree by "
            f"{abs(res['trend_gap']):.1f} signals. Metrics that all grow "
            f"over time correlate strongly for that reason alone. Every "
            f"headline figure on this page uses the differenced "
            f"(period-over-period) view, which is the one about your "
            f"metrics rather than the calendar.</div>")

    notes_html = "".join(f"<li>{e(n)}</li>" for n in res["notes"])
    if notes_html:
        notes_html = f"<ul class='notes'>{notes_html}</ul>"

    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Signal audit — {e(res['file'])}</title>
<style>
 :root {{ --ink:#1a1a1a; --sub:#666; --line:#e3e3e3; --bg:#fafafa; }}
 * {{ box-sizing:border-box; }}
 body {{ font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",
        Roboto,Helvetica,Arial,sans-serif; color:var(--ink);
        background:var(--bg); margin:0; padding:32px 20px; }}
 .wrap {{ max-width:980px; margin:0 auto; }}
 h1 {{ font-size:22px; margin:0 0 4px; }}
 h2 {{ font-size:15px; text-transform:uppercase; letter-spacing:.06em;
       color:var(--sub); margin:34px 0 10px; font-weight:600; }}
 .file {{ color:var(--sub); font-size:13px; margin-bottom:24px;
          font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
 .card {{ background:#fff; border:1px solid var(--line); border-radius:10px;
          padding:20px 22px; }}
 .headline {{ font-size:30px; font-weight:650; color:{tone};
              line-height:1.15; }}
 .headline small {{ display:block; font-size:15px; font-weight:400;
                    color:var(--ink); margin-top:10px; }}
 .stats {{ display:flex; gap:34px; margin-top:18px; padding-top:16px;
           border-top:1px solid var(--line); flex-wrap:wrap; }}
 .stat b {{ display:block; font-size:21px; font-weight:600; }}
 .stat span {{ font-size:12px; color:var(--sub); }}
 table {{ border-collapse:collapse; width:100%; font-size:13px;
          background:#fff; }}
 th,td {{ border:1px solid var(--line); padding:6px 9px; text-align:left; }}
 th {{ background:#f4f4f4; font-weight:600; }}
 td.num {{ text-align:right; font-variant-numeric:tabular-nums;
           font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
 .heat {{ overflow-x:auto; }}
 .heat table {{ font-size:11px; }}
 .heat td {{ text-align:center; padding:5px 6px; min-width:52px;
             font-variant-numeric:tabular-nums; }}
 .heat th.rowh {{ position:sticky; left:0; background:#f4f4f4;
                  max-width:170px; }}
 .heat th.colh {{ font-size:10px; writing-mode:vertical-rl;
                  transform:rotate(180deg); height:110px; padding:5px 3px; }}
 .bar {{ background:#eee; border-radius:3px; height:9px; width:110px; }}
 .bar i {{ display:block; background:{tone}; height:100%;
           border-radius:3px; }}
 ul {{ margin:0; padding-left:20px; }}
 li {{ margin:5px 0; }}
 .none {{ color:var(--sub); font-style:italic; }}
 .warn {{ background:#fff8e1; border:1px solid #ffe082; border-radius:8px;
          padding:13px 16px; margin-top:16px; font-size:14px; }}
 .sub {{ color:var(--sub); font-size:13px; }}
 .notes {{ color:var(--sub); font-size:13px; }}
 footer {{ margin-top:40px; padding-top:16px; border-top:1px solid var(--line);
           color:var(--sub); font-size:12px; }}
</style></head><body><div class="wrap">

<h1>Signal audit</h1>
<div class="file">{e(res['path'])}</div>

<div class="card">
  <div class="headline">{pr:.1f} independent signals
    <small>from {d} metrics over {res['n_rows']} rows. {e(_tail_sentence(res))}</small>
  </div>
  <div class="stats">
    <div class="stat"><b>{d}</b><span>metrics supplied</span></div>
    <div class="stat"><b>{pr:.1f}</b><span>independent signals</span></div>
    <div class="stat"><b>{res['diff']['n95']}</b><span>components for 95% variance</span></div>
    <div class="stat"><b>{len(ident)}</b><span>definitional identities</span></div>
    <div class="stat"><b>{len(clusters)}</b><span>redundancy clusters</span></div>
  </div>
  {trend_html}
</div>

<h2>Correlation matrix (differenced)</h2>
<div class="heat card"><table>
<tr><th></th>{''.join(f'<th class="colh">{e(n)}</th>' for n in names)}</tr>
{table_rows()}
</table></div>

<h2>Definitional identities (|r| ≥ {IDENTITY_R})</h2>
<table><tr><th>Metric</th><th>Metric</th><th>r</th></tr>{ident_html}</table>
<p class="sub">Pairs this tightly coupled are typically the same quantity
recorded twice — a rate and its inverse, a count and its percentage, a
total and its complement. Each pair costs a dashboard slot and returns
nothing.</p>

<h2>Redundancy clusters (|r| ≥ {REDUNDANT_R})</h2>
<div class="card"><ul>{cluster_html}</ul></div>
<p class="sub">Linked by single-linkage: if A tracks B and B tracks C, all
three sit in one cluster even when A and C are not directly correlated.
One well-chosen representative per cluster carries most of what the
cluster carries.</p>

<h2>What each metric contributes</h2>
<table><tr><th>Metric</th><th>Unique variance</th><th></th>
<th>Best predicted by</th><th>r</th></tr>{uniq_html}</table>
{crowd_html}
<p class="sub">Unique variance is what remains unexplained when every
other metric in the set is used to predict this one — the honest measure
of what deleting it would cost. Low values are candidates for removal;
high values are load-bearing.</p>

{basis_html}
{sums_html}
<h2>Derived aggregates</h2>
<div class="card">{agg_html}</div>

<h2>Nonlinear dependence</h2>
<div class="card">{nl_html}</div>

{('<h2>Columns not audited</h2><div class="card">' + notes_html + '</div>')
 if notes_html else ''}

<footer>
Generated by signal_audit.py v{__version__}. Headline figures use
first-differenced series to remove shared trend. Independent-signal count
is the participation ratio of the correlation spectrum — a continuous
measure, so it will rarely be a whole number. This tool describes the
statistical structure of the columns you supplied; it does not know what
any metric means, and a redundant metric may still be worth keeping for
reasons it cannot see.
</footer>
</div></body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return out_path


# ----------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="How many independent signals does your dashboard "
                    "actually have?")
    ap.add_argument("csv", help="CSV file; columns are metrics, rows are "
                                "observations over time")
    ap.add_argument("--html", nargs="?", const="", default=None,
                    metavar="PATH",
                    help="also write an HTML report (default: alongside "
                         "the input)")
    ap.add_argument("--ignore", default="",
                    help="comma-separated column names to exclude")
    ap.add_argument("--max-rows", type=int, default=None,
                    help="use only the first N data rows")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress the terminal report")
    ap.add_argument("--version", action="version",
                    version=f"signal_audit {__version__}")
    a = ap.parse_args(argv)

    try:
        res = audit(a.csv, ignore=[s for s in a.ignore.split(",") if s],
                    max_rows=a.max_rows)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not a.quiet:
        print_report(res)
    if a.html is not None:
        out = a.html or os.path.splitext(a.csv)[0] + "_signal_audit.html"
        write_html(res, out)
        print(f"[saved] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
