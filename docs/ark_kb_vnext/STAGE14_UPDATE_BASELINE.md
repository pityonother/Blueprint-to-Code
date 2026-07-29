# Stage 14 Update Baseline and Pre-publication Delta Inspection

## Status

This slice is pre-publication infrastructure only. It does not run narrow
gates, call a publisher, rename an immutable snapshot into service, update
`current.json`, or complete E4 scenario 2.

It also does not add production artifact authorization. The existing
`incremental_delta.py` boundary remains unchanged: additive delta construction
and receipt validation accept only explicit `TEST_ONLY` artifacts. This module
does not expose a caller-controlled production trust label.

The required runtime state remains:

```json
{
  "mode": "shadow",
  "defaultQuerySource": "legacy",
  "cutoverEligible": false
}
```

## Exact current pointer and manifest baseline

`capture_current_snapshot_baseline()` acquires the same persistent
`.current.json.lock` used by full publication and rollback. While holding that
lock it:

1. reads the exact raw `current.json` bytes;
2. binds their SHA-256 and parsed `buildId`;
3. resolves only the direct `snapshots/<buildId>` child;
4. rejects escaping paths, links, junctions, reparse points, malformed strict
   JSON, and a manifest with a different schema or build identity;
5. freezes the raw manifest bytes and SHA-256;
6. rereads both pointer identity and manifest bytes before releasing the lock.

`validate_current_snapshot_baseline()` later rechecks the exact pointer raw
SHA, build ID, snapshot destination, and manifest raw SHA under that shared
lock. The bounded file reader compares pre-open path identity, opened-handle
identity, and post-read path identity and rejects links, reparse points,
hard-link aliases, replacements, and in-place metadata drift. A whitespace-only
pointer rewrite is therefore a conflict.

This is a cooperative-writer, ACL-protected-root contract. It assumes the
snapshot root, its parent directories, and `.current.json.lock` cannot be
renamed or replaced by an uncooperative same-user process while sampled. It is
not a directory-handle attestation and must not be described as safe against
adversarial parent-directory replacement.

This baseline deliberately records `tree_validated=false`. A matching manifest
does not by itself prove that every database, report, or projection file still
matches that manifest.

`stage_snapshot_from_baseline()` therefore fails closed with:

```text
REPARSE_SAFE_STAGING_COPY_UNAVAILABLE
```

No directory is created. Whole-tree staging remains unavailable until a later
implementation can pin source and destination handles without path races,
reject links/reparse points/hard-link aliases at open time, validate the exact
manifest-declared file set and hashes, and rescan the final staged tree. This
slice must not be described as having staged an exact snapshot.

## Source-diff identity

`source_manifest.canonical_source_diff_bytes()` is the sole source-diff
serializer used by both the additive foundation and this pre-publication
inspection. It requires:

- the exact source-diff schema;
- tuple-backed, sorted, unique change groups;
- exact `ADDED`, `CHANGED`, and `DELETED` shapes;
- source IDs that match their immutable revisions;
- no source ID replay across groups.

`build_update_baseline()` accepts the snapshot root rather than a caller-made
baseline object. It captures the current pointer and manifest through the
shared lock, and `UpdateBaseline` construction immediately revalidates that
captured identity against the same root. A value object for a nonexistent or
foreign root therefore cannot be passed through the public builder.

`UpdateBaseline` is immutable. `SourceManifest.entries` must be an exact tuple
of immutable `SourceRevision` objects, so mutating a caller-owned list cannot
change a stored candidate fingerprint after the source diff was computed. The
baseline parses the base source manifest from the frozen raw snapshot
manifest, recomputes the candidate diff internally, and binds:

```text
baseBuildId
basePointerSha256
baseManifestSha256
baseSourceManifestFingerprint
candidateSourceManifestFingerprint
sourceDiffSha256
```

Supplying a manually constructed diff is not an API option. Replacing a
dataclass field with a different diff fails its constructor invariant.
The resulting payload is still only
`UNSIGNED_LOCAL_UPDATE_BASELINE`; it fixes `treeValidated=false` and
`productionAuthority=false`. Local capture and revalidation do not turn it
into a signed production artifact or a whole-tree attestation.

`validate_final_source_manifest()` requires the final live rescan payload,
including its fixed `generatedAt`, to equal the initial candidate payload
exactly. It also recomputes the same canonical diff bytes and SHA. A caller of
this helper must rescan using the initial candidate's `generatedAt`; a new
timestamp is intentionally not normalized away.

## Additive quarantine deliberately unavailable

`freeze_additive_blueprint_input()` always fails before inspecting either
filesystem path or creating any directory:

