-- 046: 工具调用结构化审计日志（分区表）
-- 记录每次 Agent 工具调用的完整信息，支持按请求/工具/企业/时间段查询
-- 按月分区 + 90 天保留策略

-- 1. 分区主表
CREATE TABLE IF NOT EXISTS tool_audit_log (
    id UUID DEFAULT gen_random_uuid(),
    -- 上下文
    task_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    org_id TEXT NOT NULL,
    -- 工具执行
    tool_name TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    turn INT NOT NULL DEFAULT 0,
    args_hash TEXT,                              -- MD5(sorted args JSON)，不存明文
    -- 性能
    result_length INT DEFAULT 0,
    elapsed_ms INT DEFAULT 0,
    -- 状态
    status TEXT NOT NULL DEFAULT 'success',      -- success / timeout / error
    is_cached BOOLEAN DEFAULT FALSE,
    is_truncated BOOLEAN DEFAULT FALSE,
    -- 时间
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- 分区键必须在主键中
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- 2. 索引（自动应用到所有分区）
CREATE INDEX IF NOT EXISTS idx_tool_audit_task
    ON tool_audit_log (task_id);
CREATE INDEX IF NOT EXISTS idx_tool_audit_org_time
    ON tool_audit_log (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tool_audit_tool
    ON tool_audit_log (tool_name);
-- 部分索引：只索引异常记录（减少 ~90% 索引体积）
CREATE INDEX IF NOT EXISTS idx_tool_audit_errors
    ON tool_audit_log (status, created_at DESC) WHERE status != 'success';

-- 3. 预创建分区（当前月 + 未来 2 月）
CREATE TABLE IF NOT EXISTS tool_audit_log_2026_04 PARTITION OF tool_audit_log
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE IF NOT EXISTS tool_audit_log_2026_05 PARTITION OF tool_audit_log
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS tool_audit_log_2026_06 PARTITION OF tool_audit_log
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

-- 4. 分区自动管理函数（创建新分区 + 删除 90 天前旧分区）
-- 建议通过 pg_cron 每月 1 号 02:00 调用：
-- SELECT cron.schedule('maintain_tool_audit', '0 2 1 * *', 'SELECT maintain_tool_audit_partitions()');
CREATE OR REPLACE FUNCTION maintain_tool_audit_partitions()
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    target_month DATE;
    partition_name TEXT;
    old_month DATE;
    old_partition TEXT;
BEGIN
    -- 创建未来 2 个月的分区（幂等，已存在则跳过）
    FOR i IN 1..2 LOOP
        target_month := DATE_TRUNC('month', NOW()) + (i || ' months')::INTERVAL;
        partition_name := 'tool_audit_log_' || TO_CHAR(target_month, 'YYYY_MM');
        BEGIN
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF tool_audit_log
                 FOR VALUES FROM (%L) TO (%L)',
                partition_name, target_month, target_month + INTERVAL '1 month'
            );
            RAISE NOTICE 'Created partition: %', partition_name;
        EXCEPTION WHEN duplicate_table THEN
            NULL; -- 已存在，跳过
        END;
    END LOOP;

    -- 删除 90 天前的月分区
    old_month := DATE_TRUNC('month', NOW() - INTERVAL '90 days');
    old_partition := 'tool_audit_log_' || TO_CHAR(old_month, 'YYYY_MM');
    EXECUTE format('DROP TABLE IF EXISTS %I', old_partition);
    RAISE NOTICE 'Dropped old partition (if existed): %', old_partition;
END;
$$;
