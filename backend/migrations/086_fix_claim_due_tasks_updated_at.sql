-- 修复 claim_due_tasks：领取时同步更新 updated_at
-- 原函数不更新 updated_at，导致 recovery 机制在 5 分钟后
-- 误判刚领取的任务为"卡死"（updated_at 还是上次执行的时间）

CREATE OR REPLACE FUNCTION claim_due_tasks(p_now TIMESTAMPTZ, p_limit INT)
RETURNS SETOF scheduled_tasks AS $$
    UPDATE scheduled_tasks
    SET status = 'running',
        next_run_at = NULL,
        updated_at = p_now      -- 同步更新，防止 recovery 误判
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
