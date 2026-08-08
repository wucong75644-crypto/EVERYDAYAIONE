-- AR-18-A1.1: keep Runtime-owned compatibility tasks out of legacy lifecycle owners.
SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION worker_claim_orphan_tasks(
    p_limit INTEGER DEFAULT 100,
    p_lease_seconds INTEGER DEFAULT 60
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_tasks JSONB;
BEGIN
    PERFORM public._assert_worker_orphan_recovery_scope();
    IF p_limit NOT BETWEEN 1 AND 500
       OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
        RAISE EXCEPTION 'ORPHAN_RECOVERY_CLAIM_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    WITH candidates AS (
        SELECT task.id
          FROM public.tasks task
         WHERE task.status IN ('pending', 'running')
           AND NOT (task.delivery_context @> '{"actor": true}'::JSONB)
           AND NOT (task.delivery_context @> '{"runtime": true}'::JSONB)
           AND (
               task.execution_token IS NULL
               OR task.lease_expires_at IS NULL
               OR task.lease_expires_at <= NOW()
           )
         ORDER BY task.created_at, task.id
         FOR UPDATE SKIP LOCKED
         LIMIT p_limit
    ),
    claimed AS (
        UPDATE public.tasks task
           SET status = 'running',
               execution_token = gen_random_uuid(),
               lease_expires_at = NOW() + make_interval(
                   secs => p_lease_seconds
               ),
               execution_attempt = task.execution_attempt + 1,
               started_at = COALESCE(task.started_at, NOW()),
               terminal_reason = 'startup_recovery_claimed'
          FROM candidates
         WHERE task.id = candidates.id
        RETURNING task.*
    )
    SELECT COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'id', claimed.id,
                'execution_token', claimed.execution_token,
                'type', claimed.type,
                'external_task_id', claimed.external_task_id,
                'placeholder_message_id', claimed.placeholder_message_id,
                'conversation_id', claimed.conversation_id,
                'model_id', claimed.model_id,
                'client_task_id', claimed.client_task_id,
                'accumulated_content', claimed.accumulated_content,
                'accumulated_blocks', claimed.accumulated_blocks,
                'delivery_context', claimed.delivery_context
            )
            ORDER BY claimed.created_at, claimed.id
        ),
        '[]'::JSONB
    )
      INTO v_tasks
      FROM claimed;
    RETURN v_tasks;
END;
$$;

