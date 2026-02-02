-- 014_add_placeholder_created_at.sql
-- 添加占位符创建时间字段，用于任务恢复时保持消息排序
-- 创建日期: 2026-02-02

-- 添加占位符创建时间字段
ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS placeholder_created_at TIMESTAMPTZ;

-- 添加注释
COMMENT ON COLUMN tasks.placeholder_created_at IS '占位符消息的原始创建时间，用于任务恢复时保持消息排序';
