SET LOCAL ROLE everydayai_owner;

REVOKE EXECUTE ON FUNCTION enqueue_wecom_generation_turn_v2(
    JSONB, UUID, UUID, UUID, JSONB, JSONB, UUID
)
FROM everydayai_wecom_runtime;

RESET ROLE;
