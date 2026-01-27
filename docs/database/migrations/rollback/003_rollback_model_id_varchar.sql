-- ========================================
-- Rollback: 003_change_model_id_to_varchar
-- Description: 回滚 model_id 类型变更
-- 警告：此回滚会丢失现有的字符串 model_id 数据
-- ========================================

-- 1. 删除 last_message_preview 字段
ALTER TABLE conversations DROP COLUMN IF EXISTS last_message_preview;

-- 2. 删除索引
DROP INDEX IF EXISTS idx_conversations_model_id;

-- 3. 清空 model_id 数据（VARCHAR 无法直接转回 UUID）
UPDATE conversations SET model_id = NULL WHERE model_id IS NOT NULL;

-- 4. 修改字段类型回 UUID
ALTER TABLE conversations
ALTER COLUMN model_id TYPE UUID USING NULL;

-- 5. 重新创建索引
CREATE INDEX IF NOT EXISTS idx_conversations_model_id ON conversations(model_id);

-- 6. 重新添加外键约束（如果 models 表存在）
-- ALTER TABLE conversations
-- ADD CONSTRAINT conversations_model_id_fkey
-- FOREIGN KEY (model_id) REFERENCES models(id);
