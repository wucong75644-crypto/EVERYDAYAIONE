SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION worker_record_error_log(
    TEXT, TEXT, TEXT, TEXT, INTEGER, TEXT, TEXT, INTEGER,
    TIMESTAMPTZ, TIMESTAMPTZ, UUID, BOOLEAN
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

DROP FUNCTION worker_record_error_log(
    TEXT, TEXT, TEXT, TEXT, INTEGER, TEXT, TEXT, INTEGER,
    TIMESTAMPTZ, TIMESTAMPTZ, UUID, BOOLEAN
);

RESET ROLE;
