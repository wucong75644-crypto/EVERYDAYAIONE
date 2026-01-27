-- ========================================
-- Rollback: 001_add_image_url_to_messages
-- Description: 回滚 image_url 字段添加
-- ========================================

-- 删除 image_url 字段
ALTER TABLE messages DROP COLUMN IF EXISTS image_url;
