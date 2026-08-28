-- 163A: Actor Worker actorless discovery and claim capabilities.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION _assert_actor_worker_discovery_scope()
RETURNS VOID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_worker'
       OR current_setting(
           'app.access_kind', TRUE
       ) IS DISTINCT FROM 'worker'
       OR NULLIF(
           current_setting('app.actor_user_id', TRUE), ''
       ) IS NOT NULL
       OR NULLIF(
           current_setting('app.org_id', TRUE), ''
       ) IS NOT NULL
       OR NULLIF(
           current_setting('app.request_id', TRUE), ''
       ) IS NULL THEN
        RAISE EXCEPTION 'ACTOR_WORKER_DISCOVERY_SCOPE_REQUIRED'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION discover_generation_turn_candidates(
    p_limit INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_candidates JSONB;
BEGIN
    PERFORM public._assert_actor_worker_discovery_scope();
    IF p_limit NOT BETWEEN 1 AND 1000 THEN
        RAISE EXCEPTION 'ACTOR_DISCOVERY_LIMIT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'task_id', candidate.id,
                'conversation_id', candidate.conversation_id,
                'execution_mode', candidate.execution_mode
            )
            ORDER BY candidate.queue_sequence, candidate.id
        ),
        '[]'::JSONB
    )
      INTO v_candidates
      FROM (
          SELECT task.id, task.conversation_id, task.execution_mode,
                 task.queue_sequence
            FROM public.tasks task
           WHERE task.type = 'chat'
             AND task.delivery_context @> '{"actor": true}'::JSONB
             AND task.status IN ('pending', 'running')
             AND task.conversation_id IS NOT NULL
             AND task.execution_mode IN ('serial', 'branch')
           ORDER BY task.queue_sequence, task.id
           LIMIT p_limit
      ) candidate;
    RETURN v_candidates;
END;
$$;

CREATE OR REPLACE FUNCTION worker_claim_next_serial_generation_turn(
    p_conversation_id UUID,
    p_lease_seconds INTEGER DEFAULT 90,
    p_max_attempts INTEGER DEFAULT 3
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_conversation public.conversations%ROWTYPE;
    v_owner public.tasks%ROWTYPE;
    v_task public.tasks%ROWTYPE;
    v_token UUID;
BEGIN
    PERFORM public._assert_actor_worker_discovery_scope();
    IF p_conversation_id IS NULL
       OR p_lease_seconds NOT BETWEEN 15 AND 300
       OR p_max_attempts < 1 THEN
        RAISE EXCEPTION 'ACTOR_CLAIM_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_conversation
      FROM public.conversations
     WHERE id = p_conversation_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'ACTOR_CONVERSATION_NOT_FOUND'
            USING ERRCODE = 'P0002';
    END IF;

    IF v_conversation.active_serial_task_id IS NOT NULL THEN
        SELECT * INTO v_owner
          FROM public.tasks
         WHERE id = v_conversation.active_serial_task_id
         FOR UPDATE;
        IF v_owner.id IS NOT NULL
           AND v_owner.status = 'running'
           AND v_owner.lease_expires_at > NOW() THEN
            RETURN jsonb_build_object('outcome', 'busy');
        END IF;
        IF v_owner.id IS NOT NULL AND v_owner.status = 'running' THEN
            IF v_owner.execution_attempt >= p_max_attempts THEN
                UPDATE public.tasks
                   SET status = 'failed',
                       terminal_reason = 'lease_attempts_exhausted',
                       completed_at = NOW(),
                       execution_token = NULL,
                       lease_expires_at = NULL
                 WHERE id = v_owner.id;
            ELSE
                UPDATE public.tasks
                   SET status = 'pending',
                       terminal_reason = 'lease_expired',
                       execution_token = NULL,
                       lease_expires_at = NULL
                 WHERE id = v_owner.id;
            END IF;
        END IF;
        UPDATE public.conversations
           SET active_serial_task_id = NULL,
               actor_updated_at = NOW()
         WHERE id = p_conversation_id;
    END IF;

    SELECT * INTO v_task
      FROM public.tasks
     WHERE conversation_id = p_conversation_id
       AND type = 'chat'
       AND delivery_context @> '{"actor": true}'::JSONB
       AND execution_mode = 'serial'
       AND status = 'pending'
       AND input_message_id IS NOT NULL
       AND turn_id IS NOT NULL
     ORDER BY queue_sequence, id
     FOR UPDATE SKIP LOCKED
     LIMIT 1;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'empty');
    END IF;

    v_token := gen_random_uuid();
    UPDATE public.tasks
       SET status = 'running',
           execution_token = v_token,
           lease_expires_at = NOW() + make_interval(
               secs => p_lease_seconds
           ),
           execution_attempt = execution_attempt + 1,
           started_at = COALESCE(started_at, NOW()),
           base_context_revision = v_conversation.context_revision,
           context_through_message_id =
               v_conversation.last_closed_message_id,
           terminal_reason = NULL
     WHERE id = v_task.id
     RETURNING * INTO v_task;

    UPDATE public.conversations
       SET active_serial_task_id = v_task.id,
           actor_updated_at = NOW()
     WHERE id = p_conversation_id;

    RETURN jsonb_build_object(
        'outcome', 'claimed',
        'task_id', v_task.id,
        'execution_token', v_token,
        'turn_id', v_task.turn_id,
        'input_message_id', v_task.input_message_id,
        'base_context_revision', v_task.base_context_revision,
        'context_through_message_id',
            v_task.context_through_message_id,
        'execution_attempt', v_task.execution_attempt,
        'user_id', v_task.user_id,
        'org_id', v_task.org_id
    );
