SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS worker_settle_scheduled_credits(
    UUID, UUID, UUID, UUID, BOOLEAN, INTEGER
);
DROP FUNCTION IF EXISTS worker_lock_scheduled_credits(UUID, UUID, UUID);
DROP FUNCTION IF EXISTS worker_fail_scheduled_run(
    UUID, UUID, UUID, TEXT, TIMESTAMPTZ, INTEGER, TEXT,
    INTEGER, INTEGER, TIMESTAMPTZ
);
DROP FUNCTION IF EXISTS worker_complete_scheduled_run(
    UUID, UUID, UUID, TEXT, TIMESTAMPTZ, TEXT, JSONB, JSONB,
    TEXT, INTEGER, INTEGER, INTEGER, TIMESTAMPTZ
);
DROP FUNCTION IF EXISTS worker_append_scheduled_result_message(
    UUID, UUID, UUID, TEXT
);
DROP FUNCTION IF EXISTS worker_get_scheduled_task(UUID, UUID, UUID);
DROP FUNCTION IF EXISTS worker_renew_scheduled_run(UUID, UUID, UUID, INTEGER);
DROP FUNCTION IF EXISTS worker_create_scheduled_run(UUID, INTEGER);
DROP FUNCTION IF EXISTS _assert_scheduled_run_scope(UUID, UUID, UUID);
DROP TRIGGER IF EXISTS trg_clear_scheduled_run_fence_on_terminal
ON public.scheduled_task_runs;
DROP FUNCTION IF EXISTS clear_scheduled_run_fence_on_terminal();

ALTER TABLE public.scheduled_task_runs
    DROP COLUMN IF EXISTS result_message_id,
    DROP COLUMN IF EXISTS lease_expires_at,
    DROP COLUMN IF EXISTS execution_token;

GRANT EXECUTE ON FUNCTION worker_create_scheduled_run(UUID),
    worker_get_scheduled_task(UUID),
    worker_complete_scheduled_run(
        UUID, UUID, TEXT, TIMESTAMPTZ, TEXT, JSONB, JSONB, TEXT,
        INTEGER, INTEGER, INTEGER, TIMESTAMPTZ
    ),
    worker_fail_scheduled_run(
        UUID, UUID, TEXT, TIMESTAMPTZ, INTEGER, TEXT, INTEGER,
        INTEGER, TIMESTAMPTZ
    ),
    worker_lock_scheduled_credits(UUID, UUID),
    worker_settle_scheduled_credits(UUID, UUID, UUID, BOOLEAN, INTEGER)
TO everydayai_worker;

RESET ROLE;
