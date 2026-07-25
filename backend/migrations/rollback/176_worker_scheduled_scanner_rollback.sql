SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION worker_claim_due_scheduled_tasks(
    TIMESTAMPTZ, INTEGER
), worker_list_stale_scheduled_tasks(TIMESTAMPTZ),
worker_recover_stale_scheduled_task(
    UUID, TIMESTAMPTZ, TEXT, TIMESTAMPTZ, TIMESTAMPTZ
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

DROP FUNCTION worker_recover_stale_scheduled_task(
    UUID, TIMESTAMPTZ, TEXT, TIMESTAMPTZ, TIMESTAMPTZ
);
DROP FUNCTION worker_list_stale_scheduled_tasks(TIMESTAMPTZ);
DROP FUNCTION worker_claim_due_scheduled_tasks(TIMESTAMPTZ, INTEGER);

RESET ROLE;