CREATE OR REPLACE FUNCTION worker_complete_orphan_task(
    p_task_id UUID,
    p_execution_token UUID,
    p_content JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
    v_conversation public.conversations%ROWTYPE;
    v_message public.messages%ROWTYPE;
BEGIN
    PERFORM public._assert_worker_orphan_recovery_scope();
    IF p_task_id IS NULL OR p_execution_token IS NULL
       OR p_content IS NULL OR jsonb_typeof(p_content) <> 'array'
       OR jsonb_array_length(p_content) = 0 THEN
        RAISE EXCEPTION 'ORPHAN_RECOVERY_COMPLETE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task FROM public.tasks
     WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    IF v_task.delivery_context @> '{"actor": true}'::JSONB THEN
        RAISE EXCEPTION 'ORPHAN_RECOVERY_ACTOR_TASK_FORBIDDEN'
            USING ERRCODE = '42501';
    END IF;
    IF v_task.delivery_context @> '{"runtime": true}'::JSONB THEN
        RAISE EXCEPTION 'ORPHAN_RECOVERY_RUNTIME_TASK_FORBIDDEN'
            USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'completed'
       AND v_task.execution_token IS NOT DISTINCT FROM p_execution_token
       AND v_task.terminal_reason = 'startup_recovered_partial' THEN
        RETURN jsonb_build_object(
            'outcome', 'already_completed', 'task_id', v_task.id);
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object(
            'outcome', 'terminal', 'status', v_task.status);
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_task.lease_expires_at IS NULL OR v_task.lease_expires_at <= NOW() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;
    IF v_task.placeholder_message_id IS NULL
       OR v_task.conversation_id IS NULL THEN
        RAISE EXCEPTION 'ORPHAN_RECOVERY_MESSAGE_ANCHOR_MISSING'
            USING ERRCODE = '55000';
    END IF;

    SELECT * INTO v_conversation
      FROM public.conversations
     WHERE id = v_task.conversation_id
     FOR UPDATE;
    IF NOT FOUND
       OR v_conversation.user_id IS DISTINCT FROM v_task.user_id
       OR v_conversation.org_id IS DISTINCT FROM v_task.org_id THEN
        RAISE EXCEPTION 'ORPHAN_RECOVERY_CONVERSATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_message FROM public.messages
     WHERE id = v_task.placeholder_message_id::TEXT::UUID FOR UPDATE;
    IF FOUND THEN
        IF v_message.conversation_id IS DISTINCT FROM v_task.conversation_id
           OR v_message.org_id IS DISTINCT FROM v_task.org_id
           OR v_message.role::TEXT <> 'assistant' THEN
            RAISE EXCEPTION 'ORPHAN_RECOVERY_MESSAGE_SCOPE_MISMATCH'
                USING ERRCODE = '42501';
        END IF;
        UPDATE public.messages
           SET content = p_content::TEXT,
               status = 'interrupted',
               credits_cost = 0,
               is_error = FALSE,
               generation_params = COALESCE(generation_params, '{}'::JSONB)
                   || jsonb_build_object(
                       'type', COALESCE(v_task.type, 'chat'),
                       'model', COALESCE(v_task.model_id, 'unknown')
                   )
         WHERE id = v_message.id;
    ELSE
        INSERT INTO public.messages(
            id, conversation_id, org_id, role, content, status,
            credits_cost, is_error, generation_params
        ) VALUES (
            v_task.placeholder_message_id::TEXT::UUID,
            v_task.conversation_id,
            v_task.org_id,
            'assistant',
            p_content::TEXT,
            'interrupted',
            0,
            FALSE,
            jsonb_build_object(
                'type', COALESCE(v_task.type, 'chat'),
                'model', COALESCE(v_task.model_id, 'unknown'))
        );
    END IF;

    UPDATE public.tasks
       SET status = 'completed',
           completed_at = NOW(),
           error_message = '服务重启，已恢复部分内容',
           lease_expires_at = NULL,
           terminal_reason = 'startup_recovered_partial'
     WHERE id = v_task.id;

    RETURN jsonb_build_object(
        'outcome', 'completed', 'task_id', v_task.id);
END;
$$;

CREATE OR REPLACE FUNCTION worker_fail_orphan_task(
    p_task_id UUID,
    p_execution_token UUID,
    p_error_message TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
    v_refund JSONB;
BEGIN
    PERFORM public._assert_worker_orphan_recovery_scope();
    IF p_task_id IS NULL
       OR p_execution_token IS NULL
       OR NULLIF(BTRIM(p_error_message), '') IS NULL THEN
        RAISE EXCEPTION 'ORPHAN_RECOVERY_FAIL_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task
      FROM public.tasks
     WHERE id = p_task_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    IF v_task.delivery_context @> '{"actor": true}'::JSONB THEN
        RAISE EXCEPTION 'ORPHAN_RECOVERY_ACTOR_TASK_FORBIDDEN'
            USING ERRCODE = '42501';
    END IF;
    IF v_task.delivery_context @> '{"runtime": true}'::JSONB THEN
        RAISE EXCEPTION 'ORPHAN_RECOVERY_RUNTIME_TASK_FORBIDDEN'
            USING ERRCODE = '42501';
    END IF;
    IF v_task.status = 'failed'
       AND v_task.execution_token IS NOT DISTINCT FROM p_execution_token
       AND v_task.terminal_reason = 'startup_recovery_failed' THEN
        RETURN jsonb_build_object(
            'outcome', 'already_failed', 'task_id', v_task.id
        );
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object(
            'outcome', 'terminal', 'status', v_task.status
        );
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome', 'ownership_lost');
    END IF;
    IF v_task.lease_expires_at IS NULL
       OR v_task.lease_expires_at <= NOW() THEN
        RETURN jsonb_build_object('outcome', 'lease_expired');
    END IF;

    IF v_task.credit_transaction_id IS NOT NULL THEN
        SELECT public.atomic_refund_credits(
            v_task.credit_transaction_id
        ) INTO v_refund;
    END IF;

    UPDATE public.tasks
       SET status = 'failed',
           completed_at = NOW(),
           error_message = BTRIM(p_error_message),
           lease_expires_at = NULL,
           terminal_reason = 'startup_recovery_failed'
     WHERE id = v_task.id;

    RETURN jsonb_build_object(
        'outcome', 'failed',
        'task_id', v_task.id,
        'refund', v_refund
    );
END;
$$;

CREATE OR REPLACE FUNCTION worker_discover_legacy_active_tasks()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_tasks JSONB;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'MEDIA_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT COALESCE(jsonb_agg(to_jsonb(task_row)), '[]'::JSONB)
      INTO v_tasks
      FROM (
          SELECT task.*
            FROM public.tasks task
           WHERE task.status IN ('pending', 'running')
             AND task.started_at IS NOT NULL
             AND COALESCE(
                 (task.delivery_context ->> 'actor')::BOOLEAN,
                 FALSE
             ) IS FALSE
             AND NOT (task.delivery_context @> '{"runtime": true}'::JSONB)
           ORDER BY task.started_at, task.id
      ) task_row;
    RETURN v_tasks;
END;
$$;

CREATE OR REPLACE FUNCTION worker_fail_legacy_stale_task(
    p_task_id UUID,
    p_error_message TEXT,
    p_message_content JSONB DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'MEDIA_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF p_task_id IS NULL OR NULLIF(BTRIM(p_error_message), '') IS NULL THEN
        RAISE EXCEPTION 'MEDIA_WORKER_STALE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task
      FROM public.tasks
     WHERE id = p_task_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    IF COALESCE((v_task.delivery_context ->> 'actor')::BOOLEAN, FALSE) THEN
        RAISE EXCEPTION 'MEDIA_WORKER_ACTOR_TASK_FORBIDDEN'
            USING ERRCODE = '42501';
    END IF;
    IF v_task.delivery_context @> '{"runtime": true}'::JSONB THEN
        RAISE EXCEPTION 'MEDIA_WORKER_RUNTIME_TASK_FORBIDDEN'
            USING ERRCODE = '42501';
    END IF;
    IF v_task.status IN ('completed', 'failed', 'cancelled') THEN
        RETURN jsonb_build_object('outcome', 'already_terminal');
    END IF;

    IF p_message_content IS NOT NULL
       AND v_task.placeholder_message_id IS NOT NULL THEN
        INSERT INTO public.messages (
            id, conversation_id, role, content, status, credits_cost,
            task_id, generation_params
        ) VALUES (
            v_task.placeholder_message_id,
            v_task.conversation_id,
            'assistant',
            p_message_content,
            'failed',
            0,
            COALESCE(v_task.client_task_id, v_task.external_task_id),
            jsonb_build_object(
                'type', v_task.type,
                'model', COALESCE(v_task.model_id, 'unknown')
            )
        )
        ON CONFLICT (id) DO UPDATE
           SET content = EXCLUDED.content,
               status = EXCLUDED.status,
               generation_params = EXCLUDED.generation_params;
    END IF;

    UPDATE public.tasks
       SET status = 'failed',
           error_message = BTRIM(p_error_message),
           completed_at = NOW()
     WHERE id = v_task.id;
    RETURN jsonb_build_object('outcome', 'failed');
END;
$$;

REVOKE ALL ON FUNCTION worker_claim_orphan_tasks(INTEGER, INTEGER),
    worker_complete_orphan_task(UUID, UUID, JSONB),
    worker_fail_orphan_task(UUID, UUID, TEXT),
    worker_discover_legacy_active_tasks(),
    worker_fail_legacy_stale_task(UUID, TEXT, JSONB)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai,
    everydayai_agent_runtime_worker, everydayai_agent_model_gateway,
    everydayai_projection_worker, everydayai_authorization_worker,
    everydayai_sandbox_worker;

GRANT EXECUTE ON FUNCTION worker_claim_orphan_tasks(INTEGER, INTEGER),
    worker_complete_orphan_task(UUID, UUID, JSONB),
    worker_fail_orphan_task(UUID, UUID, TEXT),
    worker_discover_legacy_active_tasks(),
    worker_fail_legacy_stale_task(UUID, TEXT, JSONB)
TO everydayai_worker;

RESET ROLE;
