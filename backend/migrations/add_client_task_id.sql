-- 添加 client_task_id 字段到 tasks 表
-- 用于支持前端乐观订阅（提前生成 task_id 并订阅）

-- 添加字段
ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS client_task_id VARCHAR(100);

-- 添加索引（用于快速查询）
CREATE INDEX IF NOT EXISTS idx_tasks_client_task_id
ON tasks(client_task_id)
WHERE client_task_id IS NOT NULL;

-- 注释
COMMENT ON COLUMN tasks.client_task_id IS '前端生成的任务 ID（用于乐观订阅），与 external_task_id 形成映射关系';
