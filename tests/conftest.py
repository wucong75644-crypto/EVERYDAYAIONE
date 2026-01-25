"""
Pytest 共享 fixtures

提供测试所需的 mock 对象和通用配置。
"""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# 将 backend 目录添加到 Python 路径
backend_path = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(backend_path))


class MockQueryBuilder:
    """模拟 Supabase 查询构建器"""

    def __init__(self, data: list | None = None, count: int | None = None):
        self._data = data or []
        self._count = count

    def select(self, *args: Any, **kwargs: Any) -> "MockQueryBuilder":
        return self

    def insert(self, data: dict) -> "MockQueryBuilder":
        self._data = [{**data, "id": "test-uuid-123", "created_at": "2026-01-25T00:00:00Z", "updated_at": "2026-01-25T00:00:00Z"}]
        return self

    def update(self, data: dict) -> "MockQueryBuilder":
        if self._data:
            self._data[0].update(data)
        return self

    def delete(self) -> "MockQueryBuilder":
        return self

    def eq(self, column: str, value: Any) -> "MockQueryBuilder":
        return self

    def lt(self, column: str, value: Any) -> "MockQueryBuilder":
        return self

    def order(self, column: str, desc: bool = False) -> "MockQueryBuilder":
        return self

    def limit(self, count: int) -> "MockQueryBuilder":
        return self

    def offset(self, count: int) -> "MockQueryBuilder":
        return self

    def range(self, start: int, end: int) -> "MockQueryBuilder":
        return self

    def single(self) -> "MockQueryBuilder":
        return self

    def execute(self) -> MagicMock:
        result = MagicMock()
        result.data = self._data
        result.count = self._count
        return result


class MockSupabaseClient:
    """模拟 Supabase 客户端"""

    def __init__(self) -> None:
        self._tables: dict[str, list] = {}

    def table(self, name: str) -> MockQueryBuilder:
        return MockQueryBuilder(self._tables.get(name, []))

    def set_table_data(self, name: str, data: list) -> None:
        """设置表数据（用于测试）"""
        self._tables[name] = data


@pytest.fixture
def mock_db() -> MockSupabaseClient:
    """提供模拟的数据库客户端"""
    return MockSupabaseClient()


@pytest.fixture
def mock_user() -> dict:
    """提供测试用户数据"""
    return {
        "id": "user-123",
        "phone": "13800138000",
        "nickname": "测试用户",
        "avatar_url": None,
        "role": "user",
        "credits": 100,
        "status": "active",
        "password_hash": "$2b$12$test_hash",
        "login_methods": ["phone"],
        "created_at": "2026-01-25T00:00:00Z",
        "last_login_at": None,
    }


@pytest.fixture
def mock_conversation() -> dict:
    """提供测试对话数据"""
    return {
        "id": "conv-123",
        "user_id": "user-123",
        "title": "测试对话",
        "model_id": "gemini-3-flash",
        "message_count": 0,
        "credits_consumed": 0,
        "last_message_preview": None,
        "created_at": "2026-01-25T00:00:00Z",
        "updated_at": "2026-01-25T00:00:00Z",
    }


@pytest.fixture
def mock_message() -> dict:
    """提供测试消息数据"""
    return {
        "id": "msg-123",
        "conversation_id": "conv-123",
        "role": "user",
        "content": "测试消息内容",
        "credits_cost": 0,
        "image_url": None,
        "video_url": None,
        "created_at": "2026-01-25T00:00:00Z",
    }
