# Discovery review subset

This directory is a bounded, Git-readable SQL export of the final Discovery database. Rebuild it; do not edit generated rows by hand.

## Rebuild

```powershell
runtime\python\python.exe scripts\export_kb_discovery_review_subset.py `
  --database knowledge_base\discovery_bundle\kb_discovery.sqlite `
  --output docs\discovery_review
```

## Source identity

- Discovery SQLite SHA-256: `9f106a091815dd88aa729d28140db728e0f1b37dbeebf2fd5f2182492ef4ea50`
- Discovery SQLite size: `3816177664` bytes
- Discovery schema: `blueprint-to-code-kb-discovery/v1`
- Discovery generated at: `2026-07-26T22:13:14+00:00`
- Source commit: `7411c56310c4096afd35e536fd18cd4de32f432e`
- Review subset size: `1003814` bytes

## Bounds

- Top exports are capped at 300 rows.
- `current_provisional_tiers.csv` is capped independently per tier.
- The old tier is published only to expose classifier defects.
- Object paths are ARK logical identities, not local filesystem paths.

## Export row counts

| File | Rows |
|---|---:|
| `class_identity_coverage.csv` | 300 |
| `current_provisional_tiers.csv` | 1051 |
| `data_asset_classification_candidates.csv` | 94 |
| `existing_kb_coverage.csv` | 74 |
| `query_corpus.jsonl` | 38 |
| `representative_sample_manifest.json` | 109 |
| `stale_and_high_gap_assets.csv` | 300 |
| `system_registration_summary.csv` | 4 |
| `top_component_reuse_assets.csv` | 0 |
| `top_cross_domain_assets.csv` | 300 |
| `top_descendant_assets.csv` | 300 |
| `top_native_boundary_candidates.csv` | 50 |
| `top_referenced_assets.csv` | 300 |
| `top_registration_targets.csv` | 26 |

## SQL

### `query_corpus.jsonl`

Parameters: `[]`

```sql
SELECT *
            FROM query_corpus
            ORDER BY query_id
```

### `representative_sample_manifest.json`

Parameters: `[]`

```sql
SELECT
                s.object_path,
                s.selection_reason,
                s.source_rank,
                a.asset_name,
                a.asset_class_path,
                a.parent_class_path,
                a.native_parent_class_path,
                a.provisional_tier,
                a.evidence_freshness,
                a.identity_status
            FROM sample_membership AS s
            LEFT JOIN assets AS a ON a.object_path=s.object_path
            ORDER BY s.selection_reason, s.source_rank, s.object_path
```

### `top_descendant_assets.csv`

Parameters: `[300]`

```sql
SELECT
        object_path, asset_name, asset_class_path, generated_class_path,
        parent_class_path, native_parent_class_path, blueprint_kind,
        identity_status, identity_confidence, capture_exists,
        evidence_freshness, descendant_count, referencer_count,
        component_reuse_count, cross_domain_reference_count,
        registry_usage_count, native_call_count, unresolved_native_call_count,
        query_hit_count, existing_report_count, provisional_tier,
        provisional_reasons_json

            FROM assets
            WHERE descendant_count > 0
            ORDER BY descendant_count DESC, object_path
            LIMIT ?
```

### `top_referenced_assets.csv`

Parameters: `[300]`

```sql
SELECT
        object_path, asset_name, asset_class_path, generated_class_path,
        parent_class_path, native_parent_class_path, blueprint_kind,
        identity_status, identity_confidence, capture_exists,
        evidence_freshness, descendant_count, referencer_count,
        component_reuse_count, cross_domain_reference_count,
        registry_usage_count, native_call_count, unresolved_native_call_count,
        query_hit_count, existing_report_count, provisional_tier,
        provisional_reasons_json

            FROM assets
            WHERE referencer_count > 0
            ORDER BY referencer_count DESC, object_path
            LIMIT ?
```

### `top_component_reuse_assets.csv`

Parameters: `[300]`

```sql
SELECT
        object_path, asset_name, asset_class_path, generated_class_path,
        parent_class_path, native_parent_class_path, blueprint_kind,
        identity_status, identity_confidence, capture_exists,
        evidence_freshness, descendant_count, referencer_count,
        component_reuse_count, cross_domain_reference_count,
        registry_usage_count, native_call_count, unresolved_native_call_count,
        query_hit_count, existing_report_count, provisional_tier,
        provisional_reasons_json

            FROM assets
            WHERE component_reuse_count > 0
            ORDER BY component_reuse_count DESC, object_path
            LIMIT ?
```

