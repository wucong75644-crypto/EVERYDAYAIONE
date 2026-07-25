-- 174: Worker 错误日志汇聚能力，避免授予 error_logs 表权限。

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION worker_record_error_log(
    p_fingerprint TEXT,
    p_level TEXT,
    p_module TEXT,
    p_function TEXT,
    p_line INTEGER,
    p_message TEXT,
    p_traceback TEXT,
    p_occurrence_count INTEGER,
    p_first_seen_at TIMESTAMPTZ,
    p_last_seen_at TIMESTAMPTZ,
    p_org_id UUID,
    p_is_critical BOOLEAN
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_log public.error_logs%ROWTYPE;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'WORKER_ERROR_LOG_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF NULLIF(BTRIM(p_fingerprint), '') IS NULL
       OR NULLIF(BTRIM(p_level), '') IS NULL
       OR NULLIF(BTRIM(p_message), '') IS NULL
       OR p_occurrence_count IS NULL OR p_occurrence_count < 1
       OR p_first_seen_at IS NULL OR p_last_seen_at IS NULL THEN
        RAISE EXCEPTION 'WORKER_ERROR_LOG_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO public.error_logs (
        fingerprint, level, module, function, line, message, traceback,
        occurrence_count, first_seen_at, last_seen_at, org_id, is_critical
    ) VALUES (
        p_fingerprint, p_level, p_module, p_function, p_line, p_message,
        p_traceback, p_occurrence_count, p_first_seen_at, p_last_seen_at,
        p_org_id, COALESCE(p_is_critical, FALSE)
    )
    ON CONFLICT (fingerprint) WHERE is_resolved = FALSE
    DO UPDATE SET
        occurrence_count = error_logs.occurrence_count
            + EXCLUDED.occurrence_count,
        last_seen_at = GREATEST(
            error_logs.last_seen_at, EXCLUDED.last_seen_at
        ),
        first_seen_at = LEAST(
            error_logs.first_seen_at, EXCLUDED.first_seen_at
        ),
        level = CASE
            WHEN EXCLUDED.level = 'CRITICAL' THEN 'CRITICAL'
            ELSE error_logs.level
        END,
        is_critical = error_logs.is_critical OR EXCLUDED.is_critical,
        message = EXCLUDED.message,
        traceback = COALESCE(EXCLUDED.traceback, error_logs.traceback)
    RETURNING * INTO v_log;

    RETURN jsonb_build_object(
        'outcome', 'recorded',
        'id', v_log.id,
        'occurrence_count', v_log.occurrence_count
    );
END;
$$;

REVOKE ALL ON FUNCTION worker_record_error_log(
    TEXT, TEXT, TEXT, TEXT, INTEGER, TEXT, TEXT, INTEGER,
    TIMESTAMPTZ, TIMESTAMPTZ, UUID, BOOLEAN
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

GRANT EXECUTE ON FUNCTION worker_record_error_log(
    TEXT, TEXT, TEXT, TEXT, INTEGER, TEXT, TEXT, INTEGER,
    TIMESTAMPTZ, TIMESTAMPTZ, UUID, BOOLEAN
) TO everydayai_worker;

RESET ROLE;
