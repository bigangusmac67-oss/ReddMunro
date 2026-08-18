"""
signal_audit_cli.py — the `redd` command (brand: Redd Munro).

Separate from `signal_audit.py` on purpose. The engine stays a single
importable module with no presentation logic and no dependencies beyond
numpy; everything about tables, colour and terminal width lives here. A
library that formats its own output is hard to embed, and this one has
to embed in a web service and in a browser.

    redd run metrics.csv
    redd run metrics.csv --html report.html --json out.json
    redd run metrics.csv --ignore date,region --top 15
    redd prune metrics.csv --worksheet   # blast-radius safety sheet

Exit codes are meaningful, so this composes in CI:
    0  audit completed
    1  audit completed AND found heavy redundancy (< 0.5 signal ratio)
    2  input error — unreadable, too few columns, too few rows
Use `--no-fail` to always exit 0.
"""

from __future__ import annotations   # 3.8/3.9: defer PEP 604 unions

import argparse
import json
import os
import shutil
import sys

import signal_audit as SA

__version__ = SA.__version__


# ----------------------------------------------------------------------
# terminal formatting
# ----------------------------------------------------------------------
# Box-drawing, ticks and arrows are readable in a terminal and fatal in
# a pipe. On Windows a REDIRECTED stream uses the locale codepage, not
# UTF-8, so `redd run board.csv > report.txt` raised UnicodeEncodeError
# and produced a traceback and no report — on the first command a new
# user is likely to type, since redirecting the output is how you send
# it to a colleague.
#
# Degrading the glyphs is a RENDERING choice and touches no number, the
# same trade `Fmt` already makes for colour. Crashing rather than
# rendering a hash instead of a block is not a defensible reading of
# "never alter the data".
_ASCII_FALLBACK = str.maketrans({
    "█": "#", "─": "-", "·": ".", "✓": "v", "▲": "!", "○": "o",
    "×": "x", "⬆": "^", "…": "...", "—": "--", "–": "-", "€": "EUR",
    "️": "",          # variation selector, rides along with ⬆
})


def _supports_unicode(stream) -> bool:
    """Can this stream encode the glyphs the report uses?"""
    enc = getattr(stream, "encoding", None)
    if not enc:
        return False
    try:
        "█─·✓▲○×⬆…—–€".encode(enc)
        return True
    except (LookupError, UnicodeEncodeError, TypeError):
        return False


class _AsciiStream:
    """Wraps a stream that cannot encode the report's glyphs."""

    def __init__(self, stream):
        self._s = stream

    def write(self, text):
        return self._s.write(text.translate(_ASCII_FALLBACK))

    def __getattr__(self, name):
        return getattr(self._s, name)


