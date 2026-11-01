SET LOCAL ROLE everydayai;
DO $$ BEGIN
    ALTER TABLE public.agent_task_events NO FORCE ROW LEVEL SECURITY;
    IF EXISTS(SELECT 1 FROM public.agent_task_events WHERE continuation_task_id IS NOT NULL) THEN
        RAISE EXCEPTION 'TASK_CONTINUATION_ROLLBACK_REQUIRES_DRAIN';
    END IF;
    ALTER TABLE public.agent_task_events FORCE ROW LEVEL SECURITY;
END $$;
DROP FUNCTION public.apply_agent_image_continuation(uuid,uuid,bigint,jsonb);
DROP FUNCTION public.load_agent_image_continuation(uuid,uuid);
DROP FUNCTION public.enqueue_agent_image_continuation(uuid,bigint,uuid);
DROP FUNCTION public.enqueue_agent_task_continuation(uuid,bigint,uuid,text);
DROP FUNCTION public.list_agent_task_child_hints(uuid,integer);
DROP FUNCTION public.mark_agent_task_child_hint(uuid);
DROP INDEX public.agent_task_continuation_revision_once;
DROP INDEX public.agent_task_child_scan;
DO $restore$ DECLARE v_owner text; v_body text; BEGIN
    SELECT pg_get_userbyid(proowner),prosrc INTO v_owner,v_body FROM pg_proc
      WHERE oid='public.close_generation_turn(uuid,uuid,uuid)'::regprocedure;
    IF v_owner NOT IN ('everydayai','everydayai_owner') OR md5(v_body)<>'a522041d35870eefde6c2ecd14923b84' THEN
        RAISE EXCEPTION 'TASK_CONTINUATION_CLOSE_OWNER_UNSUPPORTED';
    END IF;
    EXECUTE format('SET LOCAL ROLE %I',v_owner);
END $restore$;
CREATE OR REPLACE FUNCTION close_generation_turn(
    p_conversation_id UUID,
    p_task_id UUID,
    p_output_message_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_conversation conversations%ROWTYPE;
    v_task tasks%ROWTYPE;
    v_output messages%ROWTYPE;
    v_closed_revision BIGINT;
BEGIN
    SELECT * INTO v_conversation
      FROM conversations
     WHERE id = p_conversation_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'TURN_CONVERSATION_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;

    SELECT * INTO v_task FROM tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND
       OR v_task.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_task.org_id IS DISTINCT FROM v_conversation.org_id
       OR v_task.input_message_id IS NULL
       OR v_task.turn_id IS NULL THEN
        RAISE EXCEPTION 'TURN_TASK_NOT_BOUND' USING ERRCODE = '55000';
    END IF;

    SELECT * INTO v_output FROM messages WHERE id = p_output_message_id FOR UPDATE;
    IF NOT FOUND
       OR v_output.conversation_id IS DISTINCT FROM p_conversation_id
       OR v_output.org_id IS DISTINCT FROM v_conversation.org_id
       OR v_output.role::TEXT <> 'assistant'
       OR (v_task.assistant_message_id IS NOT NULL
           AND v_task.assistant_message_id IS DISTINCT FROM p_output_message_id)
       OR v_output.reply_to_message_id IS DISTINCT FROM v_task.input_message_id
       OR v_output.turn_id IS DISTINCT FROM v_task.turn_id THEN
        RAISE EXCEPTION 'TURN_OUTPUT_SCOPE_MISMATCH' USING ERRCODE = '42501';
    END IF;

    IF v_output.context_revision IS NOT NULL THEN
        UPDATE tasks
           SET status = 'completed',
               completed_at = COALESCE(completed_at, NOW())
         WHERE id = p_task_id;
        RETURN jsonb_build_object(
            'turn_id', v_task.turn_id,
            'closed_revision', v_output.context_revision,
            'output_message_id', p_output_message_id,
            'already_closed', TRUE
        );
    END IF;

    v_closed_revision := v_conversation.context_revision + 1;
    UPDATE messages
       SET turn_id = v_task.turn_id,
           context_revision = v_closed_revision,
           message_kind = 'conversation'
     WHERE id = v_task.input_message_id
       AND conversation_id = p_conversation_id
       AND context_revision IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'TURN_INPUT_NOT_FOUND' USING ERRCODE = 'P0002';
    END IF;

    UPDATE messages
       SET context_revision = v_closed_revision,
           message_kind = 'conversation'
     WHERE id = p_output_message_id;

    UPDATE tasks
       SET assistant_message_id = p_output_message_id,
           status = 'completed',
           completed_at = COALESCE(completed_at, NOW())
     WHERE id = p_task_id
       AND (assistant_message_id IS NULL OR assistant_message_id = p_output_message_id);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'TURN_OUTPUT_ALREADY_BOUND' USING ERRCODE = '23505';
    END IF;

    UPDATE conversations
       SET context_revision = v_closed_revision,
           last_closed_message_id = p_output_message_id
     WHERE id = p_conversation_id;

    RETURN jsonb_build_object(
        'turn_id', v_task.turn_id,
        'closed_revision', v_closed_revision,
        'output_message_id', p_output_message_id,
        'already_closed', FALSE
    );
END;
$$;
RESET ROLE;
