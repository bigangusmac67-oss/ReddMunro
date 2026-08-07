# AI evaluation corpus — pre-registered predictions

**Registered before any data was fetched and before any leaderboard schema was inspected.** The ordering is the evidence, as with every other corpus in this project.

## Why this one exists

`cli/domains/ai.json` shipped before any AI evaluation data had ever been through the engine. The lexicon is honest in its wording — it offers competing readings rather than verdicts — but it is a set of **hypotheses about what findings would mean**, written without a single row of evidence. That is the only part of this product that has not been through the falsification loop, and this document is the correction.

Until it is scored, the demo page says so explicitly: *"Lenses ship for AI evaluation and retail. Neither has a validated corpus behind it yet."*

## The data

Public model-evaluation leaderboards publish per-model scores across many benchmarks. That is exactly the shape the engine takes: **columns are benchmarks, rows are models.**

Sources in preference order — first that yields a clean matrix wins, and which one was used will be recorded:

1. **HuggingFace Open LLM Leaderboard** — many models, ~6 core benchmarks plus derived averages
2. **HELM** — broader benchmark coverage, fewer models
3. Any published leaderboard export with per-model, per-benchmark numbers

## The structural fact that matters before anything is measured

**Rows are models, not time.** This is an entity-indexed cross-section, exactly like the FDIC corpus and unlike every time series before it. So §7 F0 applies directly: differencing model *i+1* minus model *i* subtracts two unrelated systems, and the engine will do it anyway and headline the result unless a basis is declared.

The FDIC run showed that failure is **silent** — raw 2.52 against differenced 2.47, near-identical, so nothing warns you. A second independent confirmation of that on different data would move it from "observed once" to "a property of the engine."

## Predictions

| # | Prediction | Basis | Result |
|---|---|---|---|
| **A1** | (as registered) | | **hit, decisively.** 15 columns → **2.24 effective signals, 85.1% redundant**. Registered under 7.5; measured 2.24. Six benchmarks correlate with each other at mean \|r\| = 0.620. |
| **A2** | (as registered) | | **FAILED — 22.5% divergence** (raw 2.239, differenced 2.743) against a ±15% band. F0's silent-agreement does **not** replicate. But the failure produced something worse than silence: the **"Calendar trend confound" check FIRED** on a dataset with no time axis, reporting shared calendar movement across 541 language models. See finding 1. |
| **A3** | (as registered) | | **hit.** `#Params (B)` correlates with the six benchmarks at mean \|r\| = **0.380**, while the benchmarks correlate with *each other* at mean **0.620**. Model size is not the shared factor; capability is, and it is not a column. **The scale cure requires the confound to be measured, not merely present.** |
| **A4** | (as registered) | | **hit.** Every Raw/normalised pair clustered, and three are **exact identities at r = 1.0000** — `IFEval`, `MMLU-PRO`, `MATH Lvl 5`. The leaderboard publishes each of those twice. |
| **A5** | (as registered) | | **MISSED — and it exposed a new gap.** `Average ⬆️` **is** exactly the mean of the six normalised benchmarks: r = **1.00000000**, max deviation **0.000000**, on **100%** of 541 models. `derived_aggregates` returned **empty**. It tests mean over *all* other columns; this is a mean over a **subset** (6 of 14). `subset_sums` finds exact sums over subsets. **Neither finds a mean over a subset.** See finding 2. |
| **A6** | (as registered) | | **hit.** Grade **A**, 36.0 rows per metric, 541 models × 15 columns. |
| **A7** | (as registered) | | **hit** — six flagged, all involving size or cost rather than benchmark pairs. Strongest: `IFEval` ~ `#Params (B)` at r = **0.11** but **27.6×** its Gaussian-implied dependence. Instruction-following and model size are strongly related and almost perfectly uncorrelated. |

## The one I most want to test and probably cannot

The `ai.json` lexicon's headline entry is **"score moves with output length"** — the verbosity hypothesis. Testing it needs a **token-count or response-length column alongside the scores**, and most leaderboards do not publish one.

**If length data is unavailable, that entry stays unvalidated and must be labelled as such** — in the lexicon file and on the page. Scoring six other predictions does not license the one we could not test. This is recorded now so the temptation to quietly count A1–A7 as validating the whole lexicon is closed off in advance.

