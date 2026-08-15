-- 229: 工具审计分区在唯一写入边界自维护。
-- PostgreSQL 未安装 pg_cron；record_runtime_tool_audit 在写入前调用 owner-only
-- 维护函数，以轻量目录检查保证当前月及未来两月分区存在，并执行 90 天保留策略。

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION maintain_tool_audit_partitions()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    current_month DATE := date_trunc('month', CURRENT_DATE)::DATE;
    target_month DATE;
    partition_month DATE;
    partition_name TEXT;
    partition_owner TEXT;
    partition_bound TEXT;
    relation_oid REGCLASS;
    required_partition_count INTEGER;
    has_expired_partition BOOLEAN;
    partition_record RECORD;
BEGIN
    SELECT count(*)
      INTO required_partition_count
      FROM generate_series(0, 2) AS month_offset
     WHERE EXISTS (
        SELECT 1
          FROM pg_catalog.pg_inherits inheritance
          JOIN pg_catalog.pg_class child
            ON child.oid = inheritance.inhrelid
          JOIN pg_catalog.pg_namespace namespace
            ON namespace.oid = child.relnamespace
          JOIN pg_catalog.pg_roles owner
            ON owner.oid = child.relowner
         WHERE inheritance.inhparent = 'public.tool_audit_log'::regclass
           AND namespace.nspname = 'public'
           AND owner.rolname = 'everydayai_owner'
           AND child.relname = 'tool_audit_log_' || to_char(
                current_month + make_interval(months => month_offset),
                'YYYY_MM'
           )
           AND position(
                to_char(
                    current_month + make_interval(months => month_offset),
                    'YYYY-MM-DD'
                ) IN pg_get_expr(child.relpartbound, child.oid)
           ) > 0
           AND position(
                to_char(
                    current_month + make_interval(months => month_offset + 1),
                    'YYYY-MM-DD'
                ) IN pg_get_expr(child.relpartbound, child.oid)
           ) > 0
     );

    SELECT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_inherits inheritance
          JOIN pg_catalog.pg_class child
            ON child.oid = inheritance.inhrelid
         WHERE inheritance.inhparent = 'public.tool_audit_log'::regclass
           AND child.relname ~ '^tool_audit_log_[0-9]{4}_[0-9]{2}$'
           AND (
                to_date(substring(child.relname FROM 16), 'YYYY_MM')
                + INTERVAL '1 month'
           ) <= CURRENT_DATE - INTERVAL '90 days'
    ) INTO has_expired_partition;

    IF required_partition_count = 3 AND NOT has_expired_partition THEN
        RETURN;
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'everydayai:tool_audit_log:partitions',
            0
        )
    );

    FOR month_offset IN 0..2 LOOP
        target_month := (
            current_month + make_interval(months => month_offset)
        )::DATE;
        partition_name := 'tool_audit_log_' || to_char(
            target_month,
            'YYYY_MM'
        );
        relation_oid := to_regclass(format('public.%I', partition_name));

        IF relation_oid IS NOT NULL AND NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_inherits inheritance
             WHERE inheritance.inhparent = 'public.tool_audit_log'::regclass
               AND inheritance.inhrelid = relation_oid
        ) THEN
            RAISE EXCEPTION 'TOOL_AUDIT_PARTITION_NAME_CONFLICT: %',
                partition_name USING ERRCODE = '55000';
        END IF;

        IF relation_oid IS NOT NULL THEN
            SELECT owner.rolname, pg_get_expr(relation.relpartbound, relation.oid)
              INTO partition_owner, partition_bound
              FROM pg_catalog.pg_class relation
              JOIN pg_catalog.pg_roles owner ON owner.oid = relation.relowner
             WHERE relation.oid = relation_oid;
            IF partition_owner <> 'everydayai_owner' THEN
                RAISE EXCEPTION 'TOOL_AUDIT_PARTITION_OWNER_INVALID: %',
                    partition_name USING ERRCODE = '55000';
            END IF;
            IF position(to_char(target_month, 'YYYY-MM-DD') IN partition_bound) = 0
               OR position(
                    to_char(target_month + INTERVAL '1 month', 'YYYY-MM-DD')
                    IN partition_bound
               ) = 0 THEN
                RAISE EXCEPTION 'TOOL_AUDIT_PARTITION_BOUND_INVALID: %',
                    partition_name USING ERRCODE = '55000';
            END IF;
        ELSE
            EXECUTE format(
                'CREATE TABLE public.%I PARTITION OF public.tool_audit_log '
                'FOR VALUES FROM (%L) TO (%L)',
                partition_name,
                target_month,
                target_month + INTERVAL '1 month'
            );
        END IF;
    END LOOP;

    FOR partition_record IN
        SELECT child.relname
          FROM pg_catalog.pg_inherits inheritance
          JOIN pg_catalog.pg_class child
            ON child.oid = inheritance.inhrelid
          JOIN pg_catalog.pg_namespace namespace
            ON namespace.oid = child.relnamespace
         WHERE inheritance.inhparent = 'public.tool_audit_log'::regclass
           AND namespace.nspname = 'public'
           AND child.relname ~ '^tool_audit_log_[0-9]{4}_[0-9]{2}$'
    LOOP
        partition_month := to_date(
            substring(partition_record.relname FROM 16),
            'YYYY_MM'
        );
        IF partition_month + INTERVAL '1 month'
                <= CURRENT_DATE - INTERVAL '90 days' THEN
            EXECUTE format(
                'DROP TABLE public.%I',
                partition_record.relname
            );
        END IF;
    END LOOP;
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

    PERFORM public.maintain_tool_audit_partitions();

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
    everydayai_worker, everydayai_sync, everydayai;
REVOKE ALL ON FUNCTION record_runtime_tool_audit(
    UUID, TEXT, TEXT, INTEGER, TEXT, INTEGER, INTEGER, TEXT,
    BOOLEAN, BOOLEAN, INTEGER, INTEGER, TEXT
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION record_runtime_tool_audit(
    UUID, TEXT, TEXT, INTEGER, TEXT, INTEGER, INTEGER, TEXT,
    BOOLEAN, BOOLEAN, INTEGER, INTEGER, TEXT
) TO everydayai_runtime;

SELECT maintain_tool_audit_partitions();

RESET ROLE;
