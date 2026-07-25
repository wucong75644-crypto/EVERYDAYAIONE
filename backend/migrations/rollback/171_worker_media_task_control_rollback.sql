SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION worker_discover_media_tasks(INTEGER),
    worker_get_media_task(TEXT),
    worker_touch_media_task(TEXT),
    worker_claim_media_task_completion(TEXT, INTEGER),
    worker_settle_media_batch_item(TEXT, INTEGER, TEXT, JSONB, TEXT),
    worker_discover_legacy_active_tasks(),
    worker_fail_legacy_stale_task(UUID, TEXT, JSONB),
    worker_get_media_batch_message(TEXT),
    worker_commit_media_batch_message(TEXT, JSONB, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

DROP FUNCTION worker_commit_media_batch_message(TEXT, JSONB, TEXT);
DROP FUNCTION worker_get_media_batch_message(TEXT);
DROP FUNCTION worker_fail_legacy_stale_task(UUID, TEXT, JSONB);
DROP FUNCTION worker_discover_legacy_active_tasks();
DROP FUNCTION worker_settle_media_batch_item(TEXT, INTEGER, TEXT, JSONB, TEXT);
DROP FUNCTION worker_claim_media_task_completion(TEXT, INTEGER);
DROP FUNCTION worker_touch_media_task(TEXT);
DROP FUNCTION worker_get_media_task(TEXT);
DROP FUNCTION worker_discover_media_tasks(INTEGER);

RESET ROLE;
