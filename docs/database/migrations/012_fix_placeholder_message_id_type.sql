-- 012_fix_placeholder_message_id_type.sql
-- 修复 placeholder_message_id 字段类型，支持前端自定义格式的临时ID
-- 创建日期: 2026-01-30
-- 问题: 前端使用 "pending-{timestamp}-{random}" 格式，不符合UUID规范
-- 影响: 导致任务保存失败，前端卡在"生成中"状态

-- 修改字段类型：UUID → VARCHAR(100)
ALTER TABLE tasks
  ALTER COLUMN placeholder_message_id TYPE VARCHAR(100)
  USING placeholder_message_id::VARCHAR;

-- 更新注释
COMMENT ON COLUMN tasks.placeholder_message_id IS '前端占位符消息ID (格式: pending-{timestamp}-{random} 或 restored-{task_id})';
