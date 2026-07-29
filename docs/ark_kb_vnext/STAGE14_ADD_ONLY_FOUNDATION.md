# Stage 14A Add-only Blueprint Delta Foundation

## Status

This slice is a fail-closed engineering foundation. It is not an incremental
publication, is not E4 scenario 2 evidence, and does not make vNext eligible
for cutover.

The current production blockers remain:

```text
BLOCKED_BY_MISSING_AUTHORIZED_ADDITIVE_BLUEPRINT_EVIDENCE
BLOCKED_BY_MISSING_SIGNED_PRODUCTION_ARTIFACT_AUTHORIZATION
BLOCKED_BY_UNPROVEN_ADDITIVE_DERIVED_DEPENDENCY_SCOPE
BLOCKED_BY_MISSING_PRODUCTION_BACKEND_TERMINAL_RECEIPTS
```

Until real authorized Evidence, production wiring, narrow/global gates,
atomic publication, rollback, and concurrent-reader evidence are all present,
the required state remains:

```json
{
  "mode": "shadow",
  "defaultQuerySource": "legacy",
  "cutoverEligible": false
}
```

## Implemented contract

`incremental_delta.py` compares two independently readable databases:

- the immutable base Core database;
- the staged Core database after bounded Blueprint ingestion.

It recomputes a logical row digest for every durable user table and a schema
digest covering every non-SQLite-internal table, index, trigger, and view.
The delta is accepted only when the actual changed-table set is limited to:

```text
source_revisions
facts
fact_evidence
```

`source_revisions` and `fact_evidence` must both have real durable additions.
Updates, deletes, unrelated rows, broad table changes, empty fact evidence,
stale Evidence, and mismatched entity/fact scope fail with
`BLOCKED_GAP`.

The source diff must contain exactly one added `BLUEPRINT_EVIDENCE` revision;
multi-Blueprint batches fail closed even when every item has Evidence. That
single addition must have one artifact binding. The Evidence SQLite file is
read once into an immutable byte snapshot. Its byte length, SHA-256, SQLite
revision identity, source aggregate fingerprint, and `revisionLabel` are all
checked from that same snapshot. If an adjacent `manifest.json` exists, its
frozen bytes are included using the same aggregate algorithm as
`source_manifest._blueprint_revision`.

The binding contains:

- the exact source ID and source fingerprint;
- a safe relative `artifact://` URI;
- the Evidence SQLite byte length and SHA-256;
- an Evidence `asset_revisions` identity that matches the staged Core source
  revision, source-diff entity, source-diff size, and `revisionLabel`;
- the observed adjacent manifest identity, when present.

New source revisions must be `FRESH`. New facts must be current,
`CONFIRMED/HIGH`, declared defaults. Their evidence must use the exact bound
`bp://<asset>@<revision>/...` URI and `DEFAULT_VALUE_ACTUAL` role.

The foundation currently accepts only the explicit `TEST_ONLY` trust context.
`PRODUCTION` is not a caller-controlled label: until an independent signed
artifact-authorization contract is implemented, every production attempt
returns `PRODUCTION_ARTIFACT_AUTHORIZATION_REQUIRED`. This check runs both
when the delta is built and again when its receipt is constructed, so replacing
an already validated delta object's context cannot mint a production receipt.

The add-only invalidation planner derives its scope from the verified durable
delta and materialized `invalidation_dependencies`. It always binds the exact
`FACT` and `EFFECTIVE_ENTITY` targets. It also requires a complete, exact
dependency scope for:

```text
ROLE_ENTITY       = the added entity IDs
DOMAIN_ENTITY     = the added entity IDs
PROJECTION        = every declared projection ID
QUERY_SNAPSHOT    = the added source revision IDs
```

Any other real supported dependency rows are added exactly. Ambiguous reasons,
unknown kinds, missing targets, or an incomplete required family return
`BLOCKED_GAP`. In particular, a property such as `CollisionRadius` cannot be
represented as only `FACT` plus `EFFECTIVE_ENTITY`.

Receipt construction reruns that planner against the current durable
dependency graph and requires byte-for-byte equivalent task scopes and
reasons. A caller cannot substitute a hand-built `FACT`/`EFFECTIVE_ENTITY`
plan. The delta also freezes per-table digests for `source_revisions`, `facts`,
and `fact_evidence`; those truth-table digests are recomputed immediately
before receipt construction so a post-validation value or evidence mutation
fails with `DELTA_TRUTH_TABLE_DRIFT`. Logical state, protected digests,
planner output, event payload, queue rows, and terminal receipts are read under
one SQLite `BEGIN IMMEDIATE` transaction. That database-native lock serializes
writers across processes; a final full-state recheck runs before the lock is
released. Initial delta validation similarly holds one base read snapshot and
one staged `BEGIN IMMEDIATE` snapshot through row-scope verification and its
final database-digest recheck.

