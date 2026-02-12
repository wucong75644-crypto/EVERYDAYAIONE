-- ========================================
-- Rollback: 002_add_video_url_to_messages
-- Description: 回滚 video_url 字段添加
-- ⚠️ DEPRECATED: 此回滚脚本已废弃（2026-02-11）
-- 原因：对应的迁移 002 已废弃，改用 content 数组格式
-- ========================================

-- 删除 video_url 字段
ALTER TABLE messages DROP COLUMN IF EXISTS video_url;
