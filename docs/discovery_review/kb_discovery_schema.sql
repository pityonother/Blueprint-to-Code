CREATE TABLE asset_references (
    reference_id TEXT PRIMARY KEY,
    source_object_path TEXT NOT NULL,
    target_object_path TEXT NOT NULL,
    edge_kind TEXT NOT NULL,
    reference_strength TEXT NOT NULL,
    source_property TEXT NOT NULL,
    source_graph TEXT NOT NULL,
    source_function TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    confidence TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'UNKNOWN'
);

CREATE TABLE assets (
    object_path TEXT PRIMARY KEY,
    package_path TEXT NOT NULL,
    asset_name TEXT NOT NULL,
    asset_class_path TEXT NOT NULL DEFAULT 'UNKNOWN',
    blueprint_kind TEXT NOT NULL DEFAULT 'UNKNOWN',
    generated_class_path TEXT NOT NULL DEFAULT 'UNKNOWN',
    parent_class_path TEXT NOT NULL DEFAULT 'UNKNOWN',
    native_parent_class_path TEXT NOT NULL DEFAULT 'UNKNOWN',
    mount_point TEXT NOT NULL,
    top_folder TEXT NOT NULL,
    plugin_or_dlc TEXT NOT NULL,
    is_blueprint INTEGER DEFAULT NULL,
    is_data_only_blueprint INTEGER DEFAULT NULL,
    is_map INTEGER NOT NULL DEFAULT 0,
    is_data_asset INTEGER DEFAULT NULL,
    is_data_table INTEGER DEFAULT NULL,
    is_function_library INTEGER DEFAULT NULL,
    is_blueprint_interface INTEGER DEFAULT NULL,
    is_user_defined_struct INTEGER DEFAULT NULL,
    is_user_defined_enum INTEGER DEFAULT NULL,
    is_editor_only INTEGER DEFAULT NULL,
    has_uasset INTEGER NOT NULL DEFAULT 0,
    has_uexp INTEGER NOT NULL DEFAULT 0,
    has_ubulk INTEGER NOT NULL DEFAULT 0,
    file_size_total INTEGER NOT NULL DEFAULT 0,
    source_fingerprint TEXT NOT NULL,
    source_modified TEXT NOT NULL,
    capture_exists INTEGER NOT NULL DEFAULT 0,
    evidence_revision TEXT NOT NULL DEFAULT '',
    evidence_freshness TEXT NOT NULL DEFAULT 'NOT_AVAILABLE',
    parse_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    parse_confidence TEXT NOT NULL DEFAULT 'UNKNOWN',
    graph_count INTEGER NOT NULL DEFAULT 0,
    function_count INTEGER NOT NULL DEFAULT 0,
    event_count INTEGER NOT NULL DEFAULT 0,
    macro_count INTEGER NOT NULL DEFAULT 0,
    variable_count INTEGER NOT NULL DEFAULT 0,
    component_count INTEGER NOT NULL DEFAULT 0,
    default_property_count INTEGER NOT NULL DEFAULT 0,
    dependency_count INTEGER NOT NULL DEFAULT 0,
    referencer_count INTEGER NOT NULL DEFAULT 0,
    hard_referencer_count INTEGER NOT NULL DEFAULT 0,
    soft_referencer_count INTEGER NOT NULL DEFAULT 0,
    direct_child_count INTEGER NOT NULL DEFAULT 0,
    descendant_count INTEGER NOT NULL DEFAULT 0,
    implemented_by_count INTEGER NOT NULL DEFAULT 0,
    map_usage_count INTEGER NOT NULL DEFAULT 0,
    registry_usage_count INTEGER NOT NULL DEFAULT 0,
    cross_domain_reference_count INTEGER NOT NULL DEFAULT 0,
    component_reuse_count INTEGER NOT NULL DEFAULT 0,
    native_call_count INTEGER NOT NULL DEFAULT 0,
    unresolved_native_call_count INTEGER NOT NULL DEFAULT 0,
    query_hit_count INTEGER DEFAULT NULL,
    existing_report_count INTEGER DEFAULT NULL,
    query_hit_status TEXT NOT NULL DEFAULT 'NOT_MEASURED',
    existing_report_status TEXT NOT NULL DEFAULT 'NOT_MEASURED',
    estimated_deep_read_cost INTEGER NOT NULL DEFAULT 0,
    provisional_tier INTEGER NOT NULL DEFAULT 0,
    provisional_reasons_json TEXT NOT NULL DEFAULT '[]',
    identity_source_kind TEXT NOT NULL DEFAULT 'filesystem_metadata',
    identity_confidence TEXT NOT NULL DEFAULT 'UNKNOWN',
    identity_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    relative_logical_path TEXT NOT NULL,
    file_extension TEXT NOT NULL
);