## What would falsify the reasoning rather than the tool

**A1 failing** — if benchmarks turn out to be largely independent, then the redundancy premise does not transfer to this domain, the AI lens should be withdrawn rather than reworded, and that is a genuinely useful negative result worth publishing.

## What would be a finding about the engine

**A2 failing** in the direction of large divergence would be good news for the tool and bad news for my write-up of F0: it would mean the silent-agreement behaviour was a property of that dataset rather than of entity-indexed data generally, and §7 would need correcting.

**A3 holding** would establish a limit worth stating plainly: the scale-confound cure requires the confound to exist as a column. Where the dominant factor is latent, the engine can show you that redundancy exists but cannot remove it for you.

## Known handling decisions, fixed in advance

- **Missing scores.** Leaderboards have gaps where a model was not run on a benchmark. The engine drops rows with any missing value (listwise) and reports the count. If that discards more than half the models, the benchmark set will be narrowed to a complete block **before** the audit, and the narrowing recorded.
- **Model families.** Multiple checkpoints of the same base model are not independent observations. If the fetched slice is dominated by variants of a few bases, that is a limitation on the effective sample and will be stated rather than glossed.
- **Basis will be declared**, not assumed: `--basis raw`, because rows are entities. The differenced figure will still be reported, as evidence for A2.

---

## Result — 5 hits, 2 failures, and both failures are the valuable part

**Corpus:** HuggingFace Open LLM Leaderboard, `open-llm-leaderboard/contents`, 541 models × 15 columns recovered from 4,576 available, grade A. Basis declared `raw`, because rows are models.

**Headline: 15 columns carry 2.24 effective signals — 85.1% redundant.** The premise transfers to AI evaluation intact, and more strongly than to any dashboard corpus so far.

### Finding 1 — a time-based check fired on data with no time

A2 predicted the differenced basis would fail silently, as it did on FDIC. It did not. It diverged 22.5%, and in doing so **tripped the "Calendar trend confound" check** — which told an operator that shared calendar movement explained part of the structure across 541 language models. There is no calendar. There is no order to these rows at all.

This is worse than the silent failure it replaced. A silent wrong number is dangerous; **a named, confident, categorically impossible finding is worse**, because it invites an explanation and someone will construct one. The engine has no notion of whether rows are ordered, so `trend_gap` is computed and named regardless.

Registered as an open defect. The fix is not to suppress the check but to gate it on a declared ordered basis — which the declaration machinery already supports and this check does not consult.

### Finding 2 — the subset-MEAN gap

A5 assumed `derived_aggregates` would catch a published average. It did not, and the reason generalises:

| detector | finds | misses |
|---|---|---|
| `derived_aggregates` | max / min / mean over **all** other columns | any aggregate over a **subset** |
| `subset_sums` | exact **sum** over a **subset** | any **mean** over a subset |

`Average ⬆️` is the arithmetic mean of exactly six of the fourteen other columns, exact to floating point on every one of 541 models, and **nothing detected it**. It is the same hole `subset_sums` was built to close, in its averaging form — and closing that hole is what exposed this one, because `subset_sums` deliberately requires an additive relation.

This is a directly actionable next repair: the dominance-peeling argument that made subset sums tractable does not transfer to means, but a mean over k columns is a sum divided by k, so a bounded search over subset sizes may. It needs its own pre-registration and its own negative controls.

### What remains untested, and must not be counted

The `ai.json` lexicon's headline entry is **"score moves with output length"** — the verbosity hypothesis. **This corpus contains no token-count or response-length column, so that entry is still unvalidated.** Five other predictions holding does not license it, and the pre-registration said so before the data was fetched precisely to close off the temptation. The lexicon has been marked accordingly.

### Honest limits of this corpus

- **541 of 4,576 models**, taken as a contiguous block by leaderboard order rather than sampled. Order correlates with score, so this slice skews toward the top of the board.
- **Model families are not independent.** Many rows are fine-tunes or merges of a few base models. The effective sample is smaller than 541 and that is not quantified here.
- **Six benchmarks, published twice each.** The 85.1% redundancy figure is inflated by the leaderboard publishing Raw and normalised versions of everything — that is a real property of the board as presented, but it is not six independent findings.
