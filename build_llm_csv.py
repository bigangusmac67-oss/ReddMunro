"""
build_llm_csv.py -- assemble the AI evaluation corpus.

Source: HuggingFace Open LLM Leaderboard, dataset `open-llm-leaderboard/contents`,
read through the public datasets-server /rows endpoint. 4,576 models.

ROWS ARE MODELS, NOT TIME. This is an entity-indexed cross-section like the
FDIC call reports, so first differences across rows subtract two unrelated
models. Prediction A2 in AI_EVAL_PREREG.md is about exactly that, and the
audit is run with `basis="raw"` declared.

Same ~64KB fetch ceiling as every other corpus here: responses truncate
mid-JSON, so complete row objects are recovered by brace matching and the
partial tail is discarded rather than trusted.
"""
import csv, glob, json, os

SRC = ("/sessions/peaceful-happy-knuth/mnt/.claude/projects/"
       "C--Users-shaun-AppData-Roaming-Claude-local-agent-mode-sessions-"
       "634f597b-911a-4863-9929-bbf5916a718e-a780dba2-364e-4a52-a342-"
       "b1965f4fa6ed-local-1c3d725e-2705-4233-a736-3415a4d1eb82-outputs/"
       "9d019725-894b-4e86-9a28-6dd7b4add57a/tool-results")
WANT = "datasets-server.huggingface.co/rows?dataset=open-llm-leaderboard"

# The six benchmarks, each published twice (Raw and normalised), plus the
# leaderboard's own Average and two size/cost columns. Deliberately ALL of
# them: the Raw/normalised pairing and the Average are what predictions
# A4 and A5 are about, so removing them would be removing the test.
COLS = ["Average ⬆️",
        "IFEval Raw", "IFEval", "BBH Raw", "BBH",
        "MATH Lvl 5 Raw", "MATH Lvl 5", "GPQA Raw", "GPQA",
        "MUSR Raw", "MUSR", "MMLU-PRO Raw", "MMLU-PRO",
        "#Params (B)", "CO₂ cost (kg)"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_leaderboard.csv")


def rows_from(path):
    """Recover every COMPLETE row object from a truncated response."""
    with open(path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    out, i = [], 0
    key = '{"row_idx":'
    while True:
        i = raw.find(key, i)
        if i < 0:
            break
        depth, j, instr, esc = 0, i, False, False
        while j < len(raw):
            c = raw[j]
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = not instr
            elif not instr:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        if j >= len(raw) or depth != 0:
            break                      # truncated tail -- discard
        try:
            out.append(json.loads(raw[i:j + 1]))
        except json.JSONDecodeError:
            pass
        i = j + 1
    return out


def main():
    seen, dropped = {}, 0
    for p in sorted(glob.glob(os.path.join(SRC, "mcp-workspace-web_fetch-*.txt"))):
        with open(p, encoding="utf-8", errors="replace") as f:
            if WANT not in f.readline():
                continue
        got = rows_from(p)
        print(f"  {os.path.basename(p)[-17:-4]}  {len(got):>3} complete rows")
        for r in got:
            seen[r["row_idx"]] = r["row"]

    print(f"\n  {len(seen)} unique models recovered")
    if not seen:
        return 1

    kept = []
    for idx in sorted(seen):
        r = seen[idx]
        vals = {}
        ok = True
        for c in COLS:
            v = r.get(c)
            if v is None or v == "":
                ok = False
                break
            try:
                vals[c] = float(v)
            except (TypeError, ValueError):
                ok = False
                break
        if ok:
            vals["eval_name"] = r.get("eval_name", f"model_{idx}")
            kept.append(vals)
        else:
            dropped += 1

    print(f"  {dropped} dropped for a missing or non-numeric score")
    print(f"  FINAL: {len(kept)} models x {len(COLS)} benchmarks"
          f"   ({len(kept)/len(COLS):.1f} rows per metric)")

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for r in kept:
            w.writerow([r[c] for c in COLS])
    print(f"  wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
