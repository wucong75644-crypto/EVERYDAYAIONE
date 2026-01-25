-- Migration: 004_add_is_error_to_messages.sql
-- Description: 添加 is_error 字段以支持错误消息的保存和重新生成功能
-- Date: 2026-01-25

-- 添加 is_error 字段到 messages 表
ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_error BOOLEAN DEFAULT false;

-- 添加索引以优化查询性能（查询对话的历史消息）
CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
ON messages(conversation_id, created_at);

-- 添加注释
COMMENT ON COLUMN messages.is_error IS '标记消息是否为错误消息（AI 调用失败时保存）';
