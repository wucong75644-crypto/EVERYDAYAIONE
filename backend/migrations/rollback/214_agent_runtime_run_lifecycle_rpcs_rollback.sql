SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION
    set_agent_run_waiting(UUID, UUID, BIGINT, TEXT),
    wake_agent_run(UUID, BIGINT),
    complete_agent_run(UUID, UUID, BIGINT, TEXT),
    fail_agent_run(UUID, UUID, BIGINT, TEXT),
    cancel_agent_run(UUID, BIGINT, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

DROP FUNCTION IF EXISTS
    cancel_agent_run(UUID, BIGINT, TEXT),
    fail_agent_run(UUID, UUID, BIGINT, TEXT),
    complete_agent_run(UUID, UUID, BIGINT, TEXT),
    _finish_agent_run(UUID, UUID, BIGINT, TEXT, TEXT, TEXT),
    wake_agent_run(UUID, BIGINT),
    set_agent_run_waiting(UUID, UUID, BIGINT, TEXT);

RESET ROLE;
