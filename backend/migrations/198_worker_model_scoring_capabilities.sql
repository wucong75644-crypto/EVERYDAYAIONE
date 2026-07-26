-- 198: Worker 模型评分读取与提交窄能力。
-- 企业指标按 org 聚合；散客指标按 user_id 聚合，禁止混成系统知识。

SET LOCAL ROLE everydayai_owner;

ALTER TABLE scoring_audit_log
    ADD COLUMN owner_user_id UUID REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE scoring_audit_log
    ADD CONSTRAINT scoring_audit_owner_scope_check
    CHECK (org_id IS NULL OR owner_user_id IS NULL);
CREATE INDEX idx_scoring_audit_owner_user
ON scoring_audit_log(owner_user_id, created_at DESC)
WHERE owner_user_id IS NOT NULL;

CREATE FUNCTION _assert_worker_model_scoring_scope(p_org_id UUID)
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
       OR tenant_org_id() IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'MODEL_SCORING_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE FUNCTION worker_model_scoring_snapshot(p_org_id UUID)
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

CREATE FUNCTION worker_commit_model_score(
    p_org_id UUID,
    p_owner_user_id UUID,
    p_model_id TEXT,
    p_task_type TEXT,
    p_old_score DOUBLE PRECISION,
    p_new_score DOUBLE PRECISION,
    p_score_change DOUBLE PRECISION,
    p_sample_count INTEGER,
    p_metrics JSONB,
    p_period_start TIMESTAMPTZ,
    p_period_end TIMESTAMPTZ,
    p_status TEXT,
    p_title TEXT,
    p_content TEXT,
    p_metadata JSONB,
    p_confidence DOUBLE PRECISION,
    p_content_hash TEXT,
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

    IF p_status = 'auto_applied' THEN
        IF p_title IS NULL OR p_content IS NULL OR p_metadata IS NULL
           OR p_confidence IS NULL OR p_confidence NOT BETWEEN 0 AND 1
           OR p_content_hash IS NULL
           OR p_content_hash !~ '^[0-9a-f]{64}$' THEN
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
        SELECT node.id INTO v_node_id
          FROM knowledge_nodes node
         WHERE node.content_hash = p_content_hash
           AND node.org_id IS NOT DISTINCT FROM p_org_id
           AND node.owner_user_id IS NOT DISTINCT FROM p_owner_user_id
         FOR UPDATE;
        IF v_node_id IS NULL THEN
            INSERT INTO knowledge_nodes (
                category, subcategory, node_type, title, content,
                metadata, embedding, source, confidence, scope,
                content_hash, org_id, owner_user_id
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

REVOKE ALL ON FUNCTION worker_model_scoring_snapshot(UUID),
    worker_commit_model_score(
        UUID, UUID, TEXT, TEXT, DOUBLE PRECISION, DOUBLE PRECISION,
        DOUBLE PRECISION, INTEGER, JSONB, TIMESTAMPTZ, TIMESTAMPTZ,
        TEXT, TEXT, TEXT, JSONB, DOUBLE PRECISION, TEXT, TEXT
    )
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
REVOKE ALL ON FUNCTION _assert_worker_model_scoring_scope(UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
GRANT EXECUTE ON FUNCTION worker_model_scoring_snapshot(UUID),
    worker_commit_model_score(
        UUID, UUID, TEXT, TEXT, DOUBLE PRECISION, DOUBLE PRECISION,
        DOUBLE PRECISION, INTEGER, JSONB, TIMESTAMPTZ, TIMESTAMPTZ,
        TEXT, TEXT, TEXT, JSONB, DOUBLE PRECISION, TEXT, TEXT
    )
TO everydayai_worker;

RESET ROLE;
