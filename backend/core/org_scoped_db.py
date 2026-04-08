"""
多租户数据隔离 — OrgScopedDB

包装 Supabase client，对 TENANT_TABLES 中的表自动注入 org_id 过滤/写入。
非租户表（organizations, users 等）直接透传，不干预。

设计文档: docs/document/TECH_多租户隔离架构.md V1.6
核心行为:
  - SELECT/UPDATE/DELETE: 自动追加 .eq("org_id", x) 或 .is_("org_id", "null")
  - INSERT/UPSERT: 自动将 org_id 注入到数据 dict 中
  - RPC: 透传，不自动注入 p_org_id（部分函数不接受该参数）
  - on_conflict: 透传，不自动追加（COALESCE 表达式索引不兼容）
  - unscoped("原因"): 显式跳过隔离，grep 可审计
"""

from __future__ import annotations

from typing import Any

from loguru import logger

# ── 需要租户隔离的 36 个表 ──────────────────────────────────
# 维护规则：新增租户表时同步加入此集合，豁免表不加。
# 豁免表：organizations, org_members, org_configs, org_invitations,
#         users, models, admin_action_logs, user_subscriptions

TENANT_TABLES: frozenset[str] = frozenset({
    # 对话/消息
    "conversations", "messages", "tasks",
    # 积分/账单
    "credits_history", "credit_transactions",
    # 媒体
    "image_generations",
    # 记忆/知识
    "user_memory_settings", "knowledge_nodes",
    "knowledge_metrics", "knowledge_edges", "scoring_audit_log",
    # 企微
    "wecom_user_mappings", "wecom_chat_targets",
    "wecom_departments", "wecom_employees",
    # ERP 主数据
    "erp_products", "erp_product_skus", "erp_stock_status",
    "erp_suppliers", "erp_shops", "erp_warehouses",
    "erp_tags", "erp_categories", "erp_logistics_companies",
    # ERP 单据/库存
    "erp_document_items", "erp_document_items_archive",
    "erp_batch_stock", "erp_product_daily_stats",
    "erp_product_platform_map",
    # ERP 搭便车
    "erp_order_logs", "erp_order_packages", "erp_aftersale_logs",
    # ERP 同步
    "erp_sync_state", "erp_sync_dead_letter",
    # ERP 物化视图
    "mv_kit_stock",
    # 审计
    "tool_audit_log",
})


class OrgScopedDB:
    """
    包装 Supabase client，租户表自动注入 org_id。

    用法:
        scoped = OrgScopedDB(raw_db, org_id="abc-123")
        scoped.table("conversations").select("*").execute()
        # → 自动追加 WHERE org_id = 'abc-123'

        scoped.table("users").select("*").execute()
        # → 透传，不加 org_id（users 不在 TENANT_TABLES 中）

        scoped.unscoped("数据迁移").table("messages").select("*").execute()
        # → 跳过隔离，审计日志记录原因
    """

    def __init__(self, raw_db: Any, org_id: str | None) -> None:
        self._db = raw_db
        self.org_id = org_id

    @property
    def pool(self) -> Any:
        """透传连接池（erp_sync 等模块用到 raw SQL）"""
        return getattr(self._db, "pool", None)

    def table(self, name: str) -> Any:
        """获取表查询构建器，租户表自动注入 org_id 过滤"""
        if name in TENANT_TABLES:
            return _TenantScopedTable(self._db.table(name), self.org_id)
        return self._db.table(name)

    def rpc(self, fn_name: str, params: dict | None = None) -> Any:
        """调用 RPC 函数（透传，不自动注入 p_org_id）"""
        return self._db.rpc(fn_name, params)

    def unscoped(self, reason: str) -> Any:
        """
        显式跳过隔离，返回原始 db。

        审计方式: grep -rn 'unscoped(' backend/
        """
        logger.warning(
            f"Unscoped DB access | org_id={self.org_id} | reason={reason}"
        )
        return self._db

    def __getattr__(self, name: str) -> Any:
        """透传其他属性（storage, auth 等）"""
        return getattr(self._db, name)


class _TenantScopedTable:
    """
    代理 PostgREST query builder，自动注入 org_id。

    - select/update/delete: 返回的 query 自动追加 org_id WHERE 条件
    - insert/upsert: 自动将 org_id 注入到数据 dict
    - on_conflict: 透传，不自动追加（COALESCE 表达式索引不兼容）
    """

    def __init__(self, table: Any, org_id: str | None) -> None:
        self._table = table
        self._org_id = org_id

    def select(self, *args: Any, **kwargs: Any) -> Any:
        q = self._table.select(*args, **kwargs)
        return _apply_org_filter(q, self._org_id)

    def insert(self, data: dict | list[dict], **kwargs: Any) -> Any:
        return self._table.insert(_inject_org_id(data, self._org_id), **kwargs)

    def upsert(
        self,
        data: dict | list[dict],
        on_conflict: str = "",
        **kwargs: Any,
    ) -> Any:
        return self._table.upsert(
            _inject_org_id(data, self._org_id),
            on_conflict=on_conflict,
            **kwargs,
        )

    def update(self, data: dict, **kwargs: Any) -> Any:
        q = self._table.update(data, **kwargs)
        return _apply_org_filter(q, self._org_id)

    def delete(self) -> Any:
        q = self._table.delete()
        return _apply_org_filter(q, self._org_id)


# ── 内部工具函数 ──────────────────────────────────────────


def _apply_org_filter(q: Any, org_id: str | None) -> Any:
    """给 query 追加 org_id 过滤条件"""
    if org_id:
        return q.eq("org_id", org_id)
    return q.is_("org_id", "null")


def _inject_org_id(
    data: dict | list[dict], org_id: str | None,
) -> dict | list[dict]:
    """给 INSERT/UPSERT 数据注入 org_id 字段"""
    if isinstance(data, list):
        return [{**row, "org_id": org_id} for row in data]
    return {**data, "org_id": org_id}