CREATE TABLE blueprint_functions (
    function_id TEXT PRIMARY KEY,
    asset_object_path TEXT NOT NULL,
    function_name TEXT NOT NULL,
    function_kind TEXT NOT NULL,
    graph_evidence_id TEXT NOT NULL,
    replication_kind TEXT NOT NULL,
    is_pure INTEGER NOT NULL DEFAULT 0,
    is_override INTEGER NOT NULL DEFAULT 0,
    declaring_class_path TEXT NOT NULL,
    call_count_out INTEGER NOT NULL DEFAULT 0,
    call_count_in INTEGER NOT NULL DEFAULT 0,
    native_boundary TEXT NOT NULL,
    confidence TEXT NOT NULL,
    measurement_status TEXT NOT NULL DEFAULT 'PARTIAL'
);

CREATE TABLE blueprint_native_edges (
    edge_id TEXT PRIMARY KEY,
    blueprint_asset_path TEXT NOT NULL,
    blueprint_graph_evidence_id TEXT NOT NULL,
    blueprint_function_name TEXT NOT NULL,
    native_evidence_id TEXT NOT NULL,
    resolution_method TEXT NOT NULL,
    confidence TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'UNRESOLVED'
);

CREATE TABLE class_edges (
    child_class_path TEXT NOT NULL,
    parent_class_path TEXT NOT NULL,
    edge_kind TEXT NOT NULL,
    inheritance_depth INTEGER NOT NULL DEFAULT 1,
    source_kind TEXT NOT NULL,
    confidence TEXT NOT NULL,
    PRIMARY KEY (child_class_path, parent_class_path, edge_kind)
);

CREATE TABLE components (
    owner_object_path TEXT NOT NULL,
    component_name TEXT NOT NULL,
    component_class_path TEXT NOT NULL,
    component_object_path TEXT NOT NULL,
    is_inherited INTEGER NOT NULL DEFAULT 0,
    source_property TEXT NOT NULL,
    confidence TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'UNKNOWN',
    PRIMARY KEY (owner_object_path, component_name, component_class_path, source_kind)
);

CREATE TABLE coverage (
    object_path TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    confirmed_count INTEGER NOT NULL DEFAULT 0,
    heuristic_count INTEGER NOT NULL DEFAULT 0,
    ambiguous_count INTEGER NOT NULL DEFAULT 0,
    not_recovered_count INTEGER NOT NULL DEFAULT 0,
    source_not_available_count INTEGER NOT NULL DEFAULT 0,
    stale_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT NOT NULL,
    failure_reason TEXT NOT NULL,
    PRIMARY KEY (object_path, stage)
);

CREATE TABLE default_property_surface (
    surface_id TEXT PRIMARY KEY,
    asset_object_path TEXT NOT NULL,
    property_name TEXT NOT NULL,
    property_type TEXT NOT NULL,
    declaring_class_path TEXT NOT NULL,
    has_value INTEGER NOT NULL DEFAULT 0,
    value_status TEXT NOT NULL,
    value_fingerprint TEXT NOT NULL,
    is_object_reference INTEGER NOT NULL DEFAULT 0,
    is_array INTEGER NOT NULL DEFAULT 0,
    is_map INTEGER NOT NULL DEFAULT 0,
    is_struct INTEGER NOT NULL DEFAULT 0,
    source_evidence_id TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'UNKNOWN'
);

CREATE TABLE existing_knowledge_tables (
    database_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    distinct_asset_count INTEGER NOT NULL,
    source_asset_count INTEGER NOT NULL,
    stale_row_count INTEGER NOT NULL,
    duplicate_key_count INTEGER NOT NULL,
    distinct_asset_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    stale_count_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    duplicate_count_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    PRIMARY KEY (database_name, table_name)
);

