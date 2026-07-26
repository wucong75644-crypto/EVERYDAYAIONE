-- 204: Complete the runtime capability chain used by prepare_generation.

SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION _prepare_generation_messages(
    TEXT, UUID, UUID, UUID, JSONB, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
REVOKE ALL ON FUNCTION _prepare_generation_tasks(
    JSONB, UUID, UUID, UUID, UUID, UUID, UUID, BIGINT, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

GRANT EXECUTE ON FUNCTION _prepare_generation_messages(
    TEXT, UUID, UUID, UUID, JSONB, JSONB
) TO everydayai_runtime;
GRANT EXECUTE ON FUNCTION _prepare_generation_tasks(
    JSONB, UUID, UUID, UUID, UUID, UUID, UUID, BIGINT, UUID
) TO everydayai_runtime;

RESET ROLE;
