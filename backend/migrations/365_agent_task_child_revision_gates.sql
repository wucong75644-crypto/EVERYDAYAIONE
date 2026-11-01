-- Narrow worker check: derive the actor from an already authorized native child.
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.agent_child_revision_gate(p_task_id uuid,p_org_id uuid)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; root uuid; operation_id text; op jsonb; result text;
 actor_setting text:=current_setting('app.actor_user_id',true); kind_setting text:=current_setting('app.access_kind',true);
BEGIN
 PERFORM public.assert_chat_image_worker(p_org_id);
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id AND org_id IS NOT DISTINCT FROM p_org_id;
 IF t.id IS NULL THEN RAISE EXCEPTION 'TASK_CHILD_GATE_DENIED' USING ERRCODE='42501'; END IF;
 IF t.type='image' THEN root:=(t.request_params#>>'{_agent_task_dependency_v1,root_task_id}')::uuid;
 ELSIF t.type='video' THEN root:=(t.request_params#>>'{_agent_video_request_v1,origin,logical_task_id}')::uuid;
 ELSE RAISE EXCEPTION 'TASK_CHILD_GATE_DENIED' USING ERRCODE='42501'; END IF;
 IF root IS NULL THEN RETURN 'ready'; END IF;
 -- Only the native worker's current organization/child can be narrowed to its
 -- real actor. Restore settings even on failure; never return private input.
 PERFORM set_config('app.actor_user_id',t.user_id::text,true);
 PERFORM set_config('app.access_kind','runtime',true);
 -- Match pause/cancel and native video dispatch: conversation, owner/child,
 -- logical state. Serialize STEER admission before testing its pending facts.
 PERFORM 1 FROM public.conversations WHERE id=t.conversation_id FOR UPDATE;
 PERFORM 1 FROM public.tasks WHERE id=(SELECT owner_task_id FROM public.agent_task_states WHERE root_task_id=root) FOR UPDATE;
 PERFORM 1 FROM public.tasks WHERE id=t.id FOR UPDATE;
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=root AND actor_user_id=t.user_id
   AND org_id IS NOT DISTINCT FROM t.org_id AND conversation_id=t.conversation_id FOR SHARE;
 IF s.root_task_id IS NULL THEN result:='stopped';
 ELSIF s.state->>'image_binding_version' IS DISTINCT FROM '3' THEN result:='ready';
 ELSIF jsonb_array_length(public.agent_task_unreviewed_changes(root))>0 THEN result:='pending';
 ELSIF s.state->>'status'='paused' THEN result:='paused';
 ELSIF s.state->>'status' IN ('cancelled','failed','completed') THEN result:='stopped';
 ELSE
   IF t.type='image' THEN SELECT e.operation_id INTO operation_id FROM public.agent_task_events e WHERE e.root_task_id=root AND e.child_task_id=t.id;
   ELSE operation_id:=t.delivery_context->>'operation_id'; END IF;
   SELECT value INTO op FROM jsonb_array_elements(s.state->'operations') WHERE value->>'id'=operation_id;
   result:=CASE WHEN op IS NOT NULL AND EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'steps') step WHERE step->>'id'=op->>'step_id')
       THEN 'ready' ELSE 'superseded' END;
 END IF;
 PERFORM set_config('app.actor_user_id',COALESCE(actor_setting,''),true);
 PERFORM set_config('app.access_kind',COALESCE(kind_setting,''),true);
 RETURN result;
EXCEPTION WHEN OTHERS THEN
 PERFORM set_config('app.actor_user_id',COALESCE(actor_setting,''),true);
 PERFORM set_config('app.access_kind',COALESCE(kind_setting,''),true);
 RAISE;
