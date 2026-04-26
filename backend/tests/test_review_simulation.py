"""
变更审查模拟测试 — 覆盖 per-org encrypt key + erp_tool_description parallel_hint

验证目标：
1. per-org 密钥：企业用自己的密钥加解密，降级到全局密钥
2. 缓存：第二次不查 DB
3. 隔离：企业密钥加密的数据，全局密钥解不开
4. async 路径：_get_encrypt_key 只读缓存，不调异步方法
5. parallel_hint：正确渲染到工具描述
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from core.crypto import aes_encrypt, aes_decrypt, generate_encrypt_key
from services.org.config_resolver import (
    OrgConfigResolver,
    AsyncOrgConfigResolver,
    _ConfigResolverCore,
)

GLOBAL_KEY = generate_encrypt_key()
ORG_KEY = generate_encrypt_key()


# ── Fake DB helpers ──────────────────────────────────


class SyncFakeBuilder:
    def __init__(self, data=None):
        self._data = [data] if isinstance(data, dict) else (data or [])
        self._is_single = False

    def select(self, *a, **kw): return self
    def eq(self, *a, **kw): return self
    def maybe_single(self):
        self._is_single = True
        return self
    def upsert(self, *a, **kw): return self
    def execute(self):
        result = MagicMock()
        if self._is_single:
            result.data = self._data[0] if self._data else None
        else:
            result.data = self._data
        return result


class AsyncFakeBuilder(SyncFakeBuilder):
    async def execute(self):
        return super().execute()


class SyncFakeDB:
    def __init__(self):
        self._queue: list[tuple[str, SyncFakeBuilder]] = []

    def enqueue(self, table: str, data):
        self._queue.append((table, SyncFakeBuilder(data)))

    def table(self, name):
        for i, (t, builder) in enumerate(self._queue):
            if t == name:
                self._queue.pop(i)
                return builder
        return SyncFakeBuilder()


class AsyncFakeDB:
    def __init__(self):
        self._queue: list[tuple[str, AsyncFakeBuilder]] = []

    def enqueue(self, table: str, data):
        self._queue.append((table, AsyncFakeBuilder(data)))

    def table(self, name):
        for i, (t, builder) in enumerate(self._queue):
            if t == name:
                self._queue.pop(i)
                return builder
        return AsyncFakeBuilder()


def _make_resolver(cls, db, global_key=GLOBAL_KEY):
    with patch("services.org.config_resolver.get_settings") as m:
        s = MagicMock(spec=[])
        s.org_config_encrypt_key = global_key
        m.return_value = s
        return cls(db)


# ============================================================
# 1. per-org 密钥：企业有专属密钥时用企业密钥加解密
# ============================================================


class TestPerOrgKeySync:

    @pytest.fixture(autouse=True)
    def _clear(self):
        OrgConfigResolver._org_key_cache.clear()

    def test_per_org_key_used_for_decrypt(self):
        """企业有 encrypt_key 时，用企业密钥解密（而非全局密钥）"""
        db = SyncFakeDB()
        resolver = _make_resolver(OrgConfigResolver, db)

        # organizations 表返回企业专属密钥
        db.enqueue("organizations", {"encrypt_key": ORG_KEY})
        # org_configs 表返回用 ORG_KEY 加密的数据
        encrypted = aes_encrypt("per_org_secret", ORG_KEY)
        db.enqueue("org_configs", {"config_value_encrypted": encrypted})

        result = resolver.get("org-A", "kuaimai_app_key")
        assert result == "per_org_secret"

    def test_per_org_key_fallback_to_global(self):
        """企业无 encrypt_key 时，降级到全局密钥"""
        db = SyncFakeDB()
        resolver = _make_resolver(OrgConfigResolver, db)

        db.enqueue("organizations", {"encrypt_key": None})
        encrypted = aes_encrypt("global_secret", GLOBAL_KEY)
        db.enqueue("org_configs", {"config_value_encrypted": encrypted})

        result = resolver.get("org-B", "kuaimai_app_key")
        assert result == "global_secret"

    def test_per_org_key_isolation(self):
        """用企业密钥加密的数据，全局密钥解不开 → _load_encrypted 返回 None"""
        db = SyncFakeDB()
        resolver = _make_resolver(OrgConfigResolver, db)

        # organizations 表返回 None（无企业密钥）→ 用全局密钥解密
        db.enqueue("organizations", {"encrypt_key": None})
        # 但数据是用 ORG_KEY 加密的 → 全局密钥解不开
        encrypted = aes_encrypt("wrong_key_data", ORG_KEY)
        db.enqueue("org_configs", {"config_value_encrypted": encrypted})

        # 解密失败被 except 吞掉 → 返回 None → 降级到 .env
        result = resolver.get("org-C", "kuaimai_app_key")
        # kuaimai_app_key 是企业专属 key，不降级 → None
        assert result is None


# ============================================================
# 2. 缓存：第二次不查 DB
# ============================================================


class TestCacheHit:

    @pytest.fixture(autouse=True)
    def _clear(self):
        OrgConfigResolver._org_key_cache.clear()

    def test_second_call_hits_cache(self):
        """第二次调用同一 org_id 不查 organizations 表"""
        db = SyncFakeDB()
        resolver = _make_resolver(OrgConfigResolver, db)

        # 第一次：organizations 返回 per-org key
        db.enqueue("organizations", {"encrypt_key": ORG_KEY})
        encrypted1 = aes_encrypt("val1", ORG_KEY)
        db.enqueue("org_configs", {"config_value_encrypted": encrypted1})
        r1 = resolver.get("org-D", "kuaimai_app_key")
        assert r1 == "val1"

        # 第二次：不 enqueue organizations（模拟 DB 没被查询）
        encrypted2 = aes_encrypt("val2", ORG_KEY)
        db.enqueue("org_configs", {"config_value_encrypted": encrypted2})
        r2 = resolver.get("org-D", "kuaimai_app_key")
        assert r2 == "val2"  # 成功解密说明缓存命中


# ============================================================
# 3. 异步版端到端
# ============================================================


class TestPerOrgKeyAsync:

    @pytest.fixture(autouse=True)
    def _clear(self):
        AsyncOrgConfigResolver._org_key_cache.clear()

    @pytest.mark.asyncio
    async def test_async_per_org_key_decrypt(self):
        """异步版：企业有 encrypt_key 时用企业密钥解密"""
        db = AsyncFakeDB()
        resolver = _make_resolver(AsyncOrgConfigResolver, db)

        db.enqueue("organizations", {"encrypt_key": ORG_KEY})
        encrypted = aes_encrypt("async_secret", ORG_KEY)
        db.enqueue("org_configs", {"config_value_encrypted": encrypted})

        result = await resolver.get("org-E", "kuaimai_app_key")
        assert result == "async_secret"

    @pytest.mark.asyncio
    async def test_async_fallback_to_global(self):
        """异步版：企业无 encrypt_key 时降级全局密钥"""
        db = AsyncFakeDB()
        resolver = _make_resolver(AsyncOrgConfigResolver, db)

        db.enqueue("organizations", {"encrypt_key": None})
        encrypted = aes_encrypt("global_async", GLOBAL_KEY)
        db.enqueue("org_configs", {"config_value_encrypted": encrypted})

        result = await resolver.get("org-F", "kuaimai_app_key")
        assert result == "global_async"

    @pytest.mark.asyncio
    async def test_async_cache_hit(self):
        """异步版：第二次调用命中缓存"""
        db = AsyncFakeDB()
        resolver = _make_resolver(AsyncOrgConfigResolver, db)

        db.enqueue("organizations", {"encrypt_key": ORG_KEY})
        encrypted1 = aes_encrypt("v1", ORG_KEY)
        db.enqueue("org_configs", {"config_value_encrypted": encrypted1})
        r1 = await resolver.get("org-G", "kuaimai_app_key")
        assert r1 == "v1"

        # 第二次不 enqueue organizations
        encrypted2 = aes_encrypt("v2", ORG_KEY)
        db.enqueue("org_configs", {"config_value_encrypted": encrypted2})
        r2 = await resolver.get("org-G", "kuaimai_app_key")
        assert r2 == "v2"

    @pytest.mark.asyncio
    async def test_async_update_erp_token_uses_org_key(self):
        """异步版：update_erp_token 用企业密钥加密"""
        db = AsyncFakeDB()
        resolver = _make_resolver(AsyncOrgConfigResolver, db)

        upsert_calls = []

        class SpyBuilder:
            def __init__(self, record=True):
                self._record = record
            def select(self, *a, **kw): return self
            def eq(self, *a, **kw): return self
            def maybe_single(self): return self
            def upsert(self, data, on_conflict=""):
                if self._record:
                    upsert_calls.append(data)
                return self
            async def execute(self):
                return MagicMock(data={"encrypt_key": ORG_KEY})

        class SpyDB:
            def table(self, name):
                return SpyBuilder(record=(name == "org_configs"))

        resolver.db = SpyDB()
        await resolver.update_erp_token("org-H", "access_new", "refresh_new")

        assert len(upsert_calls) == 2
        # 用 ORG_KEY 加密的数据应能用 ORG_KEY 解密
        for call in upsert_calls:
            decrypted = aes_decrypt(call["config_value_encrypted"], ORG_KEY)
            assert decrypted in ("access_new", "refresh_new")
            # 用 GLOBAL_KEY 解不开
            with pytest.raises(ValueError):
                aes_decrypt(call["config_value_encrypted"], GLOBAL_KEY)


# ============================================================
# 4. _get_encrypt_key 安全性：只读缓存 dict，不调异步方法
# ============================================================


class TestGetEncryptKeySafety:
    """确认 _get_encrypt_key 直接读 _org_key_cache.get()，
    不调用 _load_org_encrypt_key（避免 sync 调 async 返回 coroutine）。
    """

    @pytest.fixture(autouse=True)
    def _clear(self):
        AsyncOrgConfigResolver._org_key_cache.clear()

    def test_get_encrypt_key_reads_cache_not_method(self):
        """缓存已填充时，_get_encrypt_key 返回 str 而非 coroutine"""
        db = MagicMock()
        resolver = _make_resolver(AsyncOrgConfigResolver, db)

        # 手动填充缓存
        AsyncOrgConfigResolver._org_key_cache["org-safe"] = ORG_KEY

        result = resolver._get_encrypt_key("org-safe")
        assert result == ORG_KEY
        assert isinstance(result, str)  # 不是 coroutine
        assert not asyncio.iscoroutine(result)

    def test_get_encrypt_key_cache_none_falls_to_global(self):
        """缓存值为 None 时降级到全局密钥"""
        db = MagicMock()
        resolver = _make_resolver(AsyncOrgConfigResolver, db)

        AsyncOrgConfigResolver._org_key_cache["org-no-key"] = None

        result = resolver._get_encrypt_key("org-no-key")
        assert result == GLOBAL_KEY
        assert isinstance(result, str)

    def test_get_encrypt_key_not_in_cache_falls_to_global(self):
        """org_id 不在缓存中时降级到全局密钥"""
        db = MagicMock()
        resolver = _make_resolver(AsyncOrgConfigResolver, db)

        result = resolver._get_encrypt_key("org-unknown")
        assert result == GLOBAL_KEY


# ============================================================
# 5. erp_tool_description parallel_hint
# ============================================================


class TestParallelHint:

    def test_manifest_contains_parallel_hint(self):
        from services.agent.erp_tool_description import get_capability_manifest
        m = get_capability_manifest()
        assert "parallel_hint" in m
        assert "并行" in m["parallel_hint"]

    def test_description_renders_parallel_hint(self):
        from services.agent.erp_tool_description import build_tool_description
        desc = build_tool_description()
        assert "并行调用：" in desc
        assert "同时发起多个 erp_agent 调用" in desc

    def test_description_parallel_before_returns(self):
        """并行调用段在'返回'段之前"""
        from services.agent.erp_tool_description import build_tool_description
        desc = build_tool_description()
        parallel_pos = desc.index("并行调用：")
        returns_pos = desc.index("\n返回：")
        assert parallel_pos < returns_pos


# ============================================================
# 6. 同步版 update_erp_token + set 用企业密钥
# ============================================================


class TestSyncWriteWithOrgKey:

    @pytest.fixture(autouse=True)
    def _clear(self):
        OrgConfigResolver._org_key_cache.clear()

    def test_sync_update_erp_token_uses_org_key(self):
        """同步版 update_erp_token 用企业密钥加密"""
        upsert_calls = []

        class SpyBuilder:
            def __init__(self, record=True):
                self._record = record
            def select(self, *a, **kw): return self
            def eq(self, *a, **kw): return self
            def maybe_single(self): return self
            def upsert(self, data, on_conflict=""):
                if self._record:
                    upsert_calls.append(data)
                return self
            def execute(self):
                return MagicMock(data={"encrypt_key": ORG_KEY})

        class SpyDB:
            def table(self, name):
                return SpyBuilder(record=(name == "org_configs"))

        db = SpyDB()
        resolver = _make_resolver(OrgConfigResolver, db)
        resolver.update_erp_token("org-sync", "at_new", "rt_new")

        assert len(upsert_calls) == 2
        for call in upsert_calls:
            decrypted = aes_decrypt(call["config_value_encrypted"], ORG_KEY)
            assert decrypted in ("at_new", "rt_new")

    def test_sync_set_uses_org_key(self):
        """同步版 set 用企业密钥加密"""
        upsert_calls = []

        class SpyBuilder:
            def __init__(self, record=True):
                self._record = record
            def select(self, *a, **kw): return self
            def eq(self, *a, **kw): return self
            def maybe_single(self): return self
            def upsert(self, data, on_conflict=""):
                if self._record:
                    upsert_calls.append(data)
                return self
            def execute(self):
                return MagicMock(data={"encrypt_key": ORG_KEY})

        class SpyDB:
            def table(self, name):
                return SpyBuilder(record=(name == "org_configs"))

        db = SpyDB()
        resolver = _make_resolver(OrgConfigResolver, db)
        resolver.set("org-set", "custom_key", "custom_val", "admin")

        assert len(upsert_calls) == 1
        decrypted = aes_decrypt(
            upsert_calls[0]["config_value_encrypted"], ORG_KEY,
        )
        assert decrypted == "custom_val"


# ============================================================
# 7. _load_org_encrypt_key DB 异常降级
# ============================================================


class TestLoadOrgKeyDbError:

    @pytest.fixture(autouse=True)
    def _clear(self):
        OrgConfigResolver._org_key_cache.clear()
        AsyncOrgConfigResolver._org_key_cache.clear()

    def test_sync_db_error_returns_none_and_no_cache_pollution(self):
        """同步版：organizations 查询异常 → 返回 None，不写入缓存"""
        class ErrorBuilder(SyncFakeBuilder):
            def execute(self):
                raise RuntimeError("DB connection lost")

        db = SyncFakeDB()
        resolver = _make_resolver(OrgConfigResolver, db)

        # 注入一个会抛异常的 organizations builder
        db._queue.append(("organizations", ErrorBuilder()))

        result = resolver._load_org_encrypt_key("org-err")
        assert result is None
        # 不应污染缓存（下次应重新查询）
        assert "org-err" not in OrgConfigResolver._org_key_cache

    @pytest.mark.asyncio
    async def test_async_db_error_returns_none_and_no_cache_pollution(self):
        """异步版：organizations 查询异常 → 返回 None，不写入缓存"""
        class ErrorBuilder(AsyncFakeBuilder):
            async def execute(self):
                raise RuntimeError("DB timeout")

        db = AsyncFakeDB()
        resolver = _make_resolver(AsyncOrgConfigResolver, db)

        db._queue.append(("organizations", ErrorBuilder()))

        result = await resolver._load_org_encrypt_key("org-err-async")
        assert result is None
        assert "org-err-async" not in AsyncOrgConfigResolver._org_key_cache


# ============================================================
# 8. _org_key_cache 类变量：不同实例共享
# ============================================================


class TestCacheSharedAcrossInstances:

    @pytest.fixture(autouse=True)
    def _clear(self):
        OrgConfigResolver._org_key_cache.clear()

    def test_cache_shared_between_instances(self):
        """同一类的不同实例共享 _org_key_cache"""
        db1 = SyncFakeDB()
        db2 = SyncFakeDB()
        r1 = _make_resolver(OrgConfigResolver, db1)
        r2 = _make_resolver(OrgConfigResolver, db2)

        # r1 填充缓存
        db1.enqueue("organizations", {"encrypt_key": ORG_KEY})
        r1._load_org_encrypt_key("org-shared")

        # r2 直接命中缓存，不查 DB
        result = r2._load_org_encrypt_key("org-shared")
        assert result == ORG_KEY

    def test_sync_and_async_cache_shared(self):
        """OrgConfigResolver 和 AsyncOrgConfigResolver 共享基类 dict
        （_org_key_cache 定义在 _ConfigResolverCore，子类未覆写）
        """
        OrgConfigResolver._org_key_cache["org-iso"] = "sync_key"
        # 共享同一个 dict 对象
        assert AsyncOrgConfigResolver._org_key_cache.get("org-iso") == "sync_key"
        assert OrgConfigResolver._org_key_cache is AsyncOrgConfigResolver._org_key_cache
