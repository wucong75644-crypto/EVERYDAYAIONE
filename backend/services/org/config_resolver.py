"""
企业配置解析链

优先级：企业自有配置（AES 加密存储） > 系统默认配置（.env）。
散客直接返回系统默认值。

提供同步版 OrgConfigResolver（API 路由用）和异步版 AsyncOrgConfigResolver（Worker/消费者用）。
共享逻辑在 _ConfigResolverCore 中，DB 访问各自实现。
"""

from loguru import logger

from core.config import get_settings
from core.crypto import aes_decrypt, aes_encrypt
from core.exceptions import PermissionDeniedError


# ── 核心逻辑层（不碰 DB）────────────────────────────


class _ConfigResolverCore:
    """纯逻辑：加密/解密、key 校验、降级规则。不包含 DB 访问。"""

    ERP_CREDENTIAL_KEYS = [
        "kuaimai_app_key",
        "kuaimai_app_secret",
        "kuaimai_access_token",
        "kuaimai_refresh_token",
    ]
    SUPPORTED_CONFIG_KEYS = frozenset({
        "ai_google_api_key",
        "ai_kie_api_key",
        "ai_openrouter_api_key",
        "erp_warehouse_ids",
        "kuaimai_access_token",
        "kuaimai_app_key",
        "kuaimai_app_secret",
        "kuaimai_refresh_token",
        "wecom_agent_id",
        "wecom_agent_secret",
        "wecom_bot_id",
        "wecom_bot_secret",
    })

    # 企业专属 key — 未配置时返回 None，不降级到 .env
    # 这些凭证指向企业自己的资源，降级到别人的会导致数据泄露
    ENTERPRISE_ONLY_KEYS = {
        # ERP
        "kuaimai_app_key", "kuaimai_app_secret",
        "kuaimai_access_token", "kuaimai_refresh_token",
        # 企微智能机器人
        "wecom_bot_id", "wecom_bot_secret",
        # 企微自建应用（扫码登录）
        "wecom_agent_id", "wecom_agent_secret",
    }

    def __init__(self, db):
        self.db = db
        self._settings = get_settings()

    # 进程级 per-org 密钥缓存（sync/async 共享，encrypt_key 变更需重启生效）
    _org_key_cache: dict[str, str | None] = {}

    def _get_encrypt_key(self, org_id: str | None = None) -> str:
        """获取加密密钥：优先企业专属密钥（从缓存），降级到全局密钥。

        优先级：_org_key_cache[org_id] > .env ORG_CONFIG_ENCRYPT_KEY
        per-org 密钥与 .env 解耦，避免部署覆盖 .env 导致全企业停摆。
        缓存由子类的 _load_org_encrypt_key() 填充。
        """
        if org_id:
            org_key = self._org_key_cache.get(org_id)
            if org_key:
                return org_key
        # 降级到全局密钥（兼容未迁移的企业）
        key = self._settings.org_config_encrypt_key
        if not key:
            raise ValueError(
                "加密密钥未配置：organizations.encrypt_key 为空且"
                " ORG_CONFIG_ENCRYPT_KEY 未设置"
            )
        return key

    def _decrypt_result(
        self, result_data: dict | None, org_id: str | None = None,
    ) -> str | None:
        """解密查询结果"""
        if not result_data:
            return None
        encrypt_key = self._get_encrypt_key(org_id)
        return aes_decrypt(result_data["config_value_encrypted"], encrypt_key)

    def _get_default(self, key: str) -> str | None:
        """降级到系统默认配置"""
        return getattr(self._settings, key, None)

    @classmethod
    def _validate_config_key(cls, key: str) -> str:
        normalized = key.strip()
        if normalized not in cls.SUPPORTED_CONFIG_KEYS:
            raise ValueError("不支持的企业配置键")
        return normalized

    @staticmethod
    def _raise_capability_error(exc: Exception) -> None:
        error = str(exc)
        if (
            "GOVERNANCE_CONFIG_KEY_INVALID" in error
            or "GOVERNANCE_CONFIG_VALUE_INVALID" in error
        ):
            raise ValueError("企业配置参数无效") from exc
        if (
            "GOVERNANCE_" in error
            or "WORKER_ERP_TOKEN_SCOPE_MISMATCH" in error
        ):
            raise PermissionDeniedError("无权修改该企业配置") from exc


# ── 同步版（API 路由、ToolExecutor 用）────────────────


