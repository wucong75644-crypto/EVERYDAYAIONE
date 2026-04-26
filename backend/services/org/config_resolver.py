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


# ── 核心逻辑层（不碰 DB）────────────────────────────


class _ConfigResolverCore:
    """纯逻辑：加密/解密、key 校验、降级规则。不包含 DB 访问。"""

    ERP_CREDENTIAL_KEYS = [
        "kuaimai_app_key",
        "kuaimai_app_secret",
        "kuaimai_access_token",
        "kuaimai_refresh_token",
    ]

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
        self._load_org_encrypt_key(org_id)
        encrypt_key = self._get_encrypt_key(org_id)
        encrypted = aes_encrypt(value, encrypt_key)
        self.db.table("org_configs").upsert(
            {
                "org_id": org_id,
                "config_key": key,
                "config_value_encrypted": encrypted,
                "updated_by": updated_by,
            },
            on_conflict="org_id,config_key",
        ).execute()
        logger.info(f"Org config set | org_id={org_id} | key={key} | by={updated_by}")

    def delete(self, org_id: str, key: str) -> None:
        """删除企业配置"""
        self.db.table("org_configs").delete().eq(
            "org_id", org_id,
        ).eq("config_key", key).execute()
        logger.info(f"Org config deleted | org_id={org_id} | key={key}")

    def list_keys(self, org_id: str) -> list[str]:
        """列出企业已配置的 key（不返回值）"""
        result = (
            self.db.table("org_configs")
            .select("config_key")
            .eq("org_id", org_id)
            .execute()
        )
        return [r["config_key"] for r in (result.data or [])]

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

        原子性: 单条 upsert × 2 — schema 反射白名单已覆盖 org_configs
        的复合唯一键 (org_id, config_key)，不需要显式事务。
        """
        from datetime import datetime, timezone
        self._load_org_encrypt_key(org_id)
        encrypt_key = self._get_encrypt_key(org_id)
        now = datetime.now(timezone.utc)
        for key, val in [
            ("kuaimai_access_token", access_token),
            ("kuaimai_refresh_token", refresh_token),
        ]:
            encrypted = aes_encrypt(val, encrypt_key)
            self.db.table("org_configs").upsert(
                {
                    "org_id": org_id,
                    "config_key": key,
                    "config_value_encrypted": encrypted,
                    "updated_by": None,  # 系统自动刷新
                    "updated_at": now,
                },
                on_conflict="org_id,config_key",
            ).execute()
        logger.info(f"ERP token auto-refreshed and persisted | org_id={org_id}")

    def list_orgs_with_wecom_bot(self) -> list[dict]:
        """返回所有配了 wecom_bot_id + wecom_bot_secret 的企业。

        Returns:
            [{"org_id": ..., "bot_id": ..., "bot_secret": ..., "corp_id": ...}, ...]
        """
        # 查所有配了 wecom_bot_id 的 org_id
        result = (
            self.db.table("org_configs")
            .select("org_id")
            .eq("config_key", "wecom_bot_id")
            .execute()
        )
        org_ids = [r["org_id"] for r in (result.data or [])]
        if not org_ids:
            return []

        orgs = []
        for oid in org_ids:
            bot_id = self._load_encrypted(oid, "wecom_bot_id")
            bot_secret = self._load_encrypted(oid, "wecom_bot_secret")
            if not bot_id or not bot_secret:
                continue
            # 从 organizations 表取 corp_id
            org_result = (
                self.db.table("organizations")
                .select("wecom_corp_id")
                .eq("id", oid)
                .maybe_single()
                .execute()
            )
            corp_id = (org_result.data or {}).get("wecom_corp_id", "")
            orgs.append({
                "org_id": oid,
                "bot_id": bot_id,
                "bot_secret": bot_secret,
                "corp_id": corp_id or "",
            })
        return orgs

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
    """异步企业配置解析器（传入 AsyncLocalDBClient）"""

    async def _load_org_encrypt_key(self, org_id: str) -> str | None:
        """从 organizations 表读取企业专属加密密钥（异步，带内存缓存）"""
        if org_id in self._org_key_cache:
            return self._org_key_cache[org_id]
        try:
            result = await (
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

    async def get(self, org_id: str | None, key: str) -> str | None:
        """获取配置值。企业专属 key 不降级，AI/平台级 key 降级到 .env。"""
        if org_id:
            val = await self._load_encrypted(org_id, key)
            if val is not None:
                return val
        # 企业专属 key：未配置时不降级到 .env
        if key in self.ENTERPRISE_ONLY_KEYS:
            return None
        return self._get_default(key)

    async def get_erp_credentials(self, org_id: str) -> dict:
        """加载企业 ERP 凭证，缺失则报错。不降级到系统默认。"""
        creds = {}
        for k in self.ERP_CREDENTIAL_KEYS:
            val = await self._load_encrypted(org_id, k)
            if not val:
                raise ValueError(f"企业 ERP 未配置 {k}，请联系管理员")
            creds[k] = val
        return creds

    async def _async_get_encrypt_key(self, org_id: str | None) -> str:
        """异步版获取加密密钥：先 await 预热缓存，再走同步链路。"""
        if org_id:
            await self._load_org_encrypt_key(org_id)
        return self._get_encrypt_key(org_id)

    async def update_erp_token(
        self, org_id: str, access_token: str, refresh_token: str,
    ) -> None:
        """ERP token 自动刷新成功后回写 DB（异步版，供 worker / dead letter 用）。

        原子性: 单条 upsert × 2 — schema 反射白名单已覆盖 org_configs
        的复合唯一键 (org_id, config_key)，不需要显式事务。
        """
        from datetime import datetime, timezone
        encrypt_key = await self._async_get_encrypt_key(org_id)
        now = datetime.now(timezone.utc)
        for key, val in [
            ("kuaimai_access_token", access_token),
            ("kuaimai_refresh_token", refresh_token),
        ]:
            encrypted = aes_encrypt(val, encrypt_key)
            await (
                self.db.table("org_configs").upsert(
                    {
                        "org_id": org_id,
                        "config_key": key,
                        "config_value_encrypted": encrypted,
                        "updated_by": None,  # 系统自动刷新
                        "updated_at": now,
                    },
                    on_conflict="org_id,config_key",
                ).execute()
            )
        logger.info(f"ERP token auto-refreshed and persisted | org_id={org_id}")

    async def _load_encrypted(self, org_id: str, key: str) -> str | None:
        """从 org_configs 表读取并解密（异步）"""
        try:
            # 预热缓存：确保 _decrypt_result 能同步拿到 org 密钥
            await self._load_org_encrypt_key(org_id)
            result = await (
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
