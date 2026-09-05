-- 任务外部 ID 必须唯一：Webhook 和轮询都通过该字段定位 Provider 任务。
-- 迁移不自动修改历史数据；如存在重复值，先人工核对后再执行迁移。

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM tasks
        WHERE external_task_id IS NOT NULL
        GROUP BY external_task_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'tasks.external_task_id contains duplicates; reconcile before migration';
    END IF;
END;
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_external_task_id
    ON tasks(external_task_id)
    WHERE external_task_id IS NOT NULL;

COMMENT ON INDEX uq_tasks_external_task_id IS
    'Provider external task IDs map to at most one local task';

-- 同一条本地任务可以有多次重试，但不能同时锁定两笔 pending 积分。
-- 历史 refunded/confirmed 事务必须保留，便于审计和对账。
DROP INDEX IF EXISTS idx_credit_tx_task_unique;
DROP INDEX IF EXISTS uq_credit_tx_task_org;

CREATE UNIQUE INDEX IF NOT EXISTS uq_credit_tx_task_pending_org
    ON credit_transactions (
        task_id,
        COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
    )
    WHERE status = 'pending';
