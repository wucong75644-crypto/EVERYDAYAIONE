SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION worker_fail_generation_turn(
    UUID, UUID, TEXT, TEXT
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker;
REVOKE ALL ON FUNCTION worker_commit_generation_turn_with_context_v2(
    UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB, JSONB,
    JSONB, JSONB, JSONB, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker;
REVOKE ALL ON FUNCTION worker_renew_generation_lease(
    UUID, UUID, INTEGER
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker;
REVOKE ALL ON FUNCTION worker_get_claimed_generation_task(
    UUID, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker;
REVOKE ALL ON FUNCTION worker_claim_branch_generation_turn(
    UUID, INTEGER, INTEGER
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker;
REVOKE ALL ON FUNCTION worker_claim_next_serial_generation_turn(
    UUID, INTEGER, INTEGER
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker;
REVOKE ALL ON FUNCTION discover_generation_turn_candidates(INTEGER)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker;

DROP FUNCTION IF EXISTS worker_fail_generation_turn(
    UUID, UUID, TEXT, TEXT
);
DROP FUNCTION IF EXISTS worker_commit_generation_turn_with_context_v2(
    UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB, JSONB,
    JSONB, JSONB, JSONB, JSONB
);
DROP FUNCTION IF EXISTS worker_renew_generation_lease(
    UUID, UUID, INTEGER
);
DROP FUNCTION IF EXISTS worker_get_claimed_generation_task(UUID, UUID);
DROP FUNCTION IF EXISTS worker_claim_branch_generation_turn(
    UUID, INTEGER, INTEGER
);
DROP FUNCTION IF EXISTS worker_claim_next_serial_generation_turn(
    UUID, INTEGER, INTEGER
);
DROP FUNCTION IF EXISTS discover_generation_turn_candidates(INTEGER);
DROP FUNCTION IF EXISTS _assert_actor_worker_task_scope(UUID);
DROP FUNCTION IF EXISTS _assert_actor_worker_discovery_scope();

RESET ROLE;
