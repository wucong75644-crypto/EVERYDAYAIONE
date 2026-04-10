"""
pytest 配置和公共 fixtures

提供测试所需的 mock 对象和工具函数。
"""

import pytest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo


# ============ 时间事实层 fixtures ============
# 设计文档：docs/document/TECH_ERP时间准确性架构.md §8.4

_CN_TZ_TEST = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def freeze_2026_04_10():
    """Freeze 在 2026-04-10 13:05 周五（4-10 bug 复现时刻）。

    使用 time-machine（asyncio + zoneinfo + Pydantic v2 兼容）。
    """
    import time_machine
    with time_machine.travel(
        datetime(2026, 4, 10, 13, 5, tzinfo=_CN_TZ_TEST),
        tick=False,
    ):
        yield


@pytest.fixture
def freeze_spring_festival_2026():
    """Freeze 在 2026 年春节当天（2026-02-17 周二）。"""
    import time_machine
    with time_machine.travel(
        datetime(2026, 2, 17, 9, 0, tzinfo=_CN_TZ_TEST),
        tick=False,
    ):
        yield


@pytest.fixture
def request_ctx_2026_04_10():
    """构造 4-10 时刻的 RequestContext（不依赖系统时钟）。"""
    from utils.time_context import RequestContext, TimePoint
    now = datetime(2026, 4, 10, 13, 5, tzinfo=_CN_TZ_TEST)
    return RequestContext(
        now=now,
        today=TimePoint.from_datetime(now, reference=now),
        user_id="test_user",
        org_id="test_org",
        request_id="test_req",
    )


# ============ Mock 数据 ============

def create_test_user(
    user_id: str = None,
    phone: str = "13800138000",
    nickname: str = "测试用户",
    credits: int = 100,
    status: str = "active",
    role: str = "user",
    password_hash: str = None,
) -> dict:
    """创建测试用户数据"""
    return {
        "id": user_id or str(uuid4()),
        "phone": phone,
        "nickname": nickname,
        "credits": credits,
        "status": status,
        "role": role,
        "password_hash": password_hash,
        "avatar_url": None,
        "login_methods": ["phone"],
        "created_by": "phone",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }


