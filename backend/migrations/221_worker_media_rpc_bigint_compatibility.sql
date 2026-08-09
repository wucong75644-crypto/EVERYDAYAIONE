-- 221: 兼容 PostgREST 将 JSON 数字解析为 BIGINT 的媒体 Worker RPC 调用。
-- 171/186 的原始函数保留 INTEGER 签名；这里提供精确 BIGINT 重载，
-- 通过范围校验后委托给原实现，避免改变既有 Worker 事务语义。

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION worker_claim_media_task_completion(
    p_external_task_id TEXT,
    p_expected_version BIGINT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'MEDIA_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_expected_version IS NULL
       OR p_expected_version < 0
       OR p_expected_version > 2147483647 THEN
        RAISE EXCEPTION 'MEDIA_WORKER_CLAIM_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    RETURN public.worker_claim_media_task_completion(
        p_external_task_id,
        p_expected_version::INTEGER
    );
END;
$$;

CREATE FUNCTION worker_settle_media_batch_item(
    p_external_task_id TEXT,
    p_expected_version BIGINT,
    p_status TEXT,
    p_result_data JSONB,
    p_error_message TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'MEDIA_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_expected_version IS NULL
       OR p_expected_version < 0
       OR p_expected_version > 2147483647 THEN
        RAISE EXCEPTION 'MEDIA_WORKER_SETTLE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    RETURN public.worker_settle_media_batch_item(
        p_external_task_id,
        p_expected_version::INTEGER,
        p_status,
        p_result_data,
        p_error_message
    );
END;
$$;

REVOKE ALL ON FUNCTION worker_claim_media_task_completion(TEXT, BIGINT),
    worker_settle_media_batch_item(TEXT, BIGINT, TEXT, JSONB, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

GRANT EXECUTE ON FUNCTION worker_claim_media_task_completion(TEXT, BIGINT),
    worker_settle_media_batch_item(TEXT, BIGINT, TEXT, JSONB, TEXT)
TO everydayai_worker;

RESET ROLE;
