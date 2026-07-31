# Changelog

All notable changes are recorded here. The project version is sourced from
`VERSION`; release tooling verifies that `package.json` and `package-lock.json`
match it.

## [Unreleased]

### Added

- Immutable-v2 ARK KB snapshots with an atomic root `current.json`, sealed
  quality reports, previous-snapshot identity binding, rollback checks, and
  fail-closed burn-in/cutover contracts.
- Typed registration and domain projections, independent Gold review packs,
  signed receipt/registry foundations, and proposal-only Gold freeze tooling.
- Durable rebuild worker row scope, add-only Blueprint evidence foundations,
  exact narrow-gate diagnostics, and UpdateBaseline pre-publication inspection.
- Production `QUERY_SNAPSHOT` cache invalidation for the exact
  `context_packs`, `answer_plans`, `materialized_neighborhoods`, and
  `query_snapshots` cache tables, with external markers and crash recovery.
- Selective production `ROLE_ENTITY` and ontology-owned `DOMAIN_ENTITY`
  materializers, plus an exact one-file-at-a-time backend for all six domain
  projections.
- A computed fixed-11 production narrow-gate runner, full candidate reseal, and
  content-addressed atomic shadow publication receipt with independent
  post-switch verification.

### Changed

- Production KB updates now use reparse-safe whole-tree staging, explicit
  staging quarantine, current-pointer compare-and-swap, and source-manifest
  base binding.
- Incremental update planning now binds the scan, writer lock, delta receipt,
  base snapshot, and publication decision to one verified scope.
- Incremental candidates now receive a new immutable build identity, sealed
  quality report, exact previous-Snapshot lineage, and a final live-source
  recheck immediately before current-pointer CAS.
- Whole-cache equal-digest receipts are now accepted only under the strict
  `QUERY_SNAPSHOT` contract; the real Scarecrow replay produced a base-bound v3
  inspection with `baseBindingVerified=true`.
- Role invalidation now carries a content-addressed percentile-closure proof;
  projection downstream IDs have a fixed reverse-checked name mapping and no
  longer authorize a whole projection-directory rebuild.
- GitHub-facing status documentation now separates current `main` behavior from
  dated GPT Pro handoff/audit records.

### Fixed

- Reparse points cannot escape staging or additive quarantine boundaries.
- A stale or mismatched delta receipt cannot be treated as evidence for the
  current base snapshot.
- Query cache invalidation preserves worker-owned row scope through cache-first
  commit, recovered `RUNNING` replay, and terminal receipt validation.
- Domain rebuilds preserve manual and other producer rows; projection publish
  rejects reparse/cross-volume paths and atomically replaces only its queued
  artifact.
- Incremental publication failures now preserve the distinction between a
  verified `NOT_REPLACED` pointer and an `UNCERTAIN` post-attempt state.
- Documentation no longer presents the 2026-07-27 `58/75` snapshot or a
  machine-specific Capture count as current.

### Operations

- Documented retention and validation rules for temporary KB builds,
  immutable snapshots, Registry generations, Git LFS objects, worktrees, and
  legacy Capture artifacts.
- Recorded a production-shaped 12/12 backend result separately from the live
  input audit. The live candidate is non-selective (14 additions and 10
  changes), so no incremental publication occurred.

## [0.2.0] - 2026-07-27

### Added

- DLL-hash-isolated Ghidra projects with PE/PDB GUID + Age verification and
  fail-closed Native Evidence provenance v2.
- Declarative native recipes, a public C++/PDB fixture, Native Evidence Store
  JSON/SQLite artifacts, bounded queries, and question-driven context packs.
- Explicit Blueprint-to-Native edges, Hybrid Context Packs, report Claim
  Manifests, sanitized public evidence manifests, and stale-source validation.
- Runtime observation calibration schema, synthetic Harvest fixtures, and a
  real-data collection protocol.
- Versioned HTTP analysis contracts and GitHub Actions release/native-fixture
  gates.
- A GPT Pro progress-review brief covering completed implementation, verified
  results, remaining evidence limits, and questions for next-step direction.

### Changed

- Project positioning now describes an evidence-first local analyzer rather
  than a complete Blueprint or C++ decompiler.
- The legacy native import command delegates to the versioned loot/quality
  recipe; the local server and frontend entrypoints delegate shared security,
  response, routing, API, escaping, and error responsibilities to modules.
- The former license-decision placeholder is resolved as an author-retained
  rights policy; no open-source license is granted by default.

### Fixed

- Compact Native Evidence indexes now distinguish zero gaps from gap details
  omitted by the token budget.
- The npm lock contract now includes the WASM runtime peer required by
  Linux/Node 24 clean installs.

### Security

- Local mutations require a same-session request boundary, bounded bodies, and
  loopback-safe startup defaults.
- Remote binding requires an explicit bearer token; background output is
  bounded/redacted and cancellation terminates the process tree.

## [0.1.0] - 2026-05-03

### Added

- Initial public Blueprint-to-Code analysis workflow.
