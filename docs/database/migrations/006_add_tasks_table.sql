-- 006_add_tasks_table.sql
-- 创建任务追踪表（用于任务队列限制）
-- 创建日期：2026-01-26

-- 创建任务表
CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    type VARCHAR(20) NOT NULL CHECK (type IN ('chat', 'image', 'video')),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    credits_locked INTEGER DEFAULT 0,
    credits_used INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_conversation ON tasks(conversation_id);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);

-- 启用行级安全
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;

-- 用户只能查看自己的任务
DROP POLICY IF EXISTS "Users can view own tasks" ON tasks;
CREATE POLICY "Users can view own tasks" ON tasks FOR SELECT
    USING (auth.uid() = user_id);

-- 服务角色可以管理所有任务
DROP POLICY IF EXISTS "Service role can manage all tasks" ON tasks;
CREATE POLICY "Service role can manage all tasks" ON tasks FOR ALL
    USING (auth.role() = 'service_role');

-- 添加注释
COMMENT ON TABLE tasks IS '任务追踪表，用于限制并发任务数量';
COMMENT ON COLUMN tasks.type IS '任务类型：chat/image/video';
COMMENT ON COLUMN tasks.status IS '任务状态：pending/running/completed/failed/cancelled';
COMMENT ON COLUMN tasks.credits_locked IS '锁定的积分数量';
COMMENT ON COLUMN tasks.credits_used IS '实际使用的积分数量';
