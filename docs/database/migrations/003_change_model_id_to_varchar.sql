-- ========================================
-- 迁移脚本：将 conversations.model_id 从 UUID 改为 VARCHAR
-- ========================================
-- 创建时间: 2026-01-24
-- 说明: 支持使用字符串模型ID（如 'gemini-3-pro'）而非UUID
-- ========================================

-- 1. 删除外键约束
ALTER TABLE conversations
DROP CONSTRAINT IF EXISTS conversations_model_id_fkey;

-- 2. 删除索引
DROP INDEX IF EXISTS idx_conversations_model_id;

-- 3. 修改字段类型（如果有现有UUID数据会丢失，但目前应该为空或NULL）
ALTER TABLE conversations
ALTER COLUMN model_id TYPE VARCHAR(100) USING model_id::text;

-- 4. 重新创建索引
CREATE INDEX IF NOT EXISTS idx_conversations_model_id ON conversations(model_id);

-- 5. 添加注释
COMMENT ON COLUMN conversations.model_id IS '模型标识符（字符串，如 gemini-3-pro）';

-- 6. 添加 last_message_preview 字段（如果不存在）
ALTER TABLE conversations
ADD COLUMN IF NOT EXISTS last_message_preview TEXT;
