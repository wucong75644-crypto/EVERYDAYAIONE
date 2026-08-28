-- 171: 跨租户媒体 Worker 的任务发现、读取、轮询触达与完成权领取能力。

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION worker_discover_media_tasks(p_limit INTEGER DEFAULT 100)
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
    IF p_limit IS NULL OR p_limit < 1 OR p_limit > 500 THEN
        RAISE EXCEPTION 'MEDIA_WORKER_LIMIT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT COALESCE(jsonb_agg(to_jsonb(task_row)), '[]'::JSONB)
      INTO v_tasks
      FROM (
          SELECT task.*
            FROM public.tasks task
           WHERE task.status IN ('pending', 'running')
             AND task.type IN ('image', 'video')
           ORDER BY COALESCE(task.last_polled_at, task.created_at), task.id
           LIMIT p_limit
      ) task_row;
    RETURN v_tasks;
END;
$$;

CREATE FUNCTION worker_get_media_task(p_external_task_id TEXT)
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
    IF NULLIF(BTRIM(p_external_task_id), '') IS NULL THEN
        RAISE EXCEPTION 'MEDIA_WORKER_TASK_ID_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task
      FROM public.tasks
     WHERE external_task_id = BTRIM(p_external_task_id)
       AND type IN ('image', 'video');
    RETURN CASE WHEN FOUND THEN to_jsonb(v_task) ELSE NULL END;
END;
$$;

