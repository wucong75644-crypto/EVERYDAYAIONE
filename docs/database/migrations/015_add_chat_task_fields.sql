-- 聊天任务恢复方案所需字段
-- 版本: 015
-- 日期: 2026-02-04

-- 1. 累积的流式内容（每 500ms 更新一次）
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS accumulated_content TEXT;

-- 2. 聊天任务使用的模型
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS model_id VARCHAR(100);

-- 3. 消耗的积分
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS total_credits INTEGER DEFAULT 0;

-- 4. 预分配的助手消息 ID（解决 ID 映射闪烁问题）
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS assistant_message_id UUID;

-- 添加索引：按 assistant_message_id 查询
CREATE INDEX IF NOT EXISTS idx_tasks_assistant_message_id ON tasks(assistant_message_id) WHERE assistant_message_id IS NOT NULL;

-- 添加注释
COMMENT ON COLUMN tasks.accumulated_content IS '聊天任务累积的流式内容，每500ms更新一次';
COMMENT ON COLUMN tasks.model_id IS '聊天任务使用的AI模型ID';
COMMENT ON COLUMN tasks.total_credits IS '聊天任务消耗的积分';
COMMENT ON COLUMN tasks.assistant_message_id IS '预分配的助手消息ID，避免前端React Key变化导致组件重新挂载';
