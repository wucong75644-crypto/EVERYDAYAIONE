-- 242: 定时任务企微结果持久投递 Outbox。
-- 执行成功与待投递事实由同一 RPC 提交；Redis 不再参与定时任务投递。

CREATE TABLE IF NOT EXISTS scheduled_task_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES scheduled_task_runs(id) ON DELETE CASCADE,
    task_id UUID NOT NULL REFERENCES scheduled_tasks(id) ON DELETE CASCADE,
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    delivery_key TEXT NOT NULL,
    delivery_kind TEXT NOT NULL DEFAULT 'result'
        CHECK (delivery_kind IN ('result', 'owner_alert')),
    target_context JSONB NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'delivering', 'delivered', 'dead')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    last_error TEXT,
    delivered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT scheduled_task_deliveries_run_key_unique
        UNIQUE (run_id, delivery_key),
    CONSTRAINT scheduled_task_deliveries_target_object_check
        CHECK (jsonb_typeof(target_context) = 'object'),
    CONSTRAINT scheduled_task_deliveries_payload_object_check
        CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT scheduled_task_deliveries_lease_pair_check
        CHECK ((lease_token IS NULL) = (lease_expires_at IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_scheduled_task_deliveries_claim
    ON scheduled_task_deliveries(status, next_attempt_at, created_at)
    WHERE status IN ('pending', 'delivering');
CREATE INDEX IF NOT EXISTS idx_scheduled_task_deliveries_run
    ON scheduled_task_deliveries(run_id, delivery_kind, status);

CREATE OR REPLACE FUNCTION refresh_scheduled_task_run_push_status(
    p_run_id UUID
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_total INTEGER;
    v_pending INTEGER;
    v_delivered INTEGER;
    v_dead INTEGER;
    v_status TEXT;
BEGIN
    SELECT
        COUNT(*),
        COUNT(*) FILTER (WHERE status IN ('pending', 'delivering')),
        COUNT(*) FILTER (WHERE status = 'delivered'),
        COUNT(*) FILTER (WHERE status = 'dead')
      INTO v_total, v_pending, v_delivered, v_dead
      FROM scheduled_task_deliveries
     WHERE run_id = p_run_id
       AND delivery_kind = 'result';

    v_status := CASE
        WHEN v_total = 0 THEN 'skipped'
        WHEN v_pending > 0 AND v_delivered = 0 THEN 'queued'
        WHEN v_pending > 0 THEN 'retrying'
        WHEN v_dead = 0 THEN 'pushed'
        WHEN v_delivered = 0 THEN 'push_failed'
        ELSE 'partial'
    END;

    UPDATE scheduled_task_runs
       SET push_status = v_status
     WHERE id = p_run_id;
    RETURN v_status;
END;
$$;

CREATE OR REPLACE FUNCTION complete_scheduled_task_success(
    p_task_id UUID,
    p_run_id UUID,
    p_next_status TEXT,
    p_next_run_at TIMESTAMPTZ,
    p_last_summary TEXT,
    p_last_result JSONB,
    p_credits_used INTEGER,
    p_tokens_used INTEGER,
    p_duration_ms INTEGER,
    p_deliveries JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task scheduled_tasks%ROWTYPE;
    v_run scheduled_task_runs%ROWTYPE;
    v_delivery JSONB;
    v_push_status TEXT;
BEGIN
    IF p_next_status NOT IN ('active', 'paused')
       OR p_last_result IS NULL OR jsonb_typeof(p_last_result) <> 'object'
       OR p_deliveries IS NULL OR jsonb_typeof(p_deliveries) <> 'array'
       OR p_credits_used < 0 OR p_tokens_used < 0 OR p_duration_ms < 0 THEN
        RAISE EXCEPTION 'SCHEDULED_TASK_SUCCESS_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_run
      FROM scheduled_task_runs
     WHERE id = p_run_id AND task_id = p_task_id
     FOR UPDATE;
    IF NOT FOUND OR v_run.status <> 'running' THEN
        RETURN jsonb_build_object('outcome', 'run_not_running');
    END IF;

    SELECT * INTO v_task
      FROM scheduled_tasks
     WHERE id = p_task_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'task_missing');
    END IF;

    FOR v_delivery IN SELECT value FROM jsonb_array_elements(p_deliveries) LOOP
        IF jsonb_typeof(v_delivery) <> 'object'
           OR COALESCE(v_delivery->>'delivery_key', '') = ''
           OR v_delivery->>'delivery_kind' <> 'result'
           OR jsonb_typeof(v_delivery->'target_context') <> 'object'
           OR jsonb_typeof(v_delivery->'payload') <> 'object'
           OR v_delivery->'target_context'->>'type' NOT IN ('wecom_user', 'wecom_group')
           OR COALESCE(v_delivery->'target_context'->>'chatid', '') = '' THEN
            RAISE EXCEPTION 'SCHEDULED_TASK_DELIVERY_INVALID'
                USING ERRCODE = '22023';
        END IF;

        INSERT INTO scheduled_task_deliveries(
            run_id, task_id, org_id, delivery_key, delivery_kind,
            target_context, payload
        ) VALUES (
            p_run_id, p_task_id, v_task.org_id,
            v_delivery->>'delivery_key', 'result',
            v_delivery->'target_context', v_delivery->'payload'
        ) ON CONFLICT (run_id, delivery_key) DO NOTHING;
    END LOOP;

    UPDATE scheduled_tasks
       SET status = CASE WHEN v_task.status = 'running' THEN p_next_status ELSE v_task.status END,
           next_run_at = CASE
               WHEN v_task.status = 'running' THEN p_next_run_at
               ELSE v_task.next_run_at
           END,
           last_run_at = NOW(),
           last_summary = p_last_summary,
           last_result = p_last_result,
           run_count = v_task.run_count + 1,
           consecutive_failures = 0,
           updated_at = NOW()
     WHERE id = p_task_id;

    UPDATE scheduled_task_runs
       SET status = 'success',
           result_summary = p_last_summary,
           result_files = COALESCE(p_last_result->'files', '[]'::JSONB),
           credits_used = p_credits_used,
           tokens_used = p_tokens_used,
           duration_ms = p_duration_ms,
           finished_at = NOW()
     WHERE id = p_run_id;

    v_push_status := refresh_scheduled_task_run_push_status(p_run_id);
    RETURN jsonb_build_object(
        'outcome', 'completed',
        'push_status', v_push_status
    );
END;
$$;

CREATE OR REPLACE FUNCTION enqueue_scheduled_task_owner_alert(
    p_task_id UUID,
    p_run_id UUID,
    p_org_id UUID,
    p_delivery_key TEXT,
    p_target_context JSONB,
    p_payload JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
BEGIN
    IF COALESCE(p_delivery_key, '') = ''
       OR jsonb_typeof(p_target_context) <> 'object'
       OR jsonb_typeof(p_payload) <> 'object'
       OR p_target_context->>'type' <> 'wecom_user'
       OR COALESCE(p_target_context->>'chatid', '') = '' THEN
        RAISE EXCEPTION 'SCHEDULED_TASK_OWNER_ALERT_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO scheduled_task_deliveries(
        run_id, task_id, org_id, delivery_key, delivery_kind,
        target_context, payload
    )
    SELECT p_run_id, p_task_id, p_org_id, p_delivery_key, 'owner_alert',
           p_target_context, p_payload
      WHERE EXISTS (
          SELECT 1 FROM scheduled_task_runs r
           WHERE r.id = p_run_id AND r.task_id = p_task_id
      )
        AND EXISTS (
          SELECT 1 FROM scheduled_tasks t
           WHERE t.id = p_task_id AND t.org_id = p_org_id
      )
    ON CONFLICT (run_id, delivery_key) DO NOTHING;

    RETURN jsonb_build_object('outcome', 'queued');
END;
$$;

CREATE OR REPLACE FUNCTION claim_scheduled_task_delivery(
    p_lease_seconds INTEGER DEFAULT 120,
    p_max_attempts INTEGER DEFAULT 8
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_delivery scheduled_task_deliveries%ROWTYPE;
    v_token UUID;
BEGIN
    IF p_lease_seconds NOT BETWEEN 15 AND 300 OR p_max_attempts < 1 THEN
        RAISE EXCEPTION 'SCHEDULED_TASK_DELIVERY_CLAIM_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    UPDATE scheduled_task_deliveries
       SET status = 'dead', lease_token = NULL, lease_expires_at = NULL,
           last_error = COALESCE(last_error, 'delivery lease expired after max attempts'),
           updated_at = NOW()
     WHERE status = 'delivering'
       AND lease_expires_at <= NOW()
       AND attempt_count >= p_max_attempts;

    SELECT * INTO v_delivery
      FROM scheduled_task_deliveries
     WHERE (
            status = 'pending'
            OR (status = 'delivering' AND lease_expires_at <= NOW())
       )
       AND next_attempt_at <= NOW()
       AND attempt_count < p_max_attempts
     ORDER BY next_attempt_at, created_at, id
     FOR UPDATE SKIP LOCKED
     LIMIT 1;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'empty');
    END IF;

    v_token := gen_random_uuid();
    UPDATE scheduled_task_deliveries
       SET status = 'delivering',
           attempt_count = attempt_count + 1,
           lease_token = v_token,
           lease_expires_at = NOW() + make_interval(secs => p_lease_seconds),
           updated_at = NOW()
     WHERE id = v_delivery.id
     RETURNING * INTO v_delivery;

    RETURN jsonb_build_object(
        'outcome', 'claimed',
        'delivery_id', v_delivery.id,
        'run_id', v_delivery.run_id,
        'delivery_kind', v_delivery.delivery_kind,
        'lease_token', v_token,
        'org_id', v_delivery.org_id,
        'target_context', v_delivery.target_context,
        'payload', v_delivery.payload
    );
END;
$$;

CREATE OR REPLACE FUNCTION complete_scheduled_task_delivery(
    p_delivery_id UUID,
    p_lease_token UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_delivery scheduled_task_deliveries%ROWTYPE;
    v_push_status TEXT;
BEGIN
    SELECT * INTO v_delivery FROM scheduled_task_deliveries
     WHERE id = p_delivery_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'SCHEDULED_TASK_DELIVERY_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    IF v_delivery.status = 'delivered' THEN
        RETURN jsonb_build_object('outcome', 'already_delivered');
    END IF;
    IF v_delivery.status <> 'delivering'
       OR v_delivery.lease_token IS DISTINCT FROM p_lease_token
       OR v_delivery.lease_expires_at <= NOW() THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    UPDATE scheduled_task_deliveries
       SET status = 'delivered', delivered_at = NOW(),
           lease_token = NULL, lease_expires_at = NULL, last_error = NULL,
           updated_at = NOW()
     WHERE id = p_delivery_id;
    v_push_status := refresh_scheduled_task_run_push_status(v_delivery.run_id);
    RETURN jsonb_build_object('outcome', 'delivered', 'push_status', v_push_status);
END;
$$;

CREATE OR REPLACE FUNCTION fail_scheduled_task_delivery(
    p_delivery_id UUID,
    p_lease_token UUID,
    p_error TEXT,
    p_max_attempts INTEGER DEFAULT 8
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_delivery scheduled_task_deliveries%ROWTYPE;
    v_dead BOOLEAN;
    v_delay_seconds INTEGER;
    v_push_status TEXT;
BEGIN
    IF p_max_attempts < 1 OR COALESCE(p_error, '') = '' THEN
        RAISE EXCEPTION 'SCHEDULED_TASK_DELIVERY_FAIL_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_delivery FROM scheduled_task_deliveries
     WHERE id = p_delivery_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'SCHEDULED_TASK_DELIVERY_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;
    IF v_delivery.status <> 'delivering'
       OR v_delivery.lease_token IS DISTINCT FROM p_lease_token
       OR v_delivery.lease_expires_at <= NOW() THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;

    v_dead := v_delivery.attempt_count >= p_max_attempts;
    v_delay_seconds := LEAST(
        900,
        5 * CAST(POWER(2, GREATEST(v_delivery.attempt_count - 1, 0)) AS INTEGER)
    );
    UPDATE scheduled_task_deliveries
       SET status = CASE WHEN v_dead THEN 'dead' ELSE 'pending' END,
           next_attempt_at = CASE
               WHEN v_dead THEN next_attempt_at
               ELSE NOW() + make_interval(secs => v_delay_seconds)
           END,
           lease_token = NULL,
           lease_expires_at = NULL,
           last_error = LEFT(p_error, 2000),
           updated_at = NOW()
     WHERE id = p_delivery_id;
    v_push_status := refresh_scheduled_task_run_push_status(v_delivery.run_id);
    RETURN jsonb_build_object(
        'outcome', CASE WHEN v_dead THEN 'dead' ELSE 'retry_scheduled' END,
        'retry_seconds', CASE WHEN v_dead THEN NULL ELSE v_delay_seconds END,
        'push_status', v_push_status
    );
END;
$$;

CREATE OR REPLACE FUNCTION claim_scheduled_task_now(
    p_task_id UUID,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_task scheduled_tasks%ROWTYPE;
    v_previous_status TEXT;
BEGIN
    SELECT * INTO v_task FROM scheduled_tasks
     WHERE id = p_task_id AND org_id = p_org_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    IF v_task.status = 'running' THEN
        RETURN jsonb_build_object('outcome', 'already_running');
    END IF;

    v_previous_status := v_task.status;
    UPDATE scheduled_tasks
       SET status = 'running', next_run_at = NULL, updated_at = NOW()
     WHERE id = v_task.id
     RETURNING * INTO v_task;
    RETURN jsonb_build_object(
        'outcome', 'claimed',
        'previous_status', v_previous_status,
        'task', to_jsonb(v_task)
    );
END;
$$;

REVOKE ALL ON FUNCTION refresh_scheduled_task_run_push_status(UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION complete_scheduled_task_success(UUID, UUID, TEXT, TIMESTAMPTZ, TEXT, JSONB, INTEGER, INTEGER, INTEGER, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION enqueue_scheduled_task_owner_alert(UUID, UUID, UUID, TEXT, JSONB, JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION claim_scheduled_task_delivery(INTEGER, INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION complete_scheduled_task_delivery(UUID, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION fail_scheduled_task_delivery(UUID, UUID, TEXT, INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION claim_scheduled_task_now(UUID, UUID) FROM PUBLIC;

COMMENT ON TABLE scheduled_task_deliveries
    IS '定时任务企微投递 Outbox；数据库租约、重试和死信保证至少一次投递';