CREATE FUNCTION worker_touch_media_task(p_external_task_id TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'MEDIA_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    UPDATE public.tasks
       SET last_polled_at = NOW()
     WHERE external_task_id = BTRIM(p_external_task_id)
       AND type IN ('image', 'video')
       AND status IN ('pending', 'running');
    RETURN FOUND;
END;
$$;

CREATE FUNCTION worker_claim_media_task_completion(
    p_external_task_id TEXT,
    p_expected_version INTEGER
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
    IF NULLIF(BTRIM(p_external_task_id), '') IS NULL
       OR p_expected_version IS NULL OR p_expected_version < 0 THEN
        RAISE EXCEPTION 'MEDIA_WORKER_CLAIM_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    UPDATE public.tasks
       SET version = version + 1,
           started_at = COALESCE(started_at, NOW())
     WHERE external_task_id = BTRIM(p_external_task_id)
       AND version = p_expected_version
       AND type IN ('image', 'video')
       AND status IN ('pending', 'running')
     RETURNING * INTO v_task;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_claimed');
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'claimed',
        'task', to_jsonb(v_task)
    );
END;
$$;

CREATE FUNCTION worker_settle_media_batch_item(
    p_external_task_id TEXT,
    p_expected_version INTEGER,
    p_status TEXT,
    p_result_data JSONB,
    p_error_message TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
    v_batch_tasks JSONB;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'MEDIA_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF NULLIF(BTRIM(p_external_task_id), '') IS NULL
       OR p_expected_version IS NULL OR p_expected_version < 0
       OR p_status NOT IN ('completed', 'failed') THEN
        RAISE EXCEPTION 'MEDIA_WORKER_SETTLE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_task
      FROM public.tasks
     WHERE external_task_id = BTRIM(p_external_task_id)
       AND type = 'image'
       AND batch_id IS NOT NULL
     FOR UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    IF v_task.status IN ('completed', 'failed', 'cancelled') THEN
        RETURN jsonb_build_object('outcome', 'already_terminal');
    END IF;
    IF v_task.version <> p_expected_version THEN
        RETURN jsonb_build_object('outcome', 'version_conflict');
    END IF;

    IF p_status = 'completed' AND v_task.credit_transaction_id IS NOT NULL THEN
        UPDATE public.credit_transactions
           SET status = 'confirmed',
               confirmed_at = NOW()
         WHERE id = v_task.credit_transaction_id
           AND status = 'pending';
    END IF;

    UPDATE public.tasks
       SET status = p_status,
           result_data = p_result_data,
           error_message = CASE
               WHEN p_status = 'failed' THEN p_error_message
               ELSE NULL
           END,
           completed_at = NOW()
     WHERE id = v_task.id;

    SELECT COALESCE(
               jsonb_agg(to_jsonb(batch_task) ORDER BY batch_task.image_index),
               '[]'::JSONB
           )
      INTO v_batch_tasks
      FROM public.tasks batch_task
     WHERE batch_task.batch_id = v_task.batch_id;

    RETURN jsonb_build_object(
        'outcome', 'settled',
        'batch_tasks', v_batch_tasks
    );
END;
$$;

CREATE FUNCTION worker_discover_legacy_active_tasks()
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
           ORDER BY task.started_at, task.id
      ) task_row;
    RETURN v_tasks;
END;
$$;

CREATE FUNCTION worker_fail_legacy_stale_task(
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

CREATE FUNCTION worker_get_media_batch_message(p_batch_id TEXT)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_message public.messages%ROWTYPE;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'MEDIA_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT message.* INTO v_message
      FROM public.tasks task
      JOIN public.messages message
        ON message.id = task.placeholder_message_id
     WHERE task.batch_id = NULLIF(BTRIM(p_batch_id), '')
       AND task.type = 'image'
     ORDER BY task.image_index, task.id
     LIMIT 1;
    RETURN CASE WHEN FOUND THEN to_jsonb(v_message) ELSE NULL END;
END;
$$;

CREATE FUNCTION worker_commit_media_batch_message(
    p_batch_id TEXT,
    p_message JSONB,
    p_preview TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
    v_message public.messages%ROWTYPE;
BEGIN
    IF session_user <> 'everydayai_worker' THEN
        RAISE EXCEPTION 'MEDIA_WORKER_ROLE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    IF NULLIF(BTRIM(p_batch_id), '') IS NULL
       OR p_message IS NULL
       OR jsonb_typeof(p_message) <> 'object' THEN
        RAISE EXCEPTION 'MEDIA_WORKER_MESSAGE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task
      FROM public.tasks
     WHERE batch_id = BTRIM(p_batch_id)
       AND type = 'image'
     ORDER BY image_index, id
     LIMIT 1;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('outcome', 'not_found');
    END IF;
    IF (p_message ->> 'id')::UUID IS DISTINCT FROM v_task.placeholder_message_id
       OR (p_message ->> 'conversation_id')::UUID
          IS DISTINCT FROM v_task.conversation_id THEN
        RAISE EXCEPTION 'MEDIA_WORKER_MESSAGE_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    INSERT INTO public.messages (
        id, conversation_id, role, content, status, credits_cost,
        task_id, generation_params
    ) VALUES (
        (p_message ->> 'id')::UUID,
        (p_message ->> 'conversation_id')::UUID,
        'assistant',
        COALESCE(p_message -> 'content', '[]'::JSONB),
        p_message ->> 'status',
        COALESCE((p_message ->> 'credits_cost')::INTEGER, 0),
        p_message ->> 'task_id',
        COALESCE(p_message -> 'generation_params', '{}'::JSONB)
    )
    ON CONFLICT (id) DO UPDATE
       SET content = EXCLUDED.content,
           status = EXCLUDED.status,
           credits_cost = EXCLUDED.credits_cost,
           task_id = EXCLUDED.task_id,
           generation_params = EXCLUDED.generation_params
    RETURNING * INTO v_message;

    IF p_preview IS NOT NULL THEN
        UPDATE public.conversations
           SET last_message_preview = p_preview
         WHERE id = v_task.conversation_id;
    END IF;
    RETURN jsonb_build_object(
        'outcome', 'committed',
        'message', to_jsonb(v_message)
    );
END;
$$;

REVOKE ALL ON FUNCTION worker_discover_media_tasks(INTEGER),
    worker_get_media_task(TEXT),
    worker_touch_media_task(TEXT),
    worker_claim_media_task_completion(TEXT, INTEGER),
    worker_settle_media_batch_item(TEXT, INTEGER, TEXT, JSONB, TEXT),
    worker_discover_legacy_active_tasks(),
    worker_fail_legacy_stale_task(UUID, TEXT, JSONB),
    worker_get_media_batch_message(TEXT),
    worker_commit_media_batch_message(TEXT, JSONB, TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

GRANT EXECUTE ON FUNCTION worker_discover_media_tasks(INTEGER),
    worker_get_media_task(TEXT),
    worker_touch_media_task(TEXT),
    worker_claim_media_task_completion(TEXT, INTEGER),
    worker_settle_media_batch_item(TEXT, INTEGER, TEXT, JSONB, TEXT),
    worker_discover_legacy_active_tasks(),
    worker_fail_legacy_stale_task(UUID, TEXT, JSONB),
    worker_get_media_batch_message(TEXT),
    worker_commit_media_batch_message(TEXT, JSONB, TEXT)
TO everydayai_worker;

RESET ROLE;
