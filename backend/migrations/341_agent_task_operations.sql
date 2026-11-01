-- Host-only bridge. Does not dispatch business IO or alter legacy ledger RPCs.
SET LOCAL ROLE everydayai;

CREATE FUNCTION public.agent_task_image_rejection_verified(p_result jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
    SELECT COALESCE(p_result->'tool_result' @> '{"kind":"agent","status":"error",
        "execution":{"status":"succeeded","handler_started":true,"cancelled":false,"cached":false,"replayed":false},
        "audit":{"tool_name":"generate_image"},
        "raw":{"status":"error","metadata":{"accepted":false,"completed":false,"submission_state":"not_accepted"}}}'::jsonb
        AND p_result#>>'{tool_result,raw,metadata,task_id}' IS NULL
        AND p_result#>>'{tool_result,raw,metadata,message_id}' IS NULL,false)
$$;
REVOKE ALL ON FUNCTION public.agent_task_image_rejection_verified(jsonb) FROM PUBLIC;

-- A matching worker acknowledgement settles execution, not user delivery.
CREATE FUNCTION public.agent_task_sandbox_phase(p_operation jsonb,p_result jsonb,p_phase text)
RETURNS boolean LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT COALESCE(p_operation->>'tool_name'='code_execute' AND p_operation->>'capability'='sandbox.code.execute'
   AND p_result#>>'{tool_result,kind}'='agent'
   AND p_result#>>'{tool_result,execution,status}'='succeeded'
   AND p_result#>'{tool_result,execution,cancelled}'='false'::jsonb
   AND p_result#>'{tool_result,execution,handler_started}'='true'::jsonb
   AND p_result#>'{tool_result,execution,cached}'='false'::jsonb
   AND p_result#>'{tool_result,execution,replayed}'='false'::jsonb
   AND p_result#>>'{tool_result,audit,tool_name}'='code_execute'
   AND p_result#>>'{tool_result,audit,task_id}'=p_operation->>'owner_task_id'
   AND p_result#>>'{tool_result,audit,tool_call_id}'=p_operation->>'invocation_id'
   AND p_result#>>'{tool_result,audit,args_hash}'=p_operation->>'arguments_sha256'
   AND p_result#>>'{tool_result,raw,metadata,code_execution,phase}'=p_phase
   AND p_result#>>'{tool_result,raw,metadata,code_execution,code_sha256}' ~ '^[0-9a-f]{64}$'
   AND p_operation->>'effect_key'='sandbox-'||encode(sha256(convert_to('["'||(p_operation->>'step_id')||'","'||
     (p_result#>>'{tool_result,raw,metadata,code_execution,code_sha256}')||'"]','UTF8')),'hex')
   AND ((p_phase='not_started' AND p_result#>>'{tool_result,status}'='error') OR
     (p_phase='finished' AND p_result#>>'{tool_result,status}' IN ('success','error')
       AND p_result#>>'{tool_result,raw,metadata,code_execution,kernel_status}' IN ('ok','error')
       AND COALESCE(p_result#>>'{tool_result,raw,metadata,code_execution,request_id}','')<>'')),false)
$$;
REVOKE ALL ON FUNCTION public.agent_task_sandbox_phase(jsonb,jsonb,text) FROM PUBLIC;

-- Domain-specific negative evidence. A returned error alone cannot authorize retry.
CREATE FUNCTION public.agent_task_rejection_verified(p_operation jsonb,p_result jsonb)
RETURNS boolean LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF p_operation->>'tool_name'='generate_video' AND p_operation->>'capability'='platform.generate_video' THEN RETURN public.agent_task_video_rejection_verified(p_result); END IF;
 IF p_operation->>'tool_name'='code_execute' THEN RETURN public.agent_task_sandbox_phase(p_operation,p_result,'not_started'); END IF;
 IF p_operation->>'tool_name'='generate_image' AND p_operation->>'capability'='platform.generate_image' THEN
   RETURN public.agent_task_image_rejection_verified(p_result);
 END IF;
 IF ((p_operation->>'tool_name'='erp_execute' AND p_operation->>'capability'='platform.erp_execute')
     OR (p_operation->>'tool_name'='restore_file' AND p_operation->>'capability'='platform.restore_file')
     OR (p_operation->>'tool_name'='file_delete' AND p_operation->>'capability'='workspace.file.delete'))
   AND p_result->'tool_result' @> '{"kind":"agent","status":"error","execution":{"status":"succeeded","handler_started":true,"cancelled":false,"cached":false,"replayed":false}}'::jsonb THEN
   RETURN EXISTS(SELECT 1 FROM public.agent_task_native_writes r JOIN public.tool_invocations i ON i.id=r.native_invocation_id
     WHERE r.operation_id=p_operation->>'id' AND r.phase='prepared' AND r.domain=CASE p_operation->>'tool_name' WHEN 'erp_execute' THEN 'erp' ELSE 'file' END
       AND r.args_hash=p_operation->>'arguments_sha256' AND i.task_id=(p_operation->>'owner_task_id')::uuid
       AND i.turn_id=(p_operation->>'owner_turn_id')::uuid AND i.tool_call_id=p_operation->>'invocation_id'
       AND i.tool_name=p_operation->>'tool_name' AND i.args_hash=r.args_hash);
 END IF;
 IF p_operation->>'tool_name'='agent_reach' AND p_operation->>'capability'='platform.agent_reach'
   AND p_result->'tool_result' @> '{"kind":"agent","status":"error","execution":{"status":"succeeded","handler_started":true,"cancelled":false,"cached":false,"replayed":false}}'::jsonb THEN
   RETURN EXISTS(SELECT 1 FROM public.reach_operations r JOIN public.tool_invocations i ON i.id=r.native_invocation_id
     WHERE i.task_id=(p_operation->>'owner_task_id')::uuid AND i.turn_id=(p_operation->>'owner_turn_id')::uuid
       AND i.tool_call_id=p_operation->>'invocation_id' AND i.tool_name='agent_reach'
       AND i.args_hash=p_operation->>'arguments_sha256'
       AND r.state='failed' AND r.receipt->>'stage'='not_dispatched'
       AND NULLIF(r.receipt->>'code','')=p_result#>>'{tool_result,raw,error_message}');
 END IF;
 RETURN false;
END $$;
REVOKE ALL ON FUNCTION public.agent_task_rejection_verified(jsonb,jsonb) FROM PUBLIC;

CREATE FUNCTION public.begin_agent_task_operation(
    p_task_id uuid, p_execution_token uuid, p_logical_task_id uuid, p_expected_revision bigint,
    p_step_id text, p_capability text, p_effect_key text, p_effect_version integer,
    p_tool_call_id text, p_tool_name text, p_args_hash text
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE
    t public.tasks%ROWTYPE;
    s public.agent_task_states%ROWTYPE;
    cp jsonb;
    op jsonb;
    step jsonb;
    identity text;
    next_state jsonb;
    saved jsonb;
    invocation jsonb;
    previous_version integer;
    history jsonb := '[]'::jsonb;
    retry_at timestamptz;
BEGIN
    IF p_task_id IS NULL OR p_execution_token IS NULL OR p_logical_task_id IS NULL
       OR p_expected_revision IS NULL OR p_expected_revision<0
       OR p_step_id IS NULL OR p_step_id !~ '^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,99}$'
       OR p_capability IS NULL OR length(p_capability) NOT BETWEEN 1 AND 150 OR p_effect_key IS NULL
       OR p_effect_key !~ '^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,99}$'
       OR p_effect_version IS NULL OR p_effect_version<1
       OR p_tool_call_id IS NULL OR length(btrim(p_tool_call_id)) NOT BETWEEN 1 AND 200
       OR p_tool_name IS NULL OR p_tool_name !~ '^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,99}$'
       OR p_args_hash IS NULL OR p_args_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'TASK_OPERATION_INPUT_INVALID' USING ERRCODE='22023';
    END IF;
    -- Same lock order as checkpoint saves; parallel calls serialize facts only.
    SELECT * INTO t FROM public.tasks WHERE id=p_task_id FOR UPDATE;
    IF NOT FOUND OR t.type IS DISTINCT FROM 'chat' OR (t.delivery_context @> '{"actor":true}') IS NOT TRUE
       OR t.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
       OR t.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'TASK_OPERATION_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    IF t.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome','ownership_lost');
    END IF;
    IF t.status IS DISTINCT FROM 'running' THEN RETURN jsonb_build_object('outcome','terminal'); END IF;
    IF t.lease_expires_at IS NULL OR t.lease_expires_at<=now() THEN
        RETURN jsonb_build_object('outcome','lease_expired');
    END IF;
    SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id FOR UPDATE;
    IF NOT FOUND OR s.owner_task_id<>p_task_id OR s.conversation_id<>t.conversation_id
       OR s.actor_user_id<>t.user_id OR s.org_id IS DISTINCT FROM t.org_id THEN
        RAISE EXCEPTION 'TASK_OPERATION_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    IF s.revision<>p_expected_revision THEN RETURN jsonb_build_object('outcome','stale'); END IF;
    IF s.state->>'status' IS DISTINCT FROM 'active' THEN RETURN jsonb_build_object('outcome','not_active'); END IF;
    identity := encode(sha256(convert_to('["'||p_logical_task_id::text||'","'||p_effect_key||'",'||p_effect_version::text||']','UTF8')),'hex');
    SELECT value INTO op FROM jsonb_array_elements(s.state->'operations') WHERE value->>'id'=identity;
    IF op IS NOT NULL THEN
        IF (op->>'status'<>'failed_before_dispatch' AND op->>'arguments_sha256' IS DISTINCT FROM p_args_hash)
           OR op->>'step_id' IS DISTINCT FROM p_step_id
           OR op->>'capability' IS DISTINCT FROM p_capability
           OR op->>'tool_name' IS DISTINCT FROM p_tool_name THEN
            RAISE EXCEPTION 'TASK_EFFECT_ARGUMENTS_CHANGED' USING ERRCODE='42501';
        END IF;
        -- A new call ID/attempt never acquires another dispatch right. Only the
        -- original ledger supplies its result, even after a response was lost.
        SELECT jsonb_build_object('status',i.status,'result',i.result) INTO invocation
          FROM public.tool_invocations i JOIN public.tasks original ON original.id=i.task_id
         WHERE i.task_id=(op->>'owner_task_id')::uuid AND i.turn_id=(op->>'owner_turn_id')::uuid
           AND i.tool_call_id=op->>'invocation_id' AND i.tool_name=p_tool_name AND i.args_hash=op->>'arguments_sha256'
           AND i.conversation_id=t.conversation_id AND original.conversation_id=t.conversation_id
           AND original.type='chat' AND original.user_id=t.user_id
           AND original.org_id IS NOT DISTINCT FROM t.org_id;
        IF invocation IS NULL THEN RAISE EXCEPTION 'TASK_OPERATION_LEDGER_MISSING' USING ERRCODE='42501'; END IF;
        IF op->>'status'<>'failed_before_dispatch' THEN
            RETURN jsonb_build_object('outcome',CASE WHEN invocation->>'status'='succeeded' THEN 'replay' ELSE 'reconcile' END,
                'state',s.state,'operation',op,'result',invocation->'result');
        END IF;
        IF invocation->>'status'<>'succeeded'
           OR public.agent_task_rejection_verified(op,invocation->'result') IS NOT TRUE THEN
            RAISE EXCEPTION 'TASK_OPERATION_REJECTION_UNVERIFIED' USING ERRCODE='42501';
        END IF;
        -- A proven non-send is not permission to poll a known future budget.
        IF op->>'tool_name'='agent_reach' AND invocation#>>'{result,tool_result,raw,error_message}'='ACCESS_BUDGET_WAIT' THEN
            IF NULLIF(invocation#>>'{result,tool_result,raw,metadata,next_eligible_at}','') IS NULL THEN
                RETURN jsonb_build_object('outcome','retry_wait','next_eligible_at',null);
            END IF;
            retry_at:=(invocation#>>'{result,tool_result,raw,metadata,next_eligible_at}')::timestamptz;
            IF retry_at>now() THEN RETURN jsonb_build_object('outcome','retry_wait','next_eligible_at',retry_at); END IF;
        END IF;
        IF op->>'owner_task_id'=p_task_id::text AND op->>'owner_turn_id'=t.turn_id::text
           AND op->>'invocation_id'=btrim(p_tool_call_id) THEN
            RAISE EXCEPTION 'TASK_OPERATION_NEW_ATTEMPT_ID_REQUIRED' USING ERRCODE='42501';
        END IF;
        history := COALESCE(op->'previous_attempts','[]'::jsonb)||jsonb_build_array(
            op-ARRAY['id','effect_key','effect_version','status','receipt_ref','previous_attempts']);
        IF jsonb_array_length(history)>8 THEN
            RAISE EXCEPTION 'TASK_EFFECT_RETRY_EXHAUSTED' USING ERRCODE='42501';
        END IF;
    END IF;
    SELECT value INTO step FROM jsonb_array_elements(s.state->'steps') WHERE value->>'id'=p_step_id;
    IF step IS NULL OR step->>'capability' IS DISTINCT FROM p_capability
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'results') r WHERE r->>'step_id'=p_step_id)
       OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(step->'depends_on') dependency
                 WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'results') r WHERE r->>'step_id'=dependency)) THEN
        RAISE EXCEPTION 'TASK_STEP_NOT_READY' USING ERRCODE='42501';
    END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'operations') old
               WHERE old->>'effect_key'=p_effect_key AND old->>'status' IN ('reserved','accepted','uncertain')) THEN
        RAISE EXCEPTION 'TASK_EFFECT_UNRESOLVED' USING ERRCODE='42501';
    END IF;
    SELECT COALESCE(max((old->>'effect_version')::integer),0) INTO previous_version
      FROM jsonb_array_elements(s.state->'operations') old WHERE old->>'effect_key'=p_effect_key;
    IF (op IS NULL AND p_effect_version<>previous_version+1)
       OR p_effect_version>(s.state->>'requirements_revision')::integer+1 THEN
        RAISE EXCEPTION 'TASK_EFFECT_REVISION_UNAUTHORIZED' USING ERRCODE='42501';
    END IF;
    -- Host-owned prerequisite gates cannot be waived by changing a plan label.
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'capability_gates') g,
                           jsonb_array_elements_text(g->'requirement_ids') requirement
        WHERE g->>'capability'=p_capability AND NOT EXISTS(
            SELECT 1 FROM jsonb_array_elements(s.state->'requirements') req
             WHERE req->>'id'=requirement AND
               (SELECT count(DISTINCT a->>'reference')
                  FROM jsonb_array_elements(s.state->'steps') st,
                       jsonb_array_elements(s.state->'results') r,
                       jsonb_array_elements(s.state->'artifacts') a
                 WHERE (st->'requirement_ids' @> jsonb_build_array(requirement)
                        OR st->'contributes_to' @> jsonb_build_array(requirement))
                   AND r->>'step_id'=st->>'id' AND r->'artifact_ids' @> jsonb_build_array(a->>'id')
                   AND a->>'kind'=req->>'artifact_kind'
                   AND a->'validations' @> jsonb_build_array(req->>'validator')) >= (req->>'minimum')::integer)) THEN
        RAISE EXCEPTION 'TASK_PREREQUISITE_INCOMPLETE' USING ERRCODE='42501';
    END IF;
    SELECT state INTO cp FROM public.conversation_turn_checkpoints WHERE task_id=p_task_id FOR UPDATE;
    IF cp->'_task_execution' IS DISTINCT FROM s.state THEN
        RAISE EXCEPTION 'TASK_EXECUTION_CHECKPOINT_MISMATCH' USING ERRCODE='42501';
    END IF;
    op := jsonb_build_object('id',identity,'step_id',p_step_id,'capability',p_capability,
        'effect_key',p_effect_key,'effect_version',p_effect_version,'arguments_sha256',p_args_hash,
        'owner_task_id',p_task_id,'owner_turn_id',t.turn_id,'tool_name',p_tool_name,
        'invocation_id',btrim(p_tool_call_id),'status','reserved','receipt_ref',null);
    IF history<>'[]'::jsonb THEN op := op||jsonb_build_object('previous_attempts',history); END IF;
    next_state := jsonb_set(jsonb_set(s.state,'{operations}',
        COALESCE((SELECT jsonb_agg(value) FROM jsonb_array_elements(s.state->'operations')
                   WHERE value->>'id'<>identity),'[]'::jsonb)||jsonb_build_array(op)),
        '{revision}',to_jsonb(s.revision+1));
    cp := jsonb_set(cp,'{_task_execution}',next_state);
    saved := public.save_agent_task_checkpoint(p_task_id,p_execution_token,'before_tool',cp,s.revision);
    IF saved->>'outcome' IS DISTINCT FROM 'saved' THEN RETURN saved; END IF;
    invocation := public.begin_tool_invocation(p_task_id,t.conversation_id,t.turn_id,p_execution_token,
        p_tool_call_id,p_tool_name,p_args_hash);
    IF invocation->>'outcome' IS DISTINCT FROM 'execute' THEN
        -- Roll back BOTH writes. An unrelated old native invocation cannot be
        -- adopted as a fresh operation or cause a second dispatch.
        RAISE EXCEPTION 'TASK_OPERATION_INVOCATION_CONFLICT' USING ERRCODE='42501';
    END IF;
    RETURN jsonb_build_object('outcome','execute','state',next_state,'operation',op);
END $$;

REVOKE ALL ON FUNCTION public.begin_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.begin_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text) TO everydayai;

CREATE FUNCTION public.complete_agent_task_operation(
    p_task_id uuid, p_execution_token uuid, p_logical_task_id uuid, p_expected_revision bigint,
    p_operation_id text, p_status text, p_receipt_ref text, p_result jsonb, p_error_message text DEFAULT ''
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE
    t public.tasks%ROWTYPE;
    s public.agent_task_states%ROWTYPE;
    op jsonb;
    updated_op jsonb;
    next_state jsonb;
    cp jsonb;
    saved jsonb;
    ledger public.tool_invocations%ROWTYPE;
    completed jsonb;
    ledger_status text;
BEGIN
    IF p_status IS NULL OR p_status NOT IN ('accepted','completed','uncertain','failed_before_dispatch')
       OR p_result IS NULL OR jsonb_typeof(p_result)<>'object'
       OR (p_status IN ('accepted','completed') AND NULLIF(btrim(p_receipt_ref),'') IS NULL)
       OR length(p_receipt_ref)>2000 THEN
        RAISE EXCEPTION 'TASK_OPERATION_INPUT_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO t FROM public.tasks WHERE id=p_task_id FOR UPDATE;
    IF NOT FOUND OR t.type IS DISTINCT FROM 'chat' OR (t.delivery_context @> '{"actor":true}') IS NOT TRUE
       OR t.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
       OR t.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'TASK_OPERATION_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    IF t.execution_token IS DISTINCT FROM p_execution_token THEN RETURN jsonb_build_object('outcome','ownership_lost'); END IF;
    IF t.status IS DISTINCT FROM 'running' THEN RETURN jsonb_build_object('outcome','terminal'); END IF;
    IF t.lease_expires_at IS NULL OR t.lease_expires_at<=now() THEN RETURN jsonb_build_object('outcome','lease_expired'); END IF;
    SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id FOR UPDATE;
    IF NOT FOUND OR s.owner_task_id<>p_task_id OR s.conversation_id<>t.conversation_id
       OR s.actor_user_id<>t.user_id OR s.org_id IS DISTINCT FROM t.org_id THEN
        RAISE EXCEPTION 'TASK_OPERATION_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    IF s.revision IS DISTINCT FROM p_expected_revision THEN RETURN jsonb_build_object('outcome','stale'); END IF;
    SELECT value INTO op FROM jsonb_array_elements(s.state->'operations') WHERE value->>'id'=p_operation_id;
    IF op IS NULL OR op->>'owner_task_id' IS DISTINCT FROM p_task_id::text
       OR op->>'owner_turn_id' IS DISTINCT FROM t.turn_id::text THEN
        RAISE EXCEPTION 'TASK_EFFECT_OWNER_MISMATCH' USING ERRCODE='42501';
    END IF;
    SELECT * INTO ledger FROM public.tool_invocations
     WHERE task_id=p_task_id AND turn_id=t.turn_id AND tool_call_id=op->>'invocation_id' FOR UPDATE;
    IF NOT FOUND OR ledger.tool_name IS DISTINCT FROM op->>'tool_name'
       OR ledger.args_hash IS DISTINCT FROM op->>'arguments_sha256'
       OR ledger.conversation_id IS DISTINCT FROM t.conversation_id THEN
        RAISE EXCEPTION 'TASK_OPERATION_LEDGER_MISSING' USING ERRCODE='42501';
    END IF;
    ledger_status := CASE WHEN p_status='uncertain' THEN 'uncertain' ELSE 'succeeded' END;
    IF p_status='failed_before_dispatch' AND (
        public.agent_task_rejection_verified(op,p_result) IS NOT TRUE OR p_receipt_ref IS NOT NULL
        OR p_result#>>'{tool_result,audit,task_id}' IS DISTINCT FROM p_task_id::text
        OR p_result#>>'{tool_result,audit,tool_call_id}' IS DISTINCT FROM op->>'invocation_id'
        OR p_result#>>'{tool_result,audit,args_hash}' IS DISTINCT FROM op->>'arguments_sha256'
        OR p_result#>>'{tool_result,audit,actor_user_id}' IS DISTINCT FROM t.user_id::text
        OR p_result#>>'{tool_result,audit,conversation_id}' IS DISTINCT FROM t.conversation_id::text
        OR p_result#>>'{tool_result,audit,org_id}' IS DISTINCT FROM t.org_id::text
        OR p_result#>>'{tool_result,audit,workspace_owner_id}' IS DISTINCT FROM s.state#>>'{execution_identity,workspace_owner_id}') THEN
        RAISE EXCEPTION 'TASK_OPERATION_REJECTION_UNVERIFIED' USING ERRCODE='42501';
    END IF;
    IF op->>'status'=p_status AND op->>'receipt_ref' IS NOT DISTINCT FROM p_receipt_ref
       AND ledger.status=ledger_status AND ledger.result IS NOT DISTINCT FROM p_result THEN
        RETURN jsonb_build_object('outcome','saved','state',s.state,'operation',op);
    END IF;
    IF op->>'status'<>'reserved' OR ledger.status<>'running' THEN
        RAISE EXCEPTION 'TASK_EFFECT_TRANSITION_DENIED' USING ERRCODE='42501';
    END IF;
    IF p_status='completed' AND op->>'tool_name'='code_execute'
       AND public.agent_task_sandbox_phase(op,p_result,'finished') IS NOT TRUE THEN
        RAISE EXCEPTION 'TASK_SANDBOX_EXECUTION_UNVERIFIED' USING ERRCODE='42501';
    END IF;
    updated_op := op||jsonb_build_object('status',p_status,'receipt_ref',p_receipt_ref);
    next_state := jsonb_set(jsonb_set(s.state,'{operations}',
        (SELECT jsonb_agg(CASE WHEN value->>'id'=p_operation_id THEN updated_op ELSE value END)
           FROM jsonb_array_elements(s.state->'operations'))),'{revision}',to_jsonb(s.revision+1));
    SELECT state INTO cp FROM public.conversation_turn_checkpoints WHERE task_id=p_task_id FOR UPDATE;
    IF cp->'_task_execution' IS DISTINCT FROM s.state THEN
        RAISE EXCEPTION 'TASK_EXECUTION_CHECKPOINT_MISMATCH' USING ERRCODE='42501';
    END IF;
    saved := public.save_agent_task_checkpoint(p_task_id,p_execution_token,'after_tool',
        jsonb_set(cp,'{_task_execution}',next_state),s.revision);
    IF saved->>'outcome' IS DISTINCT FROM 'saved' THEN RETURN saved; END IF;
    completed := public.complete_tool_invocation(p_task_id,t.turn_id,op->>'invocation_id',p_execution_token,
        ledger_status,p_result,p_error_message);
    IF completed->>'outcome' IS DISTINCT FROM ledger_status THEN
        RAISE EXCEPTION 'TASK_OPERATION_COMPLETION_CONFLICT' USING ERRCODE='42501';
    END IF;
    RETURN jsonb_build_object('outcome','saved','state',next_state,'operation',updated_op);
END $$;
REVOKE ALL ON FUNCTION public.complete_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.complete_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,jsonb,text) TO everydayai;
RESET ROLE;
