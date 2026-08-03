# ADR-004: Separate immutable Evidence and Interpretation revisions

## Status

Accepted

## Date

2026-08-03

## Context

Evidence Publication v3 must publish an immutable, manifest-bound SQLite
revision before changing `evidence/current.json`. Interpretation Contract v1
is produced later and must bind the exact Evidence manifest that it explains.

Putting Interpretation files into an already published Evidence revision would
mutate an immutable directory. Making the Evidence manifest bind an
Interpretation manifest while the Interpretation manifest binds the Evidence
manifest would also create a hash cycle.

## Decision

Evidence and Interpretation use independent immutable revision trees and
independent current pointers:

```text
captures/<asset>/
  .publication.lock
  evidence/
    current.json
    revisions/<evidenceRevisionId>/
      evidence.sqlite
      agent_index.md
      manifest.json
  interpretation/
    current.json
    revisions/<interpretationRevisionId>/
      interpretation.json
      interpretation.md
      trace.json
      gaps.json
      pseudocode.txt
      manifest.json
  output/
    agent_index.md
```

The Evidence manifest binds only Evidence artifacts. The Interpretation
manifest binds the exact `evidenceRevisionId` and raw Evidence manifest
SHA-256. Evidence never points downstream to Interpretation.

Both publishers use the stable asset-level `.publication.lock`. A publisher
stages and validates a complete revision on the same volume, renames it into
the revision tree, then performs an exact raw-pointer compare-and-swap. A CAS
conflict leaves a recognizable orphan revision and must not be reported as a
successful publication.

`output/agent_index.md` is a one-release compatibility copy. It is refreshed
only after the Evidence pointer succeeds and is never release authority.

## Hash order

Evidence publication:

1. Derive the existing deterministic Evidence revision identity from source,
   parser, and schema identity.
2. Close and validate SQLite.
3. Compute a logical semantic digest that excludes `generatedAt` and local
   machine paths.
4. Render the bounded agent index.
5. Hash the database and index bytes.
6. Serialize and hash the Evidence manifest.
7. Serialize the pointer and perform exact CAS.

Interpretation publication:

1. Capture the raw Evidence pointer and manifest hashes.
2. Build deterministic IR, statements, gaps, trace, and pseudocode.
3. Compute the Interpretation semantic digest without `generatedAt` or a
   revision ID.
4. Hash all rendered artifacts and derive the Interpretation revision ID.
5. Serialize and hash the Interpretation manifest.
6. Under the shared lock, revalidate the Evidence baseline and perform exact
   Interpretation pointer CAS.

Only pointers contain their manifest SHA. No manifest contains its own hash.

## Invariants

- Published revision contents are never modified.
- An existing revision identity is reused only after complete validation.
- Same identity with different semantic or artifact content fails with
  `REVISION_COLLISION`.
- A damaged v3 pointer or revision never falls back silently to v2 or legacy.
- v2 is readable for one compatibility release but is not release authority.
- Legacy temporary projection requires explicit opt-in and is not release
  authority.
- Interpretation current may bind only fresh, release-authority v3 Evidence.
- A newer Evidence pointer makes an older Interpretation pointer unavailable
  for the default current view; old pairs remain explicitly addressable.

## Alternatives considered

### Append Interpretation to an Evidence revision

Rejected because it breaks Evidence immutability and makes crash recovery and
concurrent readers unsafe.

### Make the Evidence and Interpretation manifests reference each other

Rejected because it creates an impossible self-referential hash graph.

### Add a third container manifest and publication ID

Technically valid, but rejected for v0.3.0 because every interpreter-only
update would republish or repoint an unchanged Evidence container and replace
the established `evidence/current.json` contract.

## Consequences

- Work D can publish and validate Evidence independently.
- Work E can evolve the interpreter without copying or mutating Evidence.
- Readers need to validate two pointers when serving Interpretation.
- The release documentation must explain that Evidence and Interpretation are
  separately versioned but cryptographically bound in one direction.
