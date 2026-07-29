# ARK KB vNext Gold Review Status

Status: `BLOCKED_BY_INDEPENDENT_REVIEW`

The review factory infrastructure is available for the fixed query corpus, but
no real reviewer receipts or human-managed trusted reviewer registry are
present. No production gold was generated or promoted.

## Current production counts

- Query fixed corpus: 130 cases.
- Query human/empirical gold: 5.
- Query fixture-only cases: 125; they do not count as human gold.
- Registration Owner to Target gold: 0 of the required 100.
- Role gold: 0 of the required 300.

The checked-in query, registration, and role production-gold counts remain
`5 / 0 / 0`.

## Delivered in this slice

- common review schema;
- deterministic and blind query pack export;
- deterministic and blind registration pack export from read-only Discovery;
- exact registration payload schema and independent-source manifest checks;
- pack, candidate, and receipt SHA validation;
- duplicate, self-review, stale-evidence, leakage, and adjudicator checks;
- export, validate, and import command-line entry points;
- benchmark rejection of label-only `EMPIRICAL` cases;
- ignored local review workspace.

The actual independent Discovery source exposed 27 unique typed registration
rows, below the requested 120-candidate preparation target:

- pack: `registration-87110e3aae010067`;
- pack SHA-256:
  `8f070fd9fd34084842fb92f9475b4a63b8d481daa8b054bb84f75ffa9a7cbc2e`;
- candidates: 27;
- source shortfall: `SOURCE_CANDIDATE_SHORTFALL:27/120`;
- validation: `VALID_REVIEW_PACK`;
- import status: `BLOCKED_BY_INDEPENDENT_REVIEW`;
- production gold written: false.

The shortfall is reported rather than filled with generic references,
classifier fixtures, repeated rows, or inferred labels. The role candidate
exporter is not part of this registration slice. Both production gates remain
red.

## Cutover

This work does not change cutover state. vNext remains:

```json
{
  "mode": "shadow",
  "defaultQuerySource": "legacy"
}
```

Only real, independently reviewed receipts may remove the review blocker.
