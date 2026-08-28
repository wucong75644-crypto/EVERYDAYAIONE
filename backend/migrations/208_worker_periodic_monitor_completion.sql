-- 208: Worker 周期任务跨进程租约、企微健康快照与模型评分闭环。

SET LOCAL ROLE everydayai_owner;

CREATE TABLE worker_periodic_job_runs (
    job_name TEXT NOT NULL,
    bucket_start TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (job_name, bucket_start),
    CHECK (
        job_name IN ('model_scoring', 'wecom_dup_monitor')
        AND (
            (status = 'running'
             AND lease_token IS NOT NULL
             AND lease_expires_at IS NOT NULL)
            OR status IN ('completed', 'failed')
        )
    )
);

ALTER TABLE worker_periodic_job_runs ENABLE ROW LEVEL SECURITY;
CREATE POLICY worker_periodic_job_runs_owner_all
ON worker_periodic_job_runs
FOR ALL TO everydayai_owner
USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE worker_periodic_job_runs FORCE ROW LEVEL SECURITY;

CREATE FUNCTION _assert_global_worker_periodic_scope()
RETURNS VOID
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_worker'
       OR current_setting('app.access_kind', TRUE) <> 'worker'
       OR tenant_actor_user_id() IS NOT NULL
       OR tenant_org_id() IS NOT NULL THEN
        RAISE EXCEPTION 'WORKER_PERIODIC_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE FUNCTION worker_claim_periodic_job(p_job_name TEXT)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_bucket_start TIMESTAMPTZ;
    v_lease_seconds INTEGER;
    v_token UUID := gen_random_uuid();
    v_run worker_periodic_job_runs%ROWTYPE;
BEGIN
    PERFORM _assert_global_worker_periodic_scope();
    IF p_job_name = 'model_scoring' THEN
        v_bucket_start := date_trunc('hour', clock_timestamp());
        v_lease_seconds := 300;
    ELSIF p_job_name = 'wecom_dup_monitor' THEN
        v_bucket_start := (
            date_trunc('day', clock_timestamp() AT TIME ZONE 'Asia/Shanghai')
            AT TIME ZONE 'Asia/Shanghai'
        );
        v_lease_seconds := 300;
    ELSE
        RAISE EXCEPTION 'WORKER_PERIODIC_JOB_INVALID'
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO worker_periodic_job_runs (
        job_name, bucket_start, status, lease_token, lease_expires_at,
        attempt_count
    ) VALUES (
        p_job_name, v_bucket_start, 'running', v_token,
        clock_timestamp() + make_interval(secs => v_lease_seconds), 1
    )
    ON CONFLICT (job_name, bucket_start) DO NOTHING;

    SELECT * INTO v_run
      FROM worker_periodic_job_runs
     WHERE job_name = p_job_name AND bucket_start = v_bucket_start
     FOR UPDATE;

    IF v_run.lease_token = v_token THEN
        RETURN jsonb_build_object(
            'outcome', 'claimed',
            'lease_token', v_token,
            'bucket_start', v_bucket_start
        );
    END IF;
    IF v_run.status = 'completed' THEN
        RETURN jsonb_build_object(
            'outcome', 'completed',
            'bucket_start', v_bucket_start
        );
    END IF;
    IF v_run.lease_expires_at > clock_timestamp() THEN
        RETURN jsonb_build_object(
            'outcome', 'busy',
            'bucket_start', v_bucket_start
        );
    END IF;

    UPDATE worker_periodic_job_runs
       SET status = 'running',
           lease_token = v_token,
           lease_expires_at =
               clock_timestamp() + make_interval(secs => v_lease_seconds),
           attempt_count = attempt_count + 1,
           started_at = clock_timestamp(),
           completed_at = NULL,
           updated_at = clock_timestamp()
     WHERE job_name = p_job_name AND bucket_start = v_bucket_start;

    RETURN jsonb_build_object(
        'outcome', 'claimed',
        'lease_token', v_token,
        'bucket_start', v_bucket_start
    );
END;
$$;

