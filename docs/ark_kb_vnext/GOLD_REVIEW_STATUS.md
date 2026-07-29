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
- pack, candidate, and receipt SHA validation;
- duplicate, self-review, stale-evidence, leakage, and adjudicator checks;
- export, validate, and import command-line entry points;
- benchmark rejection of label-only `EMPIRICAL` cases;
- ignored local review workspace.

Registration and role candidate exporters are not part of this first vertical
slice. Their production gates remain red.

## Cutover

This work does not change cutover state. vNext remains:

```json
{
  "mode": "shadow",
  "defaultQuerySource": "legacy"
}
```

Only real, independently reviewed receipts may remove the review blocker.
