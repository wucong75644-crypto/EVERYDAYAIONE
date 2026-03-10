-- ========================================
-- 迁移脚本：user_subscriptions.model_id 从 UUID 改为 VARCHAR
-- ========================================
-- 创建时间: 2026-03-10
-- 说明: 订阅表使用字符串模型ID（如 'gemini-3-flash'），
--       解除对 models 表的外键依赖
-- ========================================

-- 1. 删除外键约束
ALTER TABLE user_subscriptions
DROP CONSTRAINT IF EXISTS user_subscriptions_model_id_fkey;

-- 2. 清除旧的 UUID 格式数据（与新字符串 ID 不兼容）
TRUNCATE TABLE user_subscriptions;

-- 3. 修改字段类型
ALTER TABLE user_subscriptions
ALTER COLUMN model_id TYPE VARCHAR(100) USING model_id::text;

-- 4. 确保索引存在
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_model_id
  ON user_subscriptions(model_id);

-- 5. 确保唯一约束存在（user_id + model_id）
ALTER TABLE user_subscriptions
DROP CONSTRAINT IF EXISTS user_subscriptions_user_id_model_id_key;

ALTER TABLE user_subscriptions
ADD CONSTRAINT user_subscriptions_user_id_model_id_key UNIQUE (user_id, model_id);

COMMENT ON COLUMN user_subscriptions.model_id IS '模型标识符（字符串，如 gemini-3-flash）';
