"""Async organization configuration resolver tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.crypto import aes_decrypt, aes_encrypt, generate_encrypt_key
from services.org.config_resolver import AsyncOrgConfigResolver
from testing.org_config_test_support import AsyncFakeDB, AsyncFakeQueryBuilder

TEST_KEY = generate_encrypt_key()

class TestAsyncOrgConfigResolver:

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        """每个测试前清空 per-org 密钥缓存"""
        AsyncOrgConfigResolver._org_key_cache.clear()

    @pytest.fixture
    def db(self):
        return AsyncFakeDB()

    @pytest.fixture
    def resolver(self, db):
        with patch("services.org.config_resolver.get_settings") as mock_settings:
            settings = MagicMock(spec=[])
            settings.org_config_encrypt_key = TEST_KEY
            settings.kuaimai_app_key = "system_default_key"
            settings.kuaimai_app_secret = None
            # 非企业专属 key，用于测试降级
            settings.some_ai_key = "system_ai_default"
            mock_settings.return_value = settings
            return AsyncOrgConfigResolver(db)

    @pytest.mark.asyncio
    async def test_get_from_org_config(self, resolver, db):
        """企业配置存在时返回解密值"""
        db.set_table("organizations", {"encrypt_key": None})
        encrypted = aes_encrypt("org_secret_key", TEST_KEY)
        db.set_table("org_configs", {"config_value_encrypted": encrypted})

        result = await resolver.get("org-1", "kuaimai_app_key")
        assert result == "org_secret_key"

    @pytest.mark.asyncio
    async def test_get_fallback_to_system_default(self, resolver, db):
        """企业未配置非企业专属 key 时降级到系统默认"""
        db.set_table("organizations", {"encrypt_key": None})
        db.set_table("org_configs", None)

        result = await resolver.get("org-1", "some_ai_key")
        assert result == "system_ai_default"

    @pytest.mark.asyncio
    async def test_get_enterprise_key_no_fallback(self, resolver, db):
        """企业专属 key 未配置时返回 None"""
        db.set_table("organizations", {"encrypt_key": None})
        db.set_table("org_configs", None)

        result = await resolver.get("org-1", "kuaimai_app_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_personal_returns_system_default(self, resolver):
        """散客直接返回系统默认（非企业专属 key）"""
        result = await resolver.get(None, "some_ai_key")
        assert result == "system_ai_default"

    @pytest.mark.asyncio
    async def test_get_personal_enterprise_key_returns_none(self, resolver):
        """散客查询企业专属 key 返回 None"""
        result = await resolver.get(None, "kuaimai_app_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_nonexistent_key_returns_none(self, resolver, db):
        """系统也没有的 key 返回 None"""
        db.set_table("organizations", {"encrypt_key": None})
        db.set_table("org_configs", None)
        result = await resolver.get("org-1", "nonexistent_key_xyz")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_erp_credentials_success(self, resolver, db):
        """ERP 凭证完整时返回全部"""
        db.set_table("organizations", {"encrypt_key": None})
        for key in ["kuaimai_app_key", "kuaimai_app_secret",
                     "kuaimai_access_token", "kuaimai_refresh_token"]:
            encrypted = aes_encrypt(f"value_{key}", TEST_KEY)
            db.set_table("org_configs", {"config_value_encrypted": encrypted})

        creds = await resolver.get_erp_credentials("org-1")
        assert creds["kuaimai_app_key"] == "value_kuaimai_app_key"
        assert len(creds) == 4

    @pytest.mark.asyncio
    async def test_get_erp_credentials_missing_key_raises(self, resolver, db):
        """ERP 凭证缺失时报错"""
        db.set_table("organizations", {"encrypt_key": None})
        encrypted = aes_encrypt("val", TEST_KEY)
        db.set_table("org_configs", {"config_value_encrypted": encrypted})
        for _ in range(3):
            db.set_table("org_configs", None)

        with pytest.raises(ValueError, match="未配置"):
            await resolver.get_erp_credentials("org-1")

    @pytest.mark.asyncio
    async def test_erp_credentials_no_fallback_to_system(self, resolver, db):
        """ERP 凭证不降级到系统默认"""
        db.set_table("organizations", {"encrypt_key": None})
        for _ in range(4):
            db.set_table("org_configs", None)

        with pytest.raises(ValueError, match="未配置"):
            await resolver.get_erp_credentials("org-1")

    @pytest.mark.asyncio
    async def test_load_encrypted_db_error_returns_none(self, resolver, db):
        """DB 异常时 _load_encrypted 返回 None（降级到系统默认）"""
        db.set_table("organizations", {"encrypt_key": None})

        class ErrorBuilder(AsyncFakeQueryBuilder):
            async def execute(self):
                raise RuntimeError("DB connection lost")

        db._tables["org_configs"] = [ErrorBuilder()]

        # 用非企业专属 key 测试降级逻辑
        result = await resolver.get("org-1", "some_ai_key")
        assert result == "system_ai_default"

    @pytest.mark.asyncio
    async def test_update_erp_token_persists_both_keys(self, resolver):
        """关键回归：Worker Scope 下单次 RPC 原子提交两个 Token。

        Why: 这是 2026-04-10 token 雪崩根因的修复核心 — 自动 refresh 后必须
        把新 token 加密回写到 org_configs，否则下次 client 重建会回到死态。
        """
        rpc_calls = []

        class SpyBuilder:
            def __init__(self, record=True):
                self._record = record
            def select(self, *a, **kw): return self
            def eq(self, *a, **kw): return self
            def maybe_single(self): return self
            async def execute(self):
                return MagicMock(data={"encrypt_key": None})

        class SpyDB:
            def table(self, name):
                return SpyBuilder(record=False)

        resolver.db = SpyDB()
        scoped_db = MagicMock()
        caller = MagicMock()
        caller.execute = AsyncMock(return_value=MagicMock(data={}))
        scoped_db.rpc.side_effect = (
            lambda name, params: rpc_calls.append((name, params)) or caller
        )
        org_id = "30000000-0000-0000-0000-000000000001"
        with patch(
            "services.org.config_resolver.AsyncScopedDatabaseClient",
            return_value=scoped_db,
        ) as scoped_factory:
            await resolver.update_erp_token(
                org_id, "new_access_xyz", "new_refresh_abc",
            )

        assert len(rpc_calls) == 1
        name, params = rpc_calls[0]
        assert name == "commit_worker_org_erp_tokens"
        assert aes_decrypt(
            params["p_access_token_encrypted"], TEST_KEY,
        ) == "new_access_xyz"
        assert aes_decrypt(
            params["p_refresh_token_encrypted"], TEST_KEY,
        ) == "new_refresh_abc"
        scope = scoped_factory.call_args.args[1]
        assert scope.org_id == org_id
        assert scope.actor_user_id is None
        assert scope.access_kind.value == "worker"
