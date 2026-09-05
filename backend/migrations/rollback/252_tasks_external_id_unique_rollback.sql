DROP INDEX IF EXISTS uq_tasks_external_task_id;
DROP INDEX IF EXISTS uq_credit_tx_task_pending_org;

CREATE UNIQUE INDEX IF NOT EXISTS uq_credit_tx_task_org ON credit_transactions (
    task_id,
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000'::uuid)
);
