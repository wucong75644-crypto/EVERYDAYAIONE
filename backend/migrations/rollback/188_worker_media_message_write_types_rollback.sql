-- Rollback 188: 恢复迁移 187 的隐式消息列类型转换。

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION worker_commit_media_batch_message(
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
    IF p_message ->> 'id'
          IS DISTINCT FROM v_task.placeholder_message_id::TEXT
       OR p_message ->> 'conversation_id'
          IS DISTINCT FROM v_task.conversation_id::TEXT THEN
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

REVOKE ALL ON FUNCTION worker_commit_media_batch_message(
    TEXT, JSONB, TEXT
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

GRANT EXECUTE ON FUNCTION worker_commit_media_batch_message(
    TEXT, JSONB, TEXT
) TO everydayai_worker;
