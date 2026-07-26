# Changelog

All notable changes are recorded here. The project version is sourced from
`VERSION`; release tooling verifies that `package.json` and `package-lock.json`
match it.

## [0.2.0] - 2026-07-27

### Added

- Version-bound native evidence provenance and declarative analysis foundations.
- Native/Blueprint hybrid evidence and report claim validation foundations.
- Runtime observation calibration fixtures and local control-center hardening.

### Changed

- Project positioning now describes an evidence-first local analyzer rather
  than a complete Blueprint or C++ decompiler.

### Security

- Local mutations require a same-session request boundary, bounded bodies, and
  loopback-safe startup defaults.

## [0.1.0] - 2026-05-03

### Added

- Initial public Blueprint-to-Code analysis workflow.
