# Stage 14 shared current-pointer CAS

This slice adds one reusable, unsigned filesystem primitive for every ARK KB
vNext writer of the shared `current.json` pointer.

## Contract

`compare_and_swap_current_pointer()`:

- uses a stable `.current.json.lock` with an operating-system byte-range lock,
  so independent Windows or POSIX processes serialize pointer writes;
- requires every caller, including the first publication, to supply an
  explicit `CurrentPointerBaseline`; first publication uses `(None, None)`;
- compares both the expected `buildId` and the SHA-256 of the exact raw
  `current.json` bytes while the lock is held;
- treats formatting-only byte changes as a conflict;
- validates that the destination is a direct `snapshots/<buildId>` child with
  a matching immutable manifest, rejects links/junctions/reparse points at the
  snapshots root and child boundaries, and binds the resolved snapshots parent
  to the resolved vNext root;
- uses separate input bounds: 16 KiB for the small pointer and 4 MiB for the
  snapshot manifest, covering the current approximately 150 KiB production
  manifest without making pointer parsing unbounded;
- rejects non-string, empty, coerced, or whitespace-normalized build IDs;
- writes a same-directory temporary file, flushes it, atomically replaces the
  pointer, then reopens and verifies the exact resulting bytes;
- returns an unsigned local-fact receipt with recomputable before/after pointer
  hashes and build IDs.

There is no omitted-baseline mode. Callers capture `CurrentPointerBaseline`
before validation and supply it later; any intervening raw-byte change then
fails closed.

The lock file is persistent and idempotent. It is not deleted on release, so a
different process cannot accidentally lock a replacement inode. Acquisition is
non-blocking with short bounded retries rather than a long sleep.

## Integrated writers

Full snapshot promotion and rollback both call the shared primitive. Promotion
still publishes an immutable directory before changing the reader-visible
pointer; a stale expected pointer therefore leaves an unreferenced immutable
snapshot instead of overwriting the newer pointer.

Production full builds capture their exact pointer baseline before staging and
bind the new manifest to it through `previousSnapshot` build ID and manifest
SHA-256. The same baseline is supplied at promotion; a competing publication
therefore cannot silently become last-writer-wins.

Rollback captures the raw baseline before target validation and supplies it to
the locked CAS. A pointer-changing rollback requires the current manifest to
declare `previousSnapshot`; the target must be that adjacent predecessor and
its manifest SHA-256 must match. The target manifest digest is rechecked under
the writer lock immediately before replacement and again after replacement.
Older snapshot manifests without explicit lineage fail closed for
pointer-changing rollback rather than inventing history. A same-build no-op
remains compatible.

Dry-run rollback uses the shared no-write validation primitive. Under one lock
it rechecks the captured current pointer, current manifest SHA-256, target
manifest SHA-256, and target identity before returning `VALIDATED`.

## Failure states

- A stale expected baseline or competing writer raises
  `PointerCASConflictError`; the observed pointer is not overwritten.
- Invalid or escaping destinations raise `PointerCASDestinationError` before
  replacement.
- A failure before `os.replace()` raises `PointerCASWriteError` with
  `status=NOT_REPLACED` and `pointerUpdated=false`.
- Transient Windows sharing violations from concurrent readers are retried for
  a short bounded interval while the writer lock remains held. If the source
  rename still exists at the deadline, the receipt remains `NOT_REPLACED`.
- Any exception after replacement is attempted raises
  `PointerCASUncertainStateError`. Its receipt records the intended and
  best-effort observed hashes/build IDs and sets `pointerUpdated=null`.
  No automatic rewrite or invented rollback is attempted.
- The rollback CLI exits `3` and reports `status=UNCERTAIN` for that last case;
  it does not print the ordinary blocked receipt that claims no update.

These receipts are deliberately marked `UNSIGNED_LOCAL_WRITE_FACT`. They are
not a human/operator attestation, production burn-in receipt, or cutover
authorization. This slice does not change any quality gate, `mode`,
`defaultQuerySource`, or `cutoverEligible` value.
