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
- deterministic, observable-cohort role selection from read-only Discovery;
- exact role payload schema with current-role and confidence leakage checks;
- pack, candidate, and receipt SHA validation;
- duplicate, self-review, stale-evidence, leakage, and adjudicator checks;
- export, validate, and import command-line entry points;
- benchmark rejection of label-only `EMPIRICAL` cases;
- ignored local review workspace.

The current primary registration pack combines 27 source-declared typed anchors
with 112 fresh direct raw class-default relations from 234 Blueprint Evidence
Stores. One raw relation overlaps a typed anchor, leaving 138 unique
owner/target/property candidates:

- pack: `registration-b20a2660388c32d5`;
- pack SHA-256:
  `73540bc8636ff4e9a97354572cd1caae390e80c3c2d53756f71d5fbfbccb202f`;
- candidates: 138;
- source cohorts in pack: 27 typed anchors and 111 raw relations;
- source status: `SOURCE_TYPED_CANDIDATE_SHORTFALL:27/120`;
- validation: `VALID_REVIEW_PACK`;
- import status: `BLOCKED_BY_INDEPENDENT_REVIEW`;
- production gold written: false.

Raw candidates have `declaredRegistrationType=null`; they do not borrow current
Core/classifier labels and are not counted as gold. The role candidate exporter
produced the requested primary review pool:

- pack: `role-c43e2571a8d0d505`;
- pack SHA-256:
  `d59708d6a06f31beadee98f53ed4af111e52ab4b1d0056e9ee4afeb17f7f2022`;
- eligible independent canonical entities: 576,207;
- selected blind candidates: 360 in 360 observable cohorts;
- observed breadth: 9 asset types, 92 domains, 196 ancestry cohorts;
- validation: `VALID_REVIEW_PACK`;
- import status: `BLOCKED_BY_INDEPENDENT_REVIEW`;
- production gold written: false.

Both production gates remain red.

## Signed v2 validation status

The v2 validator and artifact schema are available, but no real
human-managed registry or signed reviewer artifacts are present. Automated
contract tests generate ephemeral `TEST_ONLY` keys in process; those results
are explicitly ineligible for production Gold and are not checked in.

Existing review packs with `authorKeyFingerprint=automation:<id>` are
diagnostic-only. Production v2 requires a newly exported pack bound to an
externally trusted author public-key fingerprint and the same 64-character
lowercase SHA-256 supplied out of band during validation. No such production
author identity or re-exported production pack is present in this repository.

Current signed-v2 status remains:

```text
BLOCKED_BY_INDEPENDENT_REVIEW
productionGoldEligible=false
productionGoldWritten=false
```

Gold freeze and apply are outside this validation slice.

## Cutover

This work does not change cutover state. vNext remains:

```json
{
  "mode": "shadow",
  "defaultQuerySource": "legacy"
}
```

Only real, independently reviewed receipts may remove the review blocker.
