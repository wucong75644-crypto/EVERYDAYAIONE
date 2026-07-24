"""Shared pytest fixtures for knowledge service tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture(autouse=True)
def reset_kb_globals():
    """每个测试前重置知识库全局状态"""
    import services.knowledge_config as cfg

    cfg._pg_pool = None
    cfg._kb_available = None
    cfg._search_cache.clear()
    yield
    cfg._pg_pool = None
    cfg._kb_available = None
    cfg._search_cache.clear()


@pytest.fixture
def mock_settings():
    """Mock 配置（知识库开启）"""
    with patch("services.knowledge_config.settings") as mock_s:
        mock_s.kb_enabled = True
        mock_s.kb_extraction_model = "qwen-turbo"
        mock_s.kb_extraction_fallback_model = "qwen-plus"
        mock_s.kb_extraction_timeout = 3.0
        mock_s.kb_search_limit = 5
        mock_s.kb_search_threshold = 0.5
        mock_s.kb_max_nodes = 5000
        mock_s.kb_cache_ttl = 600
        mock_s.kb_confidence_boost = 0.1
        mock_s.kb_confidence_decay_days = 30
        mock_s.database_url = "postgresql://test"
        mock_s.dashscope_api_key = "test-key"
        mock_s.dashscope_base_url = "https://test.api.com"
        yield mock_s


@pytest.fixture
def mock_cursor():
    """Mock psycopg 异步游标"""
    cursor = AsyncMock()
    cursor.execute = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=None)
    cursor.fetchall = AsyncMock(return_value=[])
    cursor.description = []
    return cursor


@pytest.fixture
def mock_conn(mock_cursor):
    """Mock psycopg 异步连接（context manager）"""
    conn = AsyncMock()
    conn.cursor = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_cursor),
        __aexit__=AsyncMock(return_value=False),
    ))
    conn.commit = AsyncMock()
    return conn


@pytest.fixture
def mock_pg_connection(mock_conn):
    """Mock get_pg_connection 返回 context manager"""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx
