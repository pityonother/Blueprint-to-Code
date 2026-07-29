# ARK KB vNext Gold Freeze Proposal Contract

Status: `BLOCKED_BY_INDEPENDENT_REVIEW`

Stage 13C adds a proposal-only freeze boundary. It does not write production
Gold, create reviewer or operator identities, create verdicts or receipts,
approve a proposal, commit, publish, or change cutover state.

## Preconditions

`scripts/freeze_ark_kb_gold_reviews.py` reuses the signed-v2 validator. A
proposal can become `PROPOSAL_READY` only when all of the following are true:

- the pack is a complete query pack using `MANUAL_FIXED_ALL_CASES`;
- every pack case has the required valid signed-v2 reviews and any required
  independent adjudication;
- the validator returns `productionGoldEligible=true` in `PRODUCTION`;
- the pack author fingerprint, trusted reviewer registry digest, signatures,
  receipt payloads, and detached artifact bytes all validate;
- the source manifest and target are the exact raw bytes of
  `tests/fixtures/kb_query_gold_set.v1.json`;
- the observed source SHA-256, observed target SHA-256, both operator-supplied
  SHA-256 values, and the pack `sourceManifestSha256` agree.

The CLI allowlists that one tracked query Gold path for this first slice. Both
source and target arguments must resolve to the same file. It caches the raw
bytes by resolved path, so that file is read once during a run. A target edit
after pack export is rejected even if an operator supplies the edited file's
new hash.

Registration and role freeze proposals are unsupported in this slice and fail
closed. A subset validation, v1 receipt, `TEST_ONLY` validation, or old
`automation:<id>` pack author cannot make a production proposal ready.

## Signed answer boundary

For a query case, a resolved detached artifact must use this exact answer
wrapper:

```json
{
  "answer": {
    "queryExpected": {
      "route": "DB_SEMANTIC_COMPLETE",
      "identityUri": "/Game/Example/Asset.Asset",
      "facts": [
        {
          "factType": "ITEM_PROPERTY",
          "factName": "BaseItemWeight",
          "valueKind": "NUMBER",
          "value": 1,
          "status": "CONFIRMED",
          "evidenceUri": "bp://example/default/BaseItemWeight"
        }
      ],
      "relationships": [],
      "gapCodes": [],
      "mustContainEvidence": true,
      "semanticExpectation": "EXACT"
    }
  }
}
```

An arbitrary signed `answer` object is not treated as a replacement for the
Gold `expected` object. The proposal is derived only from the immutable
artifact bytes already cached by signed-v2 validation; artifact paths are not
reopened during proposal construction.

The tool constructs the complete proposed Gold payload in memory and passes it
to `validate_benchmark_gold_payload`. This public canonical validator is also
used by `load_benchmark_gold_set`; it rejects invalid corpus shape and semantic
claims such as an empty `EXACT` answer, unknown gap code, incomplete route, or
malformed fact. It also rejects unknown contract fields and implicit
`str`/`bool`/`int` coercion, including string booleans and non-string evidence
URIs. Canonical validation grants no human or signed-v2 provenance.

## Proposal output

A ready proposal follows
`schemas/kb_gold_freeze_proposal_v1.schema.json` and contains:

- deterministic old/new case hashes and JSON Patch operations;
- the complete verified receipt-set SHA-256;
- pack, source, target, and detached-artifact provenance;
- only changed `expected` objects in JSON Patch;
- every original `reviewStatus` preserved byte-for-value at the case level;
- `signedV2ReviewedCasesDelta=0` and `fixtureExactCasesDelta=0`;
- the separately named signed-v2 provenance case count;
- `qualityGateEvaluation=PENDING_FULL_SNAPSHOT_REBUILD`;
- `cutoverEligibleClaimed=false`;
- `applyBlockers=["SIGNED_V2_GOLD_PROVENANCE_CONSUMER_REQUIRED"]`;
- `applyAllowed=false`;
- `productionGoldWritten=false`.

Signed-v2 review provenance is not translated into legacy
`HUMAN_REVIEWED` or `EMPIRICAL` labels. Unchanged reviewed answers produce no
no-op patch; their signed artifact bindings still contribute to the provenance
case count.

Tracked source or target files are never accepted as output. Optional output is
restricted to the ignored
`review_work/ark_kb_gold/freeze_proposals/` tree, and an existing output is
never overwritten.

Example after real external evidence exists:

```powershell
python scripts\freeze_ark_kb_gold_reviews.py `
  --pack review_work\ark_kb_gold\query\<packId>\review_pack.json `
  --receipts review_work\ark_kb_gold\query\<packId>\signed-v2-receipts `
  --registry-v2 review_work\ark_kb_gold\registry\human-managed-v2.json `
  --expected-registry-sha256 <out-of-band-registry-sha256> `
  --expected-pack-author-key-fingerprint <out-of-band-author-key-sha256> `
  --artifact-root review_work\ark_kb_gold\query\<packId>\artifacts `
  --source-manifest tests\fixtures\kb_query_gold_set.v1.json `
  --expected-source-manifest-sha256 <out-of-band-raw-file-sha256> `
  --gold-target tests\fixtures\kb_query_gold_set.v1.json `
  --expected-gold-target-sha256 <out-of-band-raw-file-sha256> `
  --output review_work\ark_kb_gold\freeze_proposals\<proposalId>.json
```

All file arguments must remain inside the repository boundary. External
operators may stage their signed evidence under ignored `review_work/`; the
tool does not create, fetch, edit, or endorse that evidence. Registry, author,
source, and target digests still arrive through separate out-of-band
arguments.

Without real receipts, the stable result is
`BLOCKED_BY_INDEPENDENT_REVIEW`; blocked output contains no answer, expected
value, or planner prediction.

## Apply remains closed

`--apply` is reserved but deliberately unavailable in this change. It always
returns nonzero with `BLOCKED_BY_SIGNED_FREEZE_APPROVAL`, writes no proposal or
Gold bytes, and creates no approval stub. A later, separately reviewed
signed-v2 Gold provenance consumer must first preserve and verify receipt
bindings without relying on legacy `reviewStatus`. A human-signed
freeze-approval contract is then also required before apply can be implemented.

No real production reviewer receipts, human-managed production registry,
pack-author identity, or signed freeze approval are present in the repository.
Therefore no production proposal is generated now. vNext remains:

```json
{
  "mode": "shadow",
  "defaultQuerySource": "legacy"
}
```
