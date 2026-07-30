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

`stage_snapshot_from_baseline()` now creates one same-volume, independently
copied tree under `.incremental-staging/<unique-id>/snapshot`. Windows
traversal uses parent-relative `NtCreateFile` opens, no-reparse object
attributes, handle-based enumeration, volume serials, and 128-bit file IDs.
POSIX traversal uses `dir_fd`/`openat` semantics with `O_NOFOLLOW`. Both paths
reject links, reparse points, hard-link aliases, and special files.

The copier validates the manifest-declared databases and sealed reports,
separately identifies `cache.sqlite` as copied build-bound disposable state,
and verifies source and staged tree membership, file identities, sizes, and
SHA-256 values. SQLite files must be sealed in DELETE-journal mode, have no
WAL/SHM sidecars, and pass `quick_check` and foreign-key checks. The returned
receipt is explicitly unsigned, unpublished, non-authoritative, shadow-mode,
legacy-default evidence. Cleanup uncertainty returns
`STAGING_CLEANUP_UNCERTAIN` with only the controlled relative residual
identifier.

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

## Reparse-safe additive quarantine

`freeze_additive_blueprint_input()` supports exactly one added
`BLUEPRINT_EVIDENCE` revision plus the matching `captures` aggregate change.
Update, delete, rename, additional semantic changes, and batches larger than
one fail closed.

The function derives the exact Evidence directory from the locked
`UpdateBaseline`; callers cannot supply an artifact list or quarantine path.
It reuses the whole-tree staging no-follow traversal and handle identities to
copy `evidence.sqlite` plus an actually present adjacent `manifest.json` into:

```text
.incremental-staging/<staging-id>/quarantine/<source-id>/
```

This directory is beside, not inside, `snapshot/`. Source and destination
parent chains are pinned and revalidated; reparse points, special files,
hard-link aliases, path replacement, cross-volume quarantine placement,
unexpected artifacts, and content drift are rejected. The copied files have
independent file identities and are rehashed before an immutable
`FrozenAdditiveBlueprintInput` is returned.

The canonical quarantine receipt binds the UpdateBaseline, staging proof,
source identity, SQLite identity, optional manifest, exact aggregate, and
quarantine tree digest. It records only controlled relative paths and fixes
`published=false`, `productionAuthority=false`,
`e4Scenario2Complete=false`, `cutoverEligible=false`, `mode=shadow`, and
`defaultQuerySource=legacy`.

## Legacy v2 diagnostic inspection

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

## Base-bound v3 receipt and inspection

`build_base_bound_add_only_delta_receipt()` accepts only the typed
`UpdateBaseline`, `StagedBaselineSnapshot`,
`FrozenAdditiveBlueprintInput`, `BlueprintIngestResult`, and
`InvalidationPlan`, plus the durable backend event ID. It opens the base and
staged Core databases itself. The `v3` receipt binds the exact current
pointer and manifest, manifest-declared base Core bytes, opaque no-follow file
identities, the same-volume independent staged Core, staging proof and tree
digests, the frozen quarantine proof and artifact identities, the canonical
source diff, both live logical database digests, protected truth tables, and
the staged Core's durable event and terminal receipts.

`inspect_base_bound_prepublication_delta_receipt()` produces:

```text
ark-kb-prepublication-delta-inspection/v2
UNSIGNED_LOCAL_BASE_BOUND_PREPUBLICATION_INSPECTION
baseBindingVerified=true
```

It authenticates canonical raw bytes with an independently supplied SHA-256
before parsing, accepts only the `v3` receipt, then independently reopens and
revalidates the typed baseline, staging, quarantine, both Core databases,
durable event, and terminal receipts. All authority flags remain false and
the mode remains `shadow` with `defaultQuerySource=legacy`. The legacy `v2`
receipt and v1 inspection remain diagnostic-only with
`baseBindingVerified=false`.

## Runner order and deliberately unavailable capabilities

`run_incremental_update()` now captures and validates the current pointer,
manifest, live source manifest, source diff, and `UpdateBaseline` under the
incremental writer lock. The default path passes that same baseline into safe
whole-tree staging, revalidates it, freezes the exact additive Evidence bundle
outside the staged snapshot, then repeats the live source scan before
planning. Default Blueprint ingest requires the frozen receipt, revalidates
the quarantine before opening the staged Core, and passes only
`frozen_input.ingest_root` to the materializer. It does not use a lock-external
scan or `paths.capture_root` as the ingest input.

