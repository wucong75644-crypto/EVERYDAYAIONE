-- 078: AI 主动沟通 — pending_interaction 表
-- 设计文档：docs/document/TECH_AI主动沟通与打断机制.md §4.1.1
--
-- 当 AI 调用 ask_user 工具追问用户时，冻结当前工具循环的 messages 数组
-- 和循环状态快照到此表。用户回复后从此表恢复，继续工具循环。

CREATE TABLE IF NOT EXISTS pending_interaction (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    org_id UUID,

    -- 冻结的 messages 数组（完整工具循环上下文，含 tool_calls + tool_results）
    frozen_messages JSONB NOT NULL,

    -- ask_user 的问题内容（前端展示 + 恢复时校验用）
    question TEXT NOT NULL,

    -- 来源标记：哪个 agent 发起的追问
    source VARCHAR(50) NOT NULL DEFAULT 'chat',

    -- ask_user 工具的 tool_call_id（恢复时构造 tool_result 消息用）
    tool_call_id VARCHAR(100) NOT NULL,

    -- 冻结时的工具循环状态快照
    -- {
    --   "turn": 3,
    --   "tools_called": ["local_global_stats", "ask_user"],
    --   "accumulated_text": "...",
    --   "content_blocks": [...],
    --   "tool_context_state": {...},
    --   "model_id": "gemini-3-pro",
    --   "budget_snapshot": {"turns_used": 3, "tokens_used": 12500}
    -- }
    loop_snapshot JSONB NOT NULL DEFAULT '{}',

    -- 状态
    -- pending  = 等待用户回复
    -- resumed  = 已恢复（用户回复了）
    -- expired  = 已过期（超时/用户换话题）
    status VARCHAR(20) NOT NULL DEFAULT 'pending',

    created_at TIMESTAMPTZ DEFAULT NOW(),
    expired_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '24 hours'
);

-- 一个对话同时只有一条 pending（UNIQUE 约束防并发）
CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_conv_active
    ON pending_interaction(conversation_id) WHERE status = 'pending';

-- 过期清理索引（定时任务扫描用）
CREATE INDEX IF NOT EXISTS idx_pending_expired
    ON pending_interaction(expired_at) WHERE status = 'pending';
