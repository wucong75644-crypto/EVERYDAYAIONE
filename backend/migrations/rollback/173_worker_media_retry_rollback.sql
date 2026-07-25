SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION worker_prepare_media_retry(TEXT, INTEGER, TEXT),
    worker_abort_media_retry(TEXT, INTEGER, UUID),
    worker_commit_media_retry(TEXT, INTEGER, TEXT, TEXT, JSONB, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

DROP FUNCTION worker_commit_media_retry(
    TEXT, INTEGER, TEXT, TEXT, JSONB, UUID
);
DROP FUNCTION worker_abort_media_retry(TEXT, INTEGER, UUID);
DROP FUNCTION worker_prepare_media_retry(TEXT, INTEGER, TEXT);

RESET ROLE;
