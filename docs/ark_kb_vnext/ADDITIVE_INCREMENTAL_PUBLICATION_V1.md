# Additive Incremental Publication v1

Status: Work Package A implemented; Work Package B is intentionally not claimed by
this revision.

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

The 2026-07-31 read-only live audit found 14 added and 9 changed sources, including
non-selective semantic inputs. The production capability check therefore returned
`NON_SELECTIVE_CHANGE_FULL_REBUILD_REQUIRED` before staging. No live Snapshot,
pointer swap, narrow-gate result, Gold, authorization, burn-in, or cutover claim was
created.

## Work Package B boundary

Candidate reseal, the fixed production narrow-gate report, immutable Snapshot
promotion, pointer CAS, and independent publication verification belong to Work
Package B. Until those contracts are implemented and their real prerequisites are
available, the required state remains `mode=shadow`,
`defaultQuerySource=legacy`, and `cutoverEligible=false`.
