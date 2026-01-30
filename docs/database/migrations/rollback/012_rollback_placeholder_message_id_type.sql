-- 012_rollback_placeholder_message_id_type.sql
-- 回滚 placeholder_message_id 字段类型修改
-- 创建日期: 2026-01-30

-- 警告: 如果数据库中存在非UUID格式的数据，此回滚会失败
-- 建议: 在回滚前先清理或转换非UUID格式的数据

-- 回滚字段类型：VARCHAR(100) → UUID
ALTER TABLE tasks
  ALTER COLUMN placeholder_message_id TYPE UUID
  USING placeholder_message_id::UUID;

-- 恢复原注释
COMMENT ON COLUMN tasks.placeholder_message_id IS '前端占位符消息ID,用于更新UI';
