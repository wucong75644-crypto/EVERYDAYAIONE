-- Roll back the 206 facade while restoring the 204/205 capability shape.

SET LOCAL ROLE everydayai_owner;

DROP FUNCTION prepare_generation(
    UUID, TEXT, UUID, UUID, UUID, UUID, JSONB, JSONB, JSONB
);
ALTER FUNCTION _prepare_generation_owner(
    UUID, TEXT, UUID, UUID, UUID, UUID, JSONB, JSONB, JSONB
) RENAME TO prepare_generation;

GRANT EXECUTE ON FUNCTION prepare_generation(
    UUID, TEXT, UUID, UUID, UUID, UUID, JSONB, JSONB, JSONB
) TO everydayai_runtime;
GRANT EXECUTE ON FUNCTION _prepare_generation_messages(
    TEXT, UUID, UUID, UUID, JSONB, JSONB
) TO everydayai_runtime;
GRANT EXECUTE ON FUNCTION _prepare_generation_tasks(
    JSONB, UUID, UUID, UUID, UUID, UUID, UUID, BIGINT, UUID
) TO everydayai_runtime;
GRANT USAGE ON SEQUENCE task_queue_sequence_seq TO everydayai_runtime;

RESET ROLE;
