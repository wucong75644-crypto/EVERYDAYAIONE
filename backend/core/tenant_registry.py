"""多租户数据库对象的唯一分类 Registry。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class TenantTableKind(StrEnum):
    """租户表的数据库访问类别。"""

    USER_OR_ORG_FACT = "user_or_org_fact"
    ORG_CONTROL = "org_control"
    ORG_WORKER_FACT = "org_worker_fact"
    SYSTEM_FACT = "system_fact"
    PARTITION_CHILD = "partition_child"


@dataclass(frozen=True)
class TenantTableSpec:
    """单个数据库对象的租户边界元数据。"""

    kind: TenantTableKind
    personal_scope: bool = False
    user_column: str | None = None
    parent_table: str | None = None
    application_filter_active: bool = False


def _spec(
    kind: TenantTableKind,
    *,
    personal: bool = False,
    user_column: str | None = None,
    parent: str | None = None,
    active: bool = True,
) -> TenantTableSpec:
    return TenantTableSpec(kind, personal, user_column, parent, active)


_FACT = TenantTableKind.USER_OR_ORG_FACT
_CONTROL = TenantTableKind.ORG_CONTROL
_WORKER = TenantTableKind.ORG_WORKER_FACT
_SYSTEM = TenantTableKind.SYSTEM_FACT
_PARTITION = TenantTableKind.PARTITION_CHILD


TENANT_TABLE_REGISTRY: dict[str, TenantTableSpec] = {
    "conversations": _spec(_FACT, personal=True, user_column="user_id"),
    "messages": _spec(_FACT, personal=True, parent="conversations"),
    "tasks": _spec(_FACT, personal=True, user_column="user_id"),
    "credits_history": _spec(_FACT, personal=True, user_column="user_id"),
    "credit_transactions": _spec(_FACT, personal=True, user_column="user_id"),
    "image_generations": _spec(_FACT, personal=True, user_column="user_id"),
    "detail_projects": _spec(_FACT, personal=True, user_column="user_id"),
    "detail_project_images": _spec(_FACT, personal=True, parent="detail_projects"),
    "message_generation_requests": _spec(
        _FACT, personal=True, user_column="user_id", active=False,
    ),
    "deleted_files": _spec(_FACT, personal=True, user_column="user_id", active=False),
    "user_activity_events": _spec(
        _FACT, personal=True, user_column="user_id", active=False,
    ),
    "conversation_artifacts": _spec(
        _FACT, personal=True, parent="conversations", active=False,
    ),
    "conversation_attachment_refs": _spec(
        _FACT, personal=True, parent="conversations", active=False,
    ),
    "conversation_channel_bindings": _spec(
        _FACT, parent="conversations", active=False,
    ),
    "conversation_compactions": _spec(
        _FACT, personal=True, parent="conversations", active=False,
    ),
    "conversation_context_items": _spec(
        _FACT, personal=True, parent="conversations", active=False,
    ),
    "conversation_context_receipts": _spec(
        _FACT, personal=True, parent="conversations", active=False,
    ),
    "conversation_data_evidence": _spec(
        _FACT, personal=True, parent="conversations", active=False,
    ),
    "task_attachment_refs": _spec(
        _FACT, personal=True, parent="tasks", active=False,
    ),
    "memory_atoms": _spec(
        _FACT, personal=True, user_column="user_id", active=False,
    ),
    "user_assets": _spec(
        _FACT, personal=True, user_column="storage_owner_key", active=False,
    ),
    "user_asset_refs": _spec(
        _FACT, personal=True, parent="user_assets", active=False,
    ),
    "user_memory_settings": _spec(_FACT, personal=True, user_column="user_id"),
    "knowledge_nodes": _spec(_FACT),
    "knowledge_metrics": _spec(_FACT, parent="knowledge_nodes"),
    "knowledge_edges": _spec(_FACT, parent="knowledge_nodes"),
    "scoring_audit_log": _spec(_FACT, parent="knowledge_nodes"),
    "memory_personas": _spec(
        _FACT, personal=True, user_column="user_id", active=False,
    ),
    "memory_pipeline_state": _spec(
        _FACT, personal=True, user_column="user_id", active=False,
    ),
    "memory_scenes": _spec(
        _FACT, personal=True, user_column="user_id", active=False,
    ),
    "org_configs": _spec(_CONTROL, active=False),
    "org_invitations": _spec(_CONTROL, active=False),
    "org_members": _spec(_CONTROL, user_column="user_id", active=False),
    "org_departments": _spec(_CONTROL),
    "org_positions": _spec(_CONTROL),
    "org_roles": _spec(_CONTROL),
    "org_member_assignments": _spec(_CONTROL),
    "position_default_roles": _spec(_CONTROL),
    "user_extra_grants": _spec(_CONTROL, user_column="user_id"),
    "user_revocations": _spec(_CONTROL, user_column="user_id"),
    "permission_audit_log": _spec(_CONTROL),
    "wecom_user_mappings": _spec(_WORKER),
    "wecom_chat_targets": _spec(_WORKER),
    "wecom_callback_inbox": _spec(_WORKER, active=False),
    "wecom_departments": _spec(_WORKER),
    "wecom_employees": _spec(_WORKER),
    **{
        table: _spec(_WORKER)
        for table in (
            "erp_products", "erp_product_skus", "erp_stock_status",
            "erp_suppliers", "erp_shops", "erp_warehouses", "erp_tags",
            "erp_categories", "erp_logistics_companies", "erp_document_items",
            "erp_document_items_archive", "erp_batch_stock",
            "erp_product_daily_stats", "erp_product_platform_map",
            "erp_order_logs", "erp_order_packages", "erp_aftersale_logs",
            "erp_sync_state", "erp_sync_dead_letter", "mv_kit_stock",
            "scheduled_tasks", "scheduled_task_runs",
            "kuaimai_external_credentials", "erp_thinktank_profit_shop",
            "erp_viperp_sale_finance", "kuaimai_sync_logs",
            "kuaimai_field_audit", "erp_shop_operators", "erp_operators",
        )
    },
    "erp_classification_rules": _spec(_WORKER, active=False),
    "error_logs": _spec(_SYSTEM, active=False),
    "tool_audit_log": _spec(_SYSTEM),
    "tool_audit_log_2026_04": _spec(
        _PARTITION, parent="tool_audit_log", active=False,
    ),
    "tool_audit_log_2026_05": _spec(
        _PARTITION, parent="tool_audit_log", active=False,
    ),
    "tool_audit_log_2026_06": _spec(
        _PARTITION, parent="tool_audit_log", active=False,
    ),
}


TENANT_TABLES: frozenset[str] = frozenset(
    table
    for table, spec in TENANT_TABLE_REGISTRY.items()
    if spec.application_filter_active
)


@dataclass(frozen=True)
class SchemaTenantTable:
    """pg_catalog 审计所需的最小表信息。"""

    name: str
    relation_kind: str
    has_org_id: bool
    columns: frozenset[str] = frozenset()
    parent_table: str | None = None


def validate_schema_inventory(rows: Iterable[SchemaTenantTable]) -> list[str]:
    """双向校验 Registry 与实际含 org_id 的表、分区和物化视图。"""
    schema = {row.name: row for row in rows}
    errors: list[str] = []
    for name, row in schema.items():
        if row.has_org_id and name not in TENANT_TABLE_REGISTRY:
            errors.append(f"unregistered tenant table: {name}")
    for name, spec in TENANT_TABLE_REGISTRY.items():
        row = schema.get(name)
        if row is None:
            errors.append(f"registered table missing from schema: {name}")
        elif spec.kind == _PARTITION:
            if row.parent_table != spec.parent_table:
                errors.append(f"partition parent mismatch: {name}")
        elif not row.has_org_id:
            errors.append(f"registered table missing org_id: {name}")
        if spec.user_column and spec.user_column not in row.columns:
            errors.append(f"registered user column missing: {name}.{spec.user_column}")
        if spec.parent_table and spec.parent_table not in schema:
            errors.append(f"registered parent table missing: {name}.{spec.parent_table}")
    return sorted(errors)
