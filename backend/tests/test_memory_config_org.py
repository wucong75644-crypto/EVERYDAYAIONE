"""
memory_config 多租户缓存隔离测试

覆盖：_cache_key org_id 隔离、缓存 CRUD 带 org_id
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import time
from unittest.mock import patch

import pytest

from services.memory_config import (
    _cache_key,
    _get_cached_memories,
    _invalidate_cache,
    _memory_cache,
    _set_cached_memories,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """每个测试前清空缓存"""
    _memory_cache.clear()
    yield
    _memory_cache.clear()


# ── _cache_key 测试 ──────────────────────────────────


class TestCacheKey:
    def test_personal_when_no_org(self):
        """散客 → personal:user_id"""
        assert _cache_key("u1") == "personal:u1"
        assert _cache_key("u1", org_id=None) == "personal:u1"

    def test_org_when_org_provided(self):
        """企业用户 → org_id:user_id"""
        assert _cache_key("u1", org_id="org-abc") == "org-abc:u1"

    def test_different_orgs_different_keys(self):
        """不同企业 → 不同 key"""
        assert _cache_key("u1", org_id="org-a") != _cache_key("u1", org_id="org-b")

    def test_same_user_different_scope(self):
        """同一用户散客 vs 企业 → 不同 key"""
        assert _cache_key("u1") != _cache_key("u1", org_id="org-a")


# ── 缓存 CRUD 带 org_id ──────────────────────────────


class TestCacheWithOrgId:
    def test_set_and_get_personal(self):
        """散客缓存读写"""
        data = [{"id": "m1", "memory": "test"}]
        _set_cached_memories("u1", data)
        assert _get_cached_memories("u1") == data

    def test_set_and_get_org(self):
        """企业缓存读写"""
        data = [{"id": "m2", "memory": "org test"}]
        _set_cached_memories("u1", data, org_id="org-x")
        assert _get_cached_memories("u1", org_id="org-x") == data

    def test_org_isolation(self):
        """不同 org 的缓存互不干扰"""
        data_a = [{"memory": "org-a"}]
        data_b = [{"memory": "org-b"}]
        _set_cached_memories("u1", data_a, org_id="org-a")
        _set_cached_memories("u1", data_b, org_id="org-b")

        assert _get_cached_memories("u1", org_id="org-a") == data_a
        assert _get_cached_memories("u1", org_id="org-b") == data_b

    def test_personal_vs_org_isolation(self):
        """散客 vs 企业缓存互不干扰"""
        personal_data = [{"memory": "personal"}]
        org_data = [{"memory": "org"}]
        _set_cached_memories("u1", personal_data)
        _set_cached_memories("u1", org_data, org_id="org-x")

        assert _get_cached_memories("u1") == personal_data
        assert _get_cached_memories("u1", org_id="org-x") == org_data

    def test_invalidate_personal(self):
        """失效散客缓存不影响企业缓存"""
        _set_cached_memories("u1", [{"m": "p"}])
        _set_cached_memories("u1", [{"m": "o"}], org_id="org-x")

        _invalidate_cache("u1")
        assert _get_cached_memories("u1") is None
        assert _get_cached_memories("u1", org_id="org-x") == [{"m": "o"}]

    def test_invalidate_org(self):
        """失效企业缓存不影响散客缓存"""
        _set_cached_memories("u1", [{"m": "p"}])
        _set_cached_memories("u1", [{"m": "o"}], org_id="org-x")

        _invalidate_cache("u1", org_id="org-x")
        assert _get_cached_memories("u1") == [{"m": "p"}]
        assert _get_cached_memories("u1", org_id="org-x") is None

    def test_expired_cache_returns_none(self):
        """过期缓存返回 None"""
        _set_cached_memories("u1", [{"m": "old"}], org_id="org-x")
        # 手动篡改时间戳使其过期
        key = _cache_key("u1", org_id="org-x")
        _memory_cache[key]["ts"] = time.monotonic() - 9999
        assert _get_cached_memories("u1", org_id="org-x") is None
