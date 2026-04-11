"""
OrgScopedDB 单元测试

覆盖:
- TENANT_TABLES 白名单完整性
- SELECT/UPDATE/DELETE 自动追加 org_id 过滤
- INSERT/UPSERT 自动注入 org_id 到数据
- RPC 透传（不自动注入 p_org_id）
- on_conflict 透传（不自动追加 org_id）
- 非租户表直接透传
- unscoped() 跳过隔离
- 散客（org_id=None）行为
- pool 属性透传
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from core.org_scoped_db import (
    TENANT_TABLES,
    OrgScopedDB,
    _apply_org_filter,
    _inject_org_id,
)

ORG_ID = "eadc4c11-7e83-4279-a849-cfe0cbf6982b"


# ── fixtures ──────────────────────────────────────────────


def _make_mock_db():
    """构造模拟 Supabase client，支持链式调用"""
    db = MagicMock()
    # table() 返回支持链式调用的 mock
    table_mock = MagicMock()
    db.table.return_value = table_mock
    # 让链式方法返回自身
    for method in ("select", "insert", "upsert", "update", "delete",
                    "eq", "is_", "in_", "order", "limit", "range",
                    "single", "maybe_single", "or_", "neq", "gte", "lte"):
        getattr(table_mock, method).return_value = table_mock
    db.rpc.return_value = table_mock
    db.pool = MagicMock()
    return db


# ── TENANT_TABLES 白名单 ──────────────────────────────────


class TestTenantTables:
    """白名单完整性校验"""

    def test_is_frozenset(self):
        assert isinstance(TENANT_TABLES, frozenset)

    def test_count(self):
        # 36 张原有 + 8 张权限模型 V1（060-068）+ 2 张定时任务（069）
        assert len(TENANT_TABLES) == 46

    def test_core_tables_present(self):
        for t in ("conversations", "messages", "tasks"):
            assert t in TENANT_TABLES

    def test_erp_tables_present(self):
        for t in ("erp_products", "erp_stock_status", "erp_document_items"):
            assert t in TENANT_TABLES

    def test_wecom_tables_present(self):
        for t in ("wecom_user_mappings", "wecom_chat_targets"):
            assert t in TENANT_TABLES

    def test_exempt_tables_absent(self):
        for t in ("organizations", "org_members", "users", "models",
                   "admin_action_logs", "org_configs"):
            assert t not in TENANT_TABLES

    def test_mv_kit_stock_present(self):
        assert "mv_kit_stock" in TENANT_TABLES


# ── OrgScopedDB 核心行为 ─────────────────────────────────


class TestOrgScopedDBWithOrgId:
    """企业用户（org_id 有值）"""

    def setup_method(self):
        self.raw_db = _make_mock_db()
        self.db = OrgScopedDB(self.raw_db, org_id=ORG_ID)

    def test_org_id_stored(self):
        assert self.db.org_id == ORG_ID

    def test_pool_passthrough(self):
        assert self.db.pool is self.raw_db.pool

    # ── SELECT ──

    def test_select_tenant_table_adds_eq(self):
        self.db.table("conversations").select("*")
        table_mock = self.raw_db.table.return_value
        table_mock.select.assert_called_once_with("*")
        table_mock.eq.assert_called_once_with("org_id", ORG_ID)

    def test_select_exempt_table_no_filter(self):
        self.db.table("users").select("*")
        table_mock = self.raw_db.table.return_value
        table_mock.select.assert_called_once_with("*")
        table_mock.eq.assert_not_called()
        table_mock.is_.assert_not_called()

    # ── INSERT ──

    def test_insert_injects_org_id_dict(self):
        self.db.table("messages").insert({"role": "user", "content": "hi"})
        table_mock = self.raw_db.table.return_value
        table_mock.insert.assert_called_once_with(
            {"role": "user", "content": "hi", "org_id": ORG_ID}
        )

    def test_insert_injects_org_id_list(self):
        data = [{"a": 1}, {"a": 2}]
        self.db.table("tasks").insert(data)
        table_mock = self.raw_db.table.return_value
        args = table_mock.insert.call_args[0][0]
        assert all(row["org_id"] == ORG_ID for row in args)
        assert len(args) == 2

    def test_insert_exempt_table_no_injection(self):
        self.db.table("users").insert({"phone": "123"})
        table_mock = self.raw_db.table.return_value
        inserted = table_mock.insert.call_args[0][0]
        assert "org_id" not in inserted

    # ── UPSERT ──

    def test_upsert_injects_org_id(self):
        self.db.table("erp_products").upsert(
            {"outer_id": "A01"}, on_conflict="outer_id",
        )
        table_mock = self.raw_db.table.return_value
        args = table_mock.upsert.call_args
        assert args[0][0] == {"outer_id": "A01", "org_id": ORG_ID}

    # on_conflict 自动追加/白名单回归测试见 test_org_scoped_db_upsert.py

    # ── UPDATE ──

    def test_update_adds_eq(self):
        self.db.table("conversations").update({"title": "new"})
        table_mock = self.raw_db.table.return_value
        table_mock.update.assert_called_once_with({"title": "new"})
        table_mock.eq.assert_called_once_with("org_id", ORG_ID)

    # ── DELETE ──

    def test_delete_adds_eq(self):
        self.db.table("tasks").delete()
        table_mock = self.raw_db.table.return_value
        table_mock.delete.assert_called_once()
        table_mock.eq.assert_called_once_with("org_id", ORG_ID)

    # ── RPC ──

    def test_rpc_auto_injects_org_id(self):
        """普通 RPC 自动注入 p_org_id"""
        self.db.rpc("erp_aggregate_daily_stats", {"p_outer_id": "A01"})
        self.raw_db.rpc.assert_called_once_with(
            "erp_aggregate_daily_stats",
            {"p_outer_id": "A01", "p_org_id": ORG_ID},
        )

    def test_rpc_blacklist_no_injection(self):
        """黑名单函数不注入 p_org_id"""
        self.db.rpc("atomic_refund_credits", {"p_transaction_id": "tx-123"})
        self.raw_db.rpc.assert_called_once_with(
            "atomic_refund_credits", {"p_transaction_id": "tx-123"},
        )

    def test_rpc_preserves_explicit_org_id(self):
        """已有 p_org_id 不覆盖"""
        params = {"p_outer_id": "A01", "p_org_id": ORG_ID}
        self.db.rpc("erp_aggregate_daily_stats", params)
        self.raw_db.rpc.assert_called_once_with(
            "erp_aggregate_daily_stats", params,
        )

    # ── unscoped ──

    def test_unscoped_returns_raw_db(self):
        result = self.db.unscoped("数据迁移")
        assert result is self.raw_db

    def test_unscoped_on_exempt_table(self):
        """unscoped 后访问租户表也不过滤"""
        raw = self.db.unscoped("管理操作")
        raw.table("messages").select("*")
        # 直接调用 raw_db.table，不经过 _TenantScopedTable
        self.raw_db.table.assert_called_with("messages")

    # ── __getattr__ ──

    def test_getattr_passthrough(self):
        self.raw_db.storage = MagicMock()
        assert self.db.storage is self.raw_db.storage


class TestOrgScopedDBPersonal:
    """散客用户（org_id=None）"""

    def setup_method(self):
        self.raw_db = _make_mock_db()
        self.db = OrgScopedDB(self.raw_db, org_id=None)

    def test_org_id_is_none(self):
        assert self.db.org_id is None

    def test_select_uses_is_null(self):
        self.db.table("conversations").select("*")
        table_mock = self.raw_db.table.return_value
        table_mock.is_.assert_called_once_with("org_id", "null")
        table_mock.eq.assert_not_called()

    def test_insert_injects_none(self):
        self.db.table("messages").insert({"content": "hi"})
        table_mock = self.raw_db.table.return_value
        inserted = table_mock.insert.call_args[0][0]
        assert inserted["org_id"] is None

    def test_update_uses_is_null(self):
        self.db.table("tasks").update({"status": "done"})
        table_mock = self.raw_db.table.return_value
        table_mock.is_.assert_called_once_with("org_id", "null")

    def test_delete_uses_is_null(self):
        self.db.table("erp_sync_state").delete()
        table_mock = self.raw_db.table.return_value
        table_mock.is_.assert_called_once_with("org_id", "null")

    def test_upsert_injects_none(self):
        self.db.table("erp_products").upsert(
            {"outer_id": "X"}, on_conflict="outer_id",
        )
        table_mock = self.raw_db.table.return_value
        inserted = table_mock.upsert.call_args[0][0]
        assert inserted["org_id"] is None


# ── 工具函数 ─────────────────────────────────────────────


class TestApplyOrgFilter:
    def test_with_org_id(self):
        q = MagicMock()
        q.eq.return_value = q
        result = _apply_org_filter(q, ORG_ID)
        q.eq.assert_called_once_with("org_id", ORG_ID)
        assert result is q

    def test_without_org_id(self):
        q = MagicMock()
        q.is_.return_value = q
        result = _apply_org_filter(q, None)
        q.is_.assert_called_once_with("org_id", "null")
        assert result is q


class TestInjectOrgId:
    def test_dict(self):
        result = _inject_org_id({"a": 1}, ORG_ID)
        assert result == {"a": 1, "org_id": ORG_ID}

    def test_dict_none(self):
        result = _inject_org_id({"a": 1}, None)
        assert result == {"a": 1, "org_id": None}

    def test_list(self):
        result = _inject_org_id([{"a": 1}, {"a": 2}], ORG_ID)
        assert len(result) == 2
        assert all(r["org_id"] == ORG_ID for r in result)

    def test_list_preserves_original(self):
        """不修改原始数据"""
        original = {"a": 1}
        result = _inject_org_id(original, ORG_ID)
        assert "org_id" not in original
        assert result["org_id"] == ORG_ID

    def test_overrides_existing_org_id(self):
        """已有 org_id 被覆盖（OrgScopedDB 的值优先）"""
        result = _inject_org_id({"org_id": "old", "a": 1}, ORG_ID)
        assert result["org_id"] == ORG_ID


# ── 边界场景 ─────────────────────────────────────────────


class TestEdgeCases:
    def test_pool_none_when_raw_db_has_no_pool(self):
        raw_db = MagicMock(spec=[])  # no pool attribute
        db = OrgScopedDB(raw_db, org_id=ORG_ID)
        assert db.pool is None

    def test_multiple_tables_independent(self):
        """不同表的 _TenantScopedTable 独立"""
        raw_db = _make_mock_db()
        db = OrgScopedDB(raw_db, org_id=ORG_ID)
        t1 = db.table("conversations")
        t2 = db.table("messages")
        assert t1 is not t2

    def test_rpc_with_none_params_auto_injects(self):
        """params=None 时自动创建 dict 并注入 p_org_id"""
        raw_db = _make_mock_db()
        db = OrgScopedDB(raw_db, org_id=ORG_ID)
        db.rpc("some_func", None)
        raw_db.rpc.assert_called_once_with("some_func", {"p_org_id": ORG_ID})

    def test_upsert_list_injects_all(self):
        raw_db = _make_mock_db()
        db = OrgScopedDB(raw_db, org_id=ORG_ID)
        rows = [{"outer_id": f"A{i}"} for i in range(5)]
        db.table("erp_products").upsert(rows, on_conflict="outer_id")
        table_mock = raw_db.table.return_value
        injected = table_mock.upsert.call_args[0][0]
        assert len(injected) == 5
        assert all(r["org_id"] == ORG_ID for r in injected)


# ── RPC 自动注入补充测试 ─────────────────────────────────


class TestRPCAutoInjection:
    """RPC p_org_id 自动注入（黑名单模式）"""

    def setup_method(self):
        self.raw_db = _make_mock_db()
        self.db = OrgScopedDB(self.raw_db, org_id=ORG_ID)

    def test_normal_rpc_injects_org_id(self):
        """普通 RPC 自动注入 p_org_id"""
        self.db.rpc("increment_message_count", {"conv_id": "c1"})
        self.raw_db.rpc.assert_called_once_with(
            "increment_message_count",
            {"conv_id": "c1", "p_org_id": ORG_ID},
        )

    def test_blacklisted_rpc_no_injection(self):
        """黑名单 RPC（atomic_refund_credits）不注入"""
        self.db.rpc("atomic_refund_credits", {"p_transaction_id": "tx1"})
        self.raw_db.rpc.assert_called_once_with(
            "atomic_refund_credits", {"p_transaction_id": "tx1"},
        )

    def test_explicit_org_id_not_overwritten(self):
        """已有 p_org_id 不覆盖"""
        other_org = "other-org-id"
        self.db.rpc("some_func", {"p_org_id": other_org})
        args = self.raw_db.rpc.call_args[0]
        assert args[1]["p_org_id"] == other_org

    def test_personal_user_injects_none(self):
        """散客用户注入 p_org_id=None"""
        db = OrgScopedDB(self.raw_db, org_id=None)
        db.rpc("some_func", {"key": "val"})
        args = self.raw_db.rpc.call_args[0]
        assert args[1]["p_org_id"] is None

    def test_empty_params_injects(self):
        """空 params 自动创建 dict 并注入"""
        self.db.rpc("some_func")
        self.raw_db.rpc.assert_called_once_with(
            "some_func", {"p_org_id": ORG_ID},
        )


# Upsert on_conflict 自动追加（白名单驱动）+ schema 反射 loader 测试
# 见 test_org_scoped_db_upsert.py
