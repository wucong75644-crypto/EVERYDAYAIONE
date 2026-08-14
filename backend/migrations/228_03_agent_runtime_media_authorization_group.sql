-- 228.03: One confirmation leader with exact per-Action authorization facts.
SET LOCAL ROLE everydayai_owner;
ALTER TABLE agent_interactions ADD COLUMN confirmation_group_hash TEXT CHECK (confirmation_group_hash IS NULL OR confirmation_group_hash ~ '^[0-9a-f]{64}$'), ADD COLUMN confirmation_group_leader_id UUID REFERENCES agent_interactions(id) DEFERRABLE INITIALLY DEFERRED, ADD CONSTRAINT agent_interactions_confirmation_group_pair CHECK ( (confirmation_group_hash IS NULL) = (confirmation_group_leader_id IS NULL) );
CREATE INDEX idx_agent_interactions_confirmation_group ON agent_interactions(confirmation_group_hash, id) WHERE confirmation_group_hash IS NOT NULL;
CREATE UNIQUE INDEX uq_agent_interactions_confirmation_group_leader ON agent_interactions(confirmation_group_hash) WHERE id = confirmation_group_leader_id;
CREATE FUNCTION _agent_media_authorization_group_hash_v1( p_model_step_id UUID, p_batch_hash TEXT, p_session_id UUID, p_run_id UUID, p_user_id UUID, p_org_id UUID, p_members JSONB
) RETURNS TEXT LANGUAGE sql IMMUTABLE SECURITY INVOKER
SET search_path=pg_catalog,public AS $$
 SELECT encode(digest(convert_to(jsonb_build_object( 'contract','agent-media-authorization-group-v1', 'model_step_id',p_model_step_id,'batch_hash',p_batch_hash, 'session_id',p_session_id,'run_id',p_run_id, 'user_id',p_user_id,'org_id',p_org_id,'members',p_members
 )::text,'UTF8'),'sha256'),'hex')
