# ARK KB vNext Stage 10 Performance Profile

Status: `PERFORMANCE_PASS_BLOCKED_BY_GOLD_AND_BURN_IN`

This profile covers the Stage D1 instrumentation and the currently executable
part of D3. It does not change gold data, quality thresholds, cache validity,
Evidence/freshness checks, the sealed snapshot, or the current pointer.

## Measurement target

- Performance implementation head: `a92a716`
- Integrated snapshot build: `20260729T115548-1a203b594bb6`
- Immutable benchmark report:
  `reports/query_benchmark.json`
- Manifest-declared benchmark SHA-256:
  `f9950a60a0c7bf90ea2427855e84981c0222c030fe710f4e29cbbc9e79bc2361`
- Cases per run: 130 fixed gold-set cases
- Timing clock: `time.perf_counter`
- Timing mode: explicit `include_timing=True`; disabled by default
- Query-count mode: inclusive SQLite trace callbacks. Counts from nested
  segments are not additive.

The benchmark diagnostics are response-private. `VNextKnowledgeService.query`
does not accept a profiling request field and does not return timing data, even
when an internal recorder is attached.

## Three consecutive real runs

| Run | P50 ms | P95 ms | P99 ms | Storage coverage | Performance gates |
|---|---:|---:|---:|---|---|
| 1 | 0.349 | 4.857 | 7.701 | complete | pass |
| 2 | 0.358 | 4.104 | 5.652 | complete | pass |
| 3 | 0.407 | 4.935 | 8.473 | complete | pass |

All three overall single-entity P95 values are below the fixed 250 ms target.
The following semantic outcomes are unchanged in every run and match the
manifest-bound benchmark:

- Route counts: `AMBIGUOUS=3`, `DB_PARTIAL=14`,
  `DB_SEMANTIC_COMPLETE=80`, `EVIDENCE_REQUIRED=30`,
  `IDENTITY_ONLY_COMPLETE=3`
- Protocol compliance: `128/130` (`0.9846153846153847`)
- Wrong-answer count: `3/130` (`0.023076923076923078`)
- Expected-gap match: `45/47` (`0.9574468085106383`)

The sealed pre-optimization report recorded P50 `0.247 ms`, P95 `358.929 ms`,
P99 `419.260 ms`, and maximum `636.353 ms`. The new sealed report records P50
`0.269 ms`, P95 `3.786 ms`, P99 `5.298 ms`, and maximum `5.324 ms`. Both
comparisons use manifest-bound reports; no replacement report, threshold, or
gold edit was made.

## Run 1 segmented diagnostics

| Segment | Samples | SQLite queryCount | P50 ms | P95 ms | P99 ms |
|---|---:|---:|---:|---:|---:|
| planner total | 130 | 7,548 | 0.315 | 4.770 | 7.603 |
| fact requirement planning | 130 | 0 | 0.002 | 0.004 | 0.005 |
| identity lookup | 130 | 7,025 | 0.069 | 0.166 | 3.903 |
| fact query | 45 | 45 | 0.039 | 0.081 | 0.179 |
| effective fact query | 60 | 80 | 0.138 | 0.327 | 0.505 |
| relationship query | 138 | 193 | 0.022 | 0.796 | 1.052 |
| source revision validation | 303 | 55 | 0.019 | 0.184 | 0.231 |
| Evidence hydration | 126 | 150 | 0.003 | 3.698 | 4.527 |
| answer/context serialization | 130 | 0 | 0.019 | 0.066 | 0.075 |

The former alias/fuzzy long tail is no longer present in the fixed corpus.
The sealed storage run observed P95 `162.956 ms` for exact alias and
`156.337 ms` for fuzzy candidates. Across the three independent reruns, the
largest fuzzy P95 was `189.281 ms` and the largest valid cache-hit P95 was
`43.323 ms`.

Source-revision timing is intentionally marked `PARTIAL`. Identity,
relationship, native, effective-class, and post-hydration fact-revision checks
are measured. URI/status/confidence checks that are evaluated inline with their
fail-closed fact, relationship, or Evidence gate remain included in those
parent segments. Separating them would require changing the gate evaluation
flow, so no synthetic duration is reported.

Relationship SQL projection and relationship-Evidence projection also remain a
single measured operation for the same reason.

## Storage and cache result

The real full snapshot contains all three required NOCASE indexes and passed
the FTS `EXPLAIN QUERY PLAN` contract. The sealed storage run covered exact
canonical URI, exact alias, FTS phrase, fuzzy candidate, cold/warm connections,
cache miss/hit, expiration, source revision, invalidation token, and build
rejection paths.

The sealed storage result is `coverage.complete=true` and
`performanceGates.passed=true`. All 10 SQLite stores/projections passed
`integrity_check`, had zero foreign-key violations, used `journal_mode=DELETE`,
and had no WAL/SHM sidecars.

`cache.sqlite` is intentionally `disposable=true` and mutable after
publication. It is excluded from the authoritative immutable-database set;
runtime writes remain build-bound and must pass revision-set, TTL, and
invalidation-token checks. The benchmark always copies the snapshot to an
isolated directory before cache mutation, and the original runtime cache is
not used as semantic authority.

## Completion decision

- D1 opt-in segmented diagnostics: implemented.
- Default API path leakage: prevented and tested.
- Three consecutive 130-case overall P95 results below 250 ms: passed.
- Route/status/gap parity with sealed report: passed.
- Existing index/EXPLAIN contracts: preserved.
- Cache build/revision/TTL/invalidation validity: preserved.
- Real storage/search/cache performance suite: passed.
- Stage D engineering and fixed performance gates: complete.
- Cutover remains blocked by independent gold, query correctness, production
  incremental coverage, and burn-in; no threshold or gate was relaxed.
