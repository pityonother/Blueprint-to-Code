# Stage 14 Production Narrow-Gate Report Contract

## Status and boundary

This slice defines a strict, content-addressed **engineering diagnostic**
contract for the fixed Stage 14 production narrow gates. It does not run those
gates, publish an incremental snapshot, create operator/reviewer evidence, or
close E4 scenario 2.

The unresolved external blockers therefore remain explicit:

```text
BLOCKED_BY_INDEPENDENT_REVIEW
BLOCKED_BY_MISSING_PRODUCTION_NARROW_GATE_RUNNER
BLOCKED_BY_MISSING_REAL_INCREMENTAL_RUNTIME_EVIDENCE
BLOCKED_BY_MISSING_ATOMIC_INCREMENTAL_PUBLICATION
```

The report is permanently constrained to:

```json
{
  "evidenceClass": "ENGINEERING_DIAGNOSTIC",
  "published": false,
  "e4Scenario2Complete": false,
  "claimsGlobal75": false,
  "productionAuthority": false,
  "cutoverEligible": false,
  "mode": "shadow",
  "defaultQuerySource": "legacy"
}
```

This contract is not a substitute for the complete 75 critical quality gates,
independent Gold review, signed burn-in evidence, or the Stage 14 reseal and
atomic publisher.

## Fixed success-report checks

`ark-kb-production-narrow-gate-report/v1` contains exactly these 11 checks in
this order. Every check is critical and every accepted success report records
`passed=true`.

| # | ID | Fixed detail code | Required diagnostic |
|---:|---|---|---|
| 1 | `incremental.selected_source_diff_exact` | `SELECTED_SOURCE_DIFF_EXACT` | Selected source set equals the bound manifest diff. |
| 2 | `incremental.changed_revisions_fresh` | `CHANGED_REVISIONS_FRESH` | Every changed revision is fresh. |
| 3 | `incremental.no_stale_candidate_legacy_promotion` | `NO_STALE_CANDIDATE_LEGACY_PROMOTION` | No stale, candidate, or legacy value was promoted. |
| 4 | `incremental.no_orphan_rows` | `NO_ORPHAN_ROWS` | Fact, Evidence, edge, role, and domain rows have no orphans. |
| 5 | `incremental.effective_dependencies_exact` | `EFFECTIVE_DEPENDENCIES_EXACT` | Effective-fact dependencies are exact and consistent. |
| 6 | `incremental.registrations_resolvable` | `REGISTRATIONS_RESOLVABLE` | Registration owner, target, and Evidence resolve. |
| 7 | `incremental.projections_core_artifact_match` | `PROJECTIONS_CORE_ARTIFACT_MATCH` | Projection/Core and artifact digests match. |
| 8 | `incremental.search_affected_entities_exact` | `SEARCH_AFFECTED_ENTITIES_EXACT` | Search identity/index state covers exactly the affected entities. |
| 9 | `incremental.cache_old_state_absent` | `CACHE_OLD_STATE_ABSENT` | Rows from the old build, revision, or token cannot survive in cache. |
| 10 | `incremental.sqlite_sealed_integrity` | `SQLITE_SEALED_INTEGRITY` | SQLite integrity, foreign keys, journal mode, and sidecars satisfy the sealed-state policy. |
| 11 | `incremental.current_base_unchanged` | `CURRENT_BASE_UNCHANGED` | `current` still identifies the expected base build before publication. |

The builder accepts only 11 typed `NarrowGateObservation` values in that exact
order. An observation contains only:

```text
gate_id: exact fixed ID
observation_count: raw non-negative integer (bool and string are rejected)
evidence_sha256: lowercase SHA-256 of the gate runner's canonical evidence
```

There is intentionally no caller-controlled `passed` or `critical` input. A
missing/substituted/duplicate/extra ID, arbitrary dictionary with a
self-declared pass, unknown field, value coercion, or an identically reused
evidence digest fails closed. All 11 gate observations must bind distinct
evidence digests.

Distinct digests do **not** prove distinct or real evidence: one fixture could
be salted per gate to create 11 different hashes. This diagnostic-only
envelope cannot detect that attack and makes no such claim. The missing
production runner must recompute each gate-specific raw evidence artifact and
bind its semantic inputs before any later production publication decision.

