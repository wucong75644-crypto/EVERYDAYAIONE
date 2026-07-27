SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION
    get_agent_runtime_session(UUID),
    replay_agent_runtime_events(UUID, BIGINT, INTEGER),
    get_agent_runtime_run_claim(UUID, TEXT),
    get_claimed_agent_projection_event(UUID, UUID),
    _assert_agent_runtime_session_read(UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

DROP FUNCTION IF EXISTS
    get_claimed_agent_projection_event(UUID, UUID),
    get_agent_runtime_run_claim(UUID, TEXT),
    replay_agent_runtime_events(UUID, BIGINT, INTEGER),
    get_agent_runtime_session(UUID),
    _assert_agent_runtime_session_read(UUID);

RESET ROLE;
