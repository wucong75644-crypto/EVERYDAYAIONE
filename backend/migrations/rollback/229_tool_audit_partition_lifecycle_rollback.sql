-- 229 rollback: 恢复 196 的工具审计写入与旧分区维护合同。
-- 已创建的分区及其中数据保留，已按 90 天策略删除的数据不可恢复。

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION maintain_tool_audit_partitions()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    target_month DATE;
    partition_name TEXT;
    old_month DATE;
    old_partition TEXT;
BEGIN
    FOR i IN 1..2 LOOP
        target_month := date_trunc('month', NOW())
            + (i || ' months')::INTERVAL;
        partition_name := 'tool_audit_log_' || to_char(
            target_month,
            'YYYY_MM'
        );
        BEGIN
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF tool_audit_log '
                'FOR VALUES FROM (%L) TO (%L)',
                partition_name,
                target_month,
                target_month + INTERVAL '1 month'
            );
        EXCEPTION WHEN duplicate_table THEN
            NULL;
        END;
    END LOOP;

    old_month := date_trunc('month', NOW() - INTERVAL '90 days');
    old_partition := 'tool_audit_log_' || to_char(old_month, 'YYYY_MM');
    EXECUTE format('DROP TABLE IF EXISTS %I', old_partition);
END;
$$;

CREATE OR REPLACE FUNCTION record_runtime_tool_audit(
    p_task_id UUID,
    p_tool_name TEXT,
    p_tool_call_id TEXT,
    p_turn INTEGER,
    p_args_hash TEXT,
    p_result_length INTEGER,
    p_elapsed_ms INTEGER,
    p_status TEXT,
    p_is_cached BOOLEAN,
    p_is_truncated BOOLEAN,
    p_prompt_tokens INTEGER,
    p_completion_tokens INTEGER,
    p_trace_id TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
    v_audit_id UUID;
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime' THEN
        RAISE EXCEPTION 'TOOL_AUDIT_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_task_id IS NULL
       OR p_tool_name IS NULL OR length(p_tool_name) NOT BETWEEN 1 AND 200
       OR p_tool_call_id IS NULL OR length(p_tool_call_id) NOT BETWEEN 1 AND 200
       OR p_turn < 0
       OR p_result_length < 0
       OR p_elapsed_ms < 0
       OR p_status IS NULL OR p_status !~ '^[a-z][a-z0-9_]{0,49}$'
       OR p_prompt_tokens < 0
       OR p_completion_tokens < 0 THEN
        RAISE EXCEPTION 'TOOL_AUDIT_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT *
      INTO v_task
      FROM public.tasks task
     WHERE task.id = p_task_id
       AND task.user_id = tenant_actor_user_id()
       AND task.org_id IS NOT DISTINCT FROM tenant_org_id();
    IF NOT FOUND THEN
        RAISE EXCEPTION 'TOOL_AUDIT_TASK_ACCESS_DENIED'
            USING ERRCODE = '42501';
    END IF;

    INSERT INTO public.tool_audit_log (
        task_id, conversation_id, user_id, org_id,
        tool_name, tool_call_id, turn, args_hash,
        result_length, elapsed_ms, status, is_cached, is_truncated,
        prompt_tokens, completion_tokens, trace_id
    ) VALUES (
        v_task.id::TEXT, v_task.conversation_id::TEXT, v_task.user_id::TEXT,
        COALESCE(v_task.org_id::TEXT, ''),
        p_tool_name, p_tool_call_id, p_turn, p_args_hash,
        p_result_length, p_elapsed_ms, p_status,
        COALESCE(p_is_cached, FALSE), COALESCE(p_is_truncated, FALSE),
        p_prompt_tokens, p_completion_tokens, p_trace_id
    )
    RETURNING id INTO v_audit_id;

    RETURN jsonb_build_object('outcome', 'recorded', 'id', v_audit_id);
END;
$$;

REVOKE ALL ON FUNCTION maintain_tool_audit_partitions()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync;
GRANT EXECUTE ON FUNCTION maintain_tool_audit_partitions() TO everydayai;
REVOKE ALL ON FUNCTION record_runtime_tool_audit(
    UUID, TEXT, TEXT, INTEGER, TEXT, INTEGER, INTEGER, TEXT,
    BOOLEAN, BOOLEAN, INTEGER, INTEGER, TEXT
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION record_runtime_tool_audit(
    UUID, TEXT, TEXT, INTEGER, TEXT, INTEGER, INTEGER, TEXT,
    BOOLEAN, BOOLEAN, INTEGER, INTEGER, TEXT
) TO everydayai_runtime;

RESET ROLE;
