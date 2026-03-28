"""
企业配置解析链

优先级：企业自有配置（AES 加密存储） > 系统默认配置（.env）。
散客直接返回系统默认值。
"""

from typing import Optional

from loguru import logger

from core.config import get_settings
from core.crypto import aes_decrypt, aes_encrypt


class OrgConfigResolver:
    """企业配置解析器"""

    def __init__(self, db):
        self.db = db
        self._settings = get_settings()

    def _get_encrypt_key(self) -> str:
        """获取加密密钥，未配置时报错"""
        key = self._settings.org_config_encrypt_key
        if not key:
            raise ValueError(
                "ORG_CONFIG_ENCRYPT_KEY 未配置，无法读写企业配置。"
                "请运行 python -c 'from core.crypto import generate_encrypt_key; print(generate_encrypt_key())' 生成密钥"
            )
        return key

    def get(self, org_id: str | None, key: str) -> str | None:
        """
        获取配置值。

        优先级：企业配置 > 系统默认。
        散客（org_id=None）直接返回系统默认。

        Args:
            org_id: 企业 ID（None=散客）
            key: 配置键名（如 kuaimai_app_key）

        Returns:
            配置值字符串，或 None
        """
        if org_id:
            val = self._load_encrypted(org_id, key)
            if val is not None:
                return val

        # 降级到系统默认
        return getattr(self._settings, key, None)

    def set(
        self, org_id: str, key: str, value: str, updated_by: str,
    ) -> None:
        """
        写入企业配置（AES 加密存储）。

        Args:
            org_id: 企业 ID
            key: 配置键名
            value: 明文值
            updated_by: 操作者 user_id
        """
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
            "org_id", org_id
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
        """
        加载企业 ERP 凭证，缺失则报错。

        注意：ERP 凭证不降级到系统默认（每个企业必须配自己的 key），
        防止企业误用平台公共凭证。

        Returns:
            {"kuaimai_app_key": ..., "kuaimai_app_secret": ..., ...}

        Raises:
            ValueError: 缺失必要配置
        """
        keys = [
            "kuaimai_app_key",
            "kuaimai_app_secret",
            "kuaimai_access_token",
            "kuaimai_refresh_token",
        ]
        creds = {}
        for k in keys:
            # 只读企业自有配置，不降级到系统默认
            val = self._load_encrypted(org_id, k)
            if not val:
                raise ValueError(f"企业 ERP 未配置 {k}，请联系管理员")
            creds[k] = val
        return creds

    def _load_encrypted(self, org_id: str, key: str) -> str | None:
        """从 org_configs 表读取并解密"""
        try:
            result = (
                self.db.table("org_configs")
                .select("config_value_encrypted")
                .eq("org_id", org_id)
                .eq("config_key", key)
                .single()
                .execute()
            )
            if not result.data:
                return None

            encrypt_key = self._get_encrypt_key()
            return aes_decrypt(result.data["config_value_encrypted"], encrypt_key)
        except ValueError:
            raise
        except Exception as e:
            logger.warning(
                f"Failed to load org config | org_id={org_id} | key={key} | error={e}"
            )
            return None
