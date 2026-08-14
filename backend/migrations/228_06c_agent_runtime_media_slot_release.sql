/* 228.06c: durable task-slot release handoff for terminal media projection. */
SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_media_slot_release_outbox (
    release_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_projection_outbox_id UUID NOT NULL UNIQUE
        REFERENCES agent_runtime_media_projection_results(outbox_id) ON DELETE RESTRICT,
    event_id UUID NOT NULL REFERENCES agent_runtime_events(id) ON DELETE RESTRICT,
    task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE RESTRICT,
    task_slot_id TEXT NOT NULL CHECK (length(btrim(task_slot_id)) BETWEEN 1 AND 200),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'action.completed','action.failed','action.rejected','action.cancelled',
        'run.completed','run.failed','run.cancelled'
    )),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','processing','dead','delivered')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    recovery_version BIGINT NOT NULL DEFAULT 0 CHECK (recovery_version >= 0),
    recovery_count INTEGER NOT NULL DEFAULT 0 CHECK (recovery_count >= 0),
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    last_error_code TEXT CHECK (
        last_error_code IS NULL OR length(last_error_code) BETWEEN 1 AND 200
    ),
    delivered_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(task_id,task_slot_id),
    CHECK (
        (status='processing' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (status<>'processing' AND lease_token IS NULL AND lease_expires_at IS NULL)
    ),
    CHECK ((status='delivered')=(delivered_at IS NOT NULL))
);

