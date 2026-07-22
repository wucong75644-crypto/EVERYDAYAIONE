-- 回滚 148：仅在新生成入口关闭且无 preparing task 时执行。

DROP FUNCTION IF EXISTS fail_prepared_generation_task(UUID, TEXT, TEXT, UUID);
DROP FUNCTION IF EXISTS attach_generation_external_task(
    UUID, TEXT, UUID, UUID, TEXT, JSONB
);
DROP FUNCTION IF EXISTS prepare_generation(
    UUID, TEXT, UUID, UUID, UUID, UUID, JSONB, JSONB, JSONB
);
DROP FUNCTION IF EXISTS _prepare_generation_tasks(
    JSONB, UUID, UUID, UUID, UUID, UUID, UUID, BIGINT, UUID
);
DROP FUNCTION IF EXISTS _prepare_generation_messages(
    TEXT, UUID, UUID, UUID, JSONB, JSONB
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM tasks WHERE status = 'preparing') THEN
        RAISE EXCEPTION 'ROLLBACK_148_PREPARING_TASKS_EXIST';
    END IF;
    IF EXISTS (
        SELECT 1 FROM credit_transactions GROUP BY task_id, org_id HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'ROLLBACK_148_MULTIPLE_CREDIT_ATTEMPTS_EXIST';
    END IF;
END;
$$;

DROP INDEX IF EXISTS uq_tasks_external_task_id;
DROP INDEX IF EXISTS uq_credit_tx_pending_task_org;
CREATE UNIQUE INDEX uq_credit_tx_task_org ON credit_transactions (
    task_id,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::UUID)
);

ALTER TABLE tasks
    DROP CONSTRAINT IF EXISTS tasks_status_check,
    ADD CONSTRAINT tasks_status_check
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled'));