$$;
CREATE FUNCTION _expire_agent_media_authorization_group_v1(p_group_hash TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE leader agent_interactions%ROWTYPE; run_row agent_runs%ROWTYPE;
 member agent_interactions%ROWTYPE; open_count INTEGER;
BEGIN
 SELECT * INTO leader FROM agent_interactions WHERE confirmation_group_hash=p_group_hash AND id=confirmation_group_leader_id;
 IF leader.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 PERFORM 1 FROM agent_runtime_sessions WHERE id=leader.session_id FOR UPDATE;
 SELECT * INTO run_row FROM agent_runs WHERE id=leader.run_id FOR UPDATE;
 PERFORM 1 FROM agent_actions WHERE run_id=leader.run_id AND id IN (SELECT action_id FROM agent_interactions WHERE confirmation_group_hash=p_group_hash) ORDER BY id FOR UPDATE;
 PERFORM 1 FROM agent_action_attempts WHERE action_id IN ( SELECT action_id FROM agent_interactions WHERE confirmation_group_hash=p_group_hash) ORDER BY id FOR UPDATE;
 PERFORM 1 FROM agent_interactions WHERE confirmation_group_hash=p_group_hash ORDER BY id FOR UPDATE;
 SELECT count(*) INTO open_count FROM agent_interactions WHERE confirmation_group_hash=p_group_hash AND status='open';
 IF open_count=0 THEN RETURN jsonb_build_object('outcome','already_terminal');
 END IF;
 IF EXISTS(SELECT 1 FROM agent_interactions WHERE confirmation_group_hash=p_group_hash AND (status<>'open' OR expires_at>clock_timestamp())) THEN RETURN jsonb_build_object('outcome','not_expired');
 END IF;
 IF run_row.open_interaction_count<open_count THEN RAISE EXCEPTION 'AGENT_INTERACTION_COUNT_UNDERFLOW' USING ERRCODE='55000';
 END IF;
 UPDATE agent_interactions SET status='expired',resolved_at=clock_timestamp(), confirmation_notification_worker=NULL,confirmation_notification_token=NULL, confirmation_notification_lease_expires_at=NULL, recovery_worker_id=NULL,recovery_token=NULL,recovery_lease_expires_at=NULL, state_version=state_version+1,updated_at=clock_timestamp() WHERE confirmation_group_hash=p_group_hash AND status='open';
 UPDATE agent_runs SET open_interaction_count=open_interaction_count-open_count, state_version=state_version+1,updated_at=clock_timestamp() WHERE id=run_row.id;
 FOR member IN SELECT * FROM agent_interactions WHERE confirmation_group_hash=p_group_hash ORDER BY id
 LOOP PERFORM _close_agent_authorization_action( member.action_id,'authorization_expired'); PERFORM append_agent_runtime_event( member.session_id,'interaction.expired',member.run_id,NULL,member.id, 'system',session_user,jsonb_build_object( 'interaction_id',member.id,'action_id',member.action_id, 'confirmation_group_hash',p_group_hash), ARRAY['web_runtime','audit']::TEXT[]);
 END LOOP;
 RETURN jsonb_build_object('outcome','expired','expired_count',open_count);
END $$;
CREATE FUNCTION open_agent_authorization_batch_v1( p_model_step_id UUID,p_batch_hash TEXT,p_members JSONB, p_ttl_seconds INTEGER DEFAULT 900
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE first_action agent_actions%ROWTYPE; run_row agent_runs%ROWTYPE;
 canonical_members JSONB; identity_members JSONB; supplied_members JSONB;
 group_hash TEXT;
 leader_id UUID:=gen_random_uuid(); interaction_id UUID; action agent_actions%ROWTYPE;
 prompt JSONB; prompt_hash TEXT; expires_at TIMESTAMPTZ; existing_count INTEGER;
 member_count INTEGER;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF p_batch_hash !~ '^[0-9a-f]{64}$' OR jsonb_typeof(p_members) IS DISTINCT FROM 'array' OR NOT _agent_action_json_is_safe(p_members) OR jsonb_array_length(p_members) NOT BETWEEN 2 AND 10 OR p_ttl_seconds NOT BETWEEN 30 AND 86400 THEN RAISE EXCEPTION 'AGENT_AUTHORIZATION_INVALID_BATCH' USING ERRCODE='22023';
 END IF;
 SELECT * INTO first_action FROM agent_actions WHERE model_step_id=p_model_step_id AND batch_hash=p_batch_hash AND tool_name='generate_image' ORDER BY action_index,id LIMIT 1;
 IF first_action.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 PERFORM 1 FROM agent_runtime_sessions WHERE id=first_action.session_id FOR UPDATE;
 SELECT * INTO run_row FROM agent_runs WHERE id=first_action.run_id FOR UPDATE;
 PERFORM 1 FROM agent_actions WHERE model_step_id=p_model_step_id AND batch_hash=p_batch_hash AND tool_name='generate_image' ORDER BY id FOR UPDATE;
 PERFORM 1 FROM agent_interactions WHERE action_id IN ( SELECT id FROM agent_actions WHERE model_step_id=p_model_step_id AND batch_hash=p_batch_hash AND tool_name='generate_image') ORDER BY id FOR UPDATE;
 SELECT count(*),jsonb_agg(jsonb_build_object( 'action_id',id,'expected_action_version',state_version, 'action_index',action_index,'arguments_hash',arguments_hash) ORDER BY action_index,id)
 INTO member_count,canonical_members FROM agent_actions
 WHERE model_step_id=p_model_step_id AND batch_hash=p_batch_hash AND tool_name='generate_image';
 SELECT jsonb_agg(jsonb_build_object( 'action_id',item->>'action_id', 'expected_action_version',(item->>'expected_action_version')::BIGINT, 'action_index',(item->>'action_index')::INTEGER, 'arguments_hash',item->>'arguments_hash') ORDER BY (item->>'action_index')::INTEGER,(item->>'action_id')::UUID)
 INTO supplied_members FROM jsonb_array_elements(p_members) item;
 IF member_count<>jsonb_array_length(p_members) OR canonical_members IS DISTINCT FROM supplied_members OR EXISTS(SELECT 1 FROM agent_actions WHERE model_step_id=p_model_step_id AND batch_hash=p_batch_hash AND tool_name='generate_image' AND ( session_id IS DISTINCT FROM first_action.session_id OR run_id IS DISTINCT FROM first_action.run_id OR user_id IS DISTINCT FROM first_action.user_id OR org_id IS DISTINCT FROM first_action.org_id OR status<>'awaiting_authorization')) OR (SELECT min(action_index) FROM agent_actions WHERE model_step_id=p_model_step_id AND batch_hash=p_batch_hash AND tool_name='generate_image')<>0 OR (SELECT max(action_index) FROM agent_actions WHERE model_step_id=p_model_step_id AND batch_hash=p_batch_hash AND tool_name='generate_image')<>member_count-1 THEN RETURN jsonb_build_object('outcome','batch_binding_mismatch');
 END IF;
 SELECT jsonb_agg(item-'expected_action_version' ORDER BY (item->>'action_index')::INTEGER,(item->>'action_id')::UUID)
 INTO identity_members FROM jsonb_array_elements(canonical_members) item;
 group_hash:=_agent_media_authorization_group_hash_v1( p_model_step_id,p_batch_hash,first_action.session_id,first_action.run_id, first_action.user_id,first_action.org_id,identity_members);
 SELECT count(*) INTO existing_count FROM agent_interactions WHERE action_id IN (SELECT id FROM agent_actions WHERE model_step_id=p_model_step_id AND batch_hash=p_batch_hash AND tool_name='generate_image');
 IF existing_count>0 THEN IF existing_count=member_count AND NOT EXISTS(SELECT 1 FROM agent_interactions WHERE action_id IN (SELECT id FROM agent_actions WHERE model_step_id=p_model_step_id AND batch_hash=p_batch_hash AND tool_name='generate_image') AND (confirmation_group_hash IS DISTINCT FROM group_hash OR status<>'open')) THEN RETURN jsonb_build_object('outcome','already_open', 'confirmation_group_hash',group_hash,'member_count',member_count); END IF; RETURN jsonb_build_object('outcome','interaction_conflict');
 END IF;
 expires_at:=clock_timestamp()+make_interval(secs=>p_ttl_seconds);
 FOR action IN SELECT * FROM agent_actions WHERE model_step_id=p_model_step_id AND batch_hash=p_batch_hash AND tool_name='generate_image' ORDER BY action_index,id
 LOOP interaction_id:=CASE WHEN action.action_index=0 THEN leader_id ELSE gen_random_uuid() END; prompt:=jsonb_build_object( 'protocol_version',4,'action_id',action.id, 'tool_call_id',action.stable_tool_call_id,'tool_name',action.tool_name, 'arguments',action.arguments,'arguments_hash',action.arguments_hash, 'confirmation_group_hash',group_hash,'confirmation_group_size',member_count); prompt_hash:=encode(digest(convert_to(prompt::text,'UTF8'),'sha256'),'hex'); INSERT INTO agent_interactions( id,action_id,session_id,run_id,org_id,user_id,prompt,prompt_hash, expires_at,confirmation_group_hash,confirmation_group_leader_id ) VALUES(interaction_id,action.id,action.session_id,action.run_id, action.org_id,action.user_id,prompt,prompt_hash,expires_at, group_hash,leader_id); PERFORM append_agent_runtime_event( action.session_id,'interaction.opened',action.run_id,action.model_step_id, interaction_id,'system',session_user,jsonb_build_object( 'interaction_id',interaction_id,'action_id',action.id, 'confirmation_group_hash',group_hash,'confirmation_group_leader_id',leader_id), ARRAY['web_runtime','audit']::TEXT[]);
 END LOOP;
 UPDATE agent_runs SET open_interaction_count=open_interaction_count+member_count, status='waiting_interaction',execution_token=NULL,lease_expires_at=NULL, state_version=state_version+1,updated_at=clock_timestamp() WHERE id=run_row.id;
 RETURN jsonb_build_object('outcome','opened', 'confirmation_group_hash',group_hash, 'confirmation_group_leader_id',leader_id,'member_count',member_count, 'authorization_expires_at',expires_at);
END $$;
CREATE FUNCTION claim_agent_tool_batch_confirmation_v1( p_worker_id TEXT,p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE interaction agent_interactions%ROWTYPE; action agent_actions%ROWTYPE;
 command agent_session_commands%ROWTYPE; token UUID; expired RECORD;
 group_size INTEGER;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 IF session_user<>'everydayai_projection_worker' OR current_setting('app.access_kind',true)<>'projection' OR nullif(btrim(p_worker_id),'') IS NULL OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN RAISE EXCEPTION 'TOOL_CONFIRMATION_NOTIFICATION_SCOPE_REQUIRED' USING ERRCODE='42501';
 END IF;
 FOR expired IN SELECT confirmation_group_hash FROM agent_interactions WHERE confirmation_group_hash IS NOT NULL AND id=confirmation_group_leader_id AND status='open' AND expires_at<=clock_timestamp() ORDER BY expires_at,id LIMIT 100
 LOOP PERFORM _expire_agent_media_authorization_group_v1( expired.confirmation_group_hash);
 END LOOP;
 SELECT candidate.* INTO interaction FROM agent_interactions candidate
 JOIN agent_actions candidate_action ON candidate_action.id=candidate.action_id
 JOIN agent_runtime_org_rollout rollout ON rollout.org_id=candidate.org_id
 CROSS JOIN agent_runtime_control control
 WHERE candidate.status='open' AND candidate.expires_at>clock_timestamp() AND (candidate.confirmation_group_hash IS NULL OR candidate.id=candidate.confirmation_group_leader_id) AND candidate.confirmation_notified_at IS NULL AND (candidate.confirmation_notification_not_before IS NULL OR candidate.confirmation_notification_not_before<=clock_timestamp()) AND (candidate.confirmation_notification_token IS NULL OR candidate.confirmation_notification_lease_expires_at<=clock_timestamp()) AND candidate_action.status='awaiting_authorization' AND rollout.enabled AND control.tool_confirmation_enabled
 ORDER BY candidate.created_at,candidate.id
 LIMIT 1 FOR UPDATE OF candidate SKIP LOCKED;
 IF interaction.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 SELECT * INTO action FROM agent_actions WHERE id=interaction.action_id;
 SELECT session_command.* INTO command FROM agent_session_commands session_command WHERE session_command.id=(SELECT run.command_id FROM agent_runs run WHERE run.id=interaction.run_id);
 IF nullif(command.payload->>'task_id','') IS NULL THEN UPDATE agent_interactions SET confirmation_notification_not_before= clock_timestamp()+interval '300 seconds' WHERE id=interaction.id; RETURN jsonb_build_object('outcome','invalid_task_binding');
 END IF;
 token:=gen_random_uuid();
 UPDATE agent_interactions SET confirmation_notification_worker=btrim(p_worker_id), confirmation_notification_token=token, confirmation_notification_lease_expires_at= clock_timestamp()+make_interval(secs=>p_lease_seconds) WHERE id=interaction.id;
 SELECT count(*) INTO group_size FROM agent_interactions WHERE confirmation_group_hash=interaction.confirmation_group_hash;
 RETURN jsonb_build_object('outcome','claimed','notification_token',token, 'interaction_id',interaction.id,'interaction_version',interaction.state_version, 'authorization_expires_at',interaction.expires_at,'action_id',action.id, 'task_id',command.payload->>'task_id','conversation_id', (SELECT session.conversation_id FROM agent_runtime_sessions session WHERE session.id=interaction.session_id), 'tool_call_id',action.stable_tool_call_id,'tool_name',action.tool_name, 'arguments',action.arguments,'arguments_hash',action.arguments_hash, 'user_id',interaction.user_id,'org_id',interaction.org_id, 'confirmation_group_hash',coalesce(interaction.confirmation_group_hash,''), 'confirmation_group_size',CASE WHEN interaction.confirmation_group_hash IS NULL THEN 1 ELSE group_size END);
END $$;
ALTER FUNCTION claim_agent_tool_confirmation_notification(TEXT,INTEGER) RENAME TO _claim_agent_tool_confirmation_notification_223;
CREATE FUNCTION claim_agent_tool_confirmation_notification( p_worker_id TEXT,p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB LANGUAGE sql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
 SELECT claim_agent_tool_batch_confirmation_v1(p_worker_id,p_lease_seconds)
$$;
ALTER FUNCTION resolve_agent_authorization_interaction( UUID,BIGINT,TEXT,TEXT,JSONB,TEXT,TEXT,INTEGER) RENAME TO _resolve_agent_authorization_interaction_220_25;
CREATE FUNCTION resolve_agent_authorization_interaction( p_interaction_id UUID,p_expected_version BIGINT,p_response TEXT, p_response_hash TEXT,p_effective_scope JSONB, p_grant_kind TEXT DEFAULT 'action',p_workflow_key TEXT DEFAULT NULL, p_ttl_seconds INTEGER DEFAULT 900
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _assert_agent_runtime_actor(FALSE);
 IF EXISTS(SELECT 1 FROM agent_interactions WHERE id=p_interaction_id AND confirmation_group_hash IS NOT NULL) THEN IF NOT EXISTS(SELECT 1 FROM agent_interactions interaction JOIN agent_actions action ON action.id=interaction.action_id JOIN agent_runtime_sessions session ON session.id=action.session_id WHERE interaction.id=p_interaction_id AND tenant_org_id() IS NOT DISTINCT FROM action.org_id AND ((session.scope_kind='user' AND session.user_id=tenant_actor_user_id()) OR (session.scope_kind='channel' AND EXISTS(SELECT 1 FROM org_members member WHERE member.org_id=session.org_id AND member.user_id=tenant_actor_user_id() AND member.status='active')))) THEN RAISE EXCEPTION 'AGENT_AUTHORIZATION_SCOPE_MISMATCH' USING ERRCODE='42501'; END IF; RETURN jsonb_build_object('outcome','group_confirmation_required');
 END IF;
 RETURN _resolve_agent_authorization_interaction_220_25( p_interaction_id,p_expected_version,p_response,p_response_hash, p_effective_scope,p_grant_kind,p_workflow_key,p_ttl_seconds);
END $$;
CREATE FUNCTION resolve_agent_tool_batch_confirmation_v1(
 p_confirmation_id TEXT,p_interaction_id UUID,p_action_id UUID,
 p_expected_interaction_version BIGINT,p_user_id UUID,p_org_id UUID,
 p_arguments_hash TEXT,p_confirmation_group_hash TEXT,
 p_expires_at TIMESTAMPTZ,p_approved BOOLEAN
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE leader agent_interactions%ROWTYPE; leader_action agent_actions%ROWTYPE;
 run_row agent_runs%ROWTYPE; existing agent_tool_confirmation_results%ROWTYPE;
 interaction agent_interactions%ROWTYPE; action agent_actions%ROWTYPE;
 members JSONB; member_count INTEGER; computed_hash TEXT; binding_hash TEXT;
 v_response_hash TEXT; decision TEXT:=CASE WHEN p_approved THEN 'approve' ELSE 'deny' END;
 grant_ttl INTEGER; grant_row agent_authorization_grants%ROWTYPE;
BEGIN
 PERFORM _assert_agent_runtime_actor(FALSE);
 IF length(coalesce(p_confirmation_id,'')) NOT BETWEEN 32 AND 200 OR p_arguments_hash !~ '^[0-9a-f]{64}$' OR p_confirmation_group_hash !~ '^[0-9a-f]{64}$' THEN RETURN jsonb_build_object('outcome','confirmation_expired_or_invalid');
 END IF;
 SELECT * INTO leader FROM agent_interactions WHERE id=p_interaction_id;
 SELECT * INTO leader_action FROM agent_actions WHERE id=p_action_id;
 IF leader.id IS NULL OR leader_action.id IS NULL OR leader.action_id IS DISTINCT FROM leader_action.id OR leader.confirmation_group_hash IS DISTINCT FROM p_confirmation_group_hash OR leader.confirmation_group_leader_id IS DISTINCT FROM leader.id OR leader.user_id IS DISTINCT FROM p_user_id OR leader.org_id IS DISTINCT FROM p_org_id OR leader_action.user_id IS DISTINCT FROM p_user_id OR leader_action.org_id IS DISTINCT FROM p_org_id OR leader_action.arguments_hash IS DISTINCT FROM p_arguments_hash OR leader.expires_at IS DISTINCT FROM p_expires_at OR tenant_actor_user_id() IS DISTINCT FROM p_user_id OR tenant_org_id() IS DISTINCT FROM p_org_id THEN RETURN jsonb_build_object('outcome','binding_mismatch');
 END IF;
 PERFORM 1 FROM agent_runtime_sessions WHERE id=leader.session_id FOR UPDATE;
 SELECT * INTO run_row FROM agent_runs WHERE id=leader.run_id FOR UPDATE;
 PERFORM 1 FROM agent_actions WHERE id IN (SELECT action_id FROM agent_interactions WHERE confirmation_group_hash=p_confirmation_group_hash) ORDER BY id FOR UPDATE;
 PERFORM 1 FROM agent_action_attempts WHERE action_id IN (SELECT action_id FROM agent_interactions WHERE confirmation_group_hash=p_confirmation_group_hash) ORDER BY id FOR UPDATE;
 PERFORM 1 FROM agent_interactions WHERE confirmation_group_hash=p_confirmation_group_hash ORDER BY id FOR UPDATE;
 SELECT count(*),jsonb_agg(jsonb_build_object( 'action_id',member_action.id, 'action_index',member_action.action_index, 'arguments_hash',member_action.arguments_hash) ORDER BY member_action.action_index,member_action.id)
 INTO member_count,members FROM agent_interactions member
 JOIN agent_actions member_action ON member_action.id=member.action_id
 WHERE member.confirmation_group_hash=p_confirmation_group_hash;
 computed_hash:=_agent_media_authorization_group_hash_v1( leader_action.model_step_id,leader_action.batch_hash,leader.session_id, leader.run_id,leader.user_id,leader.org_id,members);
 IF member_count NOT BETWEEN 2 AND 10 OR computed_hash IS DISTINCT FROM p_confirmation_group_hash OR EXISTS(SELECT 1 FROM agent_interactions member JOIN agent_actions member_action ON member_action.id=member.action_id WHERE member.confirmation_group_hash=p_confirmation_group_hash AND ( member.confirmation_group_leader_id IS DISTINCT FROM leader.id OR member.session_id IS DISTINCT FROM leader.session_id OR member.run_id IS DISTINCT FROM leader.run_id OR member.user_id IS DISTINCT FROM leader.user_id OR member.org_id IS DISTINCT FROM leader.org_id OR member.expires_at IS DISTINCT FROM leader.expires_at OR member_action.model_step_id IS DISTINCT FROM leader_action.model_step_id OR member_action.batch_hash IS DISTINCT FROM leader_action.batch_hash OR member_action.tool_name<>'generate_image' OR member.prompt->>'arguments_hash' IS DISTINCT FROM member_action.arguments_hash)) THEN RETURN jsonb_build_object('outcome','group_binding_mismatch');
 END IF;
 binding_hash:=encode(digest(concat_ws(':',p_confirmation_id, p_confirmation_group_hash,p_interaction_id,p_action_id,p_user_id, coalesce(p_org_id::text,''),p_arguments_hash,members::text, extract(epoch from p_expires_at),decision),'sha256'),'hex');
 SELECT * INTO existing FROM agent_tool_confirmation_results WHERE confirmation_id=p_confirmation_id;
 IF existing.confirmation_id IS NOT NULL THEN RETURN jsonb_build_object('outcome',CASE WHEN existing.binding_hash=binding_hash THEN 'already_resolved' ELSE 'confirmation_conflict' END);
 END IF;
 IF leader.status<>'open' OR leader.state_version<>p_expected_interaction_version OR EXISTS(SELECT 1 FROM agent_interactions member JOIN agent_actions member_action ON member_action.id=member.action_id WHERE member.confirmation_group_hash=p_confirmation_group_hash AND member_action.status<>'awaiting_authorization') OR EXISTS(SELECT 1 FROM agent_interactions WHERE confirmation_group_hash=p_confirmation_group_hash AND (status<>'open' OR expires_at<=clock_timestamp())) THEN IF EXISTS(SELECT 1 FROM agent_interactions WHERE confirmation_group_hash=p_confirmation_group_hash AND status='open' AND expires_at<=clock_timestamp()) THEN PERFORM _expire_agent_media_authorization_group_v1( p_confirmation_group_hash); END IF; RETURN jsonb_build_object('outcome','stale_or_expired');
 END IF;
 IF run_row.open_interaction_count<member_count THEN RAISE EXCEPTION 'AGENT_INTERACTION_COUNT_UNDERFLOW' USING ERRCODE='55000';
 END IF;
 v_response_hash:=encode(digest(binding_hash,'sha256'),'hex');
 UPDATE agent_interactions SET status='resolved',response=decision, response_hash=v_response_hash,resolved_at=clock_timestamp(), confirmation_notification_worker=NULL,confirmation_notification_token=NULL, confirmation_notification_lease_expires_at=NULL, state_version=state_version+1,updated_at=clock_timestamp() WHERE confirmation_group_hash=p_confirmation_group_hash;
 UPDATE agent_runs SET open_interaction_count=open_interaction_count-member_count, state_version=state_version+1,updated_at=clock_timestamp() WHERE id=run_row.id;
 grant_ttl:=greatest(30,least(86400, extract(epoch from p_expires_at-clock_timestamp())::INTEGER));
 FOR interaction IN SELECT * FROM agent_interactions WHERE confirmation_group_hash=p_confirmation_group_hash ORDER BY id
 LOOP SELECT * INTO action FROM agent_actions WHERE id=interaction.action_id; IF p_approved THEN INSERT INTO agent_authorization_grants( session_id,run_id,action_id,interaction_id,org_id,user_id,grant_kind, arguments_hash,effective_scope,expires_at) VALUES(action.session_id,action.run_id,action.id,interaction.id, action.org_id,action.user_id,'action',action.arguments_hash,'{}'::JSONB, clock_timestamp()+make_interval(secs=>grant_ttl)) RETURNING * INTO grant_row; ELSE PERFORM _close_agent_authorization_action( action.id,'authorization_denied'); END IF; PERFORM append_agent_runtime_event( action.session_id,'interaction.resolved',action.run_id,action.model_step_id, interaction.id,'user',session_user,jsonb_build_object( 'interaction_id',interaction.id,'action_id',action.id, 'response',decision,'confirmation_group_hash',p_confirmation_group_hash), ARRAY['web_runtime','audit']::TEXT[]);
 END LOOP;
 IF p_approved THEN PERFORM _recompute_agent_run_wait_state(run_row.id); END IF;
 INSERT INTO agent_tool_confirmation_results( confirmation_id,interaction_id,action_id,user_id,org_id,arguments_hash, decision,binding_hash,expires_at)
 VALUES(p_confirmation_id,p_interaction_id,p_action_id,p_user_id,p_org_id, p_arguments_hash,decision,binding_hash,p_expires_at);
 RETURN jsonb_build_object('outcome','resolved','member_count',member_count);
END $$;
ALTER FUNCTION resolve_agent_tool_confirmation_v3(
 TEXT,UUID,UUID,BIGINT,UUID,UUID,TEXT,TIMESTAMPTZ,BOOLEAN)
 RENAME TO _resolve_agent_tool_confirmation_v3_223;
CREATE FUNCTION resolve_agent_tool_confirmation_v3(
 p_confirmation_id TEXT,p_interaction_id UUID,p_action_id UUID,
 p_expected_interaction_version BIGINT,p_user_id UUID,p_org_id UUID,
 p_arguments_hash TEXT,p_expires_at TIMESTAMPTZ,p_approved BOOLEAN
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
BEGIN
 PERFORM _assert_agent_runtime_actor(FALSE);
 IF EXISTS(SELECT 1 FROM agent_interactions WHERE id=p_interaction_id AND confirmation_group_hash IS NOT NULL) THEN IF EXISTS(SELECT 1 FROM agent_interactions interaction JOIN agent_actions action ON action.id=interaction.action_id WHERE interaction.id=p_interaction_id AND interaction.action_id=p_action_id AND interaction.user_id IS NOT DISTINCT FROM p_user_id AND interaction.org_id IS NOT DISTINCT FROM p_org_id AND action.user_id IS NOT DISTINCT FROM p_user_id AND action.org_id IS NOT DISTINCT FROM p_org_id AND action.arguments_hash IS NOT DISTINCT FROM p_arguments_hash AND interaction.expires_at IS NOT DISTINCT FROM p_expires_at AND tenant_actor_user_id() IS NOT DISTINCT FROM p_user_id AND tenant_org_id() IS NOT DISTINCT FROM p_org_id) THEN RETURN jsonb_build_object('outcome','group_confirmation_required'); END IF; RETURN jsonb_build_object('outcome','binding_mismatch');
 END IF;
 RETURN _resolve_agent_tool_confirmation_v3_223( p_confirmation_id,p_interaction_id,p_action_id, p_expected_interaction_version,p_user_id,p_org_id,p_arguments_hash, p_expires_at,p_approved);
END $$;
ALTER FUNCTION complete_model_attempt_with_raw_actions(
 UUID,UUID,BIGINT,BIGINT,TEXT,JSONB,TEXT,TEXT,JSONB,INTEGER,JSONB)
 RENAME TO _complete_model_attempt_with_raw_actions_228_01;
CREATE FUNCTION complete_model_attempt_with_raw_actions(
 p_attempt_id UUID,p_run_execution_token UUID,
 p_expected_attempt_version BIGINT,p_expected_step_version BIGINT,
 p_request_hash TEXT,p_response_receipt JSONB,p_response_hash TEXT,
 p_provider_stop_reason TEXT,p_usage JSONB,p_actual_credits INTEGER,
 p_actions JSONB
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE step agent_model_steps%ROWTYPE; canonical JSONB; v_batch_hash TEXT;
 canonical_actions JSONB; result JSONB; action agent_actions%ROWTYPE;
 prompt JSONB; opened JSONB; image_count INTEGER; members JSONB;
BEGIN
 PERFORM _assert_agent_runtime_actor(TRUE);
 SELECT ms.* INTO step FROM agent_model_steps ms JOIN agent_model_attempts ma ON ma.model_step_id=ms.id WHERE ma.id=p_attempt_id;
 IF step.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
 canonical:=_canonical_agent_action_batch(step,p_actions);
 v_batch_hash:=_agent_action_batch_hash(canonical);
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_actions) supplied JOIN jsonb_array_elements(canonical) computed ON computed->>'action_id'=supplied->>'action_id' WHERE (supplied?'arguments_hash' AND supplied->>'arguments_hash' IS DISTINCT FROM computed->>'arguments_hash') OR (supplied?'request_hash' AND supplied->>'request_hash' IS DISTINCT FROM computed->>'request_hash')) THEN RETURN jsonb_build_object('outcome','request_hash_conflict');
 END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_actions) supplied WHERE supplied?'batch_hash' AND supplied->>'batch_hash' IS DISTINCT FROM v_batch_hash)
 THEN RETURN jsonb_build_object('outcome','batch_hash_conflict'); END IF;
 SELECT jsonb_agg(supplied||jsonb_build_object( 'arguments_hash',computed.item->>'arguments_hash', 'request_hash',computed.item->>'request_hash','batch_hash',v_batch_hash) ORDER BY (supplied->>'index')::INTEGER, btrim(supplied->>'stable_tool_call_id'),(supplied->>'action_id')::UUID)
 INTO canonical_actions FROM jsonb_array_elements(p_actions) supplied
 CROSS JOIN LATERAL(SELECT item FROM jsonb_array_elements(canonical) item WHERE item->>'action_id'=supplied->>'action_id' ORDER BY (item->>'index')::INTEGER,btrim(item->>'stable_tool_call_id'), (item->>'action_id')::UUID LIMIT 1) computed;
 result:=complete_model_attempt_step_and_create_actions( p_attempt_id,p_run_execution_token,p_expected_attempt_version, p_expected_step_version,p_request_hash,p_response_receipt,p_response_hash, p_provider_stop_reason,p_usage,p_actual_credits,v_batch_hash,canonical_actions);
 IF result->>'outcome'='already_completed' THEN RETURN result; END IF;
 IF result->>'outcome'<>'completed' THEN RETURN result; END IF;
 SELECT count(*),jsonb_agg(jsonb_build_object( 'action_id',id,'expected_action_version',state_version, 'action_index',action_index,'arguments_hash',arguments_hash) ORDER BY action_index,id) INTO image_count,members FROM agent_actions WHERE model_step_id=step.id AND batch_hash=v_batch_hash AND tool_name='generate_image' AND status='awaiting_authorization';
 IF image_count>1 THEN opened:=open_agent_authorization_batch_v1(step.id,v_batch_hash,members,900); IF opened->>'outcome' NOT IN ('opened','already_open') THEN RAISE EXCEPTION 'AGENT_AUTHORIZATION_BATCH_OPEN_FAILED: %', opened->>'outcome' USING ERRCODE='55000'; END IF;
 END IF;
 FOR action IN SELECT * FROM agent_actions WHERE model_step_id=step.id AND status='awaiting_authorization' AND (tool_name<>'generate_image' OR image_count=1) ORDER BY action_index,id
 LOOP prompt:=jsonb_build_object( 'protocol_version',3,'action_id',action.id, 'tool_call_id',action.stable_tool_call_id,'tool_name',action.tool_name, 'arguments',action.arguments,'arguments_hash',action.arguments_hash); opened:=open_agent_authorization_interaction( action.id,action.state_version,prompt, encode(digest(convert_to(prompt::text,'UTF8'),'sha256'),'hex'),900); IF opened->>'outcome' NOT IN ('opened','already_open') THEN RAISE EXCEPTION 'AGENT_AUTHORIZATION_INTERACTION_OPEN_FAILED: %', opened->>'outcome' USING ERRCODE='55000'; END IF;
 END LOOP;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION
 _agent_media_authorization_group_hash_v1(UUID,TEXT,UUID,UUID,UUID,UUID,JSONB),
 _expire_agent_media_authorization_group_v1(TEXT),
 _claim_agent_tool_confirmation_notification_223(TEXT,INTEGER),
 _resolve_agent_authorization_interaction_220_25( UUID,BIGINT,TEXT,TEXT,JSONB,TEXT,TEXT,INTEGER),
 _resolve_agent_tool_confirmation_v3_223( TEXT,UUID,UUID,BIGINT,UUID,UUID,TEXT,TIMESTAMPTZ,BOOLEAN),
 _complete_model_attempt_with_raw_actions_228_01( UUID,UUID,BIGINT,BIGINT,TEXT,JSONB,TEXT,TEXT,JSONB,INTEGER,JSONB),
 open_agent_authorization_batch_v1(UUID,TEXT,JSONB,INTEGER),
 claim_agent_tool_batch_confirmation_v1(TEXT,INTEGER),
 claim_agent_tool_confirmation_notification(TEXT,INTEGER),
 resolve_agent_authorization_interaction( UUID,BIGINT,TEXT,TEXT,JSONB,TEXT,TEXT,INTEGER),
 resolve_agent_tool_batch_confirmation_v1( TEXT,UUID,UUID,BIGINT,UUID,UUID,TEXT,TEXT,TIMESTAMPTZ,BOOLEAN),
 resolve_agent_tool_confirmation_v3( TEXT,UUID,UUID,BIGINT,UUID,UUID,TEXT,TIMESTAMPTZ,BOOLEAN),
 complete_model_attempt_with_raw_actions( UUID,UUID,BIGINT,BIGINT,TEXT,JSONB,TEXT,TEXT,JSONB,INTEGER,JSONB)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
 everydayai_sync,everydayai,everydayai_projection_worker,
 everydayai_authorization_worker,everydayai_sandbox_worker,
 everydayai_runtime_admin,everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION
 open_agent_authorization_batch_v1(UUID,TEXT,JSONB,INTEGER),
 complete_model_attempt_with_raw_actions( UUID,UUID,BIGINT,BIGINT,TEXT,JSONB,TEXT,TEXT,JSONB,INTEGER,JSONB)
TO everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION
 claim_agent_tool_batch_confirmation_v1(TEXT,INTEGER),
 claim_agent_tool_confirmation_notification(TEXT,INTEGER)
TO everydayai_projection_worker;
GRANT EXECUTE ON FUNCTION
 resolve_agent_authorization_interaction( UUID,BIGINT,TEXT,TEXT,JSONB,TEXT,TEXT,INTEGER),
 resolve_agent_tool_batch_confirmation_v1( TEXT,UUID,UUID,BIGINT,UUID,UUID,TEXT,TEXT,TIMESTAMPTZ,BOOLEAN),
 resolve_agent_tool_confirmation_v3( TEXT,UUID,UUID,BIGINT,UUID,UUID,TEXT,TIMESTAMPTZ,BOOLEAN)
TO everydayai_runtime,everydayai_wecom_runtime;
RESET ROLE;
