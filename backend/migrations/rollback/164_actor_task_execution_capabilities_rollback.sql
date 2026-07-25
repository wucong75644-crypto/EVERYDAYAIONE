SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION worker_get_generation_terminal_snapshot(
    UUID, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker;
REVOKE ALL ON FUNCTION worker_update_generation_model(
    UUID, UUID, TEXT, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker;
REVOKE ALL ON FUNCTION worker_update_generation_progress(
    UUID, UUID, TEXT, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker;

DROP FUNCTION IF EXISTS worker_get_generation_terminal_snapshot(UUID, UUID);
DROP FUNCTION IF EXISTS worker_update_generation_model(
    UUID, UUID, TEXT, JSONB
);
DROP FUNCTION IF EXISTS worker_update_generation_progress(
    UUID, UUID, TEXT, JSONB
);

REVOKE SELECT ON TABLE conversations, messages FROM everydayai_worker;

RESET ROLE;
