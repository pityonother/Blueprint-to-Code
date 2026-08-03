# Changelog

All notable changes are recorded here. The project version is sourced from
`VERSION`; release tooling verifies that `package.json` and `package-lock.json`
match it.

## [Unreleased]

## [0.3.1] - 2026-08-04

### Distribution

- Added a downloadable Windows x64 portable ZIP with the prebuilt frontend and
  a bundled Python runtime, so users do not need a system Python or Node.js.
- Added upstream-runtime provenance, exact inventory verification, an internal
  `SHA256SUMS.txt`, and an external ZIP SHA-256 sidecar.
- Added a clean-extract startup gate for the bundled runtime and loopback web
  service.
- Kept GitHub's automatic `Source code` archives as developer-only source
  downloads; the user-facing portable asset has an explicit stable name.
- Kept captures, analysis databases, knowledge-base snapshots, Native Evidence,
  ARK assets, DevKit, DLL/PDB files, and builder-specific paths out of the
  public portable package.

## [0.3.0] - 2026-08-03

### Blueprint

- Added immutable Evidence Publication v3 with atomic current-pointer updates,
  manifest-bound validated readers, stable evidence identity, and bounded
  Evidence queries.
- Added Interpretation Contract v1 with deterministic control/data-flow
  projections, statement trace, explicit gaps, and source-bound review links.
- Downgraded heuristic behavior hints so names, comments, defaults, and other
  non-confirmed observations cannot become confirmed behavior.
- Kept a clear legacy/experimental pseudocode boundary: pseudocode is an
  Evidence-derived review aid, not recovered source code.
- Split the Blueprint workspace into bounded frontend, route, publication, and
  interpretation modules.

### Harvest

- Added a dominance audit and separate confirmed/conditional ranking views.
- Added canonical variant selection, metric-specific units, and separate
  static total/static cycle/observed result semantics.
- Added runtime profile isolation so observations from different profiles are
  never compared as one population.
- Added relative-first specialties and an explicit Effectiveness gap where
  evidence cannot support a stronger claim.
- Split Harvest build, ranking, HTTP, and frontend responsibilities into
  explicit module boundaries.

### Engineering

- Added a source archive policy, release-content scanner, and
  path/credential/generated-artifact scan contracts for exact release refs.
- Removed the DevKit exporter machine-specific project root; resolution now
  follows environment variable, plugin-ancestor search, then current-user
  Documents fallback using Unreal's cross-platform path APIs.
- Kept the ARK KB in shadow/legacy mode. This release does not change the live
  pointer, default query source, or cutover eligibility.

### Knowledge base foundations

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

### Knowledge base changes

- Production KB updates now use reparse-safe whole-tree staging, explicit
  staging quarantine, current-pointer compare-and-swap, and source-manifest
  base binding.
- Incremental update planning now binds the scan, writer lock, delta receipt,
  base snapshot, and publication decision to one verified scope.
- Whole-cache equal-digest receipts are now accepted only under the strict
  `QUERY_SNAPSHOT` contract; the real Scarecrow replay produced a base-bound v3
  inspection with `baseBindingVerified=true`.
- GitHub-facing status documentation now separates current `main` behavior from
  dated GPT Pro handoff/audit records.

### Knowledge base fixes

- Reparse points cannot escape staging or additive quarantine boundaries.
- A stale or mismatched delta receipt cannot be treated as evidence for the
  current base snapshot.
- Query cache invalidation preserves worker-owned row scope through cache-first
  commit, recovered `RUNNING` replay, and terminal receipt validation.
- Documentation no longer presents the 2026-07-27 `58/75` snapshot or a
  machine-specific Capture count as current.

### Operations

- Documented retention and validation rules for temporary KB builds,
  immutable snapshots, Registry generations, Git LFS objects, worktrees, and
  legacy Capture artifacts.
- Recorded the real prepublication queue result
  (`SUCCEEDED=4`, `BLOCKED_GAP=8`, `FAILED=0`); Role, Domain, and Projection
  remain deliberately unavailable and no publication occurred.

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
