-- ========================================
-- Rollback: 004_add_is_error_to_messages
-- Description: 回滚 is_error 字段和索引
-- ========================================

-- 1. 删除索引
DROP INDEX IF EXISTS idx_messages_conversation_created;

-- 2. 删除 is_error 字段
ALTER TABLE messages DROP COLUMN IF EXISTS is_error;
