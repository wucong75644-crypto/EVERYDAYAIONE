DO $migration$
DECLARE definition text := pg_get_functiondef('public.append_conversation_delivery_event(uuid,uuid,text,jsonb,uuid)'::regprocedure);
 previous_role text := current_setting('role');
 old_fragment text := $old$    -- Presentation follows the fenced, durable start event, not queue/running status.
    IF p_event_type = 'message_start'
       AND v_task.delivery_context->>'input_kind' = 'agent_task_continuation' THEN
        UPDATE public.messages
           SET generation_params = COALESCE(generation_params, '{}'::jsonb)
               || jsonb_build_object('delivery_presentation',jsonb_build_object('origin','internal_continuation','output_started',true))
         WHERE id = v_task.assistant_message_id AND conversation_id = v_task.conversation_id;
    END IF;

    RETURN jsonb_build_object(
        'outcome', 'appended',$old$;
 new_fragment text := $new$    RETURN jsonb_build_object(
        'outcome', 'appended',$new$;
BEGIN
 IF strpos(definition,old_fragment)=0 THEN RAISE EXCEPTION 'Unexpected continuation presentation definition: public.append_conversation_delivery_event(uuid,uuid,text,jsonb,uuid)'; END IF;
 PERFORM set_config('role',(SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid='public.append_conversation_delivery_event(uuid,uuid,text,jsonb,uuid)'::regprocedure),true);
 EXECUTE replace(definition,old_fragment,new_fragment);
 PERFORM set_config('role',previous_role,true);
END $migration$;
DO $migration$
DECLARE definition text := pg_get_functiondef('public.enqueue_agent_task_continuation(uuid,bigint,uuid,text)'::regprocedure);
 previous_role text := current_setting('role');
 old_fragment text := $old$    INSERT INTO public.messages(id,conversation_id,org_id,role,content,status,turn_id,reply_to_message_id,generation_params)
      VALUES(v_output_id,s.conversation_id,s.org_id,'assistant','[]','pending',owner.turn_id,owner.input_message_id,
        jsonb_build_object('delivery_presentation',jsonb_build_object('origin','internal_continuation','output_started',false)));$old$;
 new_fragment text := $new$    INSERT INTO public.messages(id,conversation_id,org_id,role,content,status,turn_id,reply_to_message_id)
      VALUES(v_output_id,s.conversation_id,s.org_id,'assistant','[]','pending',owner.turn_id,owner.input_message_id);$new$;
BEGIN
 IF strpos(definition,old_fragment)=0 THEN RAISE EXCEPTION 'Unexpected continuation presentation definition: public.enqueue_agent_task_continuation(uuid,bigint,uuid,text)'; END IF;
 PERFORM set_config('role',(SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid='public.enqueue_agent_task_continuation(uuid,bigint,uuid,text)'::regprocedure),true);
 EXECUTE replace(definition,old_fragment,new_fragment);
 PERFORM set_config('role',previous_role,true);
END $migration$;
