-- Migration: 008_add_generation_params_to_messages.sql
-- Description: 添加 generation_params 字段以支持重新生成时继承原始任务参数
-- Date: 2026-01-28

-- 添加 generation_params 字段到 messages 表（JSONB 类型）
ALTER TABLE messages ADD COLUMN IF NOT EXISTS generation_params JSONB;

-- 添加注释
COMMENT ON COLUMN messages.generation_params IS '生成参数（图片/视频生成时保存，重新生成时复用）';

-- 字段结构说明：
-- {
--   "image": {
--     "aspectRatio": "1:1",
--     "resolution": "1K",
--     "outputFormat": "png",
--     "model": "gpt-image-1"
--   },
--   "video": {
--     "frames": "10",
--     "aspectRatio": "landscape",
--     "removeWatermark": true,
--     "model": "kling-v1-5"
--   }
-- }
