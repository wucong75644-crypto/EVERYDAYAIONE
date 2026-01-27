"""
pytest 配置和公共 fixtures

提供测试所需的 mock 对象和工具函数。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from uuid import uuid4


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
        self._select_fields = "*"

    def select(self, fields: str = "*"):
        self._select_fields = fields
        return self

    def insert(self, data: dict | list):
        if isinstance(data, dict):
            data = [data]
        self._data.extend(data)
        return self

    def update(self, data: dict):
        self._update_data = data
        return self

    def delete(self):
        return self

    def eq(self, field: str, value):
        self._filters[field] = value
        return self

    def single(self):
        self._single = True
        return self

    def execute(self):
        """执行查询并返回结果"""
        result = MagicMock()

        # 根据过滤条件筛选数据
        filtered = self._data
        for field, value in self._filters.items():
            filtered = [d for d in filtered if d.get(field) == value]

        if hasattr(self, '_single') and self._single:
            result.data = filtered[0] if filtered else None
        else:
            result.data = filtered

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


class MockAsyncSupabaseClient:
    """Mock 异步 Supabase 客户端"""

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
        """Mock RPC 调用"""
        mock = AsyncMock()
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

    def select(self, fields: str = "*"):
        self._select_fields = fields
        return self

    def insert(self, data: dict | list):
        if isinstance(data, dict):
            data = [data]
        self._data.extend(data)
        return self

    def update(self, data: dict):
        self._update_data = data
        return self

    def delete(self):
        return self

    def eq(self, field: str, value):
        self._filters[field] = value
        return self

    def single(self):
        self._single = True
        return self

    async def execute(self):
        """异步执行查询"""
        result = MagicMock()

        filtered = self._data
        for field, value in self._filters.items():
            filtered = [d for d in filtered if d.get(field) == value]

        if self._single:
            result.data = filtered[0] if filtered else None
        else:
            result.data = filtered

        return result


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
