-- 228.07: Runtime image message cancellation and single-slot retry controls.
SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE
        attrelid='public.agent_runtime_media_action_bindings'::regclass
        AND attname='slot_id' AND NOT attisdropped) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_228_06_REQUIRED' USING ERRCODE='55000';
    END IF;
END $$;
CREATE TABLE agent_runtime_media_cancel_requests(
 action_id UUID PRIMARY KEY REFERENCES agent_actions(id) ON DELETE RESTRICT,
 output_message_id UUID NOT NULL REFERENCES messages(id) ON DELETE RESTRICT,
 run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
 org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
 user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 disposition TEXT NOT NULL CHECK(disposition IN('cancel_now','cancel_reconcile')),
 action_status_at_request TEXT NOT NULL,idempotency_key TEXT NOT NULL CHECK(
 length(btrim(idempotency_key)) BETWEEN 1 AND 200),requested_at TIMESTAMPTZ NOT NULL
 DEFAULT clock_timestamp());
CREATE INDEX idx_agent_runtime_media_cancel_message ON
 agent_runtime_media_cancel_requests(output_message_id,requested_at,action_id);
CREATE TABLE agent_runtime_media_retry_lineage(
 retry_action_id UUID PRIMARY KEY REFERENCES agent_actions(id) ON DELETE RESTRICT,
 source_action_id UUID NOT NULL REFERENCES agent_actions(id) ON DELETE RESTRICT,
 root_action_id UUID NOT NULL REFERENCES agent_actions(id) ON DELETE RESTRICT,
 output_message_id UUID NOT NULL REFERENCES messages(id) ON DELETE RESTRICT,
 org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,user_id UUID NOT NULL
 REFERENCES users(id) ON DELETE RESTRICT,conversation_id UUID NOT NULL REFERENCES
 conversations(id) ON DELETE RESTRICT,slot_id UUID NOT NULL,slot_index INTEGER NOT NULL
 CHECK(slot_index BETWEEN 0 AND 9),retry_ordinal INTEGER NOT NULL CHECK(retry_ordinal>0),
 base_slot_revision BIGINT NOT NULL CHECK(base_slot_revision>=0),idempotency_key TEXT NOT NULL
 CHECK(length(btrim(idempotency_key)) BETWEEN 1 AND 200),created_at TIMESTAMPTZ NOT NULL
 DEFAULT clock_timestamp(),UNIQUE(output_message_id,slot_index,retry_ordinal),
 UNIQUE(output_message_id,idempotency_key));
CREATE INDEX idx_agent_runtime_media_retry_slot ON agent_runtime_media_retry_lineage(
 output_message_id,slot_index,retry_ordinal DESC);
ALTER TABLE agent_runtime_media_cancel_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_media_retry_lineage ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_media_cancel_owner_all ON agent_runtime_media_cancel_requests FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY agent_runtime_media_retry_owner_all ON agent_runtime_media_retry_lineage FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
ALTER TABLE agent_runtime_media_cancel_requests FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_media_retry_lineage FORCE ROW LEVEL SECURITY;
CREATE FUNCTION _agent_runtime_media_web_control_v1(p_org_id UUID,p_user_id UUID) RETURNS VOID LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    IF NULLIF(current_setting('app.request_id',TRUE),'') IS NULL OR NOT(
      (session_user='everydayai_runtime' AND current_setting('app.access_kind',TRUE)='runtime')
      OR(session_user='everydayai_wecom_runtime' AND
      current_setting('app.access_kind',TRUE)='runtime')) OR
      tenant_org_id() IS DISTINCT FROM p_org_id OR
      tenant_actor_user_id() IS DISTINCT FROM p_user_id THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_WEB_SCOPE_REQUIRED'
            USING ERRCODE = '42501';
    END IF;
END $$;
CREATE FUNCTION _agent_runtime_media_retry_run_guard_v1() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE command agent_session_commands%ROWTYPE; action agent_actions%ROWTYPE;
    action_result agent_action_results%ROWTYPE;