def create_test_message(
    message_id: str = None,
    conversation_id: str = None,
    role: str = "user",
    content: str = "测试消息",
    credits_cost: int = 0,
) -> dict:
    """创建测试消息数据"""
    return {
        "id": message_id or str(uuid4()),
        "conversation_id": conversation_id or str(uuid4()),
        "role": role,
        "content": content,
        "image_url": None,
        "video_url": None,
        "credits_cost": credits_cost,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def create_test_conversation(
    conversation_id: str = None,
    user_id: str = None,
    title: str = "测试对话",
) -> dict:
    """创建测试对话数据"""
    return {
        "id": conversation_id or str(uuid4()),
        "user_id": user_id or str(uuid4()),
        "title": title,
        "model_id": "gpt-4",
        "last_message": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# ============ Mock Supabase Client ============

class MockSupabaseTable:
    """Mock Supabase 表操作"""

    def __init__(self, data: list = None):
        self._data = data or []
        self._filters = {}
        self._in_filters = {}
        self._ilike_filters = {}
        self._gte_filters = {}
        self._lt_filters = {}
        self._lte_filters = {}
        self._or_filters = []
        self._select_fields = "*"
        self._count_mode = None
        self._limit = None
        self._single = False
        self._is_delete = False

    def select(self, fields: str = "*", count: str | None = None):
        # 重置查询状态（每次 select 开始新查询链）
        self._filters = {}
        self._in_filters = {}
        self._ilike_filters = {}
        self._gte_filters = {}
        self._lt_filters = {}
        self._lte_filters = {}
        self._or_filters = []
        self._select_fields = fields
        self._count_mode = count
        self._limit = None
        self._single = False
        return self

    def insert(self, data: dict | list):
        if isinstance(data, dict):
            data = [data]
        self._data.extend(data)
        return self

    def upsert(self, data: list | dict, on_conflict: str = ""):
        """UPSERT（简单实现：追加/覆盖）"""
        if isinstance(data, dict):
            data = [data]
        self._data.extend(data)
        return self

    def update(self, data: dict):
        self._update_data = data
        return self

    def delete(self):
        self._is_delete = True
        return self

    def eq(self, field: str, value):
        self._filters[field] = value
        return self

    def in_(self, field: str, values: list):
        self._in_filters[field] = values
        return self

    def ilike(self, field: str, pattern: str):
        """ILIKE 模糊匹配（去掉 % 后做 case-insensitive 子串匹配）"""
        self._ilike_filters[field] = pattern.strip("%").lower()
        return self

    def gte(self, field: str, value):
        """大于等于"""
        self._gte_filters[field] = value
        return self

    def lt(self, field: str, value):
        """小于"""
        self._lt_filters[field] = value
        return self

    def lte(self, field: str, value):
        """小于等于"""
        self._lte_filters[field] = value
        return self

    def is_(self, field: str, value: str):
        """IS NULL 过滤：.is_("org_id", "null") → WHERE org_id IS NULL"""
        if value == "null" or value is None:
            self._filters[field] = None
        return self

    def or_(self, filter_str: str):
        """OR 过滤（简单实现：解析 field.eq.value 格式）"""
        self._or_filters.append(filter_str)
        return self

    def order(self, column: str, **kwargs):
        """排序（mock 实现，不实际排序，依赖测试数据顺序）"""
        return self

    def limit(self, count: int):
        """限制结果数量"""
        self._limit = count
        return self

    def range(self, start: int, end: int):
        """分页范围（简化实现：转为 offset + limit）"""
        self._limit = end - start + 1
        return self

    def single(self):
        self._single = True
        return self

    def maybe_single(self):
        """返回单个结果或 None（类似 single 但不抛异常）"""
        self._single = True
        return self

    def _apply_filters(self, data: list) -> list:
        """应用所有过滤条件，返回匹配的行"""
        filtered = data
        for field, value in self._filters.items():
            filtered = [d for d in filtered if d.get(field) == value]

        for field, values in self._in_filters.items():
            filtered = [d for d in filtered if d.get(field) in values]

        for field, substr in self._ilike_filters.items():
            filtered = [
                d for d in filtered
                if substr in str(d.get(field, "")).lower()
            ]

        for field, value in self._gte_filters.items():
            filtered = [
                d for d in filtered if str(d.get(field, "")) >= str(value)
            ]
        for field, value in self._lt_filters.items():
            filtered = [
                d for d in filtered if str(d.get(field, "")) < str(value)
            ]
        for field, value in self._lte_filters.items():
            filtered = [
                d for d in filtered if str(d.get(field, "")) <= str(value)
            ]

        for or_str in self._or_filters:
            parts = or_str.split(",")
            or_matched = []
            for row in filtered:
                for part in parts:
                    segs = part.split(".")
                    if len(segs) >= 3 and segs[1] == "eq":
                        if str(row.get(segs[0], "")) == segs[2]:
                            or_matched.append(row)
                            break
            filtered = or_matched

        return filtered

    def execute(self):
        """执行查询并返回结果"""
        result = MagicMock()
        filtered = self._apply_filters(self._data)

        # DELETE 操作：从 _data 中移除匹配行
        if self._is_delete:
            self._data = [d for d in self._data if d not in filtered]
            result.data = filtered
            self._is_delete = False
            return result

        # 应用 limit
        if self._limit is not None:
            filtered = filtered[:self._limit]

        if self._single:
            result.data = filtered[0] if filtered else None
        else:
            result.data = filtered

        # count 模式
        if self._count_mode == "exact":
            result.count = len(filtered)

        return result


class MockSupabaseClient:
    """Mock Supabase 客户端"""

    def __init__(self):
        self._tables = {}
        self._rpc_results = {}

    def table(self, name: str) -> MockSupabaseTable:
        if name not in self._tables:
            self._tables[name] = MockSupabaseTable()
        return self._tables[name]

    def set_table_data(self, name: str, data: list):
        """设置表的初始数据"""
        self._tables[name] = MockSupabaseTable(data)

    def rpc(self, fn_name: str, params: dict = None):
        """Mock RPC 调用"""
        mock = MagicMock()
        if fn_name in self._rpc_results:
            mock.execute.return_value.data = self._rpc_results[fn_name]
        else:
            mock.execute.return_value.data = {"success": True, "new_balance": 90}
        return mock

    def set_rpc_result(self, fn_name: str, result: dict):
        """设置 RPC 返回值"""
        self._rpc_results[fn_name] = result


class MockAsyncRpcCaller:
    """Mock 异步 RPC 调用器"""

    def __init__(self, data: Any = None):
        self._data = data

    async def execute(self):
        result = MagicMock()
        result.data = self._data
        return result


class MockAsyncSupabaseClient:
    """Mock 异步 Supabase 客户端（同步 execute，兼容 CreditService 等）"""

    def __init__(self):
        self._tables = {}
        self._rpc_results = {}

    def table(self, name: str):
        if name not in self._tables:
            self._tables[name] = MockAsyncSupabaseTable()
        return self._tables[name]

    def set_table_data(self, name: str, data: list):
        """设置表的初始数据"""
        self._tables[name] = MockAsyncSupabaseTable(data)

    def rpc(self, fn_name: str, params: dict = None):
        """Mock RPC 调用（同步 execute）"""
        mock = MagicMock()
        if fn_name in self._rpc_results:
            mock.execute.return_value.data = self._rpc_results[fn_name]
        else:
            mock.execute.return_value.data = {"success": True, "new_balance": 90}
        return mock

    def set_rpc_result(self, fn_name: str, result: dict):
        """设置 RPC 返回值"""
        self._rpc_results[fn_name] = result


class MockAsyncSupabaseTable:
    """Mock 异步 Supabase 表操作"""

    def __init__(self, data: list = None):
        self._data = data or []
        self._filters = {}
        self._select_fields = "*"
        self._single = False

    def select(self, fields: str = "*", count: str | None = None):
        self._filters = {}
        self._select_fields = fields
        self._count_mode = count
        self._single = False
        # Reset optional filter dicts
        if hasattr(self, '_neq_filters'):
            del self._neq_filters
        if hasattr(self, '_in_filters'):
            del self._in_filters
        if hasattr(self, '_lt_filters'):
            del self._lt_filters
        if hasattr(self, '_gte_filters'):
            del self._gte_filters
        if hasattr(self, '_lte_filters'):
            del self._lte_filters
        if hasattr(self, '_offset'):
            del self._offset
        if hasattr(self, '_limit'):
            del self._limit
        return self

    def insert(self, data: dict | list):
        if isinstance(data, dict):
            data = [data]
        self._data.extend(data)
        return self

    def upsert(self, data: list | dict, on_conflict: str = ""):
        """UPSERT（简单实现：追加/覆盖）"""
        if isinstance(data, dict):
            data = [data]
        self._data.extend(data)
        return self

    def update(self, data: dict):
        self._update_data = data
        return self

    def delete(self):
        self._is_delete = True
        return self

    def eq(self, field: str, value):
        self._filters[field] = value
        return self

    def neq(self, field: str, value):
        """不等于过滤"""
        if not hasattr(self, '_neq_filters'):
            self._neq_filters = {}
        self._neq_filters[field] = value
        return self

    def in_(self, field: str, values: list):
        """IN 过滤"""
        if not hasattr(self, '_in_filters'):
            self._in_filters = {}
        self._in_filters[field] = values
        return self

    def lt(self, field: str, value):
        """小于过滤"""
        if not hasattr(self, '_lt_filters'):
            self._lt_filters = {}
        self._lt_filters[field] = value
        return self

    def gte(self, field: str, value):
        """大于等于过滤"""
        if not hasattr(self, '_gte_filters'):
            self._gte_filters = {}
        self._gte_filters[field] = value
        return self

    def lte(self, field: str, value):
        """小于等于过滤"""
        if not hasattr(self, '_lte_filters'):
            self._lte_filters = {}
        self._lte_filters[field] = value
        return self

    def range(self, start: int, end: int):
        """范围过滤（offset + limit 语义）"""
        self._offset = start
        self._limit = end - start + 1
        return self

    @property
    def not_(self):
        """返回 self 用于链式调用（如 .not_.is_()）"""
        return self

    def is_(self, field: str, value: str):
        """IS 过滤（简化实现，配合 not_ 使用时忽略）"""
        return self

    def single(self):
        self._single = True
        return self

    def maybe_single(self):
        """返回单个结果或 None（类似 single 但不抛异常）"""
        self._single = True
        return self

    def order(self, column: str, **kwargs):
        """排序（mock 实现）"""
        return self

    def limit(self, count: int):
        """限制结果数量"""
        self._limit = count
        return self

    def offset(self, count: int):
        """偏移量"""
        self._offset = count
        return self

    def _execute_impl(self):
        """执行查询的核心逻辑（同步/异步共用）"""
        result = MagicMock()

        filtered = self._data
        for field, value in self._filters.items():
            filtered = [d for d in filtered if d.get(field) == value]

        # DELETE 操作：从 _data 中移除匹配行
        if getattr(self, '_is_delete', False):
            # 应用 in_ 过滤
            if hasattr(self, '_in_filters'):
                for field, values in self._in_filters.items():
                    filtered = [d for d in filtered if d.get(field) in values]
            self._data = [d for d in self._data if d not in filtered]
            result.data = filtered
            self._is_delete = False
            return result

        # 应用 neq 过滤
        if hasattr(self, '_neq_filters'):
            for field, value in self._neq_filters.items():
                filtered = [d for d in filtered if d.get(field) != value]

        # 应用 in_ 过滤
        if hasattr(self, '_in_filters'):
            for field, values in self._in_filters.items():
                filtered = [d for d in filtered if d.get(field) in values]

        # 应用 lt 过滤
        if hasattr(self, '_lt_filters'):
            for field, value in self._lt_filters.items():
                filtered = [
                    d for d in filtered if str(d.get(field, "")) < str(value)
                ]

        # 应用 gte 过滤
        if hasattr(self, '_gte_filters'):
            for field, value in self._gte_filters.items():
                filtered = [
                    d for d in filtered if str(d.get(field, "")) >= str(value)
                ]

        # 应用 offset 和 limit
        if hasattr(self, '_offset'):
            filtered = filtered[self._offset:]
        if hasattr(self, '_limit'):
            filtered = filtered[:self._limit]

        if self._single:
            result.data = filtered[0] if filtered else None
        else:
            result.data = filtered

        return result

    def execute(self):
        """执行查询（同步，兼容 CreditService 等同步调用方）"""
        return self._execute_impl()


# ============ ERP Async Mock（AsyncLocalDBClient 兼容，execute 是 async）============


class MockErpAsyncTable(MockAsyncSupabaseTable):
    """ERP 专用 async mock table — execute() 是 async"""

    async def execute(self):
        return self._execute_impl()


class MockErpAsyncDBClient:
    """ERP 专用 async mock DB client — 模拟 AsyncLocalDBClient"""

    def __init__(self):
        self._tables: dict[str, MockErpAsyncTable] = {}
        self._rpc_results: dict[str, Any] = {}

    def table(self, name: str) -> MockErpAsyncTable:
        if name not in self._tables:
            self._tables[name] = MockErpAsyncTable()
        return self._tables[name]

    def set_table_data(self, name: str, data: list):
        self._tables[name] = MockErpAsyncTable(data)

    def rpc(self, fn_name: str, params: dict = None) -> MockAsyncRpcCaller:
        if fn_name in self._rpc_results:
            return MockAsyncRpcCaller(self._rpc_results[fn_name])
        return MockAsyncRpcCaller({"success": True})

    def set_rpc_result(self, fn_name: str, result):
        self._rpc_results[fn_name] = result


# ============ Fixtures ============

@pytest.fixture
def mock_db():
    """Mock 同步 Supabase 客户端"""
    return MockSupabaseClient()


@pytest.fixture
def mock_async_db():
    """Mock 异步 Supabase 客户端"""
    return MockAsyncSupabaseClient()


@pytest.fixture
def mock_redis():
    """Mock Redis 客户端"""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    return redis


@pytest.fixture
def test_user():
    """测试用户数据"""
    return create_test_user()


@pytest.fixture
def test_user_with_password():
    """带密码的测试用户"""
    # bcrypt hash for "password123"
    password_hash = "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.G9HwI0Pv0K2L4K"
    return create_test_user(password_hash=password_hash)


@pytest.fixture
def mock_sms_service():
    """Mock 短信服务"""
    with patch("services.auth_service.get_sms_service") as mock:
        sms = AsyncMock()
        sms.send_verification_code = AsyncMock(return_value=True)
        sms.verify_code = AsyncMock(return_value=True)
        mock.return_value = sms
        yield sms


@pytest.fixture
def mock_settings():
    """Mock 配置"""
    with patch("services.auth_service.get_settings") as mock:
        settings = MagicMock()
        settings.jwt_access_token_expire_minutes = 1440
        settings.jwt_secret_key = "test-secret-key"
        settings.jwt_algorithm = "HS256"
        mock.return_value = settings
        yield settings
