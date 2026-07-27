SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION
    ensure_agent_runtime_session(
        UUID, UUID, UUID, TEXT, TEXT, UUID, TEXT, TEXT
    ),
    submit_session_command(UUID, TEXT, TEXT, JSONB),
    create_agent_run(UUID, UUID, TEXT, TEXT, JSONB, JSONB, JSONB),
    claim_agent_run(UUID, TEXT, INTEGER, INTEGER),
    renew_agent_run(UUID, UUID, INTEGER)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

DROP FUNCTION IF EXISTS
    renew_agent_run(UUID, UUID, INTEGER),
    claim_agent_run(UUID, TEXT, INTEGER, INTEGER),
    create_agent_run(UUID, UUID, TEXT, TEXT, JSONB, JSONB, JSONB),
    submit_session_command(UUID, TEXT, TEXT, JSONB),
    ensure_agent_runtime_session(
        UUID, UUID, UUID, TEXT, TEXT, UUID, TEXT, TEXT
    );

RESET ROLE;
