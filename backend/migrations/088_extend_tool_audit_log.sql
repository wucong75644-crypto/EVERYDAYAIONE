-- 088: tool_audit_log 扩展（token 统计 + trace_id）
-- v6 Agent 架构细节对齐
-- 幂等：IF NOT EXISTS，可安全重复执行
-- 分区表：ALTER TABLE 主表自动传播到所有分区

ALTER TABLE tool_audit_log
    ADD COLUMN IF NOT EXISTS prompt_tokens INTEGER DEFAULT 0;

ALTER TABLE tool_audit_log
    ADD COLUMN IF NOT EXISTS completion_tokens INTEGER DEFAULT 0;

ALTER TABLE tool_audit_log
    ADD COLUMN IF NOT EXISTS trace_id TEXT DEFAULT '';

-- 索引：按 trace_id 查全链路
CREATE INDEX IF NOT EXISTS idx_tool_audit_trace_id
    ON tool_audit_log (trace_id)
    WHERE trace_id != '';
