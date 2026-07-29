# ARK KB vNext Stage 10 Performance Profile

Status: `PARTIAL_PASS_BLOCKED_BY_SNAPSHOT_REBUILD`

This profile covers the Stage D1 instrumentation and the currently executable
part of D3. It does not change gold data, quality thresholds, cache validity,
Evidence/freshness checks, the sealed snapshot, or the current pointer.

## Measurement target

- Branch baseline: `bc6843f`
- Snapshot build: `20260727T222549-a2d56bd7fed8`
- Immutable benchmark report:
  `reports/query_benchmark.json`
- Manifest-declared benchmark SHA-256:
  `fc20fd1972673840afd03b22f9ee4fdd725b8cac2b13ea9cf31c5f43e0cb9891`
- Cases per run: 130 fixed gold-set cases
- Timing clock: `time.perf_counter`
- Timing mode: explicit `include_timing=True`; disabled by default
- Query-count mode: inclusive SQLite trace callbacks. Counts from nested
  segments are not additive.

The benchmark diagnostics are response-private. `VNextKnowledgeService.query`
does not accept a profiling request field and does not return timing data, even
when an internal recorder is attached.

## Three consecutive real runs

| Run | P50 ms | P95 ms | P99 ms | Max ms | Semantic outcome SHA-256 |
|---|---:|---:|---:|---:|---|
| 1 | 0.307 | 5.365 | 210.899 | 255.208 | `e49071f8da57570045fec06a9a28e54643f8363aa13c7cf6c477d4d48faa6ae8` |
| 2 | 0.303 | 5.573 | 218.917 | 248.867 | `e49071f8da57570045fec06a9a28e54643f8363aa13c7cf6c477d4d48faa6ae8` |
| 3 | 0.305 | 5.764 | 217.886 | 243.316 | `e49071f8da57570045fec06a9a28e54643f8363aa13c7cf6c477d4d48faa6ae8` |

All three overall single-entity P95 values are below the fixed 250 ms target.
The semantic digest is computed from ordered `queryId`, `route`, `status`, and
`gapCodes` values.

The same digest computed from the sealed benchmark report is
`e49071f8da57570045fec06a9a28e54643f8363aa13c7cf6c477d4d48faa6ae8`.
The following results are also unchanged in every run:

- Route counts: `AMBIGUOUS=3`, `DB_PARTIAL=14`,
  `DB_SEMANTIC_COMPLETE=80`, `EVIDENCE_REQUIRED=30`,
  `IDENTITY_ONLY_COMPLETE=3`
- Protocol compliance: `119/130` (`0.9153846153846154`)
- Wrong-answer count: `12/130` (`0.09230769230769231`)
- Expected-gap match: `36/47` (`0.7659574468085106`)

The sealed pre-optimization report recorded P50 `0.247 ms`, P95 `358.929 ms`,
P99 `419.260 ms`, and maximum `636.353 ms`. This comparison uses the immutable
report already bound by the manifest; no replacement report or gold edit was
made.

## Run 1 segmented diagnostics

| Segment | Samples | SQLite queryCount | P50 ms | P95 ms | P99 ms |
|---|---:|---:|---:|---:|---:|
| planner total | 130 | 7,548 | 0.283 | 5.284 | 210.859 |
| fact requirement planning | 130 | 0 | 0.002 | 0.004 | 0.005 |
| identity lookup | 130 | 7,025 | 0.057 | 0.219 | 210.774 |
| fact query | 45 | 45 | 0.031 | 0.087 | 0.107 |
| effective fact query | 60 | 80 | 0.125 | 0.238 | 0.344 |
| relationship query | 138 | 193 | 0.033 | 0.761 | 0.938 |
| source revision validation | 303 | 55 | 0.016 | 0.160 | 0.211 |
| Evidence hydration | 126 | 150 | 0.003 | 2.998 | 3.724 |
| answer/context serialization | 130 | 0 | 0.018 | 0.059 | 0.105 |

Identity lookup owns the long tail. In run 1 the three exact-alias samples had
P95 `255.208 ms`, and the three fuzzy-candidate samples had P95 `205.896 ms`.
The overall fixed-corpus P95 passes, but the exact-alias tail remains the next
measured optimization target.

Source-revision timing is intentionally marked `PARTIAL`. Identity,
relationship, native, effective-class, and post-hydration fact-revision checks
are measured. URI/status/confidence checks that are evaluated inline with their
fail-closed fact, relationship, or Evidence gate remain included in those
parent segments. Separating them would require changing the gate evaluation
flow, so no synthetic duration is reported.

Relationship SQL projection and relationship-Evidence projection also remain a
single measured operation for the same reason.

## Storage and cache blocker

The real storage benchmark stops fail-closed before cache validation:

```text
BLOCKED_BY_SNAPSHOT_REBUILD
search.sqlite schema contract is incomplete:
index:idx_entity_search_display_nocase
index:idx_entity_search_internal_nocase
index:idx_search_aliases_nocase
```

Those indexes were added by `bc6843f`, but the current immutable snapshot was
built before that schema change. The benchmark must not add indexes in place,
edit the manifest, bypass schema validation, or treat a fixture as gold.

Before the refusal, run 1 recorded:

| Storage segment | Samples | SQLite queryCount | P50 ms | P95 ms | P99 ms |
|---|---:|---:|---:|---:|---:|
| pointer/manifest resolution | 2 | 0 | 0.705 | 0.769 | 0.769 |
| connection acquire | 63 | 63 | 0.239 | 0.626 | 0.988 |

`cacheValidation` and `cacheWrite` therefore have no real-snapshot samples.
Their instrumentation and unchanged build/revision/TTL/invalidation contracts
are exercised by the isolated storage contract test, but those fixture timings
are not presented as gold or as D3 completion evidence.

The storage performance gate remains failed until a new immutable snapshot is
built and sealed from the real source manifest with the new Search indexes.
This work must not switch `current.json` until all normal cutover gates pass.

## Completion decision

- D1 opt-in segmented diagnostics: implemented.
- Default API path leakage: prevented and tested.
- Three consecutive 130-case overall P95 results below 250 ms: passed.
- Route/status/gap parity with sealed report: passed.
- Existing index/EXPLAIN contracts: preserved.
- Cache build/revision/TTL/invalidation validity: preserved.
- Real storage/search/cache performance suite: `BLOCKED_BY_SNAPSHOT_REBUILD`.
- Stage D as a whole: not complete; no threshold or gate was relaxed.
