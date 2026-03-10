-- 025: 模型评分审核日志表
-- 记录每次定时聚合的评分变化，支持人工审核

CREATE TABLE IF NOT EXISTS scoring_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    old_score FLOAT,
    new_score FLOAT NOT NULL,
    score_change FLOAT NOT NULL,
    sample_count INT NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}',
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'auto_applied',
    knowledge_node_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_model_task ON scoring_audit_log (model_id, task_type);
CREATE INDEX IF NOT EXISTS idx_audit_status ON scoring_audit_log (status);
CREATE INDEX IF NOT EXISTS idx_audit_created ON scoring_audit_log (created_at DESC);
