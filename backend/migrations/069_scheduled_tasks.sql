-- 069: 定时任务系统（Phase 2）
-- 技术文档: docs/document/TECH_定时任务心跳系统.md §3

-- ════════════════════════════════════════════════════════
-- scheduled_tasks 表
-- ════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id               UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id              UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- 任务定义
    name                 VARCHAR(100) NOT NULL,
    prompt               TEXT NOT NULL,
    cron_expr            VARCHAR(50) NOT NULL,
    timezone             VARCHAR(50) NOT NULL DEFAULT 'Asia/Shanghai',

    -- 推送目标（JSONB，支持单目标/多目标）
    push_target          JSONB NOT NULL,

    -- 模板文件（可选，{path, name, url}）
    template_file        JSONB,

    -- 执行控制
    status               VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','paused','running','error')),
    max_credits          INTEGER NOT NULL DEFAULT 10,
    retry_count          SMALLINT NOT NULL DEFAULT 1,
    timeout_sec          INTEGER NOT NULL DEFAULT 180,

    -- 跨次状态（借鉴 LangGraph stateful cron）
    last_summary         TEXT,
    last_result          JSONB,

    -- 调度状态
    next_run_at          TIMESTAMPTZ,
    last_run_at          TIMESTAMPTZ,
    run_count            INTEGER NOT NULL DEFAULT 0,
    consecutive_failures SMALLINT NOT NULL DEFAULT 0,

    -- 元数据
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 调度器扫描索引（按 next_run_at 找到期任务）
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run
    ON scheduled_tasks(next_run_at)
    WHERE status = 'active';

-- 多租户索引
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_org
    ON scheduled_tasks(org_id, user_id);

COMMENT ON TABLE scheduled_tasks IS '定时任务定义表';
COMMENT ON COLUMN scheduled_tasks.cron_expr IS '5 段 cron 表达式，如 "0 9 * * *" 每天 09:00';
COMMENT ON COLUMN scheduled_tasks.push_target IS 'JSONB: {type: wecom_group/wecom_user/web/multi, ...}';
COMMENT ON COLUMN scheduled_tasks.last_summary IS '上次执行的 Agent 摘要（≤500 字），下次注入上下文';
COMMENT ON COLUMN scheduled_tasks.consecutive_failures IS '连续失败次数，达到 3 自动暂停';

-- ════════════════════════════════════════════════════════
-- scheduled_task_runs 表（执行历史）
-- ════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS scheduled_task_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         UUID NOT NULL REFERENCES scheduled_tasks(id) ON DELETE CASCADE,
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

    -- 执行信息
    status          VARCHAR(20) NOT NULL
        CHECK (status IN ('running','success','failed','timeout','skipped')),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    duration_ms     INTEGER,

    -- 结果
    result_summary  TEXT,
    result_files    JSONB,
    push_status     VARCHAR(20),
    error_message   TEXT,

    -- 成本
    credits_used    INTEGER NOT NULL DEFAULT 0,
    tokens_used     INTEGER NOT NULL DEFAULT 0,

    -- 重试链接
    retry_of_run_id UUID REFERENCES scheduled_task_runs(id),

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_task_runs_task
    ON scheduled_task_runs(task_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_task_runs_org
    ON scheduled_task_runs(org_id, started_at DESC);

COMMENT ON TABLE scheduled_task_runs IS '定时任务执行历史';

-- ════════════════════════════════════════════════════════
-- claim_due_tasks RPC（原子领取，防并发重复执行）
-- ════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION claim_due_tasks(p_now TIMESTAMPTZ, p_limit INT)
RETURNS SETOF scheduled_tasks AS $$
    UPDATE scheduled_tasks
    SET status = 'running',
        next_run_at = NULL  -- 清空，执行完再算下次
    WHERE id IN (
        SELECT id FROM scheduled_tasks
        WHERE status = 'active'
          AND next_run_at IS NOT NULL
          AND next_run_at <= p_now
        ORDER BY next_run_at
        LIMIT p_limit
        FOR UPDATE SKIP LOCKED
    )
    RETURNING *;
$$ LANGUAGE sql;

COMMENT ON FUNCTION claim_due_tasks IS '原子领取到期任务，SKIP LOCKED 防并发重复';