BEGIN
    IF NEW.status IN ('completed','failed','cancelled') OR NEW.blocking_action_count<>0
       OR NEW.capability_snapshot->>'source' IS DISTINCT FROM 'runtime_media_slot_retry'
       OR NEW.capability_snapshot->>'execution_mode' IS DISTINCT FROM 'action_only'
       OR NEW.capability_snapshot->>'projection_mode' IS DISTINCT FROM 'media_slot_retry'
       OR NEW.capability_snapshot->>'model_loop_enabled' IS DISTINCT FROM 'false' THEN
        RETURN NEW; END IF;
    SELECT * INTO command FROM agent_session_commands WHERE id=NEW.command_id;
    IF command.payload->>'source' IS DISTINCT FROM 'runtime_media_slot_retry' OR
       command.payload->>'execution_mode' IS DISTINCT FROM 'action_only' THEN
       RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_RETRY_RUN_CONTRACT_INVALID'
       USING ERRCODE='55000'; END IF;
    SELECT candidate.* INTO action FROM agent_actions candidate
     WHERE candidate.run_id=NEW.id ORDER BY candidate.action_index, candidate.id LIMIT 1;
    IF action.id IS NULL OR command.payload->>'task_id' IS DISTINCT FROM action.id::TEXT
       THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_RETRY_TASK_ANCHOR_INVALID'
       USING ERRCODE='55000'; END IF;
    IF action.status NOT IN ('completed','failed','rejected','cancelled') THEN RETURN NEW; END IF;
    IF EXISTS(SELECT 1 FROM agent_actions sibling WHERE sibling.run_id=NEW.id AND
       sibling.id<>action.id) THEN RAISE EXCEPTION
       'AGENT_RUNTIME_MEDIA_RETRY_RUN_NOT_SINGLE_ACTION' USING ERRCODE='55000'; END IF;
    SELECT * INTO action_result FROM agent_action_results WHERE action_id=action.id;
    NEW.status := CASE action.status WHEN 'completed' THEN 'completed'
        WHEN 'cancelled' THEN 'cancelled' ELSE 'failed' END;
    NEW.execution_token := NULL; NEW.lease_expires_at := NULL;
    NEW.completed_at := clock_timestamp();
    NEW.result_hash:=CASE WHEN action.status='completed' THEN action_result.result_hash
        ELSE NULL END;
    NEW.terminal_reason := CASE WHEN action.status IN ('failed','rejected')
        THEN COALESCE(action.terminal_reason,'runtime_media_retry_failed')
        WHEN action.status='cancelled' THEN 'runtime_media_retry_cancelled'
        ELSE NULL END;
    RETURN NEW;
END $$;
CREATE FUNCTION _agent_runtime_media_retry_run_event_v1() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE command agent_session_commands%ROWTYPE; action agent_actions%ROWTYPE;
    terminal_event_type TEXT;