class OrgConfigResolver(_ConfigResolverCore):
    """同步企业配置解析器（传入同步 LocalDBClient）"""

    def _load_org_encrypt_key(self, org_id: str) -> str | None:
        """从 organizations 表读取企业专属加密密钥（同步，带内存缓存）"""
        if org_id in self._org_key_cache:
            return self._org_key_cache[org_id]
        try:
            result = (
                self.db.table("organizations")
                .select("encrypt_key")
                .eq("id", org_id)
                .maybe_single()
                .execute()
            )
            key = (result.data or {}).get("encrypt_key")
            self._org_key_cache[org_id] = key
            return key
        except Exception as e:
            logger.warning(f"Failed to load org encrypt_key | org_id={org_id} | error={e}")
            return None

    def get(self, org_id: str | None, key: str) -> str | None:
        """获取配置值。企业专属 key 不降级，AI/平台级 key 降级到 .env。"""
        if org_id:
            val = self._load_encrypted(org_id, key)
            if val is not None:
                return val
        # 企业专属 key：未配置时不降级到 .env
        if key in self.ENTERPRISE_ONLY_KEYS:
            return None
        return self._get_default(key)

    def set(
        self, org_id: str, key: str, value: str, updated_by: str,
    ) -> None:
        """写入企业配置（AES 加密存储）"""
        config_key = self._validate_config_key(key)
        if not value:
            raise ValueError("企业配置值不能为空")
        self._load_org_encrypt_key(org_id)
        encrypt_key = self._get_encrypt_key(org_id)
        encrypted = aes_encrypt(value, encrypt_key)
        try:
            self.db.rpc("set_governed_org_config", {
                "p_org_id": org_id,
                "p_config_key": config_key,
                "p_config_value_encrypted": encrypted,
            }).execute()
        except Exception as exc:
            self._raise_capability_error(exc)
            raise
        logger.info(
            f"Org config set | org_id={org_id} | key={config_key} | "
            f"by={updated_by}"
        )

    def delete(self, org_id: str, key: str) -> None:
        """删除企业配置"""
        config_key = self._validate_config_key(key)
        try:
            self.db.rpc("delete_governed_org_config", {
                "p_org_id": org_id,
                "p_config_key": config_key,
            }).execute()
        except Exception as exc:
            self._raise_capability_error(exc)
            raise
        logger.info(f"Org config deleted | org_id={org_id} | key={config_key}")

    def list_keys(self, org_id: str) -> list[str]:
        """列出企业已配置的 key（不返回值）"""
        return [
            item["key"]
            for item in self.get_config_status(org_id)
            if item.get("configured")
        ]

    def get_config_status(self, org_id: str) -> list[dict]:
        """返回企业配置状态事实，不读取或解密配置值。"""
        try:
            result = self.db.rpc("list_governed_org_config_status", {
                "p_org_id": org_id,
            }).execute()
        except Exception as exc:
            self._raise_capability_error(exc)
            raise
        return result.data or []

    def get_erp_credentials(self, org_id: str) -> dict:
        """加载企业 ERP 凭证，缺失则报错。不降级到系统默认。"""
        creds = {}
        for k in self.ERP_CREDENTIAL_KEYS:
            val = self._load_encrypted(org_id, k)
            if not val:
                raise ValueError(f"企业 ERP 未配置 {k}，请联系管理员")
            creds[k] = val
        return creds

    def update_erp_token(
        self, org_id: str, access_token: str, refresh_token: str,
    ) -> None:
        """ERP token 自动刷新成功后回写 DB（同步版）。

        两份密文通过 runtime 管理员能力在同一数据库事务原子更新。
        """
        if not access_token or not refresh_token:
            raise ValueError("ERP Token 不能为空")
        self._load_org_encrypt_key(org_id)
        encrypt_key = self._get_encrypt_key(org_id)
        try:
            self.db.rpc("set_governed_org_erp_tokens", {
                "p_org_id": org_id,
                "p_access_token_encrypted": aes_encrypt(
                    access_token, encrypt_key,
                ),
                "p_refresh_token_encrypted": aes_encrypt(
                    refresh_token, encrypt_key,
                ),
            }).execute()
        except Exception as exc:
            self._raise_capability_error(exc)
            raise
        logger.info(f"ERP token auto-refreshed and persisted | org_id={org_id}")

    def _load_encrypted(self, org_id: str, key: str) -> str | None:
        """从 org_configs 表读取并解密（同步）"""
        try:
            # 预热缓存：确保 _decrypt_result → _get_encrypt_key 能取到 org 密钥
            self._load_org_encrypt_key(org_id)
            result = (
                self.db.table("org_configs")
                .select("config_value_encrypted")
                .eq("org_id", org_id)
                .eq("config_key", key)
                .maybe_single()
                .execute()
            )
            return self._decrypt_result(result.data, org_id)
        except Exception as e:
            logger.warning(
                f"Failed to load org config | org_id={org_id} | key={key} | error={e}"
            )
            return None


# ── 异步版（Worker、死信消费者用）─────────────────────


class AsyncOrgConfigResolver(_ConfigResolverCore):
    """Compatibility adapter backed by the governed Sync configuration plane."""

    def __init__(self, db):
        super().__init__(db)
        from services.configuration.sync_resolver import (
            SyncConfigurationResolver,
        )

        self._sync_resolver = SyncConfigurationResolver(db)
        self._erp_cache = {}

    async def get(self, org_id: str | None, key: str) -> str | None:
        """Resolve the legacy ERP key names from one governed bundle."""
        if org_id is None:
            return None if key in self.ENTERPRISE_ONLY_KEYS else self._get_default(key)
        credentials = await self._credentials(org_id)
        values = {
            "kuaimai_app_key": credentials.app_key,
            "kuaimai_app_secret": credentials.app_secret,
            "kuaimai_access_token": credentials.access_token,
            "kuaimai_refresh_token": credentials.refresh_token,
            "erp_warehouse_ids": ",".join(credentials.warehouse_ids),
        }
        return values.get(key)

    async def get_erp_credentials(self, org_id: str) -> dict:
        """Expose the legacy dictionary shape without reading legacy tables."""
        credentials = await self._credentials(org_id)
        return {
            "kuaimai_app_key": credentials.app_key,
            "kuaimai_app_secret": credentials.app_secret,
            "kuaimai_access_token": credentials.access_token,
            "kuaimai_refresh_token": credentials.refresh_token,
        }

    async def update_erp_token(
        self, org_id: str, access_token: str, refresh_token: str,
    ) -> None:
        """Atomically rotate the governed ERP token pair."""
        credentials = await self._credentials(org_id)
        await self._sync_resolver.commit_erp_token_pair(
            credentials,
            access_token,
            refresh_token,
        )
        self._erp_cache.pop(org_id, None)
        logger.info(f"ERP token auto-refreshed and persisted | org_id={org_id}")

    async def _credentials(self, org_id: str):
        credentials = self._erp_cache.get(org_id)
        if credentials is None:
            credentials = await self._sync_resolver.erp_credentials(org_id)
            self._erp_cache[org_id] = credentials
        return credentials