def _degrade_streams():
    """Make output impossible to crash on, before anything is printed."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        # errors="replace" is the backstop: it catches any character the
        # table above does not know about, so no future glyph can
        # reintroduce the crash.
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
        if not _supports_unicode(stream):
            setattr(sys, name, _AsciiStream(stream))


def _supports_colour(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty()


class Fmt:
    """Colour that degrades to nothing when piped. A tool whose output
    is full of escape codes in a log file is a tool people stop
    piping."""

    def __init__(self, enabled: bool):
        self.on = enabled

    def _w(self, code, s):
        return f"\033[{code}m{s}\033[0m" if self.on else s

    def bold(self, s): return self._w("1", s)
    def dim(self, s): return self._w("2", s)
    def red(self, s): return self._w("31", s)
    def green(self, s): return self._w("32", s)
    def amber(self, s): return self._w("33", s)
    def cyan(self, s): return self._w("36", s)


def table(rows, headers, aligns=None, width=None) -> str:
    """Minimal fixed-width table. No third-party dependency: this tool
    installs with numpy alone and that is a feature."""
    if not rows:
        return "  (none)"
    cols = len(headers)
    aligns = aligns or ["l"] * cols
    cells = [[str(c) for c in r] for r in rows]
    widths = [max(len(headers[i]), max((len(r[i]) for r in cells), default=0))
              for i in range(cols)]

    if width:                       # shrink the widest column to fit
        while sum(widths) + 2 * cols + 2 > width and max(widths) > 8:
            widths[widths.index(max(widths))] -= 1

    def fmt(vals, pad=" "):
        out = []
        for i, v in enumerate(vals):
            v = v if len(v) <= widths[i] else v[:widths[i] - 1] + "…"
            out.append(v.rjust(widths[i]) if aligns[i] == "r"
                       else v.ljust(widths[i]))
        return "  " + pad.join(out).rstrip()

    lines = [fmt(headers),
             "  " + " ".join("-" * w for w in widths)]
    lines += [fmt(r) for r in cells]
    return "\n".join(lines)


CATALOGUE = SA.CATALOGUE

GRADE_TONE = {"A": "green", "B": "green", "C": "amber", "D": "red"}


def render(res: dict, f: Fmt, top: int = 12, width: int = 100,
           lex=None) -> str:
    out = []
    # Count metrics in the HEADLINE BASIS, not globally. A ratio basis
    # drops its own denominator, so the two differ — and dividing the
    # headline's participation ratio by the wrong metric count silently
    # misstates "% redundant". Caught immediately once a third basis
    # existed: 14.78 over 39 reads 62% redundant, over the correct 38 it
    # is 61%.
    n = res["views"][res["headline"]]["n_metrics"]
    pr = res["headline_pr"]
    kept = SA.deletion_candidates(res)
    load_bearing = [u for u in res["diff"]["unique"] if u["unique"] >= 0.30]
    noise_pct = 100.0 * (1 - pr / n) if n else 0.0
    grade = SA.assurance(res)
    tone = getattr(f, GRADE_TONE[grade["grade"]])
    rule = "  " + "─" * min(width - 4, 76)

    # ---- headline: the finding -------------------------------------
    out += ["", rule]
    out.append("  " + f.bold("REDD MUNRO") + f.dim(f"   {res['file']}"))
    out.append(rule)
    out.append("")
    # The basis rides ON the headline, not in a banner beside it.
    # Banners get tuned out; a suffix on the number someone is about to
    # paste into a ticket travels with it. Same slot whether declared or
    # assumed, so the difference is visible at a glance rather than
    # requiring anyone to notice an absence.
    basis_tag = (f.dim(f"   [{res['headline']} · declared]")
                 if res.get("basis_declared")
                 else f.amber(f"   [{res['headline']} · ASSUMED]"))
    out.append("  " + f.bold(f"{n} metrics collapse to {pr:.1f} effective "
                             f"signals") + basis_tag)
    bar_w = 34
    filled = max(0, min(bar_w, int(round(bar_w * pr / n)))) if n else 0
    bar = ("█" * filled) + ("·" * (bar_w - filled))
    out.append(f"  {f.cyan(bar)}  " + f.bold(f"{noise_pct:.0f}% redundant"))
    out.append("")
    out.append(f"  {'load-bearing':<22}{len(load_bearing):>4}  "
               + f.dim("carry variation nothing else carries"))
    out.append(f"  {'safe to archive':<22}{len(kept):>4}  "
               + f.dim("no unique variance, identity survivors excluded"))
    out.append(f"  {'rows analysed':<22}{res['n_rows']:>4}")

    if len(res.get("views", {})) > 2:
        out += ["", rule]
        out.append("  " + f.bold("BASES")
                   + f.dim("   every one computed; the headline is a choice"))
        out.append("")
        out.append(table(
            [[k + ("  <- headline" if k == res["headline"] else ""),
              f"{v['pr']:.2f}", str(v["n_metrics"]),
              f"{100 * (1 - v['pr'] / v['n_metrics']):.0f}%"]
             for k, v in res["views"].items()],
            ["basis", "signals", "metrics", "redundant"],
            ["l", "r", "r", "r"], width))

    if lex:
        out += render_domain(res, lex, f, width)

    if res.get("basis_conflicts"):
        out += ["", rule]
        out.append("  " + f.bold("BASIS INFLATED A CORRELATION")
                   + f.dim("   injected, or exposed — you decide"))
        out.append("")
        out.append(table(
            [[b, f"{x} ~ {y}", f"{ro:+.3f}", f"{rn:+.3f}"]
             for b, x, y, ro, rn in res["basis_conflicts"][:top]],
            ["basis", "pair", "was", "became"],
            ["l", "l", "r", "r"], width))
        out.append("")
        out.append(f.dim("    A pair more correlated after a transform than "
                         "before it means the basis"))
        out.append(f.dim("    either injected a shared factor (a defect) or "
                         "exposed a real duplicate"))
        out.append(f.dim("    (a finding). The numbers cannot tell which."))

    # ---- assurance grade -------------------------------------------
    out += ["", rule]
    out.append("  " + f.bold("EVIDENCE GRADE  ") + tone(f.bold(grade["grade"]))
               + f.dim(f"   {'actionable' if grade['actionable'] else 'indicative only'}"))
    for reason in grade["reasons"]:
        out.append(f.dim(f"    · {reason}"))
    if not grade["actionable"]:
        out.append("  " + f.amber("    read the ordering, not the "
                                  "percentages — more history would fix this"))

    # ---- failure catalogue -----------------------------------------
    # A skipped check is neither run nor clear. Rendering it green was
    # the first version's bug: on entity-indexed data the two
    # time-dependent checks reported "clear", which reads as "we looked
    # and found nothing" when nothing was looked at.
    def skipped(key):
        return SA._skip_reason(res, key)

    fired = [c for c in CATALOGUE if c[2] and not skipped(c[0]) and c[3](res)]
    checked = [c for c in CATALOGUE if c[2] and not skipped(c[0])]
    gated = [c for c in CATALOGUE if c[2] and skipped(c[0])]
    out += ["", rule]
    out.append("  " + f.bold("FAILURE CATALOGUE")
               + f.dim(f"   {len(checked)} checks run · {len(fired)} fired"
                       + (f" · {len(gated)} not applicable" if gated else "")))
    out.append("")
    for key, label, available, fires, detail in CATALOGUE:
        why = skipped(key) if available else None
        if not available:
            out.append(f"  {f.dim('○')} {f.dim(label):<44}"
                       + f.dim("not implemented"))
        elif why:
            out.append(f"  {f.dim('–')} {f.dim(label):<44}"
                       + f.dim("not applicable"))
            out.append(f.dim(f"      {why}"))
        elif fires(res):
            out.append(f"  {f.amber('▲')} {f.bold(label)}")
            out.append(f.dim(f"      {detail(res)}"))
        else:
            out.append(f"  {f.green('✓')} {label:<44}" + f.dim("clear"))

    # ---- what to keep ----------------------------------------------
    out += ["", rule]
    out.append("  " + f.bold("LOAD-BEARING SIGNALS")
               + f.dim("   keep these; they explain the system"))
    out.append("")
    lb = sorted(res["diff"]["unique"], key=lambda u: -u["unique"])[:top]
    out.append(table([[u["name"], f"{u['unique']:.0%}", u["best_partner"],
                       f"{u['best_r']:.2f}"] for u in lb],
                     ["metric", "unique", "closest other", "r"],
                     ["l", "r", "l", "r"], width))

    # ---- what to archive -------------------------------------------
    out += ["", rule]
    out.append("  " + f.bold("SAFE ARCHIVE CANDIDATES")
               + f.dim("   reversible tag-exclusion, no data loss"))
    out.append("")
    if kept:
        out.append(table([[u["name"], f"{u['unique']:.0%}", u["best_partner"]]
                          for u in kept[:top]],
                         ["metric", "unique", "duplicated by"],
                         ["l", "r", "l"], width))
        if len(kept) > top:
            out.append(f.dim(f"    ... and {len(kept) - top} more"))
        out.append("")
        out.append(f.dim("    Nothing is archived until a blast-radius "
                         "check clears each metric against"))
        out.append(f.dim("    live monitors, SLOs and runbooks. "
                         "Run: redd prune --worksheet"))
    else:
        out.append(f.dim("    None — every metric carries unique variation."))

    # ---- detail sections -------------------------------------------
    ident = res["diff"]["identities"] or res["raw"]["identities"]
    if ident:
        out += ["", rule]
        out.append("  " + f.bold("DEFINITIONAL IDENTITIES")
                   + f.dim("   same number, two names"))
        out.append("")
        out.append(table([[a, b, f"{r:+.4f}"]
                          for _ar, r, a, b in ident[:top]],
                         ["metric", "same as", "r"], ["l", "l", "r"], width))

    if res["subset_sums"]:
        out += ["", rule]
        out.append("  " + f.bold("SUBSET SUMS")
                   + f.dim("   parent is exactly its parts added up"))
        out.append("")
        for parent, kids, _worst in res["subset_sums"]:
            out.append(f"  {parent} = " + " + ".join(kids))
        out.append("")
        out.append(f.dim("    Archive the PARENT, keep the parts. Every member "
                         "of an additive family"))
        out.append(f.dim("    is predictable from the others, so an unguarded "
                         "ranking offers them all —"))
        out.append(f.dim("    the parts are protected above."))

    if res["aggregates"]:
        out += ["", rule]
        out.append("  " + f.bold("DERIVED AGGREGATES")
                   + f.dim("   summaries of other tiles — not root causes"))
        out.append("")
        out.append(table([[name, kind, f"{frac:.0%}"]
                          for frac, name, kind, _r in res["aggregates"]],
                         ["metric", "equals rowwise", "of rows"],
                         ["l", "l", "r"], width))

    if res["nonlinear"]:
        out += ["", rule]
        out.append("  " + f.bold("NONLINEAR COUPLINGS")
                   + f.dim("   a correlation matrix calls these independent"))
        out.append("")
        out.append(table([[f"{a} ~ {b}", f"{r:+.2f}", f"{rt:.0f}×"]
                          for rt, a, b, r, _mi in res["nonlinear"][:top]],
                         ["pair", "r", "vs gaussian"], ["l", "r", "r"], width))

    if res["notes"]:
        out += [""]
        out.append(f.dim("  Not audited: " + "; ".join(res["notes"][:3])
                         + (" ..." if len(res["notes"]) > 3 else "")))
    out.append("")
    return "\n".join(out)


def json_payload(res: dict) -> dict:
    """Delegates to the engine so CLI, API and browser emit one shape."""
    return SA.report_payload(res)


# ----------------------------------------------------------------------
def _misplaced_flags(a, f) -> int:
    """Refuse flags that this subcommand parses but does not act on.

    v0.1.1. `--worksheet` and `--refs` are honoured under `prune` only —
    the guard is further down this file — but both subcommands share one
    parser, so `redd run --worksheet ws.csv` was ACCEPTED, wrote nothing,
    printed nothing, and exited 0.

    That is the worst failure mode a CLI has. It cost this project a
    documented command that could not work: the README and the live
    site both carried `redd run … --refs … --worksheet ws.csv` until
    someone ran it verbatim against a clean install. Anything that
    silently succeeds while doing nothing will eventually be written
    down as though it worked.

    Refusing rather than quietly honouring it, deliberately: making the
    flag work under `run` is a feature and belongs in a minor release
    with its own tests. Turning a silent no-op into an actionable error
    is the bug fix, and it changes the behaviour of no invocation that
    was already doing what its author intended.
    """
    if a.command != "run":
        return 0
    bad = []
    if getattr(a, "worksheet", None) is not None:
        bad.append("--worksheet")
    if getattr(a, "refs", None):
        bad.append("--refs")
    if not bad:
        return 0

    joined = " and ".join(bad)
    print(f"{f.red('error')}: {joined} "
          f"{'are' if len(bad) > 1 else 'is'} not available on "
          f"`redd run` — the worksheet is produced by `redd prune`.",
          file=sys.stderr)
    rest = [x for x in (f"--basis {a.basis}" if getattr(a, "basis", None)
                        else "", "--ordered" if getattr(a, "ordered", False)
                        else "") if x]
    hint = " ".join(["redd prune", a.csv] + rest)
    if "--refs" in bad:
        hint += " " + " ".join(f"--refs {p}" for p in a.refs)
    if "--worksheet" in bad:
        hint += f" --worksheet {a.worksheet or 'ws.csv'}"
    print(f"       try:  {hint}", file=sys.stderr)
    print(f"       (`redd run` gives the report; `redd prune` gives the "
          f"candidates and the safety worksheet.)", file=sys.stderr)
    return 2


def _run(a, f) -> int:
    # A flag this subcommand parses but ignores is refused before any
    # work happens, so the failure is immediate rather than discovered
    # when an expected file turns out not to exist.
    rc = _misplaced_flags(a, f)
    if rc:
        return rc

    # `history` reads the store and never touches a CSV, so it is handled
    # before anything that assumes an input file exists.
    if a.command == "history":
        import history as HI
        runs = HI.load(a.root)
        if not runs:
            print(f"no runs recorded in {os.path.join(a.root, 'history')}. "
                  f"Record one with:  redd run FILE.csv --record "
                  f"--window-label quiet", file=sys.stderr)
            return 1
        if a.source is None:
            sources = sorted({r.get("source") for r in runs})
            if len(sources) == 1:
                a.source = sources[0]
            else:
                print("  " + f.bold("HISTORY") + f.dim(
                    f"   {len(runs)} run(s) across {len(sources)} dashboards"))
                print()
                for src in sources:
                    n = sum(1 for r in runs if r.get("source") == src)
                    labels = sorted({(r.get("window") or {}).get("label")
                                     for r in runs if r.get("source") == src})
                    print(f"  {src:<34} {n:>3} run(s)   labels: "
                          f"{', '.join(labels)}")
                print()
                print(f.dim("  Histories are per-dashboard and are never "
                            "pooled — a persistence claim is about one "
                            "board. Name one to see it."))
                return 0
        an = HI.persistence(runs, source=a.source)
        if a.json:
            print(json.dumps(an, indent=2, sort_keys=True))
            return 0 if not an.get("error") else 1
        print()
        print("  " + f.bold("HISTORY") + f.dim(f"   {a.source}"))
        print()
        for line in HI.report_lines(an):
            if line.startswith("KEEP") or "UNKNOWN" in line \
                    or line.startswith("NO run is labelled"):
                print("  " + f.amber(line))
            else:
                print("  " + line if line.strip() else "")
        print()
        return 0

    try:
        strict = a.strict_basis or os.environ.get(
            "REDD_REQUIRE_BASIS") == "1"
        if strict and not a.basis:
            sys.stderr.write(
                "redd: no --basis declared and --strict-basis is set.\n"
                "  The engine would otherwise assume 'differenced', which\n"
                "  treats rows as ordered observations. On entity-indexed\n"
                "  data that assumption fails silently — it returns very\n"
                "  nearly the right number for the wrong reason.\n"
                "  Declare one of: raw, differenced, ratio:COL\n")
            return 2
        res = SA.audit(a.csv, ignore=[s for s in a.ignore.split(",") if s],
                       max_rows=a.max_rows, scale_by=tuple(a.scale_by),
                       scale_exempt=tuple(a.scale_exempt), basis=a.basis,
                       ordered=a.ordered)
        if not res["basis_declared"]:
            # stderr so it survives `--json > file`, where the terminal
            # suffix that carries this on an interactive run is not seen
            sys.stderr.write(
                f"redd: basis not declared — assuming "
                f"'{res['headline']}' (rows treated as ordered "
                f"observations). Pass --basis to make this explicit.\n")
    except (ValueError, FileNotFoundError) as exc:
        print(f"{f.red('error')}: {exc}", file=sys.stderr)
        return 2

    # --- cardinality, the second axis --------------------------------
    # Redundancy is between metrics; cardinality is within one. They are
    # printed as two columns and never summed, because the largest
    # number is usually an unbounded label on a metric nothing
    # duplicates, and an operator shown one blended score would archive
    # the wrong thing. See OTEL_INTEGRATION_SPEC.md.
    if getattr(a, "cardinality", None):
        try:
            import cardinality as CD
            raw = open(a.cardinality, encoding="utf-8").read()
            counts = (CD.parse_promql_series_count(raw)
                      if raw.lstrip().startswith("{")
                      else CD.parse_exposition(raw))
            if not counts:
                print(f"{f.red('error')}: no series found in "
                      f"{a.cardinality} — expected Prometheus exposition "
                      f"text or PromQL count JSON", file=sys.stderr)
                return 2
            rep = CD.CardinalityReport(
                counts, scope=f"as counted in {os.path.basename(a.cardinality)}; "
                              f"other ingestion paths not visible")
            cls = CD.classify(SA.report_payload(res), rep)
            print()
            print("  " + f.bold("COST AXIS")
                  + f.dim(f"   {rep.total_series:,} series across "
                          f"{len(counts)} metrics · {rep.scope}"))
            print()
            for row in cls["rows"]:
                if row["quadrant"] == "neither":
                    continue
                tag = {"redundant_and_expensive": f.amber("REDUNDANT + COSTLY"),
                       "redundant_only": f.dim("redundant, cheap  "),
                       "expensive_only": f.cyan("COSTLY, not redundant")}[row["quadrant"]]
                sers = f"{row['series']:,}" if row["series_known"] else "?"
                print(f"  {tag}  {row['metric']:<34} {sers:>9} series")
                if row["advice"]:
                    print(f.dim(f"      {row['advice']}"))
            if cls["unmatched"]:
                print(f.dim(f"\n  {len(cls['unmatched'])} audited metric(s) "
                            f"absent from the counts — no cost shown for "
                            f"them: {', '.join(cls['unmatched'][:4])}"))
            print(f.dim("\n  Redundancy and cardinality are different "
                        "problems. A costly metric nothing duplicates is "
                        "fixed by dropping a LABEL, not the metric."))

            # ---- cost, only if the operator supplied a price ----------
            unit = None
            if a.invoice:
                try:
                    tot, ser = a.invoice.split("/")
                    unit = CD.Price.from_invoice(float(tot), float(ser),
                                                 a.currency)
                except (ValueError, ZeroDivisionError):
                    print(f"{f.red('error')}: --invoice expects "
                          f"TOTAL/SERIES, e.g. 4100/82000", file=sys.stderr)
                    return 2
            elif a.price_per_series is not None:
                unit = CD.Price(a.price_per_series, a.currency)

            priced = CD.cost(cls, unit)
            print()
            print("  " + f.bold("COST") + (f.dim("   no price supplied")
                                           if not priced["priced"] else ""))
            print()
            for line in CD.cost_lines(priced):
                if line.startswith("  = "):
                    print("  " + f.cyan(line.strip()))
                elif "UPPER BOUND" in line or "not archivable" in line:
                    print("  " + f.amber(line))
                else:
                    print("  " + f.dim(line) if not line.strip()
                          else "  " + line)
        except (OSError, ValueError) as exc:
            print(f"{f.red('error')}: {exc}", file=sys.stderr)
            return 2

    # --- local history store -------------------------------------------
    # --- record this run to the local history store --------------------
    # Viewing is `redd history`, a separate subcommand: looking at your
    # own recorded findings should not require re-auditing a file.
    if getattr(a, "record", False):
        import hashlib
        import history as HI
        try:
            with open(a.csv, "rb") as fh:
                did = hashlib.sha256(fh.read()).hexdigest()[:16]
        except OSError:
            did = None
        _run_rec, path, fresh = HI.record(
            SA.report_payload(res), window_label=a.window_label,
            window_from=a.window_from, window_to=a.window_to,
            source=os.path.basename(a.csv), dataset_id=did)
        print(f"{f.cyan('recorded')} {path}")
        if fresh:
            print(f.dim("  Created .redd/ — plain JSON, one file per run, "
                        "and it gitignores itself. These files hold YOUR "
                        "metric names and are never uploaded."))
        if a.window_label == "unknown":
            print(f.dim("  Window labelled 'unknown'. Until at least one "
                        "run is labelled 'incident', the history cannot "
                        "speak to the question that matters."))
        if not (a.window_from and a.window_to):
            print(f.dim("  No --window-from/--window-to. Without spans, "
                        "overlapping runs cannot be told from independent "
                        "ones, and the history will say so rather than "
                        "imply breadth it does not have."))
        print(f.dim(f"  View it:  redd history {os.path.basename(a.csv)}"))

    # --- shadow dashboard ---------------------------------------------
    if getattr(a, "shadow", None):
        if not a.dashboard:
            print(f"{f.red('error')}: --shadow needs --dashboard, the "
                  f"Grafana JSON to copy.", file=sys.stderr)
            return 2
        try:
            import shadow as SH
            with open(a.dashboard, encoding="utf-8") as fh:
                doc, rep = SH.build_shadow(
                    fh.read(), SA.report_payload(res)["archive_candidates"])
            with open(a.shadow, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2, ensure_ascii=False)
            print(f"{f.cyan('saved')} {a.shadow}")
            lines = SH.report_lines(rep)
            print("  " + (f.amber(lines[0]) if rep["broken_panels"]
                          or rep["broken_variables"] else f.dim(lines[0])))
            for ln in lines[1:]:
                print(f.dim("  " + ln) if not ln.strip().startswith("[")
                      else "  " + f.amber(ln.strip()))
            print(f.dim("\n  Import alongside the original. It has no uid, "
                        "so it cannot overwrite anything."))
            print(f.dim("  STRUCTURAL check only: it shows what breaks, not "
                        "that nothing was lost."))
            return 0
        except (OSError, ValueError) as exc:
            print(f"{f.red('error')}: {exc}", file=sys.stderr)
            return 2

    # --- routing generator -------------------------------------------
    if getattr(a, "route", None):
        try:
            import routing as RT
            if not a.completed_worksheet:
                print(f"{f.red('error')}: --route needs "
                      f"--completed-worksheet. The attestation is the gate; "
                      f"without it there is nothing to generate.",
                      file=sys.stderr)
                return 2
            with open(a.completed_worksheet, encoding="utf-8-sig") as fh:
                ws_text = fh.read()
            yaml_text = RT.generate(
                ws_text, SA.report_payload(res),
                cold_exporter=a.cold_exporter or "",
                primary_exporter=a.primary_exporter,
                allow_drop=a.allow_drop,
                source=os.path.basename(a.csv))
            with open(a.route, "w", encoding="utf-8") as fh:
                fh.write(yaml_text)
            routed = RT.routed_metrics(yaml_text)
            n = len(routed) // (1 if a.allow_drop else 2)
            print(f"{f.cyan('saved')} {a.route}")
            if a.allow_drop:
                print("  " + f.amber(f"{n} metric(s) will STOP EXISTING. "
                                     f"There is no undo."))
            else:
                print(f.dim(f"  {n} metric(s) routed to "
                            f"'{a.cold_exporter}' — nothing deleted"))
            print(f.dim("  Review the diff and merge it yourself. This "
                        "tool does not apply configuration."))
            return 0
        except RT.RoutingRefused as exc:
            print(f"{f.red('refused')}: {exc}", file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"{f.red('error')}: {exc}", file=sys.stderr)
            return 2

    width = shutil.get_terminal_size((100, 24)).columns
    if a.command == "prune" and getattr(a, "worksheet", None) is not None:
        out = a.worksheet or os.path.splitext(a.csv)[0] + "_blast_radius.csv"
        text = SA.blast_radius_worksheet(res)

        # Pre-fill the machine-answerable columns from rule files and
        # dashboards on disk. Evidence only: cells the graph cannot
        # speak to are left EMPTY rather than filled with "no", because
        # an empty cell reads as unanswered and "no" reads as checked.
        # See SAFETY_BOUNDARIES.md condition 3 — a person still signs.
        graph = None
        if getattr(a, "refs", None):
            try:
                import refgraph as RG
                graph = RG.scan_paths(list(a.refs))
                text = RG.annotate_worksheet(text, graph)
            except (ImportError, FileNotFoundError) as exc:
                print(f"{f.red('error')}: {exc}", file=sys.stderr)
                return 2
        if out in ("-", ""):
            print(text)
        else:
            # utf-8-SIG: the worksheet's audience opens it in Excel,
            # which assumes the system ANSI codepage for .csv unless a
            # BOM says otherwise. Without it "not a clearance —" renders
            # as "not a clearance a€"", and metric names are not always
            # ASCII either (one shipped corpus has a column called
            # "Average ⬆️"). parse_worksheet reads utf-8-sig to match.
            with open(out, "w", newline="", encoding="utf-8-sig") as fh:
                fh.write(text)
            print(f"{f.cyan('saved')} {out}")
            if graph is not None:
                if not graph.scanned:
                    print(f"  {f.amber('!')} " + f.dim(
                        "no rule or dashboard files were found in the given "
                        "path(s) — every referenced_by_* cell is blank "
                        "because NOTHING WAS SCANNED, which is not the same "
                        "as nothing referencing these metrics"))
                print(f.dim(f"  pre-filled from {len(graph.scanned)} file(s); "
                            f"{len(graph.entries)} rule/panel reference(s)"))
                if graph.unreadable:
                    print(f"  {f.amber('!')} " + f.dim(
                        f"{len(graph.unreadable)} file(s) could not be parsed "
                        f"— that is a hole in the search: "
                        + "; ".join(graph.unreadable[:3])))
                print(f.dim("  Cells marked 'auto:' are machine-supplied "
                            "EVIDENCE, not clearance. An empty cell means "
                            "the scan could not see it, which is not the "
                            "same as nothing referencing it."))
            print(f.dim("  Complete the referenced_by_* columns before "
                        "generating any archive script."))

            # Tier summary. Says how many decisions of each KIND are in
            # the file, not how many are safe — a count that implied the
            # latter would be the clearance this whole document exists
            # to withhold.
            counts = SA.worksheet_tier_counts(text)
            if counts:
                print()
                order = [("C", "conflict - engine says ARCHIVE and the "
                               "scan found a reference"),
                         ("A", "identity pair"),
                         ("B", "high redundancy")]
                for letter, what in order:
                    n = counts.get(letter, 0)
                    if not n:
                        continue
                    col = f.amber if letter == "C" else f.cyan
                    print(f"  {col(letter)}  {n:>3} row(s)  {f.dim(what)}")
                if "C" in counts:
                    print(f.dim("  Start with C. Every tier still needs a "
                                "person; the grouping is by what was "
                                "checked, not by how safe it is."))
                if graph is None:
                    print(f.dim("  Tiers read NO SCAN because --refs was "
                                "not given: 'not found' means nothing "
                                "when nothing was searched."))
        return 0
    if a.command == "prune":
        drop = SA.deletion_candidates(res)
        if a.quiet:
            print("\n".join(u["name"] for u in drop))
        else:
            print()
            print(f.bold(f"  {len(drop)} deletion candidate(s) of "
                         f"{res['n_metrics']} metrics"))
            print(table([[u["name"], f"{u['unique']:.0%}",
                          u["best_partner"]] for u in drop],
                        ["metric", "unique", "duplicated by"],
                        ["l", "r", "l"], width))
            print()
    elif not a.quiet:
        try:
            lex = load_domain(a.domain) if a.domain else None
        except ValueError as exc:
            sys.stderr.write(f"redd: {exc}\n")
            return 2
        print(render(res, f, top=a.top, width=width, lex=lex))

    if a.html is not None:
        out = a.html or os.path.splitext(a.csv)[0] + "_signal_audit.html"
        SA.write_html(res, out)
        print(f"{f.cyan('saved')} {out}")
    if a.json is not None:
        payload = json_payload(res)
        if a.json in ("-", ""):
            print(json.dumps(payload, indent=2))
        else:
            with open(a.json, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            print(f"{f.cyan('saved')} {a.json}")

    ratio = res["headline_pr"] / res["n_metrics"] if res["n_metrics"] else 1
    if a.no_fail:
        return 0
    return 1 if ratio < 0.5 else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="redd",
        description="How many independent signals does your dashboard "
                    "actually have?")
    p.add_argument("--version", action="version",
                   version=f"redd {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("csv", help="CSV file; columns are metrics, rows "
                                    "are observations over time")
        sp.add_argument("--ignore", default="",
                        help="comma-separated columns to exclude")
        sp.add_argument("--max-rows", type=int, default=None,
                        help="use only the first N data rows")
        sp.add_argument("--quiet", action="store_true",
                        help="suppress the report")
        sp.add_argument("--no-fail", action="store_true",
                        help="always exit 0 (default exits 1 on heavy "
                             "redundancy, for CI gating)")
        sp.add_argument("--html", nargs="?", const="", default=None,
                        metavar="PATH",
                        help="write a self-contained HTML report")
        sp.add_argument("--json", nargs="?", const="-", default=None,
                        metavar="PATH",
                        help="write structured JSON ('-' for stdout)")
        sp.add_argument("--scale-by", action="append", default=[],
                        metavar="COL",
                        help="add a ratio basis dividing every column by "
                             "COL. Repeatable — each extra basis costs "
                             "about 2%% of a run, so declare every "
                             "candidate denominator rather than guessing "
                             "one.")
        sp.add_argument("--scale-exempt", action="append", default=[],
                        metavar="COL",
                        help="column that is ALREADY scale-free and must "
                             "not be divided. Repeatable. Dividing a rate "
                             "by a size injects the very factor the ratio "
                             "basis exists to remove.")
        sp.add_argument("--domain", default=None, metavar="NAME",
                        help="translate the failure catalogue into a "
                             "domain's language (ai, retail, ...). Naming "
                             "only — it cannot change a number or add a "
                             "check.")
        sp.add_argument("--basis", default=None, metavar="NAME",
                        help="which basis the headline reports: raw, "
                             "differenced, or ratio:COL. Declared, never "
                             "inferred.")
        sp.add_argument("--ordered", dest="ordered", action="store_true",
                        default=None,
                        help="declare that rows are consecutive "
                             "observations in time. Enables the trend and "
                             "correlation-drift checks, which mean nothing "
                             "otherwise. Assumed from a time-like column "
                             "in the header if not stated.")
        sp.add_argument("--not-ordered", dest="ordered", action="store_false",
                        help="declare that rows are entities — banks, "
                             "hosts, models — not a timeline. Suppresses "
                             "the time-dependent checks rather than "
                             "letting them invent a before and an after.")
        sp.add_argument("--strict-basis", action="store_true",
                        help="exit 2 rather than assume a basis. Intended "
                             "for CI. Will become the default for "
                             "non-interactive runs at 1.0.")
        sp.add_argument("--top", type=int, default=12,
                        help="rows per table (default 12)")
        sp.add_argument("--cardinality", default=None, metavar="PATH",
                        help="Prometheus text exposition (a saved /metrics "
                             "scrape) or the JSON from `count by (__name__)"
                             "({__name__=~\".+\"})`. Adds the cost axis. "
                             "Nothing is fetched — you run the query, this "
                             "parses the output.")
        sp.add_argument("--price-per-series", type=float, default=None,
                        metavar="X",
                        help="cost per series per month, from YOUR invoice. "
                             "No price is assumed and no vendor price list "
                             "is carried; without this, every cost figure "
                             "is reported as unknown rather than guessed.")
        sp.add_argument("--invoice", default=None, metavar="TOTAL/SERIES",
                        help="derive the unit price, e.g. --invoice "
                             "4100/82000. The arithmetic is shown.")
        sp.add_argument("--currency", default="GBP", metavar="SYM")
        sp.add_argument("--record", action="store_true",
                        help="append this run to the local history store "
                             "(.redd/history). Plain JSON, gitignored, "
                             "never uploaded.")
        sp.add_argument("--window-label", default="unknown",
                        choices=("quiet", "busy", "incident", "unknown"),
                        help="what kind of window this data covers. "
                             "DECLARED, never inferred — a classifier "
                             "guessing 'incident' would decide what this "
                             "tool reports. Label at least one window "
                             "'incident' or the history says nothing about "
                             "incidents.")
        sp.add_argument("--window-from", default=None, metavar="YYYY-MM-DD")
        sp.add_argument("--window-to", default=None, metavar="YYYY-MM-DD")
        sp.add_argument("--shadow", default=None, metavar="PATH",
                        help="write a [Shadow] copy of --dashboard in which "
                             "the archive candidates do not resolve. Load "
                             "both in Grafana to see what breaks. Proves a "
                             "STRUCTURAL claim only — see shadow.py.")
        sp.add_argument("--dashboard", default=None, metavar="PATH",
                        help="the Grafana dashboard JSON to shadow.")
        sp.add_argument("--route", default=None, metavar="PATH",
                        help="write an OTel Collector config that diverts "
                             "attested redundant metrics to cold storage. "
                             "Requires --completed-worksheet and "
                             "--cold-exporter. Writes a file; never applies "
                             "it. See SAFETY_BOUNDARIES.md Amendment 1.")
        sp.add_argument("--completed-worksheet", default=None, metavar="PATH",
                        help="a blast-radius worksheet with every "
                             "operational cell answered and a reviewer "
                             "named. Nothing is generated without one.")
        sp.add_argument("--cold-exporter", default=None, metavar="NAME",
                        help="exporter the redundant series are routed TO, "
                             "e.g. awss3/cold. Required: redundant "
                             "telemetry is relocated, not destroyed.")
        sp.add_argument("--primary-exporter", default="otlp", metavar="NAME")
        sp.add_argument("--allow-drop", action="store_true",
                        help="generate a hard drop instead of a route. The "
                             "series stop existing and there is no undo; "
                             "the generated file says so on its face.")
        sp.add_argument("--refs", action="append", default=None,
                        metavar="PATH",
                        help="a directory or file of Prometheus rule YAML "
                             "and/or Grafana dashboard JSON. Pre-fills the "
                             "worksheet's referenced_by_* columns with "
                             "evidence. Repeatable. Never a clearance: an "
                             "empty cell means the scan did not see it.")
        sp.add_argument("--worksheet", nargs="?", const="", default=None,
                        metavar="PATH",
                        help="write the blast-radius safety worksheet "
                             "('-' for stdout). Complete it before "
                             "archiving anything.")

    common(sub.add_parser("run", help="full audit"))
    common(sub.add_parser("prune", help="just the deletion candidates"))

    # Viewing history must not require re-auditing a CSV. It was a flag
    # on `run` first, which meant looking at your own recorded history
    # cost a full audit of a file you already audited.
    h = sub.add_parser("history",
                       help="what previous runs found, exceptions first")
    h.add_argument("source", nargs="?", default=None,
                   help="which dashboard's history, e.g. board.csv. "
                        "Omit to list what the store holds — histories "
                        "are per-dashboard and are never pooled.")
    h.add_argument("--root", default=".redd", metavar="DIR",
                   help="store location (default .redd)")
    h.add_argument("--json", action="store_true",
                   help="the analysis as JSON")
    return p




# ----------------------------------------------------------------------
# Domain lexicons
# ----------------------------------------------------------------------
def _domain_dir():
    """Where lexicons live, source tree or installed alike.

    Resolved relative to THIS MODULE, never to the working directory.
    `py-modules` ships flat files, so the wheel places the lexicons in a
    sibling directory of the module rather than inside a package; both
    layouts are checked and the first that exists wins. Getting this
    wrong is silent — `--domain` would simply report "none installed"
    for every pip user while working perfectly in the repo.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (
            # source tree
            os.path.join(here, "cli", "domains"),
            # installed: `data-files` land under sys.prefix, NOT beside
            # the module. Verified by building the wheel and installing
            # it into a clean venv — the first version of this function
            # checked only module-relative paths and `available_domains()`
            # returned [] for every pip user while working perfectly in
            # the repo. A feature that is silently dead on install is
            # worse than one that is absent.
            os.path.join(sys.prefix, "redd_domains"),
            os.path.join(here, "redd_domains"),
    ):
        if os.path.isdir(cand):
            return cand
    return os.path.join(here, "cli", "domains")


