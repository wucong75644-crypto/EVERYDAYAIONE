"""
一次性迁移脚本：为每个企业生成独立加密密钥，并重新加密 org_configs 数据。

用法：
    cd backend && source venv/bin/activate
    python scripts/migrate_org_encrypt_key.py

流程：
    1. 为每个没有 encrypt_key 的企业生成新密钥，写入 organizations.encrypt_key
    2. 用旧全局密钥（.env ORG_CONFIG_ENCRYPT_KEY）解密 org_configs 数据
    3. 用新企业密钥重新加密，写回 org_configs
    4. 验证：用新密钥读取每条记录，确认可解密
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import get_settings
from core.crypto import aes_decrypt, aes_encrypt, generate_encrypt_key
from core.database import get_db
from loguru import logger


def main() -> None:
    settings = get_settings()
    db = get_db()
    old_key = settings.org_config_encrypt_key

    if not old_key:
        logger.error("ORG_CONFIG_ENCRYPT_KEY 未配置，无法迁移")
        sys.exit(1)

    # 1. 查所有活跃企业
    orgs = (
        db.table("organizations")
        .select("id, name, encrypt_key")
        .eq("status", "active")
        .execute()
    )
    if not orgs.data:
        logger.info("无活跃企业，跳过")
        return

    for org in orgs.data:
        org_id = str(org["id"])
        org_name = org.get("name", "?")

        # 2. 生成新密钥（已有则跳过）
        if org.get("encrypt_key"):
            new_key = org["encrypt_key"]
            logger.info(f"[{org_name}] 已有密钥，跳过生成")
        else:
            new_key = generate_encrypt_key()
            db.table("organizations").update(
                {"encrypt_key": new_key}
            ).eq("id", org_id).execute()
            logger.info(f"[{org_name}] 新密钥已写入 organizations")

        # 3. 查该企业所有 org_configs 记录
        configs = (
            db.table("org_configs")
            .select("config_key, config_value_encrypted")
            .eq("org_id", org_id)
            .execute()
        )
        if not configs.data:
            logger.info(f"[{org_name}] 无 org_configs 记录")
            continue

        migrated = 0
        for row in configs.data:
            config_key = row["config_key"]
            enc_val = row["config_value_encrypted"]
            if not enc_val:
                continue

            # 3a. 用旧密钥解密
            try:
                plaintext = aes_decrypt(enc_val, old_key)
            except Exception:
                logger.warning(
                    f"[{org_name}] {config_key} 旧密钥解密失败，跳过"
                )
                continue

            # 3b. 用新密钥加密
            new_encrypted = aes_encrypt(plaintext, new_key)

            # 3c. 写回
            db.table("org_configs").update(
                {"config_value_encrypted": new_encrypted}
            ).eq("org_id", org_id).eq("config_key", config_key).execute()
            migrated += 1

        logger.info(f"[{org_name}] 迁移完成 | {migrated}/{len(configs.data)} 条")

        # 4. 验证
        failed = 0
        for row in configs.data:
            config_key = row["config_key"]
            result = (
                db.table("org_configs")
                .select("config_value_encrypted")
                .eq("org_id", org_id)
                .eq("config_key", config_key)
                .maybe_single()
                .execute()
            )
            if not result.data:
                continue
            try:
                aes_decrypt(result.data["config_value_encrypted"], new_key)
            except Exception:
                logger.error(f"[{org_name}] {config_key} 新密钥验证失败!")
                failed += 1

        if failed:
            logger.error(f"[{org_name}] 验证失败 {failed} 条，请检查!")
        else:
            logger.info(f"[{org_name}] 验证全部通过 ✅")


if __name__ == "__main__":
    main()
