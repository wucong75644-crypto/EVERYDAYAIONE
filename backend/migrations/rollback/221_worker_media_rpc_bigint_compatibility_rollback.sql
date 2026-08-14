-- Rollback 221: 移除 BIGINT 兼容重载，保留 171/186 的 INTEGER 函数。

SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION worker_claim_media_task_completion(TEXT, BIGINT),
    worker_settle_media_batch_item(TEXT, BIGINT, TEXT, JSONB, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

DROP FUNCTION worker_settle_media_batch_item(TEXT, BIGINT, TEXT, JSONB, TEXT);
DROP FUNCTION worker_claim_media_task_completion(TEXT, BIGINT);

RESET ROLE;