BEGIN
    IF OLD.status IN ('completed','failed','cancelled') OR
       NEW.status NOT IN ('completed','failed','cancelled')
       OR NEW.capability_snapshot->>'source' IS DISTINCT FROM 'runtime_media_slot_retry'
       OR NEW.capability_snapshot->>'execution_mode' IS DISTINCT FROM 'action_only' THEN
        RETURN NEW; END IF;
    SELECT * INTO command FROM agent_session_commands WHERE id=NEW.command_id;
    SELECT candidate.* INTO action FROM agent_actions candidate
     WHERE candidate.run_id=NEW.id ORDER BY candidate.action_index, candidate.id LIMIT 1;
    IF command.payload->>'source' IS DISTINCT FROM 'runtime_media_slot_retry'
       OR command.payload->>'execution_mode' IS DISTINCT FROM 'action_only'
       OR command.payload->>'task_id' IS DISTINCT FROM action.id::TEXT THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_RETRY_RUN_EVENT_CONTRACT_INVALID'
            USING ERRCODE='55000'; END IF;
    terminal_event_type := 'run.'||NEW.status;
    IF NOT EXISTS(SELECT 1 FROM agent_runtime_events existing WHERE
      existing.run_id=NEW.id AND existing.event_type=terminal_event_type) THEN
        PERFORM append_agent_runtime_event(NEW.session_id,terminal_event_type,NEW.id,
            action.model_step_id,action.id,
            'system',session_user,jsonb_build_object(
                'source','runtime_media_slot_retry','execution_mode','action_only',
                'action_id',action.id,'task_id',action.id,'result_hash',NEW.result_hash,
                'reason',NEW.terminal_reason),
            ARRAY['web_runtime','audit']::TEXT[]);
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER agent_runtime_media_retry_run_guard BEFORE UPDATE OF status,blocking_action_count ON agent_runs FOR EACH ROW EXECUTE FUNCTION _agent_runtime_media_retry_run_guard_v1();
CREATE CONSTRAINT TRIGGER agent_runtime_media_retry_run_terminal_event AFTER UPDATE ON agent_runs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION _agent_runtime_media_retry_run_event_v1();
CREATE FUNCTION request_agent_runtime_media_message_cancel_v1(p_output_message_id UUID,
    p_org_id UUID,p_user_id UUID,p_idempotency_key TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE message messages%ROWTYPE; candidate RECORD; active_runs UUID[]; run_id UUID;
    run_row agent_runs%ROWTYPE; result JSONB; cancelled_count INTEGER;
    reconcile_count INTEGER; completed_count INTEGER;
BEGIN
    PERFORM _agent_runtime_media_web_control_v1(p_org_id,p_user_id);
    IF p_output_message_id IS NULL OR length(btrim(COALESCE(p_idempotency_key,'')))
       NOT BETWEEN 1 AND 200 THEN RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_CANCEL_INVALID'
       USING ERRCODE='22023'; END IF;
    SELECT scoped_message.* INTO message FROM messages scoped_message JOIN
      conversations conversation ON conversation.id=scoped_message.conversation_id
     WHERE (scoped_message.id,scoped_message.org_id,scoped_message.role::TEXT,
            conversation.org_id,conversation.user_id)
        IS NOT DISTINCT FROM (p_output_message_id,p_org_id,'assistant',p_org_id,p_user_id)
       AND EXISTS (SELECT 1 FROM agent_runtime_media_action_bindings binding
        WHERE (binding.output_message_id,binding.org_id,binding.user_id)
        IS NOT DISTINCT FROM (scoped_message.id,p_org_id,p_user_id))
     FOR UPDATE OF scoped_message;
    IF message.id IS NULL THEN RETURN jsonb_build_object('outcome','not_runtime_media'); END IF;
    PERFORM 1 FROM agent_runtime_media_action_bindings binding WHERE
      (binding.output_message_id,binding.org_id,binding.user_id) IS NOT DISTINCT FROM
      (message.id,p_org_id,p_user_id) FOR UPDATE;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_runtime_media'); END IF;
    PERFORM 1 FROM agent_actions action JOIN agent_runtime_media_action_bindings binding
     ON binding.action_id=action.id WHERE binding.output_message_id=message.id
     ORDER BY action.id FOR UPDATE OF action;
    PERFORM 1 FROM agent_action_attempts attempt JOIN
     agent_runtime_media_action_bindings binding ON binding.action_id=attempt.action_id
     WHERE binding.output_message_id=message.id ORDER BY attempt.id FOR UPDATE OF attempt;
    PERFORM 1 FROM agent_runtime_provider_submission_facts fact JOIN
     agent_runtime_media_action_bindings binding ON binding.action_id=fact.action_id
     WHERE binding.output_message_id=message.id ORDER BY fact.id FOR UPDATE OF fact;
    -- Dispatch crossed the side-effect gate: preserve as unknown for reconcile.
    FOR candidate IN
        SELECT action.id AS action_id, attempt.id AS attempt_id
          FROM agent_actions action
          JOIN agent_action_attempts attempt ON attempt.action_id = action.id
          JOIN agent_runtime_media_action_bindings binding ON binding.action_id = action.id
         WHERE binding.output_message_id = message.id
           AND action.status = 'running'
           AND attempt.status IN ('claimed','dispatching')
           AND (attempt.dispatch_phase <> 'claimed' OR EXISTS (
               SELECT 1 FROM agent_runtime_provider_submission_facts fact
                WHERE fact.action_id = action.id))
    LOOP
        UPDATE agent_action_attempts SET status='unknown',ambiguity_evidence=
            jsonb_build_object('kind','message_cancel_after_dispatch',
                'cancel_idempotency_key',btrim(p_idempotency_key)),
            retry_disposition = 'retry_after_reconcile',
            state_version = state_version + 1, updated_at = clock_timestamp()
         WHERE id = candidate.attempt_id;
        UPDATE agent_actions SET status='unknown',retry_disposition='retry_after_reconcile',
            state_version = state_version + 1, updated_at = clock_timestamp()
         WHERE id = candidate.action_id;
    END LOOP;
    INSERT INTO agent_runtime_media_cancel_requests(action_id,output_message_id,run_id,
      org_id,user_id,disposition,action_status_at_request,idempotency_key)
    SELECT action.id, message.id, action.run_id, p_org_id, p_user_id,
           CASE WHEN action.status IN ('accepted','unknown')
                THEN 'cancel_reconcile' ELSE 'cancel_now' END,
           action.status, btrim(p_idempotency_key)
      FROM (
          SELECT DISTINCT ON(binding.action_index) action.* FROM agent_actions action
          JOIN agent_runtime_media_action_bindings binding ON binding.action_id=action.id
          WHERE binding.output_message_id=message.id ORDER BY binding.action_index,
          binding.created_at DESC,action.id DESC
      ) action
     WHERE action.status NOT IN ('completed','failed','rejected','cancelled')
    ON CONFLICT (action_id) DO NOTHING;
    SELECT array_agg(DISTINCT action.run_id ORDER BY action.run_id) INTO active_runs
      FROM (
          SELECT DISTINCT ON(binding.action_index) action.* FROM agent_actions action
          JOIN agent_runtime_media_action_bindings binding ON binding.action_id=action.id
          WHERE binding.output_message_id=message.id ORDER BY binding.action_index,
          binding.created_at DESC,action.id DESC
      ) action
     WHERE action.status NOT IN ('completed','failed','rejected','cancelled');
    FOREACH run_id IN ARRAY COALESCE(active_runs, ARRAY[]::UUID[]) LOOP
        SELECT * INTO run_row FROM agent_runs WHERE id = run_id FOR UPDATE;
        IF run_row.status NOT IN ('completed','failed','cancelled') THEN
            result := cancel_agent_run(
                run_row.id, run_row.state_version, 'runtime_media_message_cancel');
            IF result->>'outcome' NOT IN ('cancelled','already_cancelled') THEN RAISE
                EXCEPTION 'AGENT_RUNTIME_MEDIA_CANCEL_CONFLICT' USING ERRCODE='40001'; END IF;
        END IF;
    END LOOP;
    SELECT count(*)FILTER(WHERE action.status='cancelled'),count(*)FILTER(
      WHERE action.status IN('accepted','unknown')),count(*)FILTER(WHERE
      action.status='completed')
      INTO cancelled_count, reconcile_count, completed_count
      FROM (
          SELECT DISTINCT ON(binding.action_index) action.* FROM agent_actions action
          JOIN agent_runtime_media_action_bindings binding ON binding.action_id=action.id
          WHERE binding.output_message_id=message.id ORDER BY binding.action_index,
          binding.created_at DESC,action.id DESC
      ) action;
    RETURN jsonb_build_object('outcome',CASE WHEN COALESCE(cardinality(active_runs),0)=0
        THEN 'already_terminal' ELSE 'cancel_requested' END,'cancelled_count',
        cancelled_count,'reconcile_count',reconcile_count,'completed_count',completed_count,
        'release_task_ids', (SELECT COALESCE(jsonb_agg(DISTINCT task_id), '[]')
            FROM (SELECT binding.chat_task_id AS task_id FROM
                    agent_runtime_media_action_bindings binding WHERE
                    binding.output_message_id=message.id
                  UNION
                  SELECT binding.task_id FROM agent_runtime_media_action_bindings binding
                    JOIN agent_runtime_media_cancel_requests cancel_request
                      ON cancel_request.action_id=binding.action_id
                    JOIN agent_actions action ON action.id=binding.action_id WHERE
                    binding.output_message_id=message.id AND
                    cancel_request.disposition='cancel_now' AND
                    action.status='cancelled') releasable));
END $$;
CREATE FUNCTION retry_agent_runtime_media_slot_v1(p_output_message_id UUID,
    p_conversation_id UUID,p_slot_index INTEGER,p_slot_id UUID,
    p_expected_slot_revision BIGINT,p_org_id UUID,p_user_id UUID,
    p_idempotency_key TEXT,p_client_task_id TEXT,p_task_slot_id TEXT DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE message messages%ROWTYPE; source_binding agent_runtime_media_action_bindings%ROWTYPE;
    source_action agent_actions%ROWTYPE; source_task tasks%ROWTYPE;
    session agent_runtime_sessions%ROWTYPE; command agent_session_commands%ROWTYPE;
    run agent_runs%ROWTYPE; step agent_model_steps%ROWTYPE; action agent_actions%ROWTYPE;
    replay_lineage agent_runtime_media_retry_lineage%ROWTYPE; canonical JSONB;
    action_id UUID:=gen_random_uuid(); transaction_id UUID:=gen_random_uuid(); slot JSONB;
    stable_slot_id UUID; slot_revision BIGINT; retry_ordinal INTEGER;
    final_balance INTEGER; request_params JSONB; event JSONB;
BEGIN
    PERFORM _agent_runtime_media_web_control_v1(p_org_id, p_user_id);
    IF p_output_message_id IS NULL OR p_conversation_id IS NULL OR p_slot_id IS NULL
       OR p_slot_index NOT BETWEEN 0 AND 9 OR p_expected_slot_revision < 0
       OR length(btrim(COALESCE(p_idempotency_key, ''))) NOT BETWEEN 1 AND 200
       OR (p_client_task_id IS NOT NULL AND length(p_client_task_id) > 100)
       OR (p_task_slot_id IS NOT NULL AND length(p_task_slot_id)>200) THEN RAISE EXCEPTION
       'AGENT_RUNTIME_MEDIA_RETRY_INVALID' USING ERRCODE='22023'; END IF;
    SELECT scoped_message.* INTO message FROM messages scoped_message JOIN
      conversations conversation ON conversation.id=scoped_message.conversation_id
     WHERE (scoped_message.id,scoped_message.org_id,scoped_message.conversation_id,
            scoped_message.role::TEXT,conversation.org_id,conversation.user_id)
        IS NOT DISTINCT FROM (p_output_message_id,p_org_id,p_conversation_id,
            'assistant',p_org_id,p_user_id)
       AND EXISTS (SELECT 1 FROM agent_runtime_media_action_bindings binding
        WHERE (binding.output_message_id,binding.org_id,binding.user_id,
               binding.conversation_id) IS NOT DISTINCT FROM
              (scoped_message.id,p_org_id,p_user_id,p_conversation_id))
     FOR UPDATE OF scoped_message;
    IF message.id IS NULL THEN RETURN jsonb_build_object('outcome','not_runtime_media'); END IF;
    SELECT runtime_session.* INTO session FROM agent_runtime_media_action_bindings binding JOIN
      agent_runtime_sessions runtime_session ON runtime_session.id=binding.session_id
      WHERE (binding.output_message_id,binding.org_id,binding.user_id) IS NOT DISTINCT
      FROM (message.id,p_org_id,p_user_id) ORDER BY binding.created_at,binding.action_id
      LIMIT 1 FOR UPDATE OF runtime_session;
    IF session.id IS NULL OR session.org_id IS DISTINCT FROM p_org_id OR
       session.user_id IS DISTINCT FROM p_user_id THEN RETURN
       jsonb_build_object('outcome','not_runtime_media'); END IF;
    SELECT * INTO replay_lineage FROM agent_runtime_media_retry_lineage WHERE
     output_message_id=message.id AND idempotency_key=btrim(p_idempotency_key);
    IF FOUND THEN
        IF replay_lineage.slot_index IS DISTINCT FROM p_slot_index
           OR replay_lineage.slot_id IS DISTINCT FROM p_slot_id
           OR replay_lineage.base_slot_revision IS DISTINCT FROM p_expected_slot_revision THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_RETRY_IDEMPOTENCY_CONFLICT'
                USING ERRCODE='23505'; END IF;
        SELECT * INTO action FROM agent_actions WHERE id=replay_lineage.retry_action_id;
        RETURN jsonb_build_object('outcome','already_created','action_id',action.id,
            'run_id',action.run_id,'task_id',action.id,'slot_id',replay_lineage.slot_id,
            'slot_index',replay_lineage.slot_index,
            'slot_revision',replay_lineage.base_slot_revision+1);
    END IF;
    IF EXISTS (
        SELECT 1 FROM agent_runtime_media_action_bindings binding
        JOIN agent_actions active ON active.id=binding.action_id
        WHERE (binding.output_message_id,binding.org_id,binding.user_id,
               binding.conversation_id,binding.action_index) IS NOT DISTINCT FROM
              (message.id,p_org_id,p_user_id,p_conversation_id,p_slot_index)
          AND active.status NOT IN ('completed','failed','rejected','cancelled')
    ) THEN RETURN jsonb_build_object('outcome','slot_active'); END IF;
    SELECT binding.* INTO source_binding
      FROM agent_runtime_media_action_bindings binding
     WHERE (binding.output_message_id,binding.org_id,binding.user_id,
            binding.conversation_id,binding.action_index) IS NOT DISTINCT FROM
           (message.id,p_org_id,p_user_id,p_conversation_id,p_slot_index)
     ORDER BY binding.created_at DESC, binding.action_id DESC LIMIT 1 FOR UPDATE;
    IF source_binding.action_id IS NULL THEN RETURN jsonb_build_object(
        'outcome','slot_not_found'); END IF;
    SELECT * INTO source_action FROM agent_actions
     WHERE id=source_binding.action_id FOR UPDATE;
    SELECT * INTO source_task FROM tasks WHERE id=source_binding.task_id FOR UPDATE;
    IF source_action.status NOT IN ('failed','rejected','cancelled') THEN RETURN
        jsonb_build_object('outcome',CASE WHEN source_action.status='completed'
        THEN 'slot_completed' ELSE 'slot_active' END); END IF;
    SELECT part INTO slot FROM jsonb_array_elements(message.content::JSONB) part WHERE
     part->>'type'='image' AND COALESCE(part->>'slot_index','-1')::INTEGER=p_slot_index LIMIT 1;
    IF slot IS NULL OR COALESCE(slot->>'slot_id','') !~
       '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
       OR COALESCE(slot->>'slot_revision','') !~ '^[0-9]+$' THEN
        RETURN jsonb_build_object('outcome','slot_conflict'); END IF;
    stable_slot_id := (slot->>'slot_id')::UUID;
    slot_revision := (slot->>'slot_revision')::BIGINT;
    IF stable_slot_id IS DISTINCT FROM p_slot_id
       OR source_binding.slot_id IS DISTINCT FROM p_slot_id
       OR slot_revision IS DISTINCT FROM p_expected_slot_revision THEN
        RETURN jsonb_build_object('outcome','slot_conflict'); END IF;
    IF source_binding.credit_state <> 'refunded'
       OR source_task.id IS NULL
       OR source_task.status::TEXT NOT IN ('failed','cancelled') THEN
        RETURN jsonb_build_object('outcome','projection_pending'); END IF;
    IF slot->>'slot_status' NOT IN ('failed','cancelled') THEN RETURN
        jsonb_build_object('outcome','slot_conflict'); END IF;
    IF source_task.delivery_context @> '{"actor":false,"runtime":true}'::JSONB
       IS NOT TRUE THEN RETURN jsonb_build_object('outcome','slot_conflict'); END IF;
    SELECT COALESCE(max(lineage.retry_ordinal),0)+1 INTO retry_ordinal FROM
     agent_runtime_media_retry_lineage lineage WHERE lineage.output_message_id=message.id
     AND lineage.slot_index=p_slot_index;
    action_id := gen_random_uuid();
    INSERT INTO agent_session_commands(session_id,org_id,user_id,command_type,
      idempotency_key,payload,request_hash) VALUES(session.id,p_org_id,p_user_id,
      'submit_input',btrim(p_idempotency_key),
      jsonb_build_object('source','runtime_media_slot_retry','execution_mode','action_only',
        'task_id',action_id,'source_chat_task_id',source_binding.chat_task_id,
        'input_message_id',source_binding.input_message_id,
        'output_message_id',message.id,'retry_slot_index',p_slot_index),
      md5(jsonb_build_object('message_id',message.id,'slot_index',p_slot_index,
        'idempotency_key',btrim(p_idempotency_key))::TEXT)) RETURNING * INTO command;
    INSERT INTO agent_runs(session_id,command_id,org_id,user_id,run_kind,status,
      idempotency_key,request_hash,context_receipt,config_snapshot,capability_snapshot,
      blocking_action_count) VALUES(session.id,command.id,p_org_id,p_user_id,
      'user','waiting_actions',
      'media-slot-retry:'||command.id,command.request_hash,
      jsonb_build_object('source_action_id',source_action.id,'slot_id',stable_slot_id),
      jsonb_build_object('model_id',source_binding.pricing_model_id),
      jsonb_build_object('channel','web','source','runtime_media_slot_retry',
        'execution_mode','action_only','projection_mode','media_slot_retry',
        'model_loop_enabled',FALSE,'retry_task_id',action_id,
        'output_message_id',message.id),1)
      RETURNING * INTO run;
    UPDATE agent_session_commands SET result_entity_id=run.id WHERE id=command.id;
    INSERT INTO agent_model_steps(run_id,session_id,org_id,user_id,step_number,status,
      model_id,provider,model_revision,prompt_revision,tool_catalog_revision,
      request_receipt,response_receipt,stop_reason,completed_at) VALUES(run.id,
      session.id,p_org_id,p_user_id,1,'completed',
      source_binding.pricing_model_id,'runtime','media-slot-retry-v1',
      'media-slot-retry-v1','runtime-media-v1',
      jsonb_build_object('source_action_id',source_action.id,'slot_index',p_slot_index),
      jsonb_build_object('explicit_user_retry',TRUE),'tool_calls',clock_timestamp())
      RETURNING * INTO step;
    canonical := _canonical_agent_action_batch(step,jsonb_build_array(jsonb_build_object(
      'action_id',action_id,'index',0,'stable_tool_call_id','retry:'||stable_slot_id||':'||retry_ordinal,
      'provider_call_id',NULL,'tool_name','generate_image','arguments',source_action.arguments,
      'wave',0,'dependencies','[]'::JSONB,'blocking',TRUE,'policy_decision','preauthorized',
      'policy_snapshot',jsonb_build_object('safety_level','confirm','explicit_user_retry',TRUE,
        'source_action_id',source_action.id,'slot_id',stable_slot_id,'slot_index',p_slot_index),
      'policy_revision','runtime-media-slot-retry-v1','retry_disposition','retry_after_reconcile')));
    INSERT INTO agent_actions(id,session_id,run_id,model_step_id,org_id,user_id,
      action_index,stable_tool_call_id,tool_name,arguments,arguments_hash,request_hash,
      batch_hash,wave,dependency_ids,blocking,policy_decision,policy_snapshot,
      policy_revision,retry_disposition,status) SELECT action_id,session.id,run.id,
      step.id,p_org_id,p_user_id,0,
        item->>'stable_tool_call_id','generate_image',source_action.arguments,
        item->>'arguments_hash',item->>'request_hash',_agent_action_batch_hash(canonical),
        0,'{}',TRUE,'preauthorized',item->'policy_snapshot',item->>'policy_revision',
        'retry_after_reconcile','queued' FROM jsonb_array_elements(canonical) item
        RETURNING * INTO action;
    INSERT INTO agent_policy_receipts(action_id,session_id,run_id,org_id,user_id,
      decision,arguments_hash,executor_type,executor_revision,policy_revision,
      effective_scope,reason_codes,receipt_hash,expires_at) VALUES(action.id,
      session.id,run.id,p_org_id,p_user_id,'allow',
      action.arguments_hash,'runtime_media_generation:generate_image',1,
      action.policy_revision,jsonb_build_object('org_id',p_org_id,'user_id',p_user_id,
        'output_message_id',message.id,'slot_index',p_slot_index),
      ARRAY['explicit_runtime_media_slot_retry'],encode(digest(convert_to(
        jsonb_build_object('action_id',action.id,'arguments_hash',action.arguments_hash,
          'slot_id',stable_slot_id,'slot_index',p_slot_index)::TEXT,'UTF8'),'sha256'),'hex'),
      clock_timestamp()+interval '15 minutes');
    UPDATE users SET credits=credits-source_binding.unit_credits,updated_at=clock_timestamp()
     WHERE id=p_user_id AND status::TEXT='active' AND credits>=source_binding.unit_credits
     RETURNING credits INTO final_balance;
    IF final_balance IS NULL THEN RAISE EXCEPTION
        'AGENT_RUNTIME_MEDIA_INSUFFICIENT_CREDITS' USING ERRCODE='P0001'; END IF;
    request_params:=(COALESCE(source_task.request_params,'{}'::JSONB)-'_task_slot_id')||
      CASE WHEN p_task_slot_id IS NULL THEN '{}'::JSONB ELSE
      jsonb_build_object('_task_slot_id',p_task_slot_id) END;
    INSERT INTO credit_transactions(id,task_id,user_id,amount,type,status,reason,org_id)
      VALUES(transaction_id,action.id,p_user_id,source_binding.unit_credits,'lock','pending',
      'Agent Runtime media slot retry reservation',p_org_id);
    INSERT INTO credits_history(user_id,change_type,change_amount,balance_after,
      description,org_id) VALUES(p_user_id,'image_generation_cost'::credits_change_type,
      -source_binding.unit_credits,final_balance,'Agent Runtime media slot retry reservation',p_org_id);
    INSERT INTO tasks(id,client_task_id,user_id,org_id,conversation_id,type,status,
      credits_locked,credits_used,model_id,placeholder_message_id,assistant_message_id,
      request_params,placeholder_created_at,input_message_id,turn_id,
      base_context_revision,context_through_message_id,execution_mode,delivery_context,
      image_index,batch_id,credit_transaction_id) VALUES(action.id,
      COALESCE(NULLIF(btrim(p_client_task_id),''),
      'runtime-media-retry:'||action.id),p_user_id,p_org_id,
      message.conversation_id,'image','preparing',source_binding.unit_credits,0,
      source_binding.pricing_model_id,message.id::TEXT,message.id,request_params,
      clock_timestamp(),source_binding.input_message_id,source_task.turn_id,
      source_task.base_context_revision,source_task.context_through_message_id,'serial',
      jsonb_build_object('channel','web','actor',FALSE,'runtime',TRUE,
        'runtime_owner','action_loop','runtime_session_id',session.id,
        'runtime_command_id',command.id,'runtime_action_id',action.id,'runtime_run_id',run.id),
      p_slot_index,_agent_action_batch_hash(canonical),transaction_id);
    INSERT INTO agent_runtime_media_action_bindings(action_id,slot_id,task_id,session_id,
      run_id,model_step_id,chat_task_id,org_id,user_id,conversation_id,batch_hash,
      action_index,action_arguments_hash,action_request_hash,input_message_id,
      output_message_id,credit_transaction_id,pricing_revision,pricing_model_id,
      pricing_resolution,pricing_fact_hash,provider_request_hash,unit_credits,
      reference_manifest_hash,projection_revision) VALUES(action.id,stable_slot_id,
      action.id,session.id,run.id,step.id,source_binding.chat_task_id,
      p_org_id,p_user_id,message.conversation_id,_agent_action_batch_hash(canonical),
      p_slot_index,action.arguments_hash,action.request_hash,source_binding.input_message_id,
      message.id,transaction_id,source_binding.pricing_revision,
      source_binding.pricing_model_id,source_binding.pricing_resolution,
      source_binding.pricing_fact_hash,encode(digest(convert_to(request_params::TEXT,'UTF8'),
      'sha256'),'hex'),source_binding.unit_credits,source_binding.reference_manifest_hash,
      slot_revision+1);
    INSERT INTO agent_runtime_media_retry_lineage(retry_action_id,source_action_id,
      root_action_id,output_message_id,org_id,user_id,conversation_id,slot_id,slot_index,
      retry_ordinal,base_slot_revision,idempotency_key) VALUES(action.id,source_action.id,
      COALESCE((SELECT lineage.root_action_id FROM agent_runtime_media_retry_lineage lineage
        WHERE lineage.retry_action_id=source_action.id),
        source_action.id),message.id,p_org_id,p_user_id,message.conversation_id,stable_slot_id,
        p_slot_index,retry_ordinal,slot_revision,btrim(p_idempotency_key));
    UPDATE messages SET content=(SELECT jsonb_agg(CASE WHEN part->>'type'='image'
        AND COALESCE(part->>'slot_index','-1')::INTEGER=p_slot_index
        THEN (part - ARRAY['url','original_url','thumbnail_url','preview_url','download_url',
             'asset_id','failed','error','error_code']) || jsonb_build_object(
             'url',NULL,'slot_id',stable_slot_id,'slot_index',p_slot_index,
             'slot_status','pending','slot_revision',slot_revision+1)
        ELSE part END ORDER BY ordinality)
        FROM jsonb_array_elements(message.content::JSONB) WITH ORDINALITY
        source(part,ordinality))::TEXT,
        status='pending',
        generation_params=jsonb_set(COALESCE(generation_params,'{}'::JSONB),
            '{runtime_media_batch,projection_revision}',to_jsonb(slot_revision+1),TRUE)
        WHERE id=message.id;
    event:=append_agent_runtime_event(session.id,'action.requested',run.id,step.id,
      action.id,'user',p_user_id::TEXT,jsonb_build_object('action_id',action.id,
      'source','runtime_media_slot_retry','source_action_id',source_action.id,
      'slot_id',stable_slot_id,'slot_index',p_slot_index,'retry_ordinal',retry_ordinal),
      ARRAY['web_runtime','audit']::TEXT[]);
    RETURN jsonb_build_object('outcome','created','action_id',action.id,'run_id',run.id,
      'task_id',action.id,'slot_id',stable_slot_id,'slot_index',p_slot_index,
      'slot_revision',slot_revision+1,'event_sequence',event->'event_sequence');
END $$;
CREATE FUNCTION read_agent_runtime_media_retry_binding_v1(p_action_id UUID,
 p_attempt_id UUID,p_worker_id TEXT,p_execution_token UUID,
 p_expected_attempt_version BIGINT,p_request_hash TEXT)
RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE binding agent_runtime_media_action_bindings%ROWTYPE;
BEGIN
    PERFORM _agent_runtime_media_worker_v1();
    IF NOT _agent_runtime_media_attempt_valid_v1(p_action_id,p_attempt_id,p_worker_id,
       p_execution_token,p_expected_attempt_version,p_request_hash) THEN RAISE EXCEPTION
       'AGENT_RUNTIME_MEDIA_ATTEMPT_SCOPE_INVALID' USING ERRCODE='42501'; END IF;
    IF NOT EXISTS(SELECT 1 FROM agent_runtime_media_retry_lineage WHERE
       retry_action_id=p_action_id) THEN RETURN jsonb_build_object('outcome','not_retry'); END IF;
    SELECT * INTO binding FROM agent_runtime_media_action_bindings WHERE action_id=p_action_id;
    IF binding.action_id IS NULL THEN RAISE EXCEPTION
       'AGENT_RUNTIME_MEDIA_RETRY_BINDING_MISSING' USING ERRCODE='55000'; END IF;
    RETURN jsonb_build_object('outcome','found','binding',to_jsonb(binding));
END $$;
REVOKE ALL ON TABLE agent_runtime_media_cancel_requests,agent_runtime_media_retry_lineage FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker,everydayai_runtime_admin;
REVOKE ALL ON FUNCTION _agent_runtime_media_web_control_v1(UUID,UUID),
 _agent_runtime_media_retry_run_guard_v1(),_agent_runtime_media_retry_run_event_v1(),
 request_agent_runtime_media_message_cancel_v1(UUID,UUID,UUID,TEXT),
    retry_agent_runtime_media_slot_v1(UUID,UUID,INTEGER,UUID,BIGINT,UUID,UUID,TEXT,TEXT,TEXT),
    read_agent_runtime_media_retry_binding_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,everydayai_sync,everydayai,everydayai_agent_runtime_worker,everydayai_projection_worker,everydayai_authorization_worker,everydayai_sandbox_worker,everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION request_agent_runtime_media_message_cancel_v1(UUID,UUID,UUID,TEXT),retry_agent_runtime_media_slot_v1(UUID,UUID,INTEGER,UUID,BIGINT,UUID,UUID,TEXT,TEXT,TEXT) TO everydayai_runtime,everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION read_agent_runtime_media_retry_binding_v1(UUID,UUID,TEXT,UUID,BIGINT,TEXT) TO everydayai_agent_runtime_worker;
RESET ROLE;