CREATE TABLE agent_runtime_media_slot_release_recoveries (
    recovery_request_id UUID PRIMARY KEY,
    release_id UUID NOT NULL REFERENCES agent_runtime_media_slot_release_outbox(release_id)
        ON DELETE RESTRICT,
    org_id UUID REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    actor_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    expected_recovery_version BIGINT NOT NULL CHECK (expected_recovery_version >= 0),
    expected_attempt_count INTEGER NOT NULL CHECK (expected_attempt_count >= 8),
    reason TEXT NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 500),
    not_before TIMESTAMPTZ NOT NULL,
    database_request_id TEXT NOT NULL
        CHECK (length(btrim(database_request_id)) BETWEEN 1 AND 128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX idx_agent_runtime_media_slot_release_claim
    ON agent_runtime_media_slot_release_outbox(status,next_attempt_at,created_at);
ALTER TABLE agent_runtime_media_slot_release_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_media_slot_release_recoveries ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_runtime_media_slot_release_outbox_owner_all
    ON agent_runtime_media_slot_release_outbox FOR ALL TO everydayai_owner
    USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY agent_runtime_media_slot_release_recoveries_owner_all
    ON agent_runtime_media_slot_release_recoveries FOR ALL TO everydayai_owner
    USING (TRUE) WITH CHECK (TRUE);
ALTER TABLE agent_runtime_media_slot_release_outbox FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_media_slot_release_recoveries FORCE ROW LEVEL SECURITY;

CREATE FUNCTION _enqueue_agent_runtime_media_slot_release_v1()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    event agent_runtime_events%ROWTYPE;
    task tasks%ROWTYPE;
    resolved_task_id UUID:=NEW.task_id;
    slot_id TEXT;
BEGIN
    SELECT * INTO event FROM agent_runtime_events WHERE id=NEW.event_id;
    IF event.id IS NULL OR event.event_type NOT IN (
        'action.completed','action.failed','action.rejected','action.cancelled',
        'run.completed','run.failed','run.cancelled'
    ) THEN
        RETURN NEW;
    END IF;
    IF resolved_task_id IS NULL AND event.action_id IS NOT NULL THEN
        SELECT task_id INTO resolved_task_id FROM (
            SELECT binding.task_id, binding.created_at
              FROM agent_runtime_media_action_bindings binding
             WHERE binding.action_id=event.action_id
            UNION ALL
            SELECT binding.task_id, binding.created_at
              FROM agent_runtime_prepared_media_action_bindings binding
             WHERE binding.action_id=event.action_id
        ) candidate ORDER BY created_at DESC,task_id DESC LIMIT 1;
    END IF;
    IF resolved_task_id IS NULL AND event.run_id IS NOT NULL THEN
        SELECT task_id INTO resolved_task_id FROM (
            SELECT binding.task_id, binding.created_at
              FROM agent_runtime_media_action_bindings binding
             WHERE binding.run_id=event.run_id
            UNION ALL
            SELECT binding.task_id, binding.created_at
              FROM agent_runtime_prepared_media_action_bindings binding
             WHERE binding.run_id=event.run_id
        ) candidate ORDER BY created_at DESC,task_id DESC LIMIT 1;
    END IF;
    SELECT * INTO task FROM tasks WHERE id=resolved_task_id;
    slot_id:=NULLIF(btrim(task.request_params->>'_task_slot_id'),'');
    IF task.id IS NULL OR slot_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF task.user_id IS DISTINCT FROM NEW.user_id
       OR task.org_id IS DISTINCT FROM NEW.org_id
       OR task.conversation_id IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_SLOT_RELEASE_SCOPE_INVALID'
            USING ERRCODE='42501';
    END IF;
    INSERT INTO agent_runtime_media_slot_release_outbox(
        source_projection_outbox_id,event_id,task_id,org_id,user_id,
        conversation_id,task_slot_id,event_type
    ) VALUES(
        NEW.outbox_id,NEW.event_id,task.id,task.org_id,task.user_id,
        task.conversation_id,slot_id,event.event_type
    ) ON CONFLICT (task_id,task_slot_id) DO NOTHING;
    RETURN NEW;
END;
$$;

CREATE TRIGGER agent_runtime_media_slot_release_enqueue_v1
AFTER INSERT ON agent_runtime_media_projection_results
FOR EACH ROW EXECUTE FUNCTION _enqueue_agent_runtime_media_slot_release_v1();

CREATE FUNCTION claim_agent_runtime_media_slot_release_v1(
    p_batch_size INTEGER DEFAULT 50,p_lease_seconds INTEGER DEFAULT 60
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE rows JSONB;
BEGIN
    PERFORM _agent_runtime_media_projection_scope_v1();
    IF p_batch_size NOT BETWEEN 1 AND 100 OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_SLOT_RELEASE_CLAIM_INVALID'
            USING ERRCODE='22023';
    END IF;
    WITH eligible AS (
        SELECT release_id FROM agent_runtime_media_slot_release_outbox
         WHERE next_attempt_at<=clock_timestamp()
           AND (status='pending' OR (
               status='processing' AND lease_expires_at<=clock_timestamp()
           ))
         ORDER BY next_attempt_at,created_at,release_id
         FOR UPDATE SKIP LOCKED LIMIT p_batch_size
    ), claimed AS (
        UPDATE agent_runtime_media_slot_release_outbox outbox
           SET status='processing',attempt_count=attempt_count+1,
               lease_token=gen_random_uuid(),
               lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
               updated_at=clock_timestamp()
          FROM eligible WHERE outbox.release_id=eligible.release_id
        RETURNING outbox.*
    )
    SELECT COALESCE(jsonb_agg(to_jsonb(claimed)),'[]'::JSONB)
      INTO rows FROM claimed;
    RETURN rows;
END;
$$;

CREATE FUNCTION ack_agent_runtime_media_slot_release_v1(
    p_release_id UUID,p_lease_token UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE release agent_runtime_media_slot_release_outbox%ROWTYPE;
BEGIN
    PERFORM _agent_runtime_media_projection_scope_v1();
    SELECT * INTO release FROM agent_runtime_media_slot_release_outbox
     WHERE release_id=p_release_id FOR UPDATE;
    IF release.release_id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF release.status='delivered' THEN RETURN jsonb_build_object('outcome','already_acked'); END IF;
    IF release.status<>'processing' OR release.lease_token IS DISTINCT FROM p_lease_token
       OR release.lease_expires_at<=clock_timestamp() THEN
        RETURN jsonb_build_object('outcome','ownership_lost');
    END IF;
    UPDATE agent_runtime_media_slot_release_outbox
       SET status='delivered',lease_token=NULL,lease_expires_at=NULL,
           delivered_at=clock_timestamp(),last_error_code=NULL,
           updated_at=clock_timestamp()
     WHERE release_id=p_release_id;
    RETURN jsonb_build_object('outcome','acked','release_id',p_release_id);
END;
$$;

CREATE FUNCTION fail_agent_runtime_media_slot_release_v1(
    p_release_id UUID,p_lease_token UUID,p_error_code TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE release agent_runtime_media_slot_release_outbox%ROWTYPE; next_status TEXT;
BEGIN
    PERFORM _agent_runtime_media_projection_scope_v1();
    SELECT * INTO release FROM agent_runtime_media_slot_release_outbox
     WHERE release_id=p_release_id FOR UPDATE;
    IF release.release_id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF release.status<>'processing' OR release.lease_token IS DISTINCT FROM p_lease_token
       OR release.lease_expires_at<=clock_timestamp() THEN
        RETURN jsonb_build_object('outcome','ownership_lost');
    END IF;
    next_status:=CASE WHEN release.attempt_count>=8 THEN 'dead' ELSE 'pending' END;
    UPDATE agent_runtime_media_slot_release_outbox
       SET status=next_status,lease_token=NULL,lease_expires_at=NULL,
           next_attempt_at=clock_timestamp()+make_interval(
               secs=>LEAST(300,5*(2^attempt_count))
           ),last_error_code=left(COALESCE(NULLIF(btrim(p_error_code),''),'slot_release_failed'),200),
           updated_at=clock_timestamp()
     WHERE release_id=p_release_id;
    RETURN jsonb_build_object('outcome','failed','status',next_status);
END;
$$;

CREATE FUNCTION requeue_agent_runtime_media_slot_release_v1(
    p_release_id UUID,p_expected_recovery_version BIGINT,
    p_expected_attempt_count INTEGER,p_recovery_request_id UUID,
    p_reason TEXT,p_not_before TIMESTAMPTZ
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE
    release agent_runtime_media_slot_release_outbox%ROWTYPE;
    prior agent_runtime_media_slot_release_recoveries%ROWTYPE;
    actor UUID:=tenant_actor_user_id();
    request_id TEXT:=current_setting('app.request_id',TRUE);
BEGIN
    IF session_user<>'everydayai_runtime_admin'
       OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'runtime_admin'
       OR NOT tenant_platform_admin() OR actor IS NULL
       OR p_expected_recovery_version<0 OR p_expected_attempt_count<8
       OR p_recovery_request_id IS NULL OR p_not_before IS NULL
       OR p_not_before<clock_timestamp()-interval '1 second'
       OR p_not_before>clock_timestamp()+interval '24 hours'
       OR p_reason IS NULL OR p_reason<>btrim(p_reason)
       OR length(p_reason) NOT BETWEEN 1 AND 500
       OR NULLIF(btrim(request_id),'') IS NULL THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_SLOT_RELEASE_RECOVERY_REQUIRED'
            USING ERRCODE='42501';
    END IF;
    SELECT * INTO prior FROM agent_runtime_media_slot_release_recoveries
     WHERE recovery_request_id=p_recovery_request_id;
    IF FOUND THEN
        IF prior.release_id IS DISTINCT FROM p_release_id
           OR prior.actor_user_id IS DISTINCT FROM actor
           OR prior.reason IS DISTINCT FROM p_reason
           OR prior.not_before IS DISTINCT FROM p_not_before THEN
            RETURN jsonb_build_object('outcome','recovery_request_conflict');
        END IF;
        RETURN jsonb_build_object('outcome','already_requeued');
    END IF;
    SELECT * INTO release FROM agent_runtime_media_slot_release_outbox
     WHERE release_id=p_release_id FOR UPDATE;
    IF release.release_id IS NULL THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF release.status<>'dead' THEN RETURN jsonb_build_object('outcome','not_dead'); END IF;
    IF release.recovery_version IS DISTINCT FROM p_expected_recovery_version THEN
        RETURN jsonb_build_object('outcome','stale_version');
    END IF;
    IF release.attempt_count IS DISTINCT FROM p_expected_attempt_count THEN
        RETURN jsonb_build_object('outcome','attempt_count_conflict');
    END IF;
    IF release.org_id IS DISTINCT FROM tenant_org_id() THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_SLOT_RELEASE_RECOVERY_SCOPE_INVALID'
            USING ERRCODE='42501';
    END IF;
    INSERT INTO agent_runtime_media_slot_release_recoveries(
        recovery_request_id,release_id,org_id,user_id,actor_user_id,
        expected_recovery_version,expected_attempt_count,reason,not_before,
        database_request_id
    ) VALUES(
        p_recovery_request_id,release.release_id,release.org_id,release.user_id,
        actor,p_expected_recovery_version,p_expected_attempt_count,p_reason,
        p_not_before,btrim(request_id)
    );
    UPDATE agent_runtime_media_slot_release_outbox
       SET status='pending',next_attempt_at=p_not_before,
           recovery_version=recovery_version+1,recovery_count=recovery_count+1,
           updated_at=clock_timestamp()
     WHERE release_id=p_release_id;
    RETURN jsonb_build_object(
        'outcome','requeued','recovery_version',p_expected_recovery_version+1
    );
END;
$$;

REVOKE ALL ON TABLE agent_runtime_media_slot_release_outbox,
    agent_runtime_media_slot_release_recoveries
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;
REVOKE ALL ON FUNCTION _enqueue_agent_runtime_media_slot_release_v1(),
    claim_agent_runtime_media_slot_release_v1(INTEGER,INTEGER),
    ack_agent_runtime_media_slot_release_v1(UUID,UUID),
    fail_agent_runtime_media_slot_release_v1(UUID,UUID,TEXT),
    requeue_agent_runtime_media_slot_release_v1(UUID,BIGINT,INTEGER,UUID,TEXT,TIMESTAMPTZ)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION claim_agent_runtime_media_slot_release_v1(INTEGER,INTEGER),
    ack_agent_runtime_media_slot_release_v1(UUID,UUID),
    fail_agent_runtime_media_slot_release_v1(UUID,UUID,TEXT)
TO everydayai_projection_worker;
GRANT EXECUTE ON FUNCTION
    requeue_agent_runtime_media_slot_release_v1(UUID,BIGINT,INTEGER,UUID,TEXT,TIMESTAMPTZ)
TO everydayai_runtime_admin;
RESET ROLE;