CREATE FUNCTION worker_renew_periodic_job(
    p_job_name TEXT,
    p_lease_token UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_updated BIGINT;
BEGIN
    PERFORM _assert_global_worker_periodic_scope();
    IF p_job_name NOT IN ('model_scoring', 'wecom_dup_monitor')
       OR p_lease_token IS NULL THEN
        RAISE EXCEPTION 'WORKER_PERIODIC_RENEW_INVALID'
            USING ERRCODE = '22023';
    END IF;
    UPDATE worker_periodic_job_runs
       SET lease_expires_at = clock_timestamp() + INTERVAL '5 minutes',
           updated_at = clock_timestamp()
     WHERE job_name = p_job_name
       AND status = 'running'
       AND lease_token = p_lease_token
       AND lease_expires_at > clock_timestamp();
    GET DIAGNOSTICS v_updated = ROW_COUNT;
    IF v_updated <> 1 THEN
        RAISE EXCEPTION 'WORKER_PERIODIC_LEASE_LOST'
            USING ERRCODE = '40001';
    END IF;
    RETURN jsonb_build_object('outcome', 'renewed');
END;
$$;

CREATE FUNCTION worker_finish_periodic_job(
    p_job_name TEXT,
    p_lease_token UUID,
    p_succeeded BOOLEAN
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_updated BIGINT;
BEGIN
    PERFORM _assert_global_worker_periodic_scope();
    IF p_job_name NOT IN ('model_scoring', 'wecom_dup_monitor')
       OR p_lease_token IS NULL OR p_succeeded IS NULL THEN
        RAISE EXCEPTION 'WORKER_PERIODIC_FINISH_INVALID'
            USING ERRCODE = '22023';
    END IF;

    UPDATE worker_periodic_job_runs
       SET status = CASE WHEN p_succeeded THEN 'completed' ELSE 'failed' END,
           lease_token = NULL,
           lease_expires_at = CASE
               WHEN p_succeeded THEN NULL
               ELSE clock_timestamp() + INTERVAL '5 minutes'
           END,
           completed_at = CASE
               WHEN p_succeeded THEN clock_timestamp()
               ELSE NULL
           END,
           updated_at = clock_timestamp()
     WHERE job_name = p_job_name
       AND status = 'running'
       AND lease_token = p_lease_token
       AND lease_expires_at > clock_timestamp();
    GET DIAGNOSTICS v_updated = ROW_COUNT;
    IF v_updated <> 1 THEN
        RAISE EXCEPTION 'WORKER_PERIODIC_LEASE_LOST'
            USING ERRCODE = '40001';
    END IF;
    RETURN jsonb_build_object('outcome', 'finished');
END;
$$;

CREATE FUNCTION worker_wecom_identity_health_snapshot()
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_orphans BIGINT;
    v_duplicates BIGINT;
BEGIN
    PERFORM _assert_global_worker_periodic_scope();
    SELECT count(*) INTO v_orphans
      FROM users usr
     WHERE usr.created_by = 'wecom'
       AND NOT EXISTS (
           SELECT 1
             FROM wecom_user_mappings mapping
            WHERE mapping.user_id = usr.id
       );
    SELECT count(*) INTO v_duplicates
      FROM (
          SELECT mapping.wecom_userid, mapping.corp_id, mapping.org_id
            FROM wecom_user_mappings mapping
           GROUP BY mapping.wecom_userid, mapping.corp_id, mapping.org_id
          HAVING count(*) > 1
      ) duplicate_identity;
    RETURN jsonb_build_object(
        'orphan_users', v_orphans,
        'duplicate_groups', v_duplicates
    );
END;
$$;

CREATE OR REPLACE FUNCTION worker_model_scoring_snapshot(p_org_id UUID)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    WITH authorized AS MATERIALIZED (
        SELECT _assert_worker_model_scoring_scope(p_org_id)
    ),
    aggregated AS (
        SELECT
            metric.model_id,
            metric.task_type,
            CASE WHEN p_org_id IS NULL THEN metric.user_id END
                AS owner_user_id,
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE metric.status = 'success')
                AS success_count,
            PERCENTILE_CONT(0.75) WITHIN GROUP (
                ORDER BY metric.cost_time_ms
            ) FILTER (
                WHERE metric.status = 'success'
                  AND metric.cost_time_ms IS NOT NULL
            ) AS p75_latency,
            COUNT(*) FILTER (WHERE metric.retried) AS retry_count,
            COUNT(*) FILTER (WHERE metric.error_code = 'timeout')
                AS timeout_count,
            COUNT(*) FILTER (
                WHERE metric.error_code IS NOT NULL
                  AND metric.error_code NOT IN ('timeout', 'rate_limit')
            ) AS hard_error_count,
            MIN(metric.created_at) AS period_start,
            MAX(metric.created_at) AS period_end
        FROM knowledge_metrics metric, authorized
        WHERE metric.created_at > NOW() - INTERVAL '7 days'
          AND metric.org_id IS NOT DISTINCT FROM p_org_id
          AND metric.task_type IN ('chat', 'image', 'video')
          AND metric.model_id NOT IN ('unknown', 'auto')
        GROUP BY
            metric.model_id,
            metric.task_type,
            CASE WHEN p_org_id IS NULL THEN metric.user_id END
    )
    SELECT COALESCE(
        jsonb_agg(
            to_jsonb(aggregated)
            || jsonb_build_object(
                'old_score',
                (
                    SELECT audit.new_score
                      FROM scoring_audit_log audit
                     WHERE audit.model_id = aggregated.model_id
                       AND audit.task_type = aggregated.task_type
                       AND audit.org_id IS NOT DISTINCT FROM p_org_id
                       AND audit.owner_user_id IS NOT DISTINCT FROM
                           aggregated.owner_user_id
                       AND audit.status IN ('auto_applied', 'approved')
                     ORDER BY audit.created_at DESC
                     LIMIT 1
                )
            )
            ORDER BY aggregated.model_id, aggregated.task_type,
                     aggregated.owner_user_id
        ),
        '[]'::JSONB
    )
    FROM aggregated
$$;

CREATE FUNCTION _worker_upsert_model_score_knowledge(
    p_org_id UUID,
    p_owner_user_id UUID,
    p_task_type TEXT,
    p_title TEXT,
    p_content TEXT,
    p_metadata JSONB,
    p_embedding TEXT,
    p_confidence DOUBLE PRECISION,
    p_content_hash TEXT
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_node_id UUID;
BEGIN
    SELECT node.id INTO v_node_id
      FROM knowledge_nodes node
     WHERE node.content_hash = p_content_hash
       AND node.org_id IS NOT DISTINCT FROM p_org_id
       AND node.owner_user_id IS NOT DISTINCT FROM p_owner_user_id
     FOR UPDATE;
    IF v_node_id IS NULL THEN
        INSERT INTO knowledge_nodes (
            category, subcategory, node_type, title, content, metadata,
            embedding, source, confidence, scope, content_hash, org_id,
            owner_user_id
        ) VALUES (
            'model', p_task_type, 'performance', p_title, p_content,
            p_metadata, p_embedding::vector, 'aggregated', p_confidence,
            'global', p_content_hash, p_org_id, p_owner_user_id
        ) RETURNING id INTO v_node_id;
    ELSE
        UPDATE knowledge_nodes
           SET metadata = p_metadata,
               embedding = COALESCE(p_embedding::vector, embedding),
               confidence = GREATEST(confidence, p_confidence),
               hit_count = hit_count + 1,
               updated_at = NOW()
         WHERE id = v_node_id;
    END IF;
    RETURN v_node_id;
END;
$$;

CREATE OR REPLACE FUNCTION worker_commit_model_score(
    p_org_id UUID, p_owner_user_id UUID,
    p_model_id TEXT, p_task_type TEXT,
    p_old_score DOUBLE PRECISION, p_new_score DOUBLE PRECISION,
    p_score_change DOUBLE PRECISION, p_sample_count INTEGER,
    p_metrics JSONB, p_period_start TIMESTAMPTZ,
    p_period_end TIMESTAMPTZ, p_status TEXT,
    p_title TEXT, p_content TEXT, p_metadata JSONB,
    p_confidence DOUBLE PRECISION, p_content_hash TEXT,
    p_embedding TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_node_id UUID;
    v_audit_id UUID;
BEGIN
    IF session_user <> 'everydayai_worker'
       OR current_setting('app.access_kind', TRUE) <> 'worker'
       OR tenant_actor_user_id() IS NOT NULL
       OR tenant_org_id() IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'MODEL_SCORING_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF (p_org_id IS NOT NULL AND p_owner_user_id IS NOT NULL)
       OR (
           p_org_id IS NULL
           AND p_owner_user_id IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM knowledge_metrics metric
                WHERE metric.org_id IS NULL
                  AND metric.user_id = p_owner_user_id
           )
       ) THEN
        RAISE EXCEPTION 'MODEL_SCORING_OWNER_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_model_id IS NULL OR p_task_type IS NULL
       OR p_new_score NOT BETWEEN 0 AND 1
       OR p_score_change < 0
       OR p_sample_count < 1
       OR p_metrics IS NULL OR jsonb_typeof(p_metrics) <> 'object'
       OR p_period_start IS NULL OR p_period_end IS NULL
       OR p_period_start > p_period_end
       OR p_status NOT IN ('auto_applied', 'pending_review') THEN
        RAISE EXCEPTION 'MODEL_SCORING_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    IF p_status = 'auto_applied'
       AND (
           p_title IS NULL OR p_content IS NULL OR p_metadata IS NULL
           OR p_confidence IS NULL OR p_confidence NOT BETWEEN 0 AND 1
           OR p_content_hash IS NULL
           OR p_content_hash !~ '^[0-9a-f]{32}$'
       ) THEN
        RAISE EXCEPTION 'MODEL_SCORING_KNOWLEDGE_INVALID'
            USING ERRCODE = '22023';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            COALESCE(p_org_id::TEXT, p_owner_user_id::TEXT, 'system')
            || ':' || p_model_id || ':' || p_task_type,
            0
        )
    );
    SELECT audit.id, audit.knowledge_node_id
      INTO v_audit_id, v_node_id
      FROM scoring_audit_log audit
     WHERE audit.model_id = p_model_id
       AND audit.task_type = p_task_type
       AND audit.org_id IS NOT DISTINCT FROM p_org_id
       AND audit.owner_user_id IS NOT DISTINCT FROM p_owner_user_id
       AND audit.period_start = p_period_start
       AND audit.period_end = p_period_end
       AND audit.status = p_status
       AND audit.new_score = p_new_score
     ORDER BY audit.created_at DESC
     LIMIT 1;
    IF v_audit_id IS NOT NULL THEN
        RETURN jsonb_build_object(
            'outcome', 'already_recorded',
            'audit_id', v_audit_id,
            'knowledge_node_id', v_node_id
        );
    END IF;

    IF p_status = 'auto_applied' THEN
        v_node_id := _worker_upsert_model_score_knowledge(
            p_org_id, p_owner_user_id, p_task_type, p_title, p_content,
            p_metadata, p_embedding, p_confidence, p_content_hash
        );
    END IF;

    INSERT INTO scoring_audit_log (
        model_id, task_type, old_score, new_score, score_change,
        sample_count, metrics, period_start, period_end, status,
        knowledge_node_id, org_id, owner_user_id
    ) VALUES (
        p_model_id, p_task_type, p_old_score, p_new_score, p_score_change,
        p_sample_count, p_metrics, p_period_start, p_period_end, p_status,
        v_node_id, p_org_id, p_owner_user_id
    ) RETURNING id INTO v_audit_id;

    RETURN jsonb_build_object(
        'outcome', 'recorded',
        'audit_id', v_audit_id,
        'knowledge_node_id', v_node_id
    );
END;
$$;

REVOKE ALL ON TABLE worker_periodic_job_runs
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
REVOKE ALL ON FUNCTION _assert_global_worker_periodic_scope(),
    _worker_upsert_model_score_knowledge(
        UUID, UUID, TEXT, TEXT, TEXT, JSONB, TEXT, DOUBLE PRECISION, TEXT
    ),
    worker_claim_periodic_job(TEXT),
    worker_renew_periodic_job(TEXT, UUID),
    worker_finish_periodic_job(TEXT, UUID, BOOLEAN),
    worker_wecom_identity_health_snapshot()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;
GRANT EXECUTE ON FUNCTION
    worker_claim_periodic_job(TEXT),
    worker_renew_periodic_job(TEXT, UUID),
    worker_finish_periodic_job(TEXT, UUID, BOOLEAN),
    worker_wecom_identity_health_snapshot()
TO everydayai_worker;

RESET ROLE;
