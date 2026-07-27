SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION
    create_model_step(UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB),
    complete_model_step(
        UUID, UUID, BIGINT, JSONB, TEXT, TEXT, BIGINT, BIGINT, BIGINT
    ),
    fail_model_step(UUID, UUID, BIGINT, TEXT),
    claim_agent_projection_outbox(INTEGER, INTEGER),
    complete_agent_projection_outbox(UUID, UUID, JSONB),
    fail_agent_projection_outbox(UUID, UUID, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

DROP FUNCTION IF EXISTS
    fail_agent_projection_outbox(UUID, UUID, TEXT),
    complete_agent_projection_outbox(UUID, UUID, JSONB),
    claim_agent_projection_outbox(INTEGER, INTEGER),
    fail_model_step(UUID, UUID, BIGINT, TEXT),
    complete_model_step(
        UUID, UUID, BIGINT, JSONB, TEXT, TEXT, BIGINT, BIGINT, BIGINT
    ),
    create_model_step(UUID, UUID, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB);

RESET ROLE;
