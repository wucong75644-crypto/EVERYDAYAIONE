-- ========================================
-- Migration: 001_add_image_url_to_messages
-- Date: 2026-01-23
-- Description: Add image_url column to messages table
-- ⚠️ DEPRECATED: 此迁移已废弃（2026-02-11）
-- 原因：统一消息系统重构后，使用 content 数组格式替代独立字段
-- 参考：migrate_content_format.py 迁移脚本
-- ========================================

-- Add image_url column to messages table
ALTER TABLE messages
ADD COLUMN IF NOT EXISTS image_url VARCHAR(500);

-- Add comment for documentation
COMMENT ON COLUMN messages.image_url IS 'URL of uploaded or generated image associated with this message';