### `top_cross_domain_assets.csv`

Parameters: `[300]`

```sql
SELECT
        object_path, asset_name, asset_class_path, generated_class_path,
        parent_class_path, native_parent_class_path, blueprint_kind,
        identity_status, identity_confidence, capture_exists,
        evidence_freshness, descendant_count, referencer_count,
        component_reuse_count, cross_domain_reference_count,
        registry_usage_count, native_call_count, unresolved_native_call_count,
        query_hit_count, existing_report_count, provisional_tier,
        provisional_reasons_json

            FROM assets
            WHERE cross_domain_reference_count > 0
            ORDER BY cross_domain_reference_count DESC, object_path
            LIMIT ?
```

### `top_registration_targets.csv`

Parameters: `[300]`

```sql
SELECT
                target_object_path,
                COUNT(*) AS registration_count,
                COUNT(DISTINCT owner_object_path) AS owner_count,
                COUNT(DISTINCT registration_type) AS registration_type_count,
                GROUP_CONCAT(DISTINCT registration_type) AS registration_types,
                MIN(confidence) AS minimum_confidence
            FROM system_registrations
            GROUP BY target_object_path
            ORDER BY registration_count DESC, target_object_path
            LIMIT ?
```

### `top_native_boundary_candidates.csv`

Parameters: `[300]`

```sql
SELECT
                blueprint_asset_path,
                blueprint_function_name,
                status,
                resolution_method,
                confidence,
                COUNT(*) AS candidate_count,
                COUNT(DISTINCT native_evidence_id) AS native_target_count,
                GROUP_CONCAT(DISTINCT native_evidence_id) AS native_evidence_ids
            FROM blueprint_native_edges
            GROUP BY
                blueprint_asset_path, blueprint_function_name, status,
                resolution_method, confidence
            ORDER BY
                CASE status
                  WHEN 'CONFIRMED' THEN 0
                  WHEN 'VERIFIED' THEN 0
                  WHEN 'RESOLVED' THEN 0
                  WHEN 'AMBIGUOUS' THEN 1
                  WHEN 'NAME_ONLY_CANDIDATE' THEN 2
                  ELSE 3
                END,
                candidate_count DESC,
                blueprint_asset_path,
                blueprint_function_name
            LIMIT ?
```

### `current_provisional_tiers.csv`

Parameters: `[300]`

```sql
WITH ranked AS (
                SELECT
                    object_path,
                    asset_name,
                    asset_class_path,
                    parent_class_path,
                    native_parent_class_path,
                    provisional_tier,
                    provisional_reasons_json,
                    descendant_count,
                    referencer_count,
                    component_reuse_count,
                    cross_domain_reference_count,
                    registry_usage_count,
                    native_call_count,
                    query_hit_count,
                    existing_report_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY provisional_tier
                        ORDER BY
                            registry_usage_count DESC,
                            descendant_count DESC,
                            component_reuse_count DESC,
                            cross_domain_reference_count DESC,
                            referencer_count DESC,
                            native_call_count DESC,
                            object_path
                    ) AS tier_sample_rank
                FROM assets
            )
            SELECT
                *,
                'top structural and demand signals within provisional tier'
                    AS sample_method
            FROM ranked
            WHERE tier_sample_rank <= ?
            ORDER BY provisional_tier, tier_sample_rank
```

### `class_identity_coverage.csv`

Parameters: `[300]`

