/* 228.06a: fenced terminal compensation for deterministic media poison events. */
SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_media_projection_isolations (
    isolation_request_id UUID PRIMARY KEY,
    outbox_id UUID NOT NULL UNIQUE
        REFERENCES agent_projection_outbox(id) ON DELETE RESTRICT,
    event_id UUID NOT NULL REFERENCES agent_runtime_events(id) ON DELETE RESTRICT,
    session_id UUID NOT NULL REFERENCES agent_runtime_sessions(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    actor_user_id UUID REFERENCES users(id) ON DELETE RESTRICT,
    worker_id TEXT,
    lease_token UUID,
    expected_recovery_version BIGINT CHECK (expected_recovery_version >= 0),
    expected_attempt_count INTEGER CHECK (expected_attempt_count >= 8),
    error_code TEXT NOT NULL CHECK (length(btrim(error_code)) BETWEEN 1 AND 200),
    reason TEXT NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 500),
    database_request_id TEXT NOT NULL
        CHECK (length(btrim(database_request_id)) BETWEEN 1 AND 128),
    result_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((actor_user_id IS NULL) <> (worker_id IS NULL)),
    CHECK (worker_id IS NULL OR length(btrim(worker_id)) BETWEEN 1 AND 128)
);
ALTER TABLE agent_runtime_media_projection_isolations ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_media_projection_isolations_owner_all
    ON agent_runtime_media_projection_isolations FOR ALL TO everydayai_owner
    USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE agent_runtime_media_projection_isolations FORCE ROW LEVEL SECURITY;

