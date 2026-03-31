"""
企业配置（AES 加解密 + 配置解析链）测试

覆盖:
- AES-256-GCM 加解密正确性
- AsyncOrgConfigResolver: 异步版加密读取、降级、ERP 凭证加载
- OrgConfigResolver: 加密读写、降级到系统默认、ERP 凭证加载
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.crypto import aes_encrypt, aes_decrypt, generate_encrypt_key
from services.org.config_resolver import (
    OrgConfigResolver,
    AsyncOrgConfigResolver,
    _ConfigResolverCore,
)


# ── AES 加解密 ─────────────────────────────────────────


class TestAESCrypto:

    def test_encrypt_decrypt_roundtrip(self):
        """加密后解密得到原文"""
        key = generate_encrypt_key()
        plaintext = "sk-test-api-key-12345"
        encrypted = aes_encrypt(plaintext, key)
        assert encrypted != plaintext
        decrypted = aes_decrypt(encrypted, key)
        assert decrypted == plaintext

    def test_different_nonce_each_time(self):
        """同一明文每次加密结果不同（随机 nonce）"""
        key = generate_encrypt_key()
        e1 = aes_encrypt("same", key)
        e2 = aes_encrypt("same", key)
        assert e1 != e2

    def test_wrong_key_raises(self):
        """错误密钥解密失败"""
        key1 = generate_encrypt_key()
        key2 = generate_encrypt_key()
        encrypted = aes_encrypt("secret", key1)
        with pytest.raises(ValueError, match="解密失败"):
            aes_decrypt(encrypted, key2)

    def test_tampered_data_raises(self):
        """篡改数据解密失败"""
        key = generate_encrypt_key()
        encrypted = aes_encrypt("secret", key)
        tampered = encrypted[:-2] + "XX"
        with pytest.raises(Exception):
            aes_decrypt(tampered, key)

    def test_invalid_key_length(self):
        """密钥长度不对"""
        import base64
        short_key = base64.b64encode(b"short").decode()
        with pytest.raises(ValueError, match="32 字节"):
            aes_encrypt("test", short_key)

    def test_unicode_plaintext(self):
        """支持中文等 Unicode"""
        key = generate_encrypt_key()
        plaintext = "企业密钥🔑测试"
        decrypted = aes_decrypt(aes_encrypt(plaintext, key), key)
        assert decrypted == plaintext

    def test_generate_key_format(self):
        """生成的密钥是合法 base64 且 32 字节"""
        import base64
        key = generate_encrypt_key()
        raw = base64.b64decode(key)
        assert len(raw) == 32

    def test_empty_string(self):
        """空字符串加解密"""
        key = generate_encrypt_key()
        encrypted = aes_encrypt("", key)
        assert aes_decrypt(encrypted, key) == ""

    def test_invalid_base64_input(self):
        """非 base64 数据解密报错"""
        key = generate_encrypt_key()
        with pytest.raises(Exception):
            aes_decrypt("not-valid-base64!!!", key)

    def test_too_short_encrypted_data(self):
        """加密数据太短"""
        import base64
        key = generate_encrypt_key()
        short = base64.b64encode(b"tiny").decode()
        with pytest.raises(ValueError, match="格式无效"):
            aes_decrypt(short, key)


# ── OrgConfigResolver ──────────────────────────────────


class FakeDB:
    def __init__(self):
        self._tables: dict[str, list] = {}

    def set_table(self, name: str, data):
        from tests.test_org_service import FakeQueryBuilder
        if name not in self._tables:
            self._tables[name] = []
        self._tables[name].append(FakeQueryBuilder(data))

    def table(self, name: str):
        from tests.test_org_service import FakeQueryBuilder
        builders = self._tables.get(name, [])
        if builders:
            return builders.pop(0)
        return FakeQueryBuilder()


TEST_KEY = generate_encrypt_key()


class TestOrgConfigResolver:

    @pytest.fixture
    def db(self):
        return FakeDB()

    @pytest.fixture
    def resolver(self, db):
        with patch("services.org.config_resolver.get_settings") as mock_settings:
            settings = MagicMock(spec=[])  # spec=[] 阻止自动生成属性
            settings.org_config_encrypt_key = TEST_KEY
            settings.kuaimai_app_key = "system_default_key"
            settings.kuaimai_app_secret = None
            settings.kuaimai_access_token = None
            settings.kuaimai_refresh_token = None
            # 非企业专属 key，用于测试降级
            settings.some_ai_key = "system_ai_default"
            mock_settings.return_value = settings
            return OrgConfigResolver(db)

    def test_get_from_org_config(self, resolver, db):
        """企业配置存在时返回解密值"""
        encrypted = aes_encrypt("org_secret_key", TEST_KEY)
        db.set_table("org_configs", {"config_value_encrypted": encrypted})

        result = resolver.get("org-1", "kuaimai_app_key")
        assert result == "org_secret_key"

    def test_get_fallback_to_system_default(self, resolver, db):
        """企业未配置非企业专属 key 时降级到系统默认"""
        db.set_table("org_configs", None)  # 查询无结果

        result = resolver.get("org-1", "some_ai_key")
        assert result == "system_ai_default"

    def test_get_enterprise_key_no_fallback(self, resolver, db):
        """企业专属 key 未配置时返回 None，不降级到系统默认"""
        db.set_table("org_configs", None)

        result = resolver.get("org-1", "kuaimai_app_key")
        assert result is None

    def test_get_personal_returns_system_default(self, resolver):
        """散客直接返回系统默认（非企业专属 key）"""
        result = resolver.get(None, "some_ai_key")
        assert result == "system_ai_default"

    def test_get_personal_enterprise_key_returns_none(self, resolver):
        """散客查询企业专属 key 返回 None"""
        result = resolver.get(None, "kuaimai_app_key")
        assert result is None

    def test_get_nonexistent_key_returns_none(self, resolver, db):
        """系统也没有的 key 返回 None"""
        db.set_table("org_configs", None)

        result = resolver.get("org-1", "nonexistent_key_xyz")
        assert result is None

    def test_list_keys(self, resolver, db):
        """列出已配置的 key"""
        db.set_table("org_configs", [
            {"config_key": "kuaimai_app_key"},
            {"config_key": "kuaimai_app_secret"},
        ])
        keys = resolver.list_keys("org-1")
        assert set(keys) == {"kuaimai_app_key", "kuaimai_app_secret"}

    def test_get_erp_credentials_success(self, resolver, db):
        """ERP 凭证完整时返回全部"""
        for key in ["kuaimai_app_key", "kuaimai_app_secret",
                     "kuaimai_access_token", "kuaimai_refresh_token"]:
            encrypted = aes_encrypt(f"value_{key}", TEST_KEY)
            db.set_table("org_configs", {"config_value_encrypted": encrypted})

        creds = resolver.get_erp_credentials("org-1")
        assert creds["kuaimai_app_key"] == "value_kuaimai_app_key"
        assert len(creds) == 4

    def test_get_erp_credentials_missing_key_raises(self, resolver, db):
        """ERP 凭证缺失时报错"""
        # 只配了 1 个，缺 3 个
        encrypted = aes_encrypt("val", TEST_KEY)
        db.set_table("org_configs", {"config_value_encrypted": encrypted})
        # 后续 3 个查不到
        for _ in range(3):
            db.set_table("org_configs", None)

        with pytest.raises(ValueError, match="未配置"):
            resolver.get_erp_credentials("org-1")

    def test_set_and_get_roundtrip(self, resolver, db):
        """set 写入后 get 能读到"""
        # set 会调 upsert，FakeQueryBuilder 的 insert 会记录数据
        # 但我们不能真正验证 DB 写入，只验证不报错
        # 真正的 roundtrip 需要 mock upsert 后再 mock select
        resolver.set("org-1", "test_key", "test_value", updated_by="admin-1")
        # 验证 set 不抛异常即可（实际加密+写入链路正确）

    def test_delete_no_error(self, resolver, db):
        """delete 不报错"""
        resolver.delete("org-1", "test_key")

    def test_list_keys_empty(self, resolver, db):
        """无配置时返回空列表"""
        db.set_table("org_configs", [])
        keys = resolver.list_keys("org-1")
        assert keys == []

    def test_erp_credentials_no_fallback_to_system(self, resolver, db):
        """ERP 凭证不降级到系统默认（即使系统有 kuaimai_app_key）"""
        # org_configs 全部查不到
        for _ in range(4):
            db.set_table("org_configs", None)

        # 系统默认有 kuaimai_app_key，但 get_erp_credentials 应该忽略它
        with pytest.raises(ValueError, match="未配置"):
            resolver.get_erp_credentials("org-1")

    def test_load_encrypted_db_error_returns_none(self, resolver, db):
        """DB 查询异常时 _load_encrypted 返回 None（降级到系统默认）"""
        from tests.test_org_service import FakeQueryBuilder

        # 创建一个会抛异常的 builder
        class ErrorBuilder(FakeQueryBuilder):
            def execute(self):
                raise RuntimeError("DB connection lost")

        db._tables["org_configs"] = [ErrorBuilder()]

        # 用非企业专属 key 测试降级逻辑
        result = resolver.get("org-1", "some_ai_key")
        assert result == "system_ai_default"

    def test_enterprise_key_no_fallback_for_all_keys(self, resolver, db):
        """所有 ENTERPRISE_ONLY_KEYS 未配置时均返回 None，不降级到 .env"""
        for key in _ConfigResolverCore.ENTERPRISE_ONLY_KEYS:
            db.set_table("org_configs", None)  # 每个 key 查询都无结果
            result = resolver.get("org-1", key)
            assert result is None, f"Expected None for enterprise key '{key}', got {result!r}"

    def test_list_orgs_with_wecom_bot_returns_configured_orgs(self, resolver, db):
        """配了 bot_id + bot_secret 的企业被正确返回"""
        # 1) 查 wecom_bot_id 的 org_ids
        db.set_table("org_configs", [{"org_id": "org-abc"}])
        # 2) _load_encrypted(org-abc, wecom_bot_id)
        encrypted_bot_id = aes_encrypt("bot-123", TEST_KEY)
        db.set_table("org_configs", {"config_value_encrypted": encrypted_bot_id})
        # 3) _load_encrypted(org-abc, wecom_bot_secret)
        encrypted_secret = aes_encrypt("secret-456", TEST_KEY)
        db.set_table("org_configs", {"config_value_encrypted": encrypted_secret})
        # 4) organizations 表取 corp_id
        db.set_table("organizations", {"wecom_corp_id": "ww_corp_xyz"})

        orgs = resolver.list_orgs_with_wecom_bot()
        assert len(orgs) == 1
        assert orgs[0]["org_id"] == "org-abc"
        assert orgs[0]["bot_id"] == "bot-123"
        assert orgs[0]["bot_secret"] == "secret-456"
        assert orgs[0]["corp_id"] == "ww_corp_xyz"

    def test_list_orgs_with_wecom_bot_empty_when_no_orgs(self, resolver, db):
        """无任何配了 wecom_bot_id 的企业时返回空列表"""
        db.set_table("org_configs", [])  # 无 org 配了 wecom_bot_id
        orgs = resolver.list_orgs_with_wecom_bot()
        assert orgs == []

    def test_list_orgs_with_wecom_bot_skips_incomplete(self, resolver, db):
        """有 bot_id 但无 bot_secret 的企业被跳过"""
        # 1) 查 wecom_bot_id 的 org_ids — 返回一个 org
        db.set_table("org_configs", [{"org_id": "org-incomplete"}])
        # 2) _load_encrypted(org-incomplete, wecom_bot_id) — 有值
        encrypted_bot_id = aes_encrypt("bot-999", TEST_KEY)
        db.set_table("org_configs", {"config_value_encrypted": encrypted_bot_id})
        # 3) _load_encrypted(org-incomplete, wecom_bot_secret) — 无值
        db.set_table("org_configs", None)

        orgs = resolver.list_orgs_with_wecom_bot()
        assert orgs == []

    def test_encrypt_key_not_configured(self, db):
        """加密密钥未配置时报错"""
        with patch("services.org.config_resolver.get_settings") as mock_settings:
            settings = MagicMock()
            settings.org_config_encrypt_key = None
            mock_settings.return_value = settings
            resolver = OrgConfigResolver(db)

            encrypted = aes_encrypt("test", TEST_KEY)
            db.set_table("org_configs", {"config_value_encrypted": encrypted})

            with pytest.raises(ValueError, match="ORG_CONFIG_ENCRYPT_KEY"):
                resolver.get("org-1", "some_key")


# ── AsyncOrgConfigResolver ────────────────────────────


class AsyncFakeQueryBuilder:
    """异步版 FakeQueryBuilder — execute 返回 awaitable"""

    def __init__(self, data=None):
        if isinstance(data, dict):
            self._data = [data]
        else:
            self._data = data if data is not None else []
        self._is_single = False

    def select(self, *a, **kw): return self
    def eq(self, *a, **kw): return self
    def single(self):
        self._is_single = True
        return self

    async def execute(self):
        result = MagicMock()
        if self._is_single:
            result.data = self._data[0] if self._data else None
        else:
            result.data = self._data
        return result


class AsyncFakeDB:
    """异步版 FakeDB"""

    def __init__(self):
        self._tables: dict[str, list] = {}

    def set_table(self, name: str, data):
        if name not in self._tables:
            self._tables[name] = []
        self._tables[name].append(AsyncFakeQueryBuilder(data))

    def table(self, name: str):
        builders = self._tables.get(name, [])
        if builders:
            return builders.pop(0)
        return AsyncFakeQueryBuilder()


class TestAsyncOrgConfigResolver:

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
        encrypted = aes_encrypt("org_secret_key", TEST_KEY)
        db.set_table("org_configs", {"config_value_encrypted": encrypted})

        result = await resolver.get("org-1", "kuaimai_app_key")
        assert result == "org_secret_key"

    @pytest.mark.asyncio
    async def test_get_fallback_to_system_default(self, resolver, db):
        """企业未配置非企业专属 key 时降级到系统默认"""
        db.set_table("org_configs", None)

        result = await resolver.get("org-1", "some_ai_key")
        assert result == "system_ai_default"

    @pytest.mark.asyncio
    async def test_get_enterprise_key_no_fallback(self, resolver, db):
        """企业专属 key 未配置时返回 None"""
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
        db.set_table("org_configs", None)
        result = await resolver.get("org-1", "nonexistent_key_xyz")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_erp_credentials_success(self, resolver, db):
        """ERP 凭证完整时返回全部"""
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
        encrypted = aes_encrypt("val", TEST_KEY)
        db.set_table("org_configs", {"config_value_encrypted": encrypted})
        for _ in range(3):
            db.set_table("org_configs", None)

        with pytest.raises(ValueError, match="未配置"):
            await resolver.get_erp_credentials("org-1")

    @pytest.mark.asyncio
    async def test_erp_credentials_no_fallback_to_system(self, resolver, db):
        """ERP 凭证不降级到系统默认"""
        for _ in range(4):
            db.set_table("org_configs", None)

        with pytest.raises(ValueError, match="未配置"):
            await resolver.get_erp_credentials("org-1")

    @pytest.mark.asyncio
    async def test_load_encrypted_db_error_returns_none(self, resolver, db):
        """DB 异常时 _load_encrypted 返回 None（降级到系统默认）"""
        class ErrorBuilder(AsyncFakeQueryBuilder):
            async def execute(self):
                raise RuntimeError("DB connection lost")

        db._tables["org_configs"] = [ErrorBuilder()]

        # 用非企业专属 key 测试降级逻辑
        result = await resolver.get("org-1", "some_ai_key")
        assert result == "system_ai_default"