After worker drain, the default path builds and immediately inspects the v3
receipt before processing worker blockers and before any gate or publisher
hook. A real `BLOCKED_GAP` terminal result still produces a valid base-bound
receipt and remains blocked; it is never upgraded to
`FOUNDATION_VERIFIED`.

The default add-only runner now opens only the staged baseline's
`snapshot/cache.sqlite` in read-write, no-create mode. Before drain it
revalidates the staging receipt, exact cache path, regular-file presence,
foreign-key mode, SQLite `quick_check`, and the five-table cache schema.
`ProductionIncrementalRebuildBackend` preserves the existing Core
materializers and adds only `QUERY_SNAPSHOT`: inside the worker-owned guarded
cache transaction it deletes `context_packs`, `answer_plans`,
`materialized_neighborhoods`, then `query_snapshots`. The existing whole-cache
row-scope receipt, external marker, cache-first commit, and recovered
`RUNNING` replay remain authoritative; `metadata` is preserved apart from the
worker marker.

The following downstream capabilities remain deliberately unavailable:

- role and domain entity materialization;
- projection materialization;
- the default narrow-gate hook remains unavailable;
- the default publisher remains unavailable;
- no pointer write is reachable from `update_baseline.py`.

## Real blockers

The real replay closed the following foundation questions:

- scan-before-writer-lock is replaced by a writer-lock-bound initial scan and
  final live rescan;
- reparse-safe whole-tree staging;
- additive quarantine;
- delta receipt base binding;
- strict additive Query scope;
- the production `QUERY_SNAPSHOT` backend;
- the explicit whole-cache equal-digest receipt contract.

The authorized Scarecrow Evidence was validated as the exact single live
addition:

```text
/Game/PrimalEarth/CoreBlueprints/Engrams/EngramEntry_Scarecrow.EngramEntry_Scarecrow
BLUEPRINT_EVIDENCE added=1, changed=0, deleted=0
captures aggregate changed=1
```

There was no `semanticProducerContract`, Discovery, Ontology, Gold, Native, or
other semantic-input drift. The true prepublication worker result was:

```text
SUCCEEDED=4
BLOCKED_GAP=8
FAILED=0
```

The succeeded tasks were `FACT × 2`, `EFFECTIVE_ENTITY × 1`, and
`QUERY_SNAPSHOT × 1`. The remaining blocked tasks were `ROLE_ENTITY × 1`,
`DOMAIN_ENTITY × 1`, and `PROJECTION × 6`. The independently authenticated v3
receipt had raw SHA-256
`6c56aa85ff43349ac20b64fae93058e51ad645d27660099c87758ca62c5e94b3`
and `baseBindingVerified=true`.

The current real blocker is `REBUILD_QUEUE_NOT_DRAINED`, caused only by those
Role, Domain, and Projection backends. Narrow gates, signed production
authorization, and the publisher remain unavailable. The result therefore
still fixes `productionAuthority=false`, `published=false`,
`e4Scenario2Complete=false`, and never reaches a pointer write. Current
Snapshot authority remains at 234 Blueprint Evidence entries; live captures
have 235, with Scarecrow as the only unpublished addition.

## Verification

Focused checks:

```powershell
python -m pytest -q tests/test_kb_pointer_cas.py
python -m pytest -q tests/test_kb_additive_quarantine.py
python -m pytest -q tests/test_kb_delta_receipt_base_binding.py
python -m pytest -q tests/test_kb_update_baseline.py
python -m pytest -q tests/test_kb_incremental_delta.py
python -m pytest -q tests/test_update_ark_kb_vnext.py
python -m pytest -q tests/test_kb_safe_staging.py
ruff check scripts/update_ark_kb_vnext.py scripts/blueprint_translator/kb_vnext/blueprint_ingest.py scripts/blueprint_translator/kb_vnext/pointer_cas.py scripts/blueprint_translator/kb_vnext/source_manifest.py scripts/blueprint_translator/kb_vnext/incremental_delta.py scripts/blueprint_translator/kb_vnext/update_baseline.py scripts/blueprint_translator/kb_vnext/safe_staging.py tests/test_update_ark_kb_vnext.py tests/test_kb_additive_quarantine.py tests/test_kb_pointer_cas.py tests/test_kb_update_baseline.py tests/test_kb_safe_staging.py
git diff --check
```
