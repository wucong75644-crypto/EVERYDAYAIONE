SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION worker_record_media_metric(
    UUID, TEXT, TEXT, INTEGER, INTEGER, INTEGER, TEXT, JSONB, BOOLEAN, TEXT
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

DROP FUNCTION worker_record_media_metric(
    UUID, TEXT, TEXT, INTEGER, INTEGER, INTEGER, TEXT, JSONB, BOOLEAN, TEXT
);

RESET ROLE;