END;
$$;

CREATE OR REPLACE FUNCTION worker_claim_branch_generation_turn(
    p_task_id UUID,
    p_lease_seconds INTEGER DEFAULT 90,
    p_max_attempts INTEGER DEFAULT 3
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_conversation public.conversations%ROWTYPE;
    v_task public.tasks%ROWTYPE;
    v_token UUID;
    v_conversation_id UUID;
BEGIN
    PERFORM public._assert_actor_worker_discovery_scope();
    IF p_task_id IS NULL
       OR p_lease_seconds NOT BETWEEN 15 AND 300
       OR p_max_attempts < 1 THEN
        RAISE EXCEPTION 'ACTOR_CLAIM_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT conversation_id INTO v_conversation_id
      FROM public.tasks WHERE id = p_task_id;
    IF v_conversation_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_TASK_NOT_FOUND'
            USING ERRCODE = 'P0002';
    END IF;
    SELECT * INTO v_conversation
      FROM public.conversations
     WHERE id = v_conversation_id
     FOR UPDATE;
    SELECT * INTO v_task
      FROM public.tasks
     WHERE id = p_task_id
     FOR UPDATE;
    IF v_task.id IS NULL
       OR v_task.type <> 'chat'
       OR NOT (
           v_task.delivery_context @> '{"actor": true}'::JSONB
       )
       OR v_task.execution_mode <> 'branch'
       OR v_task.input_message_id IS NULL
       OR v_task.turn_id IS NULL THEN
        RAISE EXCEPTION 'ACTOR_BRANCH_NOT_CLAIMABLE'
            USING ERRCODE = '55000';
    END IF;
    IF v_task.status = 'running'
       AND v_task.lease_expires_at > NOW() THEN
        RETURN jsonb_build_object('outcome', 'busy');
    END IF;
    IF v_task.status = 'running'
       AND v_task.execution_attempt >= p_max_attempts THEN
        UPDATE public.tasks
           SET status = 'failed',
               terminal_reason = 'lease_attempts_exhausted',
               completed_at = NOW(),
               execution_token = NULL,
               lease_expires_at = NULL
         WHERE id = p_task_id;
        RETURN jsonb_build_object(
            'outcome', 'attempts_exhausted'
        );
    END IF;
    IF v_task.status NOT IN ('pending', 'running') THEN
        RETURN jsonb_build_object(
            'outcome', 'terminal', 'status', v_task.status
        );
    END IF;

    v_token := gen_random_uuid();
    UPDATE public.tasks
       SET status = 'running',
           execution_token = v_token,
           lease_expires_at = NOW() + make_interval(
               secs => p_lease_seconds
           ),
           execution_attempt = execution_attempt + 1,
           started_at = COALESCE(started_at, NOW()),
           base_context_revision = v_conversation.context_revision,
           context_through_message_id =
               v_conversation.last_closed_message_id,
           terminal_reason = NULL
     WHERE id = p_task_id
     RETURNING * INTO v_task;

    RETURN jsonb_build_object(
        'outcome', 'claimed',
        'task_id', v_task.id,
        'execution_token', v_token,
        'turn_id', v_task.turn_id,
        'input_message_id', v_task.input_message_id,
        'base_context_revision', v_task.base_context_revision,
        'context_through_message_id',
            v_task.context_through_message_id,
        'execution_attempt', v_task.execution_attempt,
        'user_id', v_task.user_id,
        'org_id', v_task.org_id
    );
END;
$$;

CREATE OR REPLACE FUNCTION _assert_actor_worker_task_scope(
    p_task_id UUID
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
BEGIN
    IF p_task_id IS NULL
       OR session_user <> 'everydayai_worker'
       OR current_setting(
           'app.access_kind', TRUE
       ) IS DISTINCT FROM 'worker'
       OR NULLIF(
           current_setting('app.request_id', TRUE), ''
       ) IS NULL THEN
        RAISE EXCEPTION 'ACTOR_WORKER_TASK_SCOPE_REQUIRED'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_task
      FROM public.tasks
     WHERE id = p_task_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'ACTOR_TASK_NOT_FOUND'
            USING ERRCODE = 'P0002';
    END IF;
    IF NULLIF(
           current_setting('app.actor_user_id', TRUE), ''
       )::UUID IS DISTINCT FROM v_task.user_id
       OR NULLIF(
           current_setting('app.org_id', TRUE), ''
       )::UUID IS DISTINCT FROM v_task.org_id THEN
        RAISE EXCEPTION 'ACTOR_WORKER_TASK_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION worker_renew_generation_lease(
    p_task_id UUID,
    p_execution_token UUID,
    p_lease_seconds INTEGER DEFAULT 90
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_actor_worker_task_scope(p_task_id);
    SELECT public.renew_generation_lease(
        p_task_id, p_execution_token, p_lease_seconds
    ) INTO v_result;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION worker_get_claimed_generation_task(
    p_task_id UUID,
    p_execution_token UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
BEGIN
    IF p_execution_token IS NULL THEN
        RAISE EXCEPTION 'ACTOR_TASK_READ_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    PERFORM public._assert_actor_worker_task_scope(p_task_id);
    SELECT * INTO v_task
      FROM public.tasks
     WHERE id = p_task_id;
    IF v_task.status <> 'running'
       OR v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RAISE EXCEPTION 'ACTOR_TASK_READ_OWNERSHIP_LOST'
            USING ERRCODE = '42501';
    END IF;
    RETURN to_jsonb(v_task);
END;
$$;

CREATE OR REPLACE FUNCTION worker_commit_generation_turn_with_context_v2(
    p_task_id UUID, p_execution_token UUID, p_output_message_id UUID,
    p_result_content JSONB, p_usage JSONB,
    p_credits_cost INTEGER, p_tool_digest JSONB,
    p_data_evidence JSONB, p_context_items JSONB,
    p_artifacts JSONB, p_context_receipts JSONB,
    p_compaction JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_actor_worker_task_scope(p_task_id);
    SELECT public.commit_generation_turn_with_context_v2(
        p_task_id, p_execution_token, p_output_message_id,
        p_result_content, p_usage, p_credits_cost, p_tool_digest,
        p_data_evidence, p_context_items, p_artifacts,
        p_context_receipts, p_compaction
    ) INTO v_result;
    RETURN v_result;
END;
$$;

CREATE OR REPLACE FUNCTION worker_fail_generation_turn(
    p_task_id UUID,
    p_execution_token UUID,
    p_error_code TEXT,
    p_error_message TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_result JSONB;
BEGIN
    PERFORM public._assert_actor_worker_task_scope(p_task_id);
    SELECT public.fail_generation_turn(
        p_task_id, p_execution_token, p_error_code, p_error_message
    ) INTO v_result;
    RETURN v_result;
END;
$$;

REVOKE ALL ON FUNCTION _assert_actor_worker_discovery_scope()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker;
REVOKE ALL ON FUNCTION _assert_actor_worker_task_scope(UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker;
REVOKE ALL ON FUNCTION discover_generation_turn_candidates(INTEGER)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime;
REVOKE ALL ON FUNCTION worker_claim_next_serial_generation_turn(
    UUID, INTEGER, INTEGER
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime;
REVOKE ALL ON FUNCTION worker_claim_branch_generation_turn(
    UUID, INTEGER, INTEGER
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime;
REVOKE ALL ON FUNCTION worker_renew_generation_lease(
    UUID, UUID, INTEGER
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime;
REVOKE ALL ON FUNCTION worker_get_claimed_generation_task(
    UUID, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime;
REVOKE ALL ON FUNCTION worker_commit_generation_turn_with_context_v2(
    UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB, JSONB,
    JSONB, JSONB, JSONB, JSONB
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime;
REVOKE ALL ON FUNCTION worker_fail_generation_turn(
    UUID, UUID, TEXT, TEXT
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime;

GRANT EXECUTE ON FUNCTION discover_generation_turn_candidates(INTEGER)
TO everydayai_worker;
GRANT EXECUTE ON FUNCTION worker_claim_next_serial_generation_turn(
    UUID, INTEGER, INTEGER
) TO everydayai_worker;
GRANT EXECUTE ON FUNCTION worker_claim_branch_generation_turn(
    UUID, INTEGER, INTEGER
) TO everydayai_worker;
GRANT EXECUTE ON FUNCTION worker_renew_generation_lease(
    UUID, UUID, INTEGER
) TO everydayai_worker;
GRANT EXECUTE ON FUNCTION worker_get_claimed_generation_task(
    UUID, UUID
) TO everydayai_worker;
GRANT EXECUTE ON FUNCTION worker_commit_generation_turn_with_context_v2(
    UUID, UUID, UUID, JSONB, JSONB, INTEGER, JSONB, JSONB,
    JSONB, JSONB, JSONB, JSONB
) TO everydayai_worker;
GRANT EXECUTE ON FUNCTION worker_fail_generation_turn(
    UUID, UUID, TEXT, TEXT
) TO everydayai_worker;

RESET ROLE;
