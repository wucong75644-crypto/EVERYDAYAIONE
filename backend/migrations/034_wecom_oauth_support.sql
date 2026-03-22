-- 034_wecom_oauth_support.sql
-- 企微 OAuth 扫码登录支持

-- 1. wecom_user_mappings 增加 user_id 索引（绑定状态反查）
CREATE INDEX IF NOT EXISTS idx_wecom_mappings_user_id
ON wecom_user_mappings(user_id);

-- 2. wecom_user_mappings 增加 bound_at 字段（绑定时间）
ALTER TABLE wecom_user_mappings
ADD COLUMN IF NOT EXISTS bound_at TIMESTAMPTZ DEFAULT NOW();

-- 3. 确保 user_created_by 枚举包含 'wecom'
-- 注意：生产环境已通过 ALTER TYPE 添加，此处做幂等处理
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_enum
        WHERE enumlabel = 'wecom'
        AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'user_created_by')
    ) THEN
        ALTER TYPE user_created_by ADD VALUE 'wecom';
    END IF;
END$$;
