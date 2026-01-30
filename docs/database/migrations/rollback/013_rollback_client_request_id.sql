-- Rollback: 013_rollback_client_request_id.sql
-- Description: 回滚 client_request_id 字段
-- Date: 2026-01-30

-- 删除索引
DROP INDEX IF EXISTS idx_messages_client_request_id;

-- 删除字段
ALTER TABLE messages DROP COLUMN IF EXISTS client_request_id;