CREATE TABLE graphs (
    asset_object_path TEXT NOT NULL,
    graph_evidence_id TEXT PRIMARY KEY,
    graph_name TEXT NOT NULL,
    graph_type TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    node_count INTEGER NOT NULL DEFAULT 0,
    pin_count INTEGER NOT NULL DEFAULT 0,
    wire_count INTEGER NOT NULL DEFAULT 0,
    native_call_count INTEGER NOT NULL DEFAULT 0,
    external_asset_reference_count INTEGER NOT NULL DEFAULT 0,
    gap_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE interfaces (
    owner_object_path TEXT NOT NULL,
    interface_class_path TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    confidence TEXT NOT NULL,
    PRIMARY KEY (owner_object_path, interface_class_path, source_kind)
);

CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE native_field_accesses (
    access_id TEXT PRIMARY KEY,
    native_evidence_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_offset TEXT NOT NULL,
    access_kind TEXT NOT NULL,
    containing_type TEXT NOT NULL,
    source_instruction_or_slice_id TEXT NOT NULL,
    confidence TEXT NOT NULL
);

CREATE TABLE native_gap_summary (
    evidence_set_id TEXT NOT NULL,
    recipe_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    gap_count INTEGER NOT NULL,
    next_probe TEXT NOT NULL,
    PRIMARY KEY (evidence_set_id, status, reason_code)
);

CREATE TABLE native_symbols (
    native_evidence_id TEXT PRIMARY KEY,
    module_name TEXT NOT NULL,
    binary_sha256 TEXT NOT NULL,
    pdb_sha256 TEXT NOT NULL,
    pdb_guid_age TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    simple_name TEXT NOT NULL,
    owner_class TEXT NOT NULL,
    signature TEXT NOT NULL,
    rva TEXT NOT NULL,
    symbol_source TEXT NOT NULL,
    pdb_loaded INTEGER NOT NULL DEFAULT 0,
    decompile_status TEXT NOT NULL,
    caller_count INTEGER NOT NULL DEFAULT 0,
    callee_count INTEGER NOT NULL DEFAULT 0,
    field_access_count INTEGER NOT NULL DEFAULT 0,
    called_by_blueprint_count INTEGER NOT NULL DEFAULT 0,
    confidence TEXT NOT NULL,
    recipe_ids_json TEXT NOT NULL DEFAULT '[]',
    evidence_set_ids_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE query_corpus (
    query_id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    source TEXT NOT NULL,
    target_audience TEXT NOT NULL,
    expected_answer_type TEXT NOT NULL,
    primary_domain TEXT NOT NULL,
    secondary_domains_json TEXT NOT NULL,
    requires_blueprint INTEGER NOT NULL DEFAULT 0,
    requires_defaults INTEGER NOT NULL DEFAULT 0,
    requires_references INTEGER NOT NULL DEFAULT 0,
    requires_map_evidence INTEGER NOT NULL DEFAULT 0,
    requires_native INTEGER NOT NULL DEFAULT 0,
    requires_runtime_validation INTEGER NOT NULL DEFAULT 0,
    existing_report_path TEXT NOT NULL
);

CREATE TABLE sample_membership (
    object_path TEXT NOT NULL,
    selection_reason TEXT NOT NULL,
    source_rank INTEGER NOT NULL,
    PRIMARY KEY (object_path, selection_reason)
);

CREATE TABLE scan_failures (
    failure_id TEXT PRIMARY KEY,
    object_path TEXT NOT NULL,
    stage TEXT NOT NULL,
    error_code TEXT NOT NULL,
    status TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'UNKNOWN',
    confidence TEXT NOT NULL DEFAULT 'UNKNOWN',
    detail_redacted TEXT NOT NULL
);

CREATE TABLE source_inventory (
    source_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    record_count INTEGER NOT NULL DEFAULT 0,
    generated_at TEXT NOT NULL,
    limitations_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE system_registrations (
    registration_id TEXT PRIMARY KEY,
    owner_object_path TEXT NOT NULL,
    registration_type TEXT NOT NULL,
    target_object_path TEXT NOT NULL,
    source_property TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    confidence TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'existing_knowledge_database'
);

CREATE INDEX idx_asset_refs_kind ON asset_references(edge_kind);

CREATE INDEX idx_asset_refs_source ON asset_references(source_object_path);

CREATE INDEX idx_asset_refs_target ON asset_references(target_object_path);

CREATE INDEX idx_asset_refs_target_kind ON asset_references(target_object_path, edge_kind);

CREATE INDEX idx_assets_class ON assets(asset_class_path);

CREATE INDEX idx_assets_descendant ON assets(descendant_count DESC);

CREATE INDEX idx_assets_generated_class ON assets(generated_class_path);

CREATE INDEX idx_assets_native_parent ON assets(native_parent_class_path);

CREATE INDEX idx_assets_package ON assets(package_path);

CREATE INDEX idx_assets_parent ON assets(parent_class_path);

CREATE INDEX idx_assets_referencer ON assets(referencer_count DESC);

CREATE INDEX idx_assets_tier ON assets(provisional_tier);

CREATE INDEX idx_bp_native_asset ON blueprint_native_edges(blueprint_asset_path);

CREATE INDEX idx_bp_native_symbol ON blueprint_native_edges(native_evidence_id);

CREATE INDEX idx_class_edges_child ON class_edges(child_class_path);

CREATE INDEX idx_class_edges_parent ON class_edges(parent_class_path);

CREATE INDEX idx_components_class ON components(component_class_path);

CREATE INDEX idx_components_owner ON components(owner_object_path);

CREATE INDEX idx_coverage_status ON coverage(stage, status);

CREATE INDEX idx_defaults_asset ON default_property_surface(asset_object_path);

CREATE INDEX idx_functions_asset ON blueprint_functions(asset_object_path);

CREATE INDEX idx_functions_name ON blueprint_functions(function_name);

CREATE INDEX idx_graphs_asset ON graphs(asset_object_path);

CREATE INDEX idx_interfaces_class ON interfaces(interface_class_path);

CREATE INDEX idx_interfaces_owner ON interfaces(owner_object_path);

CREATE INDEX idx_native_fields_symbol ON native_field_accesses(native_evidence_id);

CREATE INDEX idx_native_owner ON native_symbols(owner_class);

CREATE INDEX idx_native_qualified ON native_symbols(qualified_name);

CREATE INDEX idx_native_simple ON native_symbols(simple_name);

CREATE INDEX idx_query_domain ON query_corpus(primary_domain);

CREATE INDEX idx_registrations_owner ON system_registrations(owner_object_path);

CREATE INDEX idx_registrations_target ON system_registrations(target_object_path);

CREATE INDEX idx_registrations_type ON system_registrations(registration_type);