This is a success-report envelope, not the gate runner. A future runner must
compute each gate from real frozen production inputs and must not call the
builder if any check fails. The existence of a structurally valid report does
not prove that those inputs were real or authorized; the report remains an
`ENGINEERING_DIAGNOSTIC` until a later orchestrator binds it into a sealed
publication contract.

## UpdateBaseline binding

Every report binds one exact update through `updateBaseline`:

```text
baseBuildId
basePointerSha256
baseManifestSha256
baseSourceManifestFingerprint
candidateSourceManifestFingerprint
sourceDiffSha256
deltaReceiptSha256
```

The six digest/fingerprint fields are lowercase 64-character SHA-256 values.
`baseBuildId` uses the same safe build-ID grammar as the existing snapshot and
burn-in contracts. Validation requires an independent `UpdateBaseline` value
and compares every field. Rehashing a report cannot make a different base,
manifest, source diff, or delta receipt valid.

Each check contains exact nested fields:

```json
{
  "id": "incremental.selected_source_diff_exact",
  "critical": true,
  "passed": true,
  "details": {
    "detailCode": "SELECTED_SOURCE_DIFF_EXACT",
    "observationCount": 1
  },
  "digests": {
    "evidenceSha256": "<lowercase SHA-256>"
  }
}
```

The JSON Schema and Python validator reject unknown top-level or nested fields.
They also reject string/integer boolean coercion, boolean/integer count
coercion, a forged summary, reordered checks, and any ready/vNext claim.

## Proof and out-of-band authority

The report has two distinct hashes:

1. `proof` is
   `narrow-gate-proof://<sha256(canonical report body without proof)>`.
2. `expected_report_sha256` is the SHA-256 of the complete canonical report,
   including `proof`.

Canonical JSON is UTF-8, sorted by key, compact, non-floating, and
non-NaN/Infinity. Authoritative artifact consumers must use
`parse_and_validate_narrow_gate_diagnostic_report_bytes`; it rejects oversized
input, invalid UTF-8, duplicate keys, floating-number syntax, non-finite
constants, and non-canonical bytes before validating the detached plain-JSON
snapshot. It also requires the second hash out of band and an independent
`UpdateBaseline`.

The JSON Schema is intentionally structural-only. JSON Schema treats an
integral JSON number such as `1.0` as an integer and cannot detect duplicate
object keys after parsing. Passing schema validation alone is therefore never
an authoritative acceptance path.

The internal `proof` detects accidental body drift, but it is not the trust
root. An attacker can edit a report and recompute its internal proof. The
authoritative report SHA must come from an independently trusted orchestrator
or sealed manifest; deriving `expected_report_sha256` from the same untrusted
report at validation time defeats the boundary and is prohibited.

The diagnostic builder helper can calculate the artifact SHA only for a caller
that has already obtained and frozen the canonical report through the
authorized workflow. It does not turn that digest into production authority, a
signature, reviewer approval, or runtime evidence.

## Deliberately not wired

This contract adds no import to `kb_vnext.__init__`, no updater/default hook,
no current-pointer mutation, and no production command. In particular:

- it cannot make a fixture count as production Evidence or Gold;
- it cannot replace the 75-gate global invariant;
- it cannot set `published`, `e4Scenario2Complete`, `claimsGlobal75`, or
  `productionAuthority` or `cutoverEligible` to true;
- it cannot switch `mode` or `defaultQuerySource` to vNext;
- it does not create or simulate a human reviewer or burn-in operator.

Production wiring is allowed only after the real narrow-gate runner freezes
gate-specific evidence bytes, the incremental orchestrator supplies the exact
trusted `UpdateBaseline`, and the later reseal/publisher contract stores the
full report SHA out of band.

## Verification

The focused engineering checks are:

```powershell
python -m pytest -q tests/test_kb_narrow_gates.py
ruff check scripts/blueprint_translator/kb_vnext/narrow_gates.py tests/test_kb_narrow_gates.py
python -c "import json; from jsonschema import Draft202012Validator; p='schemas/kb_production_narrow_gate_report_v1.schema.json'; s=json.load(open(p, encoding='utf-8')); Draft202012Validator.check_schema(s)"
git diff --check
```

These commands validate code and contract shape only. Their results are not
production runtime evidence, independent review, Gold, E4 completion, burn-in,
or cutover authorization.
