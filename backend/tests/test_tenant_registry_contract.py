"""租户 Registry 的静态与 Schema 双向合同。"""

from core.tenant_registry import (
    TENANT_TABLE_REGISTRY,
    TENANT_TABLES,
    SchemaTenantTable,
    TenantTableKind,
    validate_schema_inventory,
)


LEGACY_ACTIVE_TABLES = {
    "conversations", "messages", "tasks",
    "credits_history", "credit_transactions", "image_generations",
    "detail_projects", "detail_project_images",
    "user_memory_settings", "knowledge_nodes", "knowledge_metrics",
    "knowledge_edges", "scoring_audit_log",
    "wecom_user_mappings", "wecom_chat_targets",
    "wecom_departments", "wecom_employees",
    "erp_products", "erp_product_skus", "erp_stock_status",
    "erp_suppliers", "erp_shops", "erp_warehouses", "erp_tags",
    "erp_categories", "erp_logistics_companies", "erp_document_items",
    "erp_document_items_archive", "erp_batch_stock",
    "erp_product_daily_stats", "erp_product_platform_map",
    "erp_order_logs", "erp_order_packages", "erp_aftersale_logs",
    "erp_sync_state", "erp_sync_dead_letter", "mv_kit_stock",
    "tool_audit_log", "org_departments", "org_positions", "org_roles",
    "org_member_assignments", "position_default_roles",
    "user_extra_grants", "user_revocations", "permission_audit_log",
    "scheduled_tasks", "scheduled_task_runs",
    "kuaimai_external_credentials", "erp_thinktank_profit_shop",
    "erp_viperp_sale_finance", "kuaimai_sync_logs", "kuaimai_field_audit",
    "erp_shop_operators", "erp_operators",
}

FIRST_RUNTIME_GROUP = {
    "conversation_artifacts",
    "conversation_attachment_refs",
    "conversation_channel_bindings",
    "conversation_compactions",
    "conversation_context_items",
    "conversation_context_receipts",
    "conversation_data_evidence",
    "message_generation_requests",
    "task_attachment_refs",
    "memory_atoms",
    "user_assets",
    "user_asset_refs",
    "user_activity_events",
}


def test_registry_preserves_all_existing_application_filters() -> None:
    assert TENANT_TABLES == LEGACY_ACTIVE_TABLES


def test_first_runtime_group_is_registered_without_behavior_activation() -> None:
    assert FIRST_RUNTIME_GROUP <= TENANT_TABLE_REGISTRY.keys()
    for table in FIRST_RUNTIME_GROUP:
        spec = TENANT_TABLE_REGISTRY[table]
        assert spec.kind == TenantTableKind.USER_OR_ORG_FACT
        assert not spec.application_filter_active


def test_personal_runtime_tables_have_direct_or_parent_user_identity() -> None:
    for table, spec in TENANT_TABLE_REGISTRY.items():
        if spec.personal_scope:
            assert spec.user_column or spec.parent_table, table


def test_schema_contract_rejects_unknown_and_missing_org_column() -> None:
    rows = [
        SchemaTenantTable(
            name,
            "r",
            name != "conversations",
            frozenset({spec.user_column}) if spec.user_column else frozenset(),
            spec.parent_table,
        )
        for name, spec in TENANT_TABLE_REGISTRY.items()
    ]
    rows.append(SchemaTenantTable("unknown_tenant_fact", "r", True))

    errors = validate_schema_inventory(rows)

    assert "unregistered tenant table: unknown_tenant_fact" in errors
    assert "registered table missing org_id: conversations" in errors


def test_schema_contract_validates_partition_parent_and_missing_table() -> None:
    rows = [
        SchemaTenantTable(
            name,
            "r",
            True,
            frozenset({spec.user_column}) if spec.user_column else frozenset(),
            "wrong_parent" if name == "tool_audit_log_2026_04"
            else spec.parent_table,
        )
        for name, spec in TENANT_TABLE_REGISTRY.items()
        if name != "messages"
    ]

    errors = validate_schema_inventory(rows)

    assert "registered table missing from schema: messages" in errors
    assert "partition parent mismatch: tool_audit_log_2026_04" in errors


def test_complete_schema_inventory_passes() -> None:
    rows = [
        SchemaTenantTable(
            name,
            "m" if name == "mv_kit_stock" else "r",
            True,
            frozenset({spec.user_column}) if spec.user_column else frozenset(),
            spec.parent_table,
        )
        for name, spec in TENANT_TABLE_REGISTRY.items()
    ]

    assert validate_schema_inventory(rows) == []


def test_schema_contract_rejects_missing_user_identity_column() -> None:
    rows = [
        SchemaTenantTable(
            name,
            "r",
            True,
            frozenset()
            if name == "conversations"
            else frozenset({spec.user_column}) if spec.user_column else frozenset(),
            spec.parent_table,
        )
        for name, spec in TENANT_TABLE_REGISTRY.items()
    ]

    assert (
        "registered user column missing: conversations.user_id"
        in validate_schema_inventory(rows)
    )
