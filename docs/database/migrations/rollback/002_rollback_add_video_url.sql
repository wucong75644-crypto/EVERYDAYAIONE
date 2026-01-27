-- ========================================
-- Rollback: 002_add_video_url_to_messages
-- Description: 回滚 video_url 字段添加
-- ========================================

-- 删除 video_url 字段
ALTER TABLE messages DROP COLUMN IF EXISTS video_url;