END $$;
REVOKE ALL ON FUNCTION public.agent_child_revision_gate(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_child_revision_gate(uuid,uuid) TO everydayai,everydayai_worker,everydayai_owner;
RESET ROLE;
DO $$ DECLARE owner name; BEGIN
 SELECT pg_get_userbyid(proowner) INTO owner FROM pg_proc WHERE oid='public.mark_chat_image_dispatch(uuid,uuid,uuid)'::regprocedure;
 EXECUTE format('SET LOCAL ROLE %I',owner);
 ALTER FUNCTION public.mark_chat_image_dispatch(uuid,uuid,uuid) RENAME TO mark_chat_image_dispatch_before365;
 EXECUTE $function$
 CREATE FUNCTION public.mark_chat_image_dispatch(p_task_id uuid,p_claim_token uuid,p_org_id uuid DEFAULT NULL)
 RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $body$
 DECLARE gate text;
 BEGIN
   gate:=public.agent_child_revision_gate(p_task_id,p_org_id);
   IF gate IN ('pending','paused') THEN RETURN jsonb_build_object('outcome','not_owned','reason',gate); END IF;
   IF gate IN ('superseded','stopped') THEN RETURN jsonb_build_object('outcome','superseded'); END IF;
   RETURN public.mark_chat_image_dispatch_before365(p_task_id,p_claim_token,p_org_id);
 END $body$ $function$;
 GRANT EXECUTE ON FUNCTION public.mark_chat_image_dispatch(uuid,uuid,uuid) TO everydayai,everydayai_worker;
 REVOKE ALL ON FUNCTION public.mark_chat_image_dispatch(uuid,uuid,uuid) FROM PUBLIC;
END $$;
RESET ROLE;
DO $$ DECLARE owner name; BEGIN
 IF to_regprocedure('public.mark_agent_video_dispatch(uuid,uuid,uuid)') IS NOT NULL THEN
   SELECT pg_get_userbyid(proowner) INTO owner FROM pg_proc WHERE oid='public.mark_agent_video_dispatch(uuid,uuid,uuid)'::regprocedure;
   EXECUTE format('SET LOCAL ROLE %I',owner);
   ALTER FUNCTION public.mark_agent_video_dispatch(uuid,uuid,uuid) RENAME TO mark_agent_video_dispatch_before365;
   EXECUTE $function$
   CREATE FUNCTION public.mark_agent_video_dispatch(p_task_id uuid,p_claim_token uuid,p_org_id uuid DEFAULT NULL)
   RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $body$
   DECLARE gate text;
   BEGIN
     gate:=public.agent_child_revision_gate(p_task_id,p_org_id);
     IF gate IN ('pending','paused') THEN RETURN jsonb_build_object('outcome','not_active','reason','paused'); END IF;
     IF gate IN ('superseded','stopped') THEN RETURN jsonb_build_object('outcome','superseded'); END IF;
     RETURN public.mark_agent_video_dispatch_before365(p_task_id,p_claim_token,p_org_id);
   END $body$ $function$;
   GRANT EXECUTE ON FUNCTION public.mark_agent_video_dispatch(uuid,uuid,uuid) TO everydayai,everydayai_worker;
   REVOKE ALL ON FUNCTION public.mark_agent_video_dispatch(uuid,uuid,uuid) FROM PUBLIC;
 END IF;
END $$;
RESET ROLE;
DO $$ DECLARE owner name; BEGIN
 IF to_regprocedure('public.agent_video_submission_gate(uuid,uuid)') IS NOT NULL THEN
   SELECT pg_get_userbyid(proowner) INTO owner FROM pg_proc WHERE oid='public.agent_video_submission_gate(uuid,uuid)'::regprocedure;
   EXECUTE format('SET LOCAL ROLE %I',owner);
   ALTER FUNCTION public.agent_video_submission_gate(uuid,uuid) RENAME TO agent_video_submission_gate_before365;
   EXECUTE $function$
   CREATE FUNCTION public.agent_video_submission_gate(p_task_id uuid,p_org_id uuid DEFAULT NULL)
   RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $body$
   DECLARE gate text;
   BEGIN
     gate:=public.agent_child_revision_gate(p_task_id,p_org_id);
     IF gate IN ('pending','paused') THEN RETURN jsonb_build_object('outcome','paused'); END IF;
     IF gate IN ('superseded','stopped') THEN RETURN jsonb_build_object('outcome','stopped'); END IF;
     RETURN public.agent_video_submission_gate_before365(p_task_id,p_org_id);
   END $body$ $function$;
   GRANT EXECUTE ON FUNCTION public.agent_video_submission_gate(uuid,uuid) TO everydayai,everydayai_worker;
   REVOKE ALL ON FUNCTION public.agent_video_submission_gate(uuid,uuid) FROM PUBLIC;
 END IF;
END $$;
RESET ROLE;

-- A delete batch can already be armed when a new directive arrives. Recheck
-- every original item authorizer, retaining the original backup/settlement.
DO $$ DECLARE owner name; BEGIN
 IF to_regprocedure('public.authorize_agent_task_file_delete_item(text,uuid,integer)') IS NOT NULL THEN
   SELECT pg_get_userbyid(proowner) INTO owner FROM pg_proc WHERE oid='public.authorize_agent_task_file_delete_item(text,uuid,integer)'::regprocedure;
   EXECUTE format('SET LOCAL ROLE %I',owner);
   ALTER FUNCTION public.authorize_agent_task_file_delete_item(text,uuid,integer) RENAME TO authorize_agent_task_file_delete_item_before365;
   EXECUTE $function$
   CREATE FUNCTION public.authorize_agent_task_file_delete_item(p_operation_id text,p_dispatch_token uuid,p_item_index integer)
   RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $body$
   DECLARE r public.agent_task_native_writes%ROWTYPE; s public.agent_task_states%ROWTYPE; op jsonb; checked jsonb;
   BEGIN
     checked:=public.authorize_agent_task_file_delete_item_before365(p_operation_id,p_dispatch_token,p_item_index);
     SELECT * INTO r FROM public.agent_task_native_writes WHERE operation_id=p_operation_id;
     SELECT * INTO s FROM public.agent_task_states WHERE owner_task_id=r.owner_task_id;
     SELECT value INTO op FROM jsonb_array_elements(s.state->'operations') WHERE value->>'id'=p_operation_id;
     IF s.state->>'image_binding_version'='3' AND (jsonb_array_length(public.agent_task_unreviewed_changes(s.root_task_id))>0
         OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'steps') step WHERE step->>'id'=op->>'step_id')) THEN
       RAISE EXCEPTION 'TASK_USER_CHANGE_REVIEW_REQUIRED' USING ERRCODE='42501';
     END IF;
     RETURN checked;
   END $body$ $function$;
   GRANT EXECUTE ON FUNCTION public.authorize_agent_task_file_delete_item(text,uuid,integer) TO everydayai;
   REVOKE ALL ON FUNCTION public.authorize_agent_task_file_delete_item(text,uuid,integer) FROM PUBLIC;
 END IF;
