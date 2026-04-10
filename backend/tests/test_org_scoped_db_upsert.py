"""
OrgScopedDB upsert 与 schema 反射 loader 测试

从 test_org_scoped_db.py 拆分（控制单文件 <500 行），
专门覆盖：
- TestUpsertOnConflictAutoAppend: 白名单驱动的 on_conflict 自动追加
- TestLoadCompositeOrgIdTables: 启动时 schema 反射 loader

白名单 _COMPOSITE_ORG_ID_TABLES 由 load_composite_org_id_tables() 在
应用启动（lifespan）时一次性反射 pg_indexes 填充。这里的测试用 monkeypatch
直接注入模拟值，避免依赖真实 DB。
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from core import org_scoped_db as org_scoped_db_module
from core.org_scoped_db import OrgScopedDB

ORG_ID = "eadc4c11-7e83-4279-a849-cfe0cbf6982b"


@pytest.fixture(autouse=True)
def _isolate_composite_tables(monkeypatch):
    """
    每个测试隔离 _COMPOSITE_ORG_ID_TABLES，默认空 frozenset。
    需要测试 ERP 自动追加的用例自行 monkeypatch 注入。
    """
    monkeypatch.setattr(
        org_scoped_db_module, "_COMPOSITE_ORG_ID_TABLES", frozenset(),
    )


def _make_mock_db():
    """构造模拟 client，支持链式调用"""
    db = MagicMock()
    table_mock = MagicMock()
    db.table.return_value = table_mock
    for method in ("select", "insert", "upsert", "update", "delete",
                    "eq", "is_", "in_", "order", "limit", "range",
                    "single", "maybe_single", "or_", "neq", "gte", "lte"):
        getattr(table_mock, method).return_value = table_mock
    db.rpc.return_value = table_mock
    db.pool = MagicMock()
    return db


# ── Upsert on_conflict 自动追加（白名单驱动） ────────────


class TestUpsertOnConflictAutoAppend:
    """
    upsert on_conflict 自动追加 org_id（白名单驱动）

    白名单 _COMPOSITE_ORG_ID_TABLES 由启动时 schema 反射填充。
    """

    def setup_method(self):
        self.raw_db = _make_mock_db()
        self.db = OrgScopedDB(self.raw_db, org_id=ORG_ID)

    def test_auto_appends_org_id_for_whitelisted(self, monkeypatch):
        """白名单内单列 on_conflict 自动追加 ,org_id"""
        monkeypatch.setattr(
            org_scoped_db_module, "_COMPOSITE_ORG_ID_TABLES",
            frozenset({"erp_products"}),
        )
        self.db.table("erp_products").upsert(
            {"outer_id": "A01"}, on_conflict="outer_id",
        )
        kwargs = self.raw_db.table.return_value.upsert.call_args[1]
        assert kwargs["on_conflict"] == "outer_id,org_id"

    def test_multi_column_appends_for_whitelisted(self, monkeypatch):
        """白名单内多列 on_conflict 也追加"""
        monkeypatch.setattr(
            org_scoped_db_module, "_COMPOSITE_ORG_ID_TABLES",
            frozenset({"erp_document_items"}),
        )
        self.db.table("erp_document_items").upsert(
            {"doc_type": "order"}, on_conflict="doc_type,doc_id,item_index",
        )
        kwargs = self.raw_db.table.return_value.upsert.call_args[1]
        assert kwargs["on_conflict"] == "doc_type,doc_id,item_index,org_id"

    def test_already_has_org_id_no_duplicate(self, monkeypatch):
        """已含 org_id 不重复追加"""
        monkeypatch.setattr(
            org_scoped_db_module, "_COMPOSITE_ORG_ID_TABLES",
            frozenset({"erp_products"}),
        )
        self.db.table("erp_products").upsert(
            {"outer_id": "A01"}, on_conflict="outer_id,org_id",
        )
        kwargs = self.raw_db.table.return_value.upsert.call_args[1]
        assert kwargs["on_conflict"] == "outer_id,org_id"

    def test_empty_on_conflict_not_appended(self, monkeypatch):
        """空 on_conflict 不追加（即使在白名单内）"""
        monkeypatch.setattr(
            org_scoped_db_module, "_COMPOSITE_ORG_ID_TABLES",
            frozenset({"erp_products"}),
        )
        self.db.table("erp_products").upsert({"outer_id": "A01"})
        kwargs = self.raw_db.table.return_value.upsert.call_args[1]
        assert kwargs["on_conflict"] == ""

    def test_exempt_table_no_append(self):
        """豁免表不经过 _TenantScopedTable，on_conflict 不变"""
        self.db.table("users").upsert(
            {"phone": "123"}, on_conflict="phone",
        )
        kwargs = self.raw_db.table.return_value.upsert.call_args[1]
        assert kwargs["on_conflict"] == "phone"

    def test_not_in_whitelist_no_append(self):
        """关键回归：白名单外的租户表（messages/tasks）on_conflict 透传不追加。

        根因：messages/tasks 的 PK 仅 id，无 (id, org_id) 复合唯一索引。
        如果自动追加，Postgres 会报 'no unique or exclusion constraint
        matching the ON CONFLICT specification'，导致 upsert 全部失败。
        autouse fixture 已把 _COMPOSITE_ORG_ID_TABLES 重置为空，
        模拟 schema 中 messages 没有含 org_id 的复合唯一索引的真实场景。
        """
        self.db.table("messages").upsert(
            {"id": "m1"}, on_conflict="id",
        )
        kwargs = self.raw_db.table.return_value.upsert.call_args[1]
        assert kwargs["on_conflict"] == "id"

    def test_tasks_table_no_append(self):
        """tasks 表同样不追加（PK 仅 id）"""
        self.db.table("tasks").upsert(
            {"id": "task-1"}, on_conflict="id",
        )
        kwargs = self.raw_db.table.return_value.upsert.call_args[1]
        assert kwargs["on_conflict"] == "id"

    def test_whitelist_partial_table_match(self, monkeypatch):
        """白名单只对集合内的表生效，其他表不受影响"""
        monkeypatch.setattr(
            org_scoped_db_module, "_COMPOSITE_ORG_ID_TABLES",
            frozenset({"erp_products"}),
        )
        # erp_products 在白名单内 → 追加
        self.db.table("erp_products").upsert(
            {"outer_id": "A"}, on_conflict="outer_id",
        )
        kwargs1 = self.raw_db.table.return_value.upsert.call_args[1]
        assert kwargs1["on_conflict"] == "outer_id,org_id"

        # messages 不在白名单内 → 透传
        self.db.table("messages").upsert(
            {"id": "m1"}, on_conflict="id",
        )
        kwargs2 = self.raw_db.table.return_value.upsert.call_args[1]
        assert kwargs2["on_conflict"] == "id"


# ── 启动时 schema 反射 loader 测试 ────────────────────────


class TestLoadCompositeOrgIdTables:
    """load_composite_org_id_tables() 的单元测试（mock pool）"""

    def _make_db_with_pool(self, fetch_rows):
        """构造带 pool 的 mock db，cursor.fetchall 返回 fetch_rows"""
        cursor = MagicMock()
        cursor.fetchall.return_value = fetch_rows
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)

        conn = MagicMock()
        conn.cursor.return_value = cursor
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)

        pool = MagicMock()
        pool.connection.return_value = conn

        db = MagicMock()
        db.pool = pool
        return db

    def test_loads_tables_from_dict_rows(self, monkeypatch):
        """dict_row 模式：fetchall 返回 dict 列表"""
        monkeypatch.setattr(
            org_scoped_db_module, "_COMPOSITE_ORG_ID_TABLES", frozenset(),
        )
        db = self._make_db_with_pool([
            {"table_name": "erp_products"},
            {"table_name": "erp_product_skus"},
        ])

        result = org_scoped_db_module.load_composite_org_id_tables(db)

        assert result == frozenset({"erp_products", "erp_product_skus"})
        # 同时写入模块全局
        assert org_scoped_db_module._COMPOSITE_ORG_ID_TABLES == result

    def test_loads_tables_from_tuple_rows(self, monkeypatch):
        """tuple_row 兼容：fetchall 返回 tuple 列表"""
        monkeypatch.setattr(
            org_scoped_db_module, "_COMPOSITE_ORG_ID_TABLES", frozenset(),
        )
        db = self._make_db_with_pool([
            ("erp_products",), ("erp_suppliers",),
        ])

        result = org_scoped_db_module.load_composite_org_id_tables(db)

        assert result == frozenset({"erp_products", "erp_suppliers"})

    def test_db_without_pool_returns_empty(self, monkeypatch):
        """db 无 pool 属性：返回空 frozenset，全局保持空"""
        monkeypatch.setattr(
            org_scoped_db_module, "_COMPOSITE_ORG_ID_TABLES", frozenset(),
        )
        db = MagicMock(spec=[])  # 没有 pool 属性

        result = org_scoped_db_module.load_composite_org_id_tables(db)

        assert result == frozenset()
        assert org_scoped_db_module._COMPOSITE_ORG_ID_TABLES == frozenset()

    def test_query_failure_returns_empty_no_crash(self, monkeypatch):
        """查询抛错：返回空 frozenset，不让启动崩溃，且不覆盖旧值"""
        monkeypatch.setattr(
            org_scoped_db_module, "_COMPOSITE_ORG_ID_TABLES",
            frozenset({"old_value"}),
        )
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("DB unreachable")
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.cursor.return_value = cursor
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        pool = MagicMock()
        pool.connection.return_value = conn
        db = MagicMock()
        db.pool = pool

        result = org_scoped_db_module.load_composite_org_id_tables(db)

        assert result == frozenset()
        # 失败时不覆盖旧值
        assert org_scoped_db_module._COMPOSITE_ORG_ID_TABLES == frozenset({"old_value"})

    def test_empty_schema_returns_empty(self, monkeypatch):
        """schema 没有任何匹配索引：返回空，运行时全部 upsert 走透传"""
        monkeypatch.setattr(
            org_scoped_db_module, "_COMPOSITE_ORG_ID_TABLES", frozenset(),
        )
        db = self._make_db_with_pool([])

        result = org_scoped_db_module.load_composite_org_id_tables(db)

        assert result == frozenset()


# ── 端到端：loader 填充全局 → upsert 读取全局 ──────────────


class TestLoaderUpsertEndToEnd:
    """
    端到端联动测试：验证 loader 写入的 _COMPOSITE_ORG_ID_TABLES
    确实被 _TenantScopedTable.upsert 读取并据此决策。

    这一类是为了防止两侧"接线断裂"——比如某次重构改了变量名或
    把全局变量换成了别的容器，单独测 loader 和单独测 upsert 都过，
    但端到端会断。
    """

    def _make_db_with_pool(self, fetch_rows):
        """构造带 pool 的 mock db"""
        cursor = MagicMock()
        cursor.fetchall.return_value = fetch_rows
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)

        conn = MagicMock()
        conn.cursor.return_value = cursor
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)

        pool = MagicMock()
        pool.connection.return_value = conn

        # 同时支持 .table()（给 OrgScopedDB 用）和 .pool（给 loader 用）
        db = MagicMock()
        db.pool = pool
        return db

    def test_loader_then_upsert_appends_for_loaded_table(self, monkeypatch):
        """loader 写入 erp_products → 后续 upsert 自动追加 ,org_id"""
        # 起始状态：空
        monkeypatch.setattr(
            org_scoped_db_module, "_COMPOSITE_ORG_ID_TABLES", frozenset(),
        )

        # Step 1: 调用 loader，让它把 erp_products 写入全局
        loader_db = self._make_db_with_pool([
            {"table_name": "erp_products"},
        ])
        org_scoped_db_module.load_composite_org_id_tables(loader_db)

        # Step 2: 用真正的 OrgScopedDB 走 upsert，验证全局确实被读到
        upsert_db = _make_mock_db()
        scoped = OrgScopedDB(upsert_db, org_id=ORG_ID)
        scoped.table("erp_products").upsert(
            {"outer_id": "A01"}, on_conflict="outer_id",
        )

        kwargs = upsert_db.table.return_value.upsert.call_args[1]
        assert kwargs["on_conflict"] == "outer_id,org_id"

    def test_loader_then_upsert_skips_for_unloaded_table(self, monkeypatch):
        """loader 反射结果不含 messages → upsert 不追加（防回归）"""
        monkeypatch.setattr(
            org_scoped_db_module, "_COMPOSITE_ORG_ID_TABLES", frozenset(),
        )

        # loader 反射出来只有 erp_products，没有 messages
        loader_db = self._make_db_with_pool([
            {"table_name": "erp_products"},
        ])
        org_scoped_db_module.load_composite_org_id_tables(loader_db)

        # upsert messages：不应该追加 ,org_id
        upsert_db = _make_mock_db()
        scoped = OrgScopedDB(upsert_db, org_id=ORG_ID)
        scoped.table("messages").upsert(
            {"id": "m1"}, on_conflict="id",
        )

        kwargs = upsert_db.table.return_value.upsert.call_args[1]
        assert kwargs["on_conflict"] == "id"

    def test_loader_replaces_previous_global(self, monkeypatch):
        """连续两次 loader 调用：第二次结果完全替换第一次（顺便覆盖幂等性）"""
        monkeypatch.setattr(
            org_scoped_db_module, "_COMPOSITE_ORG_ID_TABLES", frozenset(),
        )

        # 第一次反射：包含 erp_products
        first_db = self._make_db_with_pool([{"table_name": "erp_products"}])
        org_scoped_db_module.load_composite_org_id_tables(first_db)
        assert "erp_products" in org_scoped_db_module._COMPOSITE_ORG_ID_TABLES

        # 第二次反射：模拟 schema 变化，erp_products 没了，erp_suppliers 来了
        second_db = self._make_db_with_pool([{"table_name": "erp_suppliers"}])
        org_scoped_db_module.load_composite_org_id_tables(second_db)

        # 第二次完全覆盖，erp_products 应该不在了
        assert "erp_products" not in org_scoped_db_module._COMPOSITE_ORG_ID_TABLES
        assert "erp_suppliers" in org_scoped_db_module._COMPOSITE_ORG_ID_TABLES

        # 验证 upsert 行为也跟着变：erp_products 现在不再追加
        upsert_db = _make_mock_db()
        scoped = OrgScopedDB(upsert_db, org_id=ORG_ID)
        scoped.table("erp_products").upsert(
            {"outer_id": "A"}, on_conflict="outer_id",
        )
        kwargs = upsert_db.table.return_value.upsert.call_args[1]
        assert kwargs["on_conflict"] == "outer_id"
