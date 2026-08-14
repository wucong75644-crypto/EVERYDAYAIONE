-- 227_59 rollback: remove only the read-only adoption preflight contract.

SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION
    read_agent_runtime_scheduled_adoption_plan_v1(UUID, BOOLEAN)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker,
    everydayai_sync, everydayai;

DROP FUNCTION IF EXISTS read_agent_runtime_scheduled_adoption_plan_v1(UUID, BOOLEAN);
DROP FUNCTION IF EXISTS _agent_runtime_scheduled_adoption_target_shape(JSONB, INTEGER);

RESET ROLE;