CREATE FUNCTION _agent_runtime_media_isolate_terminal_v1(
    p_outbox_id UUID, p_error_code TEXT, p_isolation_request_id UUID,
    p_actor_user_id UUID, p_worker_id TEXT, p_lease_token UUID,
    p_expected_recovery_version BIGINT, p_expected_attempt_count INTEGER,
    p_reason TEXT, p_database_request_id TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    v_outbox agent_projection_outbox%ROWTYPE;
    v_event agent_runtime_events%ROWTYPE;
    v_checkpoint agent_runtime_media_projection_checkpoints%ROWTYPE;
    v_binding agent_runtime_media_action_bindings%ROWTYPE;
    v_prepared agent_runtime_prepared_media_action_bindings%ROWTYPE;
    v_run agent_runs%ROWTYPE;
    v_command agent_session_commands%ROWTYPE;
    v_task tasks%ROWTYPE;
    v_message messages%ROWTYPE;
    v_slot JSONB;
    v_content JSONB;
    v_result JSONB;
    v_refund JSONB;
BEGIN
    SELECT to_jsonb(audit) INTO v_result
      FROM agent_runtime_media_projection_isolations audit
     WHERE isolation_request_id=p_isolation_request_id;
    IF FOUND THEN
        IF v_result->>'outbox_id'=p_outbox_id::TEXT THEN
            RETURN jsonb_build_object('outcome','already_isolated','audit',v_result);
        END IF;
        RETURN jsonb_build_object('outcome','isolation_request_conflict');
    END IF;
    SELECT * INTO v_outbox FROM agent_projection_outbox
     WHERE id=p_outbox_id FOR UPDATE;
    IF v_outbox.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    SELECT * INTO v_event FROM agent_runtime_events WHERE id=v_outbox.event_id;
    INSERT INTO agent_runtime_media_projection_checkpoints(
        session_id,projection_kind
    ) VALUES(v_outbox.session_id,v_outbox.projection_kind)
    ON CONFLICT DO NOTHING;
    SELECT * INTO v_checkpoint FROM agent_runtime_media_projection_checkpoints
     WHERE session_id=v_outbox.session_id
       AND projection_kind=v_outbox.projection_kind FOR UPDATE;
    IF v_event.id IS NULL OR v_checkpoint.session_id IS NULL
       OR v_event.session_id IS DISTINCT FROM v_outbox.session_id
       OR v_event.org_id IS DISTINCT FROM v_outbox.org_id
       OR v_event.user_id IS DISTINCT FROM v_outbox.user_id THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ISOLATION_SCOPE_INVALID'
            USING ERRCODE='42501';
    END IF;
    IF v_event.event_type NOT IN (
        'action.completed','action.failed','action.rejected','action.cancelled',
        'run.completed','run.failed','run.cancelled'
    ) THEN RETURN jsonb_build_object('outcome','not_terminal'); END IF;
    IF EXISTS (
        SELECT 1 FROM agent_projection_outbox earlier
        JOIN agent_runtime_events earlier_event ON earlier_event.id=earlier.event_id
         WHERE earlier.id<>v_outbox.id
           AND earlier.session_id=v_outbox.session_id
           AND earlier.projection_kind=v_outbox.projection_kind
           AND earlier_event.sequence<v_event.sequence
           AND earlier_event.sequence>v_checkpoint.through_sequence
           AND earlier.status<>'delivered'
    ) THEN RETURN jsonb_build_object('outcome','projection_gap'); END IF;

    IF v_event.action_id IS NOT NULL THEN
        SELECT * INTO v_binding FROM agent_runtime_media_action_bindings
         WHERE action_id=v_event.action_id;
        SELECT * INTO v_prepared FROM agent_runtime_prepared_media_action_bindings
         WHERE action_id=v_event.action_id;
        IF v_binding.action_id IS NOT NULL THEN
            SELECT * INTO v_task FROM tasks WHERE id=v_binding.task_id FOR UPDATE;
            SELECT * INTO v_binding FROM agent_runtime_media_action_bindings
             WHERE action_id=v_event.action_id FOR UPDATE;
            SELECT * INTO v_message FROM messages
             WHERE id=v_binding.output_message_id FOR UPDATE;
            IF v_task.id IS NULL OR v_message.id IS NULL
               OR v_task.org_id IS DISTINCT FROM v_binding.org_id
               OR v_task.user_id IS DISTINCT FROM v_binding.user_id
               OR v_task.assistant_message_id IS DISTINCT FROM v_message.id THEN
                RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ISOLATION_TASK_INVALID'
                    USING ERRCODE='42501';
            END IF;
            IF v_binding.credit_state='pending' THEN
                SELECT atomic_refund_credits(v_binding.credit_transaction_id)
                  INTO v_refund;
                IF COALESCE((v_refund->>'refunded')::BOOLEAN,FALSE) IS NOT TRUE THEN
                    RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ISOLATION_REFUND_FAILED'
                        USING ERRCODE='55000';
                END IF;
                UPDATE agent_runtime_media_action_bindings
                   SET credit_state='refunded',projection_revision=v_event.sequence,
                       state_version=state_version+1,updated_at=clock_timestamp()
                 WHERE action_id=v_event.action_id;
            END IF;
            v_content:=jsonb_build_object(
                'type','image','url',NULL,'failed',TRUE,
                'error_code',p_error_code,'error',p_error_code,'isolated',TRUE
            );
            UPDATE tasks SET status='failed',credits_locked=0,credits_used=0,
                result=v_content,error_message=p_error_code,
                completed_at=clock_timestamp() WHERE id=v_task.id;
            v_slot:=_agent_runtime_media_slot_update_v1(
                v_message.id,v_binding.slot_id,v_binding.action_index,'failed',
                v_event.sequence,v_content
            );
        ELSIF v_prepared.action_id IS NOT NULL THEN
            SELECT * INTO v_task FROM tasks WHERE id=v_prepared.task_id FOR UPDATE;
            SELECT * INTO v_prepared
              FROM agent_runtime_prepared_media_action_bindings
             WHERE action_id=v_event.action_id FOR UPDATE;
            SELECT * INTO v_message FROM messages
             WHERE id=v_prepared.output_message_id FOR UPDATE;
            IF v_task.id IS NULL OR v_message.id IS NULL
               OR v_task.org_id IS DISTINCT FROM v_prepared.org_id
               OR v_task.user_id IS DISTINCT FROM v_prepared.user_id
               OR v_task.assistant_message_id IS DISTINCT FROM v_message.id THEN
                RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ISOLATION_TASK_INVALID'
                    USING ERRCODE='42501';
            END IF;
            IF v_prepared.credit_state='pending' THEN
                SELECT atomic_refund_credits(v_prepared.credit_transaction_id)
                  INTO v_refund;
                IF COALESCE((v_refund->>'refunded')::BOOLEAN,FALSE) IS NOT TRUE THEN
                    RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ISOLATION_REFUND_FAILED'
                        USING ERRCODE='55000';
                END IF;
            END IF;
            UPDATE agent_runtime_prepared_media_action_bindings
               SET credit_state='refunded',projection_revision=v_event.sequence,
                   state_version=state_version+1,updated_at=clock_timestamp()
             WHERE action_id=v_event.action_id;
            v_content:=jsonb_build_object(
                'type',v_prepared.media_kind,'url',NULL,'failed',TRUE,
                'error_code',p_error_code,'error',p_error_code,'isolated',TRUE
            );
            UPDATE tasks SET status='failed',credits_locked=0,credits_used=0,
                result=v_content,error_message=p_error_code,
                completed_at=clock_timestamp() WHERE id=v_task.id;
            UPDATE messages SET content=jsonb_build_array(v_content)::TEXT,
                status='failed' WHERE id=v_message.id;
        ELSE
            RETURN jsonb_build_object('outcome','not_media');
        END IF;
    ELSE
        SELECT * INTO v_run FROM agent_runs WHERE id=v_event.run_id;
        SELECT * INTO v_command FROM agent_session_commands WHERE id=v_run.command_id;
        SELECT * INTO v_task FROM tasks
         WHERE id=NULLIF(v_command.payload->>'task_id','')::UUID FOR UPDATE;
        PERFORM 1 FROM agent_runtime_media_action_bindings binding
         WHERE binding.run_id=v_run.id FOR UPDATE;
        PERFORM 1 FROM agent_runtime_prepared_media_action_bindings binding
         WHERE binding.run_id=v_run.id FOR UPDATE;
        SELECT * INTO v_message FROM messages
         WHERE id=NULLIF(v_command.payload->>'output_message_id','')::UUID FOR UPDATE;
        IF v_run.id IS NULL OR v_task.id IS NULL OR v_message.id IS NULL
           OR v_task.org_id IS DISTINCT FROM v_run.org_id
           OR v_task.user_id IS DISTINCT FROM v_run.user_id
           OR v_task.assistant_message_id IS DISTINCT FROM v_message.id THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ISOLATION_RUN_INVALID'
                USING ERRCODE='42501';
        END IF;
        UPDATE tasks SET status='failed',credits_locked=0,
            error_message=p_error_code,completed_at=clock_timestamp()
         WHERE id=v_task.id;
        UPDATE messages SET status='failed' WHERE id=v_message.id;
    END IF;

    v_result:=jsonb_build_object(
        'isolated',TRUE,'error_code',p_error_code,'event_type',v_event.event_type,
        'task_id',v_task.id,'message_id',v_message.id,'slot',v_slot
    );
    INSERT INTO agent_runtime_media_projection_results(
        outbox_id,event_id,session_id,org_id,user_id,projection_kind,
        event_sequence,projection_action,action_id,message_id,task_id,
        slot_id,slot_index,slot_status,slot_revision,content_part
    ) VALUES(
        v_outbox.id,v_event.id,v_event.session_id,v_outbox.org_id,v_outbox.user_id,
        v_outbox.projection_kind,v_event.sequence,'checkpoint_only',v_event.action_id,
        v_message.id,v_task.id,
        CASE WHEN v_slot IS NULL THEN NULL ELSE (v_slot->>'slot_id')::UUID END,
        CASE WHEN v_slot IS NULL THEN NULL ELSE (v_slot->>'slot_index')::INTEGER END,
        CASE WHEN v_slot IS NULL THEN NULL ELSE 'failed' END,
        CASE WHEN v_slot IS NULL THEN NULL ELSE v_event.sequence END,v_result
    );
    UPDATE agent_runtime_media_projection_checkpoints
       SET through_sequence=v_event.sequence,last_event_id=v_event.id,
           state_version=state_version+1,updated_at=clock_timestamp()
     WHERE session_id=v_event.session_id
       AND projection_kind=v_outbox.projection_kind;
    UPDATE agent_projection_outbox SET status='delivered',
        checkpoint=jsonb_build_object(
            'through_sequence',v_event.sequence,'isolated',TRUE,
            'isolation_request_id',p_isolation_request_id
        ),lease_token=NULL,lease_expires_at=NULL,
        delivered_at=clock_timestamp(),updated_at=clock_timestamp()
     WHERE id=v_outbox.id;
    INSERT INTO agent_runtime_media_projection_isolations(
        isolation_request_id,outbox_id,event_id,session_id,org_id,user_id,
        actor_user_id,worker_id,lease_token,expected_recovery_version,
        expected_attempt_count,error_code,reason,database_request_id,result_payload
    ) VALUES(
        p_isolation_request_id,v_outbox.id,v_event.id,v_event.session_id,
        v_outbox.org_id,v_outbox.user_id,p_actor_user_id,p_worker_id,p_lease_token,
        p_expected_recovery_version,p_expected_attempt_count,p_error_code,p_reason,
        p_database_request_id,v_result
    );
    RETURN jsonb_build_object('outcome','isolated','result',v_result);
END;
$$;

CREATE FUNCTION isolate_agent_runtime_media_projection_v1(
    p_outbox_id UUID,p_lease_token UUID,p_error_code TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE v_outbox agent_projection_outbox%ROWTYPE;
worker TEXT:=current_setting('app.request_id',TRUE);
BEGIN
    PERFORM _agent_runtime_media_projection_scope_v1();
    IF NULLIF(btrim(worker),'') IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ISOLATION_WORKER_REQUIRED'
            USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_outbox FROM agent_projection_outbox WHERE id=p_outbox_id;
    IF v_outbox.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF v_outbox.status<>'processing'
       OR v_outbox.lease_token IS DISTINCT FROM p_lease_token
       OR v_outbox.lease_expires_at<=clock_timestamp() THEN
        RETURN jsonb_build_object('outcome','ownership_lost');
    END IF;
    RETURN _agent_runtime_media_isolate_terminal_v1(
        p_outbox_id,left(btrim(p_error_code),200),gen_random_uuid(),NULL,
        btrim(worker),p_lease_token,NULL,NULL,'deterministic_projection_failure',
        btrim(worker)
    );
END;
$$;

CREATE FUNCTION isolate_dead_agent_runtime_media_projection_v1(
    p_outbox_id UUID,p_expected_recovery_version BIGINT,
    p_expected_attempt_count INTEGER,p_isolation_request_id UUID,p_reason TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE v_outbox agent_projection_outbox%ROWTYPE;
actor UUID:=tenant_actor_user_id(); request TEXT:=current_setting('app.request_id',TRUE);
BEGIN
    IF session_user<>'everydayai_runtime_admin'
       OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'runtime_admin'
       OR NOT tenant_platform_admin() OR actor IS NULL
       OR p_isolation_request_id IS NULL OR p_expected_attempt_count<8
       OR p_reason IS NULL OR p_reason<>btrim(p_reason)
       OR length(p_reason) NOT BETWEEN 1 AND 500
       OR NULLIF(btrim(request),'') IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ISOLATION_ADMIN_REQUIRED'
            USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_outbox FROM agent_projection_outbox WHERE id=p_outbox_id;
    IF v_outbox.id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF v_outbox.status<>'dead' THEN RETURN jsonb_build_object('outcome','not_dead'); END IF;
    IF v_outbox.recovery_version IS DISTINCT FROM p_expected_recovery_version THEN
        RETURN jsonb_build_object('outcome','stale_version');
    END IF;
    IF v_outbox.attempt_count IS DISTINCT FROM p_expected_attempt_count THEN
        RETURN jsonb_build_object('outcome','attempt_count_conflict');
    END IF;
    IF v_outbox.org_id IS DISTINCT FROM tenant_org_id() THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ISOLATION_SCOPE_INVALID'
            USING ERRCODE='42501';
    END IF;
    RETURN _agent_runtime_media_isolate_terminal_v1(
        p_outbox_id,COALESCE(v_outbox.last_error_code,'projection_dead'),
        p_isolation_request_id,actor,NULL,NULL,p_expected_recovery_version,
        p_expected_attempt_count,p_reason,btrim(request)
    );
END;
$$;

REVOKE ALL ON TABLE agent_runtime_media_projection_isolations
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;
REVOKE ALL ON FUNCTION
    _agent_runtime_media_isolate_terminal_v1(UUID,TEXT,UUID,UUID,TEXT,UUID,BIGINT,INTEGER,TEXT,TEXT),
    isolate_agent_runtime_media_projection_v1(UUID,UUID,TEXT),
    isolate_dead_agent_runtime_media_projection_v1(UUID,BIGINT,INTEGER,UUID,TEXT)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION isolate_agent_runtime_media_projection_v1(UUID,UUID,TEXT)
TO everydayai_projection_worker;
GRANT EXECUTE ON FUNCTION
    isolate_dead_agent_runtime_media_projection_v1(UUID,BIGINT,INTEGER,UUID,TEXT)
TO everydayai_runtime_admin;
RESET ROLE;
