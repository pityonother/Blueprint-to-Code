# Additive Incremental Publication v1

Status: Work Packages A and B are implemented as engineering capability. The live
candidate is still blocked before staging, so no live incremental publication or
E4 completion is claimed.

## Decision

The v1 incremental path accepts only one base-bound, add-only Blueprint Evidence
source. It reuses the immutable base Snapshot in an isolated staging tree, proves
the logical Core delta, materializes an exact invalidation graph, and lets the
worker own every rebuild transaction and terminal receipt. Any broader Source Diff
stops before staging and requires a full rebuild.

## Role dependency closure

Role classification is not entity-local. Six metrics use an empirical CDF within a
semantic class group. Replacing one entity's raw value can therefore alter peers
whose current value lies in the closed interval between the previous and candidate
value. A semantic-group change affects both complete groups.

`compute_additive_role_dependency_scope()` calculates that closure before
invalidation. The event binds the changed IDs, complete Role task IDs, classifier
version, source revision, metric transitions, and canonical proof hash. The worker
rejects a proof when its IDs differ from the durable queue. Each task recomputes
global distributions but may write only its own entity rows in:

- `knowledge_roles`;
- `knowledge_depth_policies`;
- `role_metrics`;
- `role_signal_metrics`.

The row-scope triggers prevent a selective task from hiding a whole-role rebuild.

## Domain ownership

The selective Domain producer owns only `CLASS_ANCESTRY` and
`TYPED_REGISTRATION` rows for the queued entity. It recomputes those rows from
fresh class assignments, ancestry categories, typed registrations, and the exact
ontology revision. Manual, map, and other producer rows are never invalidated or
deleted by this backend. An empty but independently recomputed owned target is a
valid equal-content result and receives the explicit
`VERIFIED_DOMAIN_OWNER_TARGET_STATE` verification basis.

## Single-projection publication

Projection IDs 1 through 6 map in declaration order to the six names in
`DOMAIN_PROJECTIONS`; the worker performs the reverse check for every task. A task
builds exactly one complete SQLite in a worker-created sibling staging directory.
The builder validates schema, integrity, foreign keys, content digest, Core content,
review binding, ontology version, and candidate build/source metadata. It updates
only the matching `projection_runs` row.

Publication rejects symlink/reparse paths, non-files, unexpected sibling artifacts,
and cross-volume staging. The previous file is copied to a durable rollback backup;
the staged file then replaces only the named target with a same-volume atomic
operation. On Windows, the replacement uses `MoveFileExW` with
`MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH`. The external marker contains
the event, task, and attempt token. A recovered `RUNNING` task must independently
verify marker, artifact, Core content, and target state; otherwise it re-executes in
the guarded transaction or fails closed.

## Receipt and failure semantics

The worker, not a backend, decides `SUCCEEDED`. It compares independently computed
before/after target digests, tracks actual SQLite write operations, enforces the
canonical table and row scope, and writes a content-addressed terminal receipt.
`BLOCKED_GAP` denotes a named missing prerequisite; unexpected code, SQL, path, or
verification failures are `FAILED`. Core partial writes roll back. Cache work and
projection files require durable external markers so a crash between external
commit and Core receipt can be replayed safely.

## Evidence boundary

The production-shaped acceptance fixture drains the original 12-task shape with
12 succeeded tasks, no gaps or failures, no pending/running tasks, and
`worker.drained=true`. It does not publish and is not E4 production evidence.

The 2026-07-31 read-only live audit found 14 added and 10 changed sources, including
non-selective semantic inputs. The production capability check therefore returned
`NON_SELECTIVE_CHANGE_FULL_REBUILD_REQUIRED` before staging. No live Snapshot,
pointer swap, narrow-gate result, Gold, authorization, burn-in, or cutover claim was
created.

## Production narrow gates

The default updater now computes the repository's fixed 11-check contract from the
final staged candidate. It does not accept caller-controlled pass flags. The runner
independently reopens the v3 delta receipt, requires the worker queue to be fully
drained, binds the canonical worker receipt set, checks changed revision freshness,
active stale state, foreign keys, effective dependencies, typed registrations, all
six Core/artifact projection digests, affected search identity, the four empty
canonical cache tables, all 10 sealed SQLite artifacts, and the exact unchanged
base pointer/manifest.

