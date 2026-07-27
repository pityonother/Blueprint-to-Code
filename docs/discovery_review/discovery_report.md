# ARK Knowledge Discovery bounded review

This report is generated from the final Discovery SQLite by the queries documented in `README.md`.

## Source

- Discovery schema: `blueprint-to-code-kb-discovery/v1`
- Discovery generated at: `2026-07-26T22:13:14+00:00`
- Discovery SQLite SHA-256: `9f106a091815dd88aa729d28140db728e0f1b37dbeebf2fd5f2182492ef4ea50`
- Source commit: `7411c56310c4096afd35e536fd18cd4de32f432e`

## Database scale

- `asset_references`: 3,480,942
- `assets`: 577,579
- `blueprint_functions`: 2,062
- `blueprint_native_edges`: 132
- `class_edges`: 38,394
- `components`: 147
- `coverage`: 1,155,160
- `default_property_surface`: 10,588
- `existing_knowledge_tables`: 74
- `graphs`: 2,252
- `interfaces`: 2,494
- `metadata`: 9
- `native_field_accesses`: 0
- `native_gap_summary`: 3
- `native_symbols`: 204
- `query_corpus`: 38
- `sample_membership`: 109
- `scan_failures`: 26
- `source_inventory`: 5
- `system_registrations`: 27

## Review exports

- `class_identity_coverage.csv`: 300 rows
- `current_provisional_tiers.csv`: 1,051 rows
- `data_asset_classification_candidates.csv`: 94 rows
- `existing_kb_coverage.csv`: 74 rows
- `query_corpus.jsonl`: 38 rows
- `representative_sample_manifest.json`: 109 rows
- `stale_and_high_gap_assets.csv`: 300 rows
- `system_registration_summary.csv`: 4 rows
- `top_component_reuse_assets.csv`: 0 rows
- `top_cross_domain_assets.csv`: 300 rows
- `top_descendant_assets.csv`: 300 rows
- `top_native_boundary_candidates.csv`: 50 rows
- `top_referenced_assets.csv`: 300 rows
- `top_registration_targets.csv`: 26 rows

## Evidence boundary

- `provisional_tier` is preserved only for review of the old classifier; it is not a production role.
- Data Asset rows are candidates until class ancestry closes.
- UNKNOWN, AMBIGUOUS, NOT_RECOVERED, NOT_MEASURED, SOURCE_NOT_AVAILABLE, and STALE remain explicit.
- No ARK package, binary, PDB, Ghidra project, decompiled body, or local absolute path is included.
