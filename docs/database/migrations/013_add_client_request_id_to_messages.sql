-- Migration: 013_add_client_request_id_to_messages.sql
-- Description: 添加 client_request_id 字段以支持乐观更新消息替换
-- Date: 2026-01-30
-- 目的: 解决聊天消息立即显示问题（使用本地预览 URL，后端返回时替换）

-- 添加 client_request_id 字段到 messages 表
ALTER TABLE messages ADD COLUMN IF NOT EXISTS client_request_id VARCHAR(100);

-- 添加索引（加速查询和替换操作）
CREATE INDEX IF NOT EXISTS idx_messages_client_request_id
ON messages(client_request_id)
WHERE client_request_id IS NOT NULL;

-- 添加注释
COMMENT ON COLUMN messages.client_request_id IS '客户端请求ID，用于乐观更新时匹配临时消息和真实消息（格式：req-{timestamp}-{random}）';

-- 字段说明：
-- - 前端发送消息时生成唯一的 client_request_id（如 req-1706597123456-abc123）
-- - 后端保存消息时存储此 ID
-- - 后端流式返回时，在用户消息中携带此 ID
-- - 前端收到后，根据 client_request_id 替换临时消息（temp-xxx）为真实消息（UUID）
-- - 这样可以避免消息重复显示，同时支持本地预览 URL 立即显示
