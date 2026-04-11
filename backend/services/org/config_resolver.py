"""
企业配置解析链

优先级：企业自有配置（AES 加密存储） > 系统默认配置（.env）。
散客直接返回系统默认值。

提供同步版 OrgConfigResolver（API 路由用）和异步版 AsyncOrgConfigResolver（Worker/消费者用）。
共享逻辑在 _ConfigResolverCore 中，DB 访问各自实现。
"""

from typing import Optional

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

    def _get_encrypt_key(self) -> str:
        """获取加密密钥，未配置时报错"""
        key = self._settings.org_config_encrypt_key
        if not key:
            raise ValueError(
                "ORG_CONFIG_ENCRYPT_KEY 未配置，无法读写企业配置。"
                "请运行 python -c 'from core.crypto import generate_encrypt_key; "
                "print(generate_encrypt_key())' 生成密钥"
            )
        return key

    def _decrypt_result(self, result_data: dict | None) -> str | None:
        """解密查询结果"""
        if not result_data:
            return None
        encrypt_key = self._get_encrypt_key()
        return aes_decrypt(result_data["config_value_encrypted"], encrypt_key)

    def _get_default(self, key: str) -> str | None:
        """降级到系统默认配置"""
        return getattr(self._settings, key, None)


# ── 同步版（API 路由、ToolExecutor 用）────────────────


class OrgConfigResolver(_ConfigResolverCore):
    """同步企业配置解析器（传入同步 LocalDBClient）"""

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
        encrypt_key = self._get_encrypt_key()
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

        Why: 系统后台自动 refresh 没有用户上下文，updated_by 留空。
        AES 加密后写入 org_configs，与初次绑定走的加密器一致。

        原子性: 单条 upsert × 2 — schema 反射白名单已覆盖 org_configs
        的复合唯一键 (org_id, config_key)，不需要显式事务。

        显式 updated_at: 因为 UPSERT 触发 ON CONFLICT 时不会重新应用列默认值，
        必须在 payload 里带上 updated_at，运维才能从该字段看到 token 最后刷新时间。
        """
        from datetime import datetime, timezone
        encrypt_key = self._get_encrypt_key()
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
            result = (
                self.db.table("org_configs")
                .select("config_value_encrypted")
                .eq("org_id", org_id)
                .eq("config_key", key)
                .maybe_single()
                .execute()
            )
            return self._decrypt_result(result.data)
        except Exception as e:
            logger.warning(
                f"Failed to load org config | org_id={org_id} | key={key} | error={e}"
            )
            return None


# ── 异步版（Worker、死信消费者用）─────────────────────


class AsyncOrgConfigResolver(_ConfigResolverCore):
    """异步企业配置解析器（传入 AsyncLocalDBClient）"""

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

    async def update_erp_token(
        self, org_id: str, access_token: str, refresh_token: str,
    ) -> None:
        """ERP token 自动刷新成功后回写 DB（异步版，供 worker / dead letter 用）。

        Why: 后台 worker 自动 refresh 没有用户上下文，updated_by 留空。
        AES 加密后写入 org_configs，与初次绑定走的加密器一致。

        历史教训: 多租户改造前，KuaiMaiClient 是 singleton，refresh 后的新 token
        靠内存 + Redis 续命，能扛住 token 30 天硬寿命。改成 per-task client
        后内存丢失，DB 又没回写，于是 12 天里所有 worker 一直用初始 token，
        到期日 16:29 集体雪崩。这个方法是闭环的关键节点。

        显式 updated_at: 因为 UPSERT 触发 ON CONFLICT 时不会重新应用列默认值，
        必须在 payload 里带上 updated_at，运维才能从该字段看到 token 最后刷新时间。
        """
        from datetime import datetime, timezone
        encrypt_key = self._get_encrypt_key()
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
            result = await (
                self.db.table("org_configs")
                .select("config_value_encrypted")
                .eq("org_id", org_id)
                .eq("config_key", key)
                .maybe_single()
                .execute()
            )
            return self._decrypt_result(result.data)
        except Exception as e:
            logger.warning(
                f"Failed to load org config | org_id={org_id} | key={key} | error={e}"
            )
            return None