The canonical report is stored at
`reports/incremental_narrow_gates.json`. Its SHA-256 and proof are sealed into the
candidate manifest together with the base identity, candidate Source Manifest
fingerprint, and v3 delta receipt SHA-256. The report remains an
`ENGINEERING_DIAGNOSTIC`; `productionAuthority`, `cutoverEligible`, and every E4 or
cutover implication remain false. Any failed observation prevents the publisher
from being called.

## Candidate reseal

After the worker drains and before the final v3 delta receipt is constructed, the
complete candidate receives a new immutable build identity. The existing snapshot
contract keeps `source.sha256` as the exact hash of the 10 semantic inputs; the full
candidate Source Manifest fingerprint is additionally bound in `incrementalUpdate`
and `incrementalPublication`. These two identities are deliberately not conflated.

Catalog, Core, Search, cache, six projections, `projection_runs`, runtime health,
database metrics, and the manifest are rebound to the new build. Catalog and Search
non-metadata truth digests must remain unchanged. WAL is checkpointed, journal mode
is sealed, sidecars are rejected, and the existing full quality suite is evaluated
and sealed twice using the same immutable-candidate procedure as a full build. A
non-75/75 result is preserved honestly and the candidate remains `shadow/legacy`.

## Atomic shadow publication

The publisher accepts only
`.incremental-staging/<32-lowercase-hex>/snapshot`, rejects symlink/reparse or
cross-volume targets and existing immutable destinations, and reuses the reviewed
full-snapshot same-volume directory rename plus raw-pointer CAS. The CAS is bound to
the expected base build ID, raw pointer SHA-256, and current manifest SHA-256. A
fresh live Source Manifest validation runs at the final pre-CAS boundary.

After the pointer attempt, the publisher independently resolves
`current.json -> manifest -> databases`, verifies the candidate Source Manifest,
previous Snapshot, quality binding, narrow-gate artifact, runtime health, and
`shadow/legacy` policy, then returns a content-addressed
`UNSIGNED_LOCAL_WRITE_FACT` receipt. A failure with the old pointer independently
observed is `NOT_REPLACED`; an unreadable or non-verifiable post-attempt state is
`UNCERTAIN`. The code never reports an automatic rollback.

`current.json` remaining on the old build does **not** imply that no new
immutable directory was created. The same-volume rename intentionally precedes
the pointer CAS. If the final callback or CAS fails after that rename, the
`NOT_REPLACED` result includes a relative `publicationResidualIdentifier`, a
deterministic relative file inventory, and the policy
`PRESERVE_FOR_MANUAL_RECONCILIATION`. The orphan is never auto-deleted; an
operator must reconcile it against the observed pointer before any retry or
cleanup.

The root-level `manifests/*.sql` files are non-authoritative compatibility
copies. Snapshot readers bind only through `current.json` and the immutable
snapshot manifest. Compatibility copies are written only after a successful
pointer CAS and identical existing bytes are left untouched, so a pre-CAS
failure cannot mutate them.

The successful fixture covers the actual rename/CAS/independent verification path,
including the final source-revalidation callback. Shared immutable-snapshot tests
continue to cover Windows old connections and concurrent readers. These tests do
not constitute a live E4 scenario.

## Live boundary after Work Package B

The 2026-07-31 live diff remains 14 additions and 10 changes with non-selective
semantic input drift. The updater therefore still stops at
`NON_SELECTIVE_CHANGE_FULL_REBUILD_REQUIRED` before staging. The current build,
pointer, manifest, three immutable Snapshots, and disk contents are unchanged. No
narrow-gate report, publication receipt, Gold, reviewer/operator signature,
authorization, burn-in, rollback drill, or E4 attestation was fabricated. The
required state remains `mode=shadow`, `defaultQuerySource=legacy`, and
`cutoverEligible=false`.
