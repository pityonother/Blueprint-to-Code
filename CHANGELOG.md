# Changelog

All notable changes are recorded here. The project version is sourced from
`VERSION`; release tooling verifies that `package.json` and `package-lock.json`
match it.

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

### Changed

- Project positioning now describes an evidence-first local analyzer rather
  than a complete Blueprint or C++ decompiler.
- The legacy native import command delegates to the versioned loot/quality
  recipe; the local server and frontend entrypoints delegate shared security,
  response, routing, API, escaping, and error responsibilities to modules.

### Security

- Local mutations require a same-session request boundary, bounded bodies, and
  loopback-safe startup defaults.
- Remote binding requires an explicit bearer token; background output is
  bounded/redacted and cancellation terminates the process tree.

## [0.1.0] - 2026-05-03

### Added

- Initial public Blueprint-to-Code analysis workflow.