## Receipt boundary

The v2 foundation receipt binds:

- source-diff SHA-256;
- Evidence artifact URI, byte SHA-256, and revision identity;
- independently recomputable base/staged database digests;
- protected truth-table digests and the database digest observed at receipt
  construction;
- actual changed tables;
- exact source revision, entity, and fact IDs;
- exact invalidation task IDs and reasons;
- the durable `invalidation_events` payload SHA-256 and exact
  `invalidation_queue` SHA-256/status snapshot;
- the content-addressed `ark-kb-rebuild-receipt/v1` for every task that reached
  a terminal state, with explicit missing-receipt gaps for the rest;
- independently checked durable before/after outcome digests, touched-table
  evidence, and worker verification data;
- missing or blocked backend gap codes.

The worker owns one canonical allowed-write-table mapping for every rebuild
kind. Any backend write outside that kind's mapping rolls back the entire task
and ends as failure, even when an expected table was also changed. Successful
receipts must report non-empty `touchedTables` and `verification.writeOperations`
whose table sets are equal and wholly inside the same canonical mapping.
Durable event JSON is decoded strictly; duplicate object keys are rejected.
Every ID must also be a real JSON/SQLite integer. Booleans, floats, integer-like
strings, and SQLite `REAL` queue/dependency IDs are rejected before comparison,
and the queue digest binds the uncoerced typed durable rows.

Table names are not enough to prove selectivity. Every supported rebuild kind
also has an exact row-scope policy enforced inside the backend transaction by
temporary `BEFORE INSERT/UPDATE/DELETE` guards. A role task for entity 1, for
example, cannot update entity 2 in the same canonical table. Class closure
uses its durable affected-class set; registration uses the bound entity URI;
native rows use the verified function identity. Projection and whole-cache
operations require explicit event-bound batch modes, and that row-scope proof
is included in the content-addressed terminal receipt. The additive receipt
validator rejects a successful backend receipt whose row-scope proof is
missing, reduced, replayed, or bound to a different task.

If a backend performs a partial expected-table write but leaves the target in
`BLOCKED_GAP`, the backend transaction is rolled back first. The worker records
the gap receipt and queue status in a separate transaction; partial materialized
state is never committed as a blocked outcome.

The receipt always contains:

```json
{
  "published": false,
  "e4Scenario2Complete": false
}
```

Receipt construction reads terminal outcomes from the durable event and queue;
it does not accept a caller-supplied `supported_task_kinds` set or an arbitrary
receipt list. `validate_add_only_delta_receipt` requires an out-of-band
`expected_receipt_sha256`. Recomputing the receipt's own proof after tampering
does not satisfy that trust root. A task-kind string is not backend evidence.
If any terminal receipt is missing, receipt status is `BLOCKED_GAP`; a no-op,
same-digest, wrong-table, replayed, or self-rehashed outcome cannot turn that
status into success. Successful validation returns a recursively immutable
copy, not a shallow wrapper around caller-owned lists or dictionaries.

`FOUNDATION_VERIFIED` means only that the staged delta, dependency plan, and
terminal outcome receipts passed this pre-publication contract. Content
addressing is not a human signature or production authorization.

## Test boundary

Tests create only synthetic SQLite rows and artifacts under an explicit
`TEST_ONLY` trust context. The production path is blocked entirely until it
has a separate signed authorization contract. These rows are not production
Evidence, fixtures are not Gold, and no test receipt is burn-in evidence.

Run the focused checks with:

```powershell
python -m pytest -q tests/test_kb_incremental_delta.py
python -m pytest -q tests/test_kb_invalidation.py tests/test_kb_rebuild_worker.py
ruff check scripts/blueprint_translator/kb_vnext/incremental_delta.py scripts/blueprint_translator/kb_vnext/invalidation.py scripts/blueprint_translator/kb_vnext/rebuild_worker.py tests/test_kb_incremental_delta.py tests/test_kb_rebuild_worker.py
git diff --check
```

## Required next production step

This foundation is deliberately not wired into
`run_incremental_update`. Wiring is safe only after an authorized, genuinely
new Blueprint Evidence store is available so the production path can prove:

1. source scanning and artifact bindings describe the same frozen bytes;
2. the staged ingest produces the verified durable delta;
3. the production dependency graph proves complete role, domain, projection,
   query, and scenario-specific targets;
4. every derived task has a real terminal backend receipt;
5. narrow gates and the complete 75-gate invariant pass;
6. reseal, current-pointer CAS, rollback, and concurrent readers are verified.

Only that later end-to-end run can close E4 scenario 2.
