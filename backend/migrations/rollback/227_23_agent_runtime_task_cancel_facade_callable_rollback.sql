-- 227_23 owns no facts; 227_22 retains every cancel intent across rollback.
SET LOCAL ROLE everydayai_owner;

DROP FUNCTION request_agent_runtime_task_cancel_v2(
    UUID,UUID,UUID,UUID,UUID,UUID,TEXT);
GRANT EXECUTE ON FUNCTION request_agent_runtime_task_cancel_v1(
    UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT)
TO everydayai_runtime, everydayai_wecom_runtime;

RESET ROLE;