DOMAIN_DIR = _domain_dir()


def load_domain(name):
    """Load a domain lexicon. Presentation only — never analysis.

    A lexicon RENAMES and CONTEXTUALISES the engine's mathematical
    states. It cannot change a number, add a check, or decide anything.
    The engine stays blind to semantics; this is the only place a
    metric's meaning is allowed to exist.

    JSON rather than TOML on purpose. `tomllib` is Python 3.11+ and this
    package declares requires-python >=3.8, so TOML would mean either
    dropping three Python versions or hand-rolling a parser. JSON is
    stdlib everywhere and costs no dependency, which is the constraint
    that keeps the Pyodide demo possible.

    The deliberate shape of every entry is `consistent_with` /
    `but_also` / `distinguish_by`, never a verdict. `basis_conflicts`
    reports that a pair's correlation ROSE after a transform; it cannot
    tell whether a factor was injected or a real duplicate exposed.
    Both appeared in one corpus — ROA~ROE 0.60 to 0.98 (injected) and
    NETINC~ROA 0.06 to 0.9997 (real). A lexicon that printed one reading
    as fact would make the tool assert exactly what its own failure
    catalogue exists to prevent.
    """
    path = os.path.join(DOMAIN_DIR, f"{name}.json")
    if not os.path.exists(path):
        raise ValueError(
            f"unknown domain {name!r}. "
            f"Available: {', '.join(available_domains()) or '(none installed)'}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def available_domains():
    if not os.path.isdir(DOMAIN_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(DOMAIN_DIR)
                  if f.endswith(".json"))


def render_domain(res, lex, f, width):
    """The domain section. Renders only for checks that actually FIRED."""
    fired = {c[0] for c in CATALOGUE_FIRED(res)}
    checks = lex.get("checks", {})
    shown = [(k, v) for k, v in checks.items() if k in fired]
    out = []
    rule = "  " + "─" * min(width - 4, 76)
    out += ["", rule]
    out.append("  " + f.bold(f"{lex.get('title', lex['domain']).upper()} — "
                             f"WHAT THESE MEAN HERE")
               + f.dim("   naming only; the arithmetic is unchanged"))
    if not shown:
        out.append("")
        out.append(f.dim("    No check in this lexicon fired."))
        return out
    for key, v in shown:
        out.append("")
        out.append("  " + f.bold(v.get("label", key)))
        out.append(f"    consistent with : {v.get('consistent_with','')}")
        out.append(f"    but also        : {v.get('but_also','')}")
        out.append(f"    tell them apart : {v.get('distinguish_by','')}")
    out.append("")
    out.append(f.dim("    Two readings are given because the engine cannot "
                     "choose between them."))
    out.append(f.dim("    A pair whose correlation rose after a transform "
                     "was either given a"))
    out.append(f.dim("    shared factor or shown to already have one. "
                     "Someone who knows what"))
    out.append(f.dim("    the metrics mean can tell; the arithmetic cannot."))
    return out


def CATALOGUE_FIRED(res):
    return [(key, label) for key, label, avail, fires, _d in SA.CATALOGUE
            if avail and not SA._skip_reason(res, key) and fires(res)]


# ----------------------------------------------------------------------
# Execution guard
# ----------------------------------------------------------------------
def _repo_root(start=None):
    """Nearest ancestor that is THIS project's source tree, or None.

    A `pyproject.toml` alone is not enough — the user may be sitting in
    an unrelated project. The directory must also contain the engine
    module, or the guard would fire on someone else's repository.
    """
    d = os.path.abspath(start or os.getcwd())
    while True:
        if (os.path.exists(os.path.join(d, "pyproject.toml"))
                and os.path.exists(os.path.join(d, "signal_audit.py"))):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def execution_guard(stream=None, cwd=None):
    """Refuse to run an INSTALLED engine from inside the source tree.

    Registered as an open hazard in REAL_DASHBOARDS.md section 9: a
    stale `signal_audit.py` in site-packages shadows the source for
    anything run outside the project directory, and one benchmark in
    this project was measured through exactly that before the mismatch
    was noticed. The numbers looked plausible. That is the whole
    problem — a silently older engine does not announce itself.

    `demo/` has a drift guard (`build_demo.py --check`). This is the
    equivalent for the installed package, and it is spatial rather than
    hash-based: if you are standing in the source tree, the engine you
    are running should be the one you are standing in.

    Fails CLOSED, with one deliberate escape hatch. HANDOFF.md's own
    release procedure runs the freshly built wheel from inside the
    repository —

        python -m venv /tmp/v && /tmp/v/bin/pip install dist/*.whl
        /tmp/v/bin/redd run demo_dashboard.csv

    — which is precisely the situation this guard rejects, and is
    legitimate. `REDD_ALLOW_INSTALLED=1` permits it. Writing the
    guard without that hatch would have broken our own checklist, which
    is how a safety check becomes something people route around.

    Returns an exit code: 0 to proceed, 1 to stop.
    """
    stream = stream or sys.stderr
    if os.environ.get("REDD_ALLOW_INSTALLED") == "1":
        return 0
    root = _repo_root(cwd)
    if root is None:
        return 0                       # not in the source tree; nothing to check
    mod = os.path.abspath(getattr(SA, "__file__", "") or "")
    if not mod:
        return 0
    local = os.path.join(root, "signal_audit.py")
    if os.path.samefile(mod, local) if os.path.exists(mod) else False:
        return 0

    stream.write(
        "redd: REFUSING TO RUN — engine loaded from outside this repo.\n"
        "\n"
        f"  you are in : {root}\n"
        f"  engine from: {mod}\n"
        "\n"
        "  You are standing in the source tree but running an INSTALLED\n"
        "  copy of the engine. Any result would describe that copy, not\n"
        "  the code you are looking at, and it would look plausible.\n"
        "\n"
        "  Fix one of:\n"
        "    pip install -e .                  # point the install at this tree\n"
        "    pip uninstall redd-munro\n"
        "    REDD_ALLOW_INSTALLED=1 redd ...   # deliberate wheel test\n")
    return 1


def main(argv=None) -> int:
    _degrade_streams()
    rc = execution_guard()
    if rc:
        return rc
    a = build_parser().parse_args(argv)
    return _run(a, Fmt(_supports_colour(sys.stdout)))


if __name__ == "__main__":
    raise SystemExit(main())
