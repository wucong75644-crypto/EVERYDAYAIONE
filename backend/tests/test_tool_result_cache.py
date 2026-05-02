"""测试 tool_result_cache — 会话级 TTL 缓存（含 AgentResult 支持）"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest

from services.agent.agent_result import AgentResult
from services.agent.tool_result_cache import ToolResultCache


@pytest.fixture
def cache():
    """创建 cache 实例，mock is_cacheable 始终返回 True"""
    c = ToolResultCache()
    with patch.object(ToolResultCache, "is_cacheable", return_value=True):
        yield c


class TestPutAndGet:
    """存取基本行为"""

    def test_str_round_trip(self, cache):
        cache.put("data_query", {"file": "x.parquet"}, "hello")
        assert cache.get("data_query", {"file": "x.parquet"}) == "hello"

    def test_agent_result_round_trip(self, cache):
        r = AgentResult(summary="查询到5条", status="success")
        cache.put("data_query", {"sql": "SELECT 1"}, r)
        assert cache.get("data_query", {"sql": "SELECT 1"}) is r

    def test_agent_result_error_cached(self, cache):
        r = AgentResult(summary="SQL错误", status="error", error_message="bad")
        cache.put("data_query", {"sql": "bad"}, r)
        cached = cache.get("data_query", {"sql": "bad"})
        assert cached is r
        assert cached.is_failure

    def test_cache_miss_returns_none(self, cache):
        assert cache.get("data_query", {"file": "not_stored"}) is None

    def test_different_args_different_entries(self, cache):
        cache.put("data_query", {"sql": "a"}, "result_a")
        cache.put("data_query", {"sql": "b"}, "result_b")
        assert cache.get("data_query", {"sql": "a"}) == "result_a"
        assert cache.get("data_query", {"sql": "b"}) == "result_b"


class TestSizeLimits:
    """大小限制"""

    def test_large_str_not_cached(self, cache):
        big = "x" * 9000
        cache.put("data_query", {"k": "big"}, big)
        assert cache.get("data_query", {"k": "big"}) is None

    def test_large_agent_result_not_cached(self, cache):
        big = AgentResult(summary="x" * 9000, status="success")
        cache.put("data_query", {"k": "big_ar"}, big)
        assert cache.get("data_query", {"k": "big_ar"}) is None

    def test_small_agent_result_cached(self, cache):
        small = AgentResult(summary="x" * 100, status="success")
        cache.put("data_query", {"k": "small"}, small)
        assert cache.get("data_query", {"k": "small"}) is small

    def test_unknown_type_not_cached(self, cache):
        cache.put("data_query", {"k": "list"}, [1, 2, 3])
        assert cache.get("data_query", {"k": "list"}) is None


class TestTTL:
    """TTL 过期"""

    def test_expired_entry_returns_none(self, cache):
        cache.put("data_query", {"k": "ttl"}, "val")
        # 伪造过期：直接修改时间戳
        key = cache._key("data_query", {"k": "ttl"})
        cache._store[key] = ("val", time.monotonic() - 400)
        assert cache.get("data_query", {"k": "ttl"}) is None
        # 过期条目被删除
        assert key not in cache._store

    def test_fresh_entry_returns_value(self, cache):
        cache.put("data_query", {"k": "fresh"}, "val")
        assert cache.get("data_query", {"k": "fresh"}) == "val"


class TestCapacity:
    """条目上限"""

    def test_max_entries_reached_skips_new(self, cache):
        # 填满缓存
        for i in range(ToolResultCache._CACHE_MAX_ENTRIES):
            cache.put("data_query", {"i": i}, f"val_{i}")
        # 超出上限的条目不存
        cache.put("data_query", {"i": 999}, "overflow")
        assert cache.get("data_query", {"i": 999}) is None


class TestCacheability:
    """is_cacheable 过滤"""

    def test_non_cacheable_tool_not_stored(self):
        cache = ToolResultCache()
        with patch.object(ToolResultCache, "is_cacheable", return_value=False):
            cache.put("erp_execute", {"action": "write"}, "result")
            assert cache.get("erp_execute", {"action": "write"}) is None
