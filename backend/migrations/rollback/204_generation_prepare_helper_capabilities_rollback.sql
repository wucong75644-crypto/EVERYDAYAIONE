-- Roll back the runtime helper grants added by migration 204.

SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION _prepare_generation_messages(
    TEXT, UUID, UUID, UUID, JSONB, JSONB
) FROM everydayai_runtime;
REVOKE ALL ON FUNCTION _prepare_generation_tasks(
    JSONB, UUID, UUID, UUID, UUID, UUID, UUID, BIGINT, UUID
) FROM everydayai_runtime;

RESET ROLE;