END $$;
RESET ROLE;
DO $$ DECLARE owner name; BEGIN
 IF to_regprocedure('public.authorize_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,integer)') IS NOT NULL THEN
   SELECT pg_get_userbyid(proowner) INTO owner FROM pg_proc WHERE oid='public.authorize_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,integer)'::regprocedure;
   EXECUTE format('SET LOCAL ROLE %I',owner);
   ALTER FUNCTION public.authorize_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,integer) RENAME TO authorize_agent_task_file_delete_resume_before365;
   EXECUTE $function$
   CREATE FUNCTION public.authorize_agent_task_file_delete_resume(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,
       p_expected_revision bigint,p_operation_id text,p_call_id text,p_item_index integer)
   RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $body$
   DECLARE s public.agent_task_states%ROWTYPE; op jsonb; checked jsonb;
   BEGIN
     checked:=public.authorize_agent_task_file_delete_resume_before365(p_task_id,p_execution_token,p_logical_task_id,
         p_expected_revision,p_operation_id,p_call_id,p_item_index);
     SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id;
     SELECT value INTO op FROM jsonb_array_elements(s.state->'operations') WHERE value->>'id'=p_operation_id;
     IF s.state->>'image_binding_version'='3' AND (jsonb_array_length(public.agent_task_unreviewed_changes(s.root_task_id))>0
         OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'steps') step WHERE step->>'id'=op->>'step_id')) THEN
       RAISE EXCEPTION 'TASK_USER_CHANGE_REVIEW_REQUIRED' USING ERRCODE='42501'; END IF;
     RETURN checked;
   END $body$ $function$;
   REVOKE ALL ON FUNCTION public.authorize_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,integer) FROM PUBLIC;
   GRANT EXECUTE ON FUNCTION public.authorize_agent_task_file_delete_resume(uuid,uuid,uuid,bigint,text,text,integer) TO everydayai;
 END IF;
END $$;
RESET ROLE;
