-- ============================================================
-- 103: organizations 表增加 per-org 加密密钥
--
-- 根因：原来所有企业共用 .env 的 ORG_CONFIG_ENCRYPT_KEY，
-- .env 覆盖事故导致全部企业 ERP 同步停摆。
-- 改为每企业独立密钥，存在 organizations 表，与 .env 解耦。
-- ============================================================

-- 1. 加列
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS encrypt_key TEXT;

COMMENT ON COLUMN organizations.encrypt_key IS
    'Per-org AES-256 加密密钥（base64），用于 org_configs 敏感数据加解密';