```sql
SELECT
                asset_class_path,
                COUNT(*) AS asset_count,
                SUM(CASE WHEN is_blueprint=1 THEN 1 ELSE 0 END)
                    AS blueprint_count,
                SUM(CASE
                    WHEN generated_class_path NOT IN ('', 'UNKNOWN')
                    THEN 1 ELSE 0 END
                ) AS generated_class_known,
                SUM(CASE
                    WHEN parent_class_path NOT IN ('', 'UNKNOWN')
                    THEN 1 ELSE 0 END
                ) AS parent_class_known,
                SUM(CASE
                    WHEN native_parent_class_path NOT IN ('', 'UNKNOWN')
                    THEN 1 ELSE 0 END
                ) AS native_parent_class_known,
                SUM(CASE WHEN identity_status='CONFIRMED' THEN 1 ELSE 0 END)
                    AS confirmed_identity_count,
                SUM(CASE
                    WHEN identity_status IN (
                        'UNKNOWN', 'AMBIGUOUS', 'NOT_RECOVERED',
                        'NOT_MEASURED', 'SOURCE_NOT_AVAILABLE', 'STALE'
                    )
                    THEN 1 ELSE 0 END
                ) AS open_identity_count
            FROM assets
            GROUP BY asset_class_path
            ORDER BY asset_count DESC, asset_class_path
            LIMIT ?
```

### `system_registration_summary.csv`

Parameters: `[300]`

```sql
SELECT
                registration_type,
                COUNT(*) AS registration_count,
                COUNT(DISTINCT owner_object_path) AS owner_count,
                COUNT(DISTINCT target_object_path) AS target_count,
                SUM(CASE
                    WHEN confidence IN ('CONFIRMED', 'HIGH')
                    THEN 1 ELSE 0 END
                ) AS high_confidence_count,
                SUM(CASE
                    WHEN confidence NOT IN ('CONFIRMED', 'HIGH')
                    THEN 1 ELSE 0 END
                ) AS non_high_confidence_count
            FROM system_registrations
            GROUP BY registration_type
            ORDER BY registration_count DESC, registration_type
            LIMIT ?
```

### `existing_kb_coverage.csv`

Parameters: `[300]`

```sql
SELECT *
            FROM existing_knowledge_tables
            ORDER BY database_name, table_name
            LIMIT ?
```

### `stale_and_high_gap_assets.csv`

Parameters: `[300]`

```sql
SELECT
                c.object_path,
                a.asset_name,
                a.asset_class_path,
                a.evidence_freshness,
                c.stage,
                c.status,
                c.ambiguous_count,
                c.not_recovered_count,
                c.source_not_available_count,
                c.stale_count,
                c.failure_reason,
                (
                    c.ambiguous_count
                    + c.not_recovered_count
                    + c.source_not_available_count
                    + c.stale_count
                ) AS gap_count
            FROM coverage AS c
            LEFT JOIN assets AS a ON a.object_path=c.object_path
            WHERE c.status IN (
                    'UNKNOWN', 'AMBIGUOUS', 'NOT_RECOVERED',
                    'NOT_MEASURED', 'SOURCE_NOT_AVAILABLE', 'STALE'
                  )
               OR c.ambiguous_count > 0
               OR c.not_recovered_count > 0
               OR c.source_not_available_count > 0
               OR c.stale_count > 0
            ORDER BY
                gap_count DESC,
                CASE c.status
                  WHEN 'STALE' THEN 0
                  WHEN 'NOT_RECOVERED' THEN 1
                  WHEN 'SOURCE_NOT_AVAILABLE' THEN 2
                  WHEN 'AMBIGUOUS' THEN 3
                  ELSE 4
                END,
                c.object_path,
                c.stage
            LIMIT ?
```

### `data_asset_classification_candidates.csv`

Parameters: `[300]`

```sql
SELECT
                object_path,
                asset_name,
                asset_class_path,
                generated_class_path,
                parent_class_path,
                native_parent_class_path,
                is_data_asset,
                identity_status,
                identity_confidence,
                CASE
                  WHEN is_data_asset=1 THEN 'registry_exact_flag'
                  WHEN lower(asset_class_path) LIKE '%dataasset%'
                    THEN 'asset_class_name_candidate'
                  WHEN lower(parent_class_path) LIKE '%dataasset%'
                    THEN 'parent_class_name_candidate'
                  WHEN lower(native_parent_class_path) LIKE '%dataasset%'
                    THEN 'native_parent_name_candidate'
                  ELSE 'not_classified'
                END AS current_classification_basis
            FROM assets
            WHERE is_data_asset=1
               OR lower(asset_class_path) LIKE '%dataasset%'
               OR lower(parent_class_path) LIKE '%dataasset%'
               OR lower(native_parent_class_path) LIKE '%dataasset%'
            ORDER BY
                CASE WHEN is_data_asset=1 THEN 0 ELSE 1 END,
                object_path
            LIMIT ?
```
