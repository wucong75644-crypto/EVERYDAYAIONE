"""
OrgContext 依赖注入 + 数据隔离工具函数测试

覆盖:
- get_org_context: 散客/企业/异常场景
- apply_data_isolation: 散客/企业过滤
- apply_org_filter: 纯企业维度过滤
- get_mem0_user_id: 记忆系统 user_id 生成
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from api.deps import OrgContext, get_org_context
from core.data_isolation import (
    apply_data_isolation,
    apply_org_filter,
    get_mem0_user_id,
    get_org_id_for_insert,
)


# ── Fixtures ────────────────────────────────────────────────


class FakeQueryBuilder:
    """记录链式调用的 mock query builder"""

    def __init__(self):
        self._calls: list[tuple[str, tuple]] = []
        self._data = None
        self._is_single = False

    def select(self, *args, **kwargs):
        self._calls.append(("select", args))
        return self

    def eq(self, col, val):
        self._calls.append(("eq", (col, val)))
        return self

    def is_(self, col, val):
        self._calls.append(("is_", (col, val)))
        return self

    def single(self):
        self._is_single = True
        return self

    def maybe_single(self):
        self._is_single = True
        return self

    def execute(self):
        result = MagicMock()
        if self._is_single:
            result.data = self._data[0] if self._data else None
        else:
            result.data = self._data or []
        return result

    def set_data(self, data):
        if isinstance(data, dict):
            self._data = [data]
        else:
            self._data = data
        return self


class FakeDB:
    def __init__(self):
        self._tables: dict[str, list[FakeQueryBuilder]] = {}

    def add_table(self, name: str, data=None) -> FakeQueryBuilder:
        builder = FakeQueryBuilder()
        if data is not None:
            builder.set_data(data)
        if name not in self._tables:
            self._tables[name] = []
        self._tables[name].append(builder)
        return builder

    def table(self, name: str):
        builders = self._tables.get(name, [])
        if builders:
            return builders.pop(0)
        return FakeQueryBuilder()


def _make_request(org_id: str | None = None) -> MagicMock:
    """创建带/不带 X-Org-Id 的 mock request"""
    request = MagicMock()
    headers = {}
    if org_id is not None:
        headers["X-Org-Id"] = org_id
    request.headers = headers
    return request


# ── get_org_context 测试 ───────────────────────────────────


class TestGetOrgContext:

    @pytest.mark.asyncio
    async def test_no_header_returns_personal(self):
        """无 X-Org-Id → 散客模式"""
        request = _make_request(org_id=None)
        ctx = await get_org_context(request, "user-1", FakeDB())
        assert ctx.user_id == "user-1"
        assert ctx.org_id is None
        assert ctx.org_role is None

    @pytest.mark.asyncio
    async def test_invalid_uuid_raises_400(self):
        """非法 UUID → 400"""
        request = _make_request(org_id="not-a-uuid")
        with pytest.raises(HTTPException) as exc_info:
            await get_org_context(request, "user-1", FakeDB())
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_org_not_found_raises_403(self):
        """企业不存在 → 403"""
        db = FakeDB()
        db.add_table("organizations", data=None)

        request = _make_request(org_id="00000000-0000-0000-0000-000000000001")
        with pytest.raises(HTTPException) as exc_info:
            await get_org_context(request, "user-1", db)
        assert exc_info.value.status_code == 403
        assert "无权访问" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_suspended_org_raises_403(self):
        """企业已停用 → 403"""
        db = FakeDB()
        db.add_table("organizations", data={"status": "suspended"})

        request = _make_request(org_id="00000000-0000-0000-0000-000000000001")
        with pytest.raises(HTTPException) as exc_info:
            await get_org_context(request, "user-1", db)
        assert exc_info.value.status_code == 403
        assert "无权访问" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_not_member_raises_403(self):
        """不是成员 → 403"""
        db = FakeDB()
        db.add_table("organizations", data={"status": "active"})
        db.add_table("org_members", data=None)

        request = _make_request(org_id="00000000-0000-0000-0000-000000000001")
        with pytest.raises(HTTPException) as exc_info:
            await get_org_context(request, "user-1", db)
        assert exc_info.value.status_code == 403
        assert "无权访问" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_disabled_member_raises_403(self):
        """成员已禁用 → 403"""
        db = FakeDB()
        db.add_table("organizations", data={"status": "active"})
        db.add_table("org_members", data={"role": "member", "status": "disabled"})

        request = _make_request(org_id="00000000-0000-0000-0000-000000000001")
        with pytest.raises(HTTPException) as exc_info:
            await get_org_context(request, "user-1", db)
        assert exc_info.value.status_code == 403
        assert "无权访问" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_valid_member_returns_context(self):
        """正常企业成员 → 返回完整上下文"""
        db = FakeDB()
        db.add_table("organizations", data={"status": "active"})
        db.add_table("org_members", data={"role": "admin", "status": "active"})

        org_id = "00000000-0000-0000-0000-000000000001"
        request = _make_request(org_id=org_id)
        ctx = await get_org_context(request, "user-1", db)

        assert ctx.user_id == "user-1"
        assert ctx.org_id == org_id
        assert ctx.org_role == "admin"

    @pytest.mark.asyncio
    async def test_owner_role(self):
        """owner 角色正确返回"""
        db = FakeDB()
        db.add_table("organizations", data={"status": "active"})
        db.add_table("org_members", data={"role": "owner", "status": "active"})

        org_id = "00000000-0000-0000-0000-000000000001"
        request = _make_request(org_id=org_id)
        ctx = await get_org_context(request, "owner-1", db)

        assert ctx.org_role == "owner"


# ── apply_data_isolation 测试 ──────────────────────────────


class TestApplyDataIsolation:

    def test_personal_mode(self):
        """散客：添加 org_id IS NULL + user_id 过滤"""
        ctx = OrgContext(user_id="u1", org_id=None, org_role=None)
        query = FakeQueryBuilder()
        result = apply_data_isolation(query, ctx)

        calls = result._calls
        assert ("is_", ("org_id", "null")) in calls
        assert ("eq", ("user_id", "u1")) in calls

    def test_org_mode(self):
        """企业：添加 org_id + user_id 过滤（各看各的）"""
        ctx = OrgContext(user_id="u1", org_id="org-1", org_role="member")
        query = FakeQueryBuilder()
        result = apply_data_isolation(query, ctx)

        calls = result._calls
        assert ("eq", ("org_id", "org-1")) in calls
        assert ("eq", ("user_id", "u1")) in calls


# ── apply_org_filter 测试 ──────────────────────────────────


class TestApplyOrgFilter:

    def test_personal_mode(self):
        """散客：只过滤 org_id IS NULL"""
        ctx = OrgContext(user_id="u1", org_id=None, org_role=None)
        query = FakeQueryBuilder()
        result = apply_org_filter(query, ctx)

        calls = result._calls
        assert ("is_", ("org_id", "null")) in calls
        assert not any(c[1][0] == "user_id" for c in calls if c[0] == "eq")

    def test_org_mode(self):
        """企业：过滤 org_id"""
        ctx = OrgContext(user_id="u1", org_id="org-1", org_role="admin")
        query = FakeQueryBuilder()
        result = apply_org_filter(query, ctx)

        calls = result._calls
        assert ("eq", ("org_id", "org-1")) in calls


# ── get_org_id_for_insert 测试 ─────────────────────────────


class TestGetOrgIdForInsert:

    def test_personal(self):
        ctx = OrgContext(user_id="u1")
        assert get_org_id_for_insert(ctx) is None

    def test_org(self):
        ctx = OrgContext(user_id="u1", org_id="org-1", org_role="member")
        assert get_org_id_for_insert(ctx) == "org-1"


# ── get_mem0_user_id 测试 ──────────────────────────────────


class TestGetMem0UserId:

    def test_personal(self):
        ctx = OrgContext(user_id="u1")
        assert get_mem0_user_id(ctx) == "personal:u1"

    def test_org(self):
        ctx = OrgContext(user_id="u1", org_id="org-1", org_role="member")
        assert get_mem0_user_id(ctx) == "org_org-1:u1"
