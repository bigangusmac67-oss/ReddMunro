# Example monitoring sources

Mock Prometheus rules and a Grafana dashboard, written against the metric
names in `demo/prometheus_infra.csv` so the reference graph can be run
end to end without a real deployment.

**These are not from a production system.** They are shaped to exercise
four specific cases, including one the tool gets wrong on purpose.

```
python -m redd prune demo/prometheus_infra.csv \
    --basis differenced --ordered \
    --worksheet ws.csv --refs examples/monitoring
```

The scan fills one column — `scan_evidence`. The four
`referenced_by_*` columns stay blank for the reviewer to answer, because
they are parsed as yes/no and because the answer is theirs to give.

**Rows where the engine says ARCHIVE and the scan finds a live reference
are marked `** CONFLICT **` and sorted to the top**, with anything behind
a paging alert first. Both facts were always on the row, in different
columns; a reviewer had to join them mentally, and on a 200-panel board
sorted by unique variance the one row that matters sits in the middle
looking like every other row.

## What the scan covers, and what it does not

Of the four attestation columns, **the scan can speak to two.** This is
worth knowing before anyone reports that it "fills in the worksheet".

| column | source | status |
|---|---|---|
| `referenced_by_monitors` | Prometheus rule YAML | **covered** — alerts and recording rules, including transitively |
| `referenced_by_other_dashboards` | Grafana dashboard JSON | **covered** — panels, nested rows, template variables |
| `referenced_by_slos` | — | **no parser.** SLO definitions live in Sloth or OpenSLO YAML, or inside a vendor's config. None is read. |
| `referenced_by_runbooks` | — | **no parser, and possibly never.** Runbooks are prose in a wiki. A metric named in a paragraph is not machine-answerable with any confidence worth having. |

So on a forty-metric board the saving is roughly half the look-ups, not
all of them. The two uncovered columns stay blank and stay the
reviewer's job, and `parse_worksheet` will not attest a metric while
either is unanswered — so a half-covered scan cannot quietly unlock an
export.

| metric | engine says | reference graph finds |
|---|---|---|
| `request_rate_total` | **ARCHIVE** | a **paging** monitor |
| `methodGET_status404` | ARCHIVE | a monitor |
| `node_load15` | KEEP | monitor + dashboard |
| `node_load5` | KEEP | dashboard (a template variable) |
| `node_memory_MemAvailable_bytes` | KEEP | dashboard + monitor, **via a recording rule** |
| `node_load1` | KEEP | **nothing found** — see below |

---

## Case 1 — the collision the worksheet exists for

`request_rate_total` matches `methodGET_status200` at **r = 0.99987**, so
the engine marks it ARCHIVE: statistically it carries no variation the
other does not.

It is also the entire expression behind `RequestRateCollapse`, which
carries `severity: critical` and `page: "true"`.

Both facts are true. **Only one of them is in the CSV.** Archive it on
the arithmetic alone and the next outage pages nobody.

## Case 2 — the reference a human scanning dashboards misses

`node_memory_MemAvailable_bytes` appears on one panel. It is also the
input to the recording rule `instance:memory_available:ratio`, which is
the input to the `MemoryAvailableLow` alert.

Someone checking "is this on a dashboard?" finds one panel and moves on.
The graph follows the chain and reports both.

## Case 3 — why this is a tokeniser and not a grep

`grep -rc node_load1 examples/monitoring` returns **6 matches.** The
reference graph reports **0 references.** The six:

- `node_load15 > 8` — a superstring, twice
- `node_load5` in the dashboard and the template variable — no, those are
  different metrics that happen to share a prefix
- `label_replace(..., "node_load1", ...)` — inside a **string literal**,
  where it is a label value, not a series
- `SELECT ts, node_load1 FROM metrics_archive` — a `rawSql` panel

A substring search would have reported `node_load1` as referenced in five
places and a reviewer would have kept a metric for no reason. Which is
the harmless direction — the next case is not.

## Case 4 — the one the tool gets wrong, deliberately

**That `rawSql` panel is a real reference.** A billing export selects
`node_load1` from an archive table, and if you archive the metric that
export breaks.

The reference graph does not see it, because **SQL is not parsed**.
Guessing at a second query language would produce exactly the confident
wrong answer this module exists to avoid.

So the tool reports `not_found_in_scanned_sources` — not "unreferenced",
not "safe". The distinction is the entire point, and this example exists
to make it concrete rather than theoretical:

> An empty cell means the scan could not see it. That is not the same as
> nothing referencing it.

A reviewer who reads that phrasing and goes looking finds the SQL panel.
A reviewer handed "unreferenced: yes" does not.

---

## Adding your own

Point `--refs` at any directory containing `.yml` / `.yaml` Prometheus
rule files or `.json` Grafana dashboard exports. Repeat the flag for
several roots:

```
--refs ./monitoring/rules --refs ./grafana/dashboards
```

Files that fail to parse are reported as **a hole in the search** rather
than skipped, and a path that does not exist is a hard error — scanning
nothing would produce a worksheet whose every cell is empty, which reads
as "nothing references these" when in fact nothing was looked at.

**Not parsed, and each for a reason:** SQL (a different language),
Terraform (next, and worth doing), PagerDuty and Opsgenie configs (an API,
not files), and anything a template variable resolves to at render time.


---

## Shadow dashboard

```
python -m redd prune demo/prometheus_infra.csv --basis differenced --ordered \
    --dashboard examples/monitoring/dashboards/platform.json \
    --shadow shadow.json
```

```
0 panel(s) go empty · 1 panel(s) BREAK · 0 template variable(s) break
  [BREAKS] Not-found ratio
      archiving methodGET_status404 leaves node_load15 without its operand
      rate(methodGET_status404[5m]) / rate(node_load15[5m])
```

**That panel is the whole point.** `methodGET_status404` is an archive
candidate — no unique variance, statistically a restatement. It is also
one operand of a ratio. Archive it and the panel does not thin out, it
breaks, and **no audit of values could have told you**, because the
breakage is in the query rather than in the numbers.

The generated board replaces archived metrics with names that will not
resolve, rather than deleting the targets. Deleting them would render
perfectly — the metric still exists in your datasource, because the
archive has not happened — and would prove nothing while looking like
proof.

It carries no `uid`, so importing it cannot overwrite the original.

**What it does not establish:** that no information was lost. Two metrics
identical during healthy traffic are exactly the pair that diverges
during the incident the metric was kept for. The audit measured
correlation over the same window the shadow renders, so agreement
between them is the same evidence twice, not independent confirmation.
The first panel of the generated dashboard says so.
