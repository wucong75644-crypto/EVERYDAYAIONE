"""
多租户隔离集成测试 — OrgScopedDB 双租户泄露验证

验证：
1. 企业 A 的数据对企业 B 不可见
2. 散客的数据对企业不可见
3. INSERT/UPSERT 自动注入 org_id
4. SELECT/UPDATE/DELETE 自动过滤 org_id
5. 豁免表（users/organizations）不受 org_id 过滤
6. unscoped() 跳过隔离时可看到所有数据
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from core.org_scoped_db import OrgScopedDB, TENANT_TABLES

ORG_A = "aaaa1111-0000-0000-0000-000000000001"
ORG_B = "bbbb2222-0000-0000-0000-000000000002"


# ── Mock DB 支持多租户过滤 ────────────────────────────────


class _FakeQueryBuilder:
    """支持链式调用和过滤的 fake query builder"""

    def __init__(self, rows: list[dict]):
        self._all_rows = rows
        self._filters: dict[str, object] = {}
        self._is_null_filters: set[str] = set()
        self._inserted: list[dict] = []

    def eq(self, field: str, value):
        clone = self._clone()
        clone._filters[field] = value
        return clone

    def is_(self, field: str, value: str):
        clone = self._clone()
        if value == "null":
            clone._is_null_filters.add(field)
        return clone

    def select(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, n: int):
        return self

    def insert(self, data):
        if isinstance(data, list):
            self._inserted.extend(data)
            self._all_rows.extend(data)
        else:
            self._inserted.append(data)
            self._all_rows.append(data)
        return self

    def upsert(self, data, on_conflict=""):
        return self.insert(data)

    def update(self, data):
        self._update_data = data
        return self

    def delete(self):
        self._is_delete = True
        return self

    def execute(self):
        result = MagicMock()
        rows = self._apply_filters()
        result.data = rows
        return result

    def _apply_filters(self) -> list[dict]:
        rows = self._all_rows
        for field, value in self._filters.items():
            rows = [r for r in rows if r.get(field) == value]
        for field in self._is_null_filters:
            rows = [r for r in rows if r.get(field) is None]
        return rows

    def _clone(self):
        c = _FakeQueryBuilder(self._all_rows)
        c._filters = dict(self._filters)
        c._is_null_filters = set(self._is_null_filters)
        c._inserted = self._inserted
        return c


class _FakeDB:
    """Fake Supabase client，支持多表数据存储"""

    def __init__(self):
        self._tables: dict[str, list[dict]] = {}

    def table(self, name: str) -> _FakeQueryBuilder:
        if name not in self._tables:
            self._tables[name] = []
        return _FakeQueryBuilder(self._tables[name])

    def rpc(self, fn_name: str, params: dict | None = None):
        return MagicMock()

    def get_rows(self, table: str) -> list[dict]:
        return self._tables.get(table, [])


# ── 测试 ─────────────────────────────────────────────────


class TestDualTenantIsolation:
    """双租户数据隔离测试"""

    def setup_method(self):
        self.raw_db = _FakeDB()
        self.db_a = OrgScopedDB(self.raw_db, org_id=ORG_A)
        self.db_b = OrgScopedDB(self.raw_db, org_id=ORG_B)
        self.db_personal = OrgScopedDB(self.raw_db, org_id=None)

    def test_insert_injects_correct_org_id(self):
        """INSERT 自动注入对应的 org_id"""
        self.db_a.table("conversations").insert({"title": "A的对话"}).execute()
        self.db_b.table("conversations").insert({"title": "B的对话"}).execute()
        self.db_personal.table("conversations").insert({"title": "散客对话"}).execute()

        rows = self.raw_db.get_rows("conversations")
        assert len(rows) == 3
        assert rows[0]["org_id"] == ORG_A
        assert rows[1]["org_id"] == ORG_B
        assert rows[2]["org_id"] is None

    def test_select_only_sees_own_org(self):
        """SELECT 只看到自己企业的数据"""
        self.raw_db._tables["messages"] = [
            {"id": "1", "content": "A的消息", "org_id": ORG_A},
            {"id": "2", "content": "B的消息", "org_id": ORG_B},
            {"id": "3", "content": "散客消息", "org_id": None},
        ]

        result_a = self.db_a.table("messages").select("*").execute()
        result_b = self.db_b.table("messages").select("*").execute()
        result_p = self.db_personal.table("messages").select("*").execute()

        assert len(result_a.data) == 1
        assert result_a.data[0]["content"] == "A的消息"

        assert len(result_b.data) == 1
        assert result_b.data[0]["content"] == "B的消息"

        assert len(result_p.data) == 1
        assert result_p.data[0]["content"] == "散客消息"

    def test_org_a_cannot_see_org_b(self):
        """企业 A 看不到企业 B 的数据"""
        self.raw_db._tables["tasks"] = [
            {"id": "t1", "title": "B的任务", "org_id": ORG_B},
        ]

        result = self.db_a.table("tasks").select("*").execute()
        assert len(result.data) == 0

    def test_personal_cannot_see_org(self):
        """散客看不到企业数据"""
        self.raw_db._tables["erp_products"] = [
            {"id": "p1", "outer_id": "A01", "org_id": ORG_A},
        ]

        result = self.db_personal.table("erp_products").select("*").execute()
        assert len(result.data) == 0

    def test_org_cannot_see_personal(self):
        """企业看不到散客数据"""
        self.raw_db._tables["credit_transactions"] = [
            {"id": "ct1", "amount": 100, "org_id": None},
        ]

        result = self.db_a.table("credit_transactions").select("*").execute()
        assert len(result.data) == 0

    def test_upsert_injects_org_id(self):
        """UPSERT 自动注入 org_id"""
        self.db_a.table("erp_products").upsert(
            {"outer_id": "X01", "title": "商品X"},
            on_conflict="outer_id",
        ).execute()

        rows = self.raw_db.get_rows("erp_products")
        assert len(rows) == 1
        assert rows[0]["org_id"] == ORG_A

    def test_upsert_list_all_get_org_id(self):
        """批量 UPSERT 每行都注入 org_id"""
        self.db_b.table("erp_stock_status").upsert(
            [{"sku": "S1"}, {"sku": "S2"}, {"sku": "S3"}],
            on_conflict="sku",
        ).execute()

        rows = self.raw_db.get_rows("erp_stock_status")
        assert len(rows) == 3
        assert all(r["org_id"] == ORG_B for r in rows)

    def test_update_only_affects_own_org(self):
        """UPDATE 自动加 org_id 过滤，只影响自己企业"""
        self.raw_db._tables["tasks"] = [
            {"id": "t1", "status": "pending", "org_id": ORG_A},
            {"id": "t2", "status": "pending", "org_id": ORG_B},
        ]

        # 企业 A 更新 — OrgScopedDB 自动加 org_id=ORG_A 过滤
        result = self.db_a.table("tasks").update({"status": "done"}).execute()
        # 验证过滤后只匹配到 ORG_A 的行
        assert len(result.data) == 1
        assert result.data[0]["org_id"] == ORG_A

    def test_delete_only_affects_own_org(self):
        """DELETE 自动加 org_id 过滤，只删自己企业"""
        self.raw_db._tables["messages"] = [
            {"id": "m1", "org_id": ORG_A},
            {"id": "m2", "org_id": ORG_B},
            {"id": "m3", "org_id": None},
        ]

        # 企业 B 删除 — OrgScopedDB 自动加 org_id=ORG_B 过滤
        result = self.db_b.table("messages").delete().execute()
        # 验证过滤后只匹配到 ORG_B 的行
        assert len(result.data) == 1
        assert result.data[0]["org_id"] == ORG_B


class TestExemptTables:
    """豁免表不受 org_id 过滤"""

    def setup_method(self):
        self.raw_db = _FakeDB()
        self.db = OrgScopedDB(self.raw_db, org_id=ORG_A)

    def test_users_table_not_filtered(self):
        """users 表不在 TENANT_TABLES，不加 org_id 过滤"""
        self.raw_db._tables["users"] = [
            {"id": "u1", "phone": "123"},
            {"id": "u2", "phone": "456"},
        ]

        result = self.db.table("users").select("*").execute()
        assert len(result.data) == 2

    def test_organizations_table_not_filtered(self):
        """organizations 表不加 org_id 过滤"""
        self.raw_db._tables["organizations"] = [
            {"id": ORG_A, "name": "企业A"},
            {"id": ORG_B, "name": "企业B"},
        ]

        result = self.db.table("organizations").select("*").execute()
        assert len(result.data) == 2

    def test_insert_exempt_table_no_injection(self):
        """豁免表 INSERT 不注入 org_id"""
        self.db.table("users").insert({"phone": "789"}).execute()

        rows = self.raw_db.get_rows("users")
        assert len(rows) == 1
        assert "org_id" not in rows[0]


class TestUnscopedAccess:
    """unscoped() 跳过隔离"""

    def setup_method(self):
        self.raw_db = _FakeDB()
        self.db = OrgScopedDB(self.raw_db, org_id=ORG_A)

    def test_unscoped_sees_all_data(self):
        """unscoped() 可以看到所有企业的数据"""
        self.raw_db._tables["messages"] = [
            {"id": "1", "org_id": ORG_A},
            {"id": "2", "org_id": ORG_B},
            {"id": "3", "org_id": None},
        ]

        raw = self.db.unscoped("测试验证")
        result = raw.table("messages").select("*").execute()
        assert len(result.data) == 3


class TestRPCTransparency:
    """RPC 透传验证"""

    def setup_method(self):
        self.raw_db = _FakeDB()
        self.db = OrgScopedDB(self.raw_db, org_id=ORG_A)

    def test_rpc_does_not_inject_org_id(self):
        """RPC 不自动注入 p_org_id"""
        params = {"p_transaction_id": "tx-123"}
        self.db.rpc("atomic_refund_credits", params)
        # 如果自动注入了，params 会多一个 p_org_id key
        assert "p_org_id" not in params


class TestTenantTablesCoverage:
    """TENANT_TABLES 白名单覆盖检查"""

    def test_all_erp_tables_covered(self):
        erp_tables = [t for t in TENANT_TABLES if t.startswith("erp_")]
        assert len(erp_tables) >= 15

    def test_all_wecom_tables_covered(self):
        wecom_tables = [t for t in TENANT_TABLES if t.startswith("wecom_")]
        assert len(wecom_tables) >= 4

    def test_core_tables_covered(self):
        for t in ("conversations", "messages", "tasks",
                   "credits_history", "credit_transactions"):
            assert t in TENANT_TABLES

    def test_knowledge_tables_covered(self):
        for t in ("knowledge_nodes", "knowledge_metrics",
                   "knowledge_edges", "scoring_audit_log"):
            assert t in TENANT_TABLES
