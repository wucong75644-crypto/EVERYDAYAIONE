SET LOCAL ROLE everydayai_owner;

DROP FUNCTION worker_fail_scheduled_run(
    UUID, UUID, TEXT, TIMESTAMPTZ, INTEGER, TEXT, INTEGER,
    INTEGER, TIMESTAMPTZ
);
DROP FUNCTION worker_complete_scheduled_run(
    UUID, UUID, TEXT, TIMESTAMPTZ, TEXT, JSONB, JSONB, TEXT,
    INTEGER, INTEGER, INTEGER, TIMESTAMPTZ
);
DROP FUNCTION worker_get_scheduled_task(UUID);
DROP FUNCTION worker_create_scheduled_run(UUID);

RESET ROLE;