```text
REPARSE_SAFE_ADDITIVE_QUARANTINE_UNAVAILABLE
```

There is no frozen-input type or successful quarantine path in this slice.
A later implementation must pin source and destination directory handles,
reject file and parent-directory replacement at open time, verify the exact
candidate-bound artifact set, and rescan the completed quarantine before it
can return an ingest root.

## Two-stage receipt inspection

`inspect_prepublication_delta_receipt()` first requires a lowercase raw receipt
SHA-256 supplied separately from the artifact. Missing input fails with:

```text
MISSING_OUT_OF_BAND_DELTA_RECEIPT_SHA256
```

The receipt bytes are hashed and compared with that out-of-band value before
JSON parsing or use of the receipt's internal `delta-proof://...` value.
Only after the raw bytes match does the helper:

1. decode bounded strict JSON with duplicate keys and non-finite constants
   rejected;
2. validate the foundation receipt's internal content proof;
3. require its `sourceDiffSha256` to equal the inspected `UpdateBaseline`
   source diff;
4. require `trustContext=TEST_ONLY`;
5. require `published=false` and `e4Scenario2Complete=false`.

Changing a receipt and recomputing its internal proof does not work unless its
independently supplied raw artifact SHA also changes. The function never
derives a missing out-of-band raw SHA from the receipt itself.

The resulting inspection is explicitly:

```text
UNSIGNED_LOCAL_PREPUBLICATION_INSPECTION
```

It records the expected and observed raw receipt SHA values, the internal
content SHA, and the exact inspected source-diff SHA. It contains no
`baseBuildId`, pointer SHA, manifest SHA, base source fingerprint, or candidate
source fingerprint. Its contract fixes:

```text
baseBindingVerified=false
productionAuthority=false
```

Matching a source-diff SHA does not prove that the receipt was produced from
this baseline database; the same diff can occur against different bases. The
inspection is not a base association, signature, reviewer receipt, operator
receipt, burn-in artifact, or publication authorization.

Passing `production=True` always returns:

```text
PRODUCTION_ARTIFACT_AUTHORIZATION_REQUIRED
```

There is no fallback that relabels a TEST_ONLY artifact as production.

## Deliberately not wired

`run_incremental_update()` and its default hooks are not changed by this
slice. In particular:

- the default narrow-gate hook remains unavailable;
- the default publisher remains unavailable;
- no pointer write is reachable from `update_baseline.py`;
- whole-tree snapshot staging is deliberately unavailable;
- additive source quarantine is deliberately unavailable;
- the existing runner's scan-before-writer-lock orchestration remains a P0
  integration gap and must not be described as closed merely because these
  reusable primitives exist.

A later reviewed orchestration PR must acquire the incremental writer lock
before candidate scan, first implement a reparse-safe quarantine, materialize
only from its verified output, build the verified delta before invalidation
queue writes, consume a real independently supplied receipt artifact SHA,
bind the receipt's base database identity independently, perform the final live
rescan and baseline recheck, then stop until gates/reseal/publication are
separately implemented.

## Real blockers

No current live source URI represents an authorized new Blueprint. Fixtures,
zero-byte files outside the supported capture layout, and synthetic test
Evidence are not production evidence.

The real slice remains blocked by:

```text
BLOCKED_BY_MISSING_AUTHORIZED_ADDITIVE_BLUEPRINT_EVIDENCE
BLOCKED_BY_MISSING_SIGNED_PRODUCTION_ARTIFACT_AUTHORIZATION
BLOCKED_BY_UNPROVEN_ADDITIVE_DERIVED_DEPENDENCY_SCOPE
BLOCKED_BY_MISSING_PRODUCTION_BACKEND_TERMINAL_RECEIPTS
BLOCKED_BY_REPARSE_SAFE_WHOLE_TREE_STAGING
BLOCKED_BY_REPARSE_SAFE_ADDITIVE_QUARANTINE
BLOCKED_BY_UNVERIFIED_DELTA_RECEIPT_BASE_BINDING
```

## Verification

Focused checks:

```powershell
python -m pytest -q tests/test_kb_pointer_cas.py
python -m pytest -q tests/test_kb_update_baseline.py
python -m pytest -q tests/test_kb_incremental_delta.py
python -m pytest -q tests/test_update_ark_kb_vnext.py
ruff check scripts/blueprint_translator/kb_vnext/pointer_cas.py scripts/blueprint_translator/kb_vnext/source_manifest.py scripts/blueprint_translator/kb_vnext/incremental_delta.py scripts/blueprint_translator/kb_vnext/update_baseline.py tests/test_kb_pointer_cas.py tests/test_kb_update_baseline.py
git diff --check
```
