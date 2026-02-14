-- 添加 conversation_id NOT NULL 约束
-- 防止未来创建没有 conversation_id 的任务

-- 1. 首先清理现有的 NULL 数据（如果有）
-- 1.1 更新 running/pending 状态的任务为 failed
UPDATE tasks
SET
  status = 'failed',
  error_message = 'Orphan task: no conversation_id',
  completed_at = NOW()
WHERE conversation_id IS NULL
  AND status IN ('running', 'pending');

-- 1.2 删除所有 conversation_id 为 NULL 的任务（包括已完成/失败的）
-- 这些任务没有关联对话，已经失去意义
DELETE FROM tasks
WHERE conversation_id IS NULL;

-- 2. 添加 NOT NULL 约束
ALTER TABLE tasks
ALTER COLUMN conversation_id SET NOT NULL;

-- 3. 添加注释
COMMENT ON COLUMN tasks.conversation_id IS '对话 ID（必填，关联 conversations 表）';
