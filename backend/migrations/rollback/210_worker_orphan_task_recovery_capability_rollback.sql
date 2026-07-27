SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION worker_claim_orphan_tasks(INTEGER, INTEGER),
    worker_complete_orphan_task(UUID, UUID, JSONB),
    worker_fail_orphan_task(UUID, UUID, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

DROP FUNCTION IF EXISTS worker_fail_orphan_task(UUID, UUID, TEXT);
DROP FUNCTION IF EXISTS worker_complete_orphan_task(UUID, UUID, JSONB);
DROP FUNCTION IF EXISTS worker_claim_orphan_tasks(INTEGER, INTEGER);
DROP FUNCTION IF EXISTS _assert_worker_orphan_recovery_scope();

RESET ROLE;
