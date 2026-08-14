SET LOCAL ROLE everydayai_owner;
DO $$
DECLARE
    control agent_runtime_media_owner_readiness%ROWTYPE;
    heartbeat agent_runtime_worker_heartbeats%ROWTYPE;
    slot_release_in_flight BOOLEAN:=FALSE;
BEGIN
    SELECT * INTO control
      FROM agent_runtime_media_owner_readiness WHERE singleton FOR UPDATE;
    IF control.singleton IS NULL OR control.projection_owner_ready
       OR (_agent_runtime_media_owner_readiness_v1()->>'ready')::BOOLEAN THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_READINESS_ROLLBACK_NOT_DRAINED'
            USING ERRCODE='55000';
    END IF;
    IF control.projection_worker_id IS NOT NULL THEN
        SELECT * INTO heartbeat FROM agent_runtime_worker_heartbeats
         WHERE process_role='projection'
           AND worker_id=control.projection_worker_id;
        IF heartbeat.process_role IS NULL OR heartbeat.ready
           OR NOT heartbeat.draining OR heartbeat.status_code<>'draining'
           OR heartbeat.release_revision IS DISTINCT FROM control.projection_revision THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_READINESS_ROLLBACK_HEARTBEAT_ACTIVE'
                USING ERRCODE='55000';
        END IF;
    END IF;
    IF to_regclass('public.agent_runtime_media_slot_release_outbox') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (
            SELECT 1 FROM agent_runtime_media_slot_release_outbox
             WHERE status<>''delivered''
        )' INTO slot_release_in_flight;
    END IF;
    IF EXISTS (
        SELECT 1 FROM agent_actions action
         WHERE action.status NOT IN ('completed','failed','rejected','cancelled')
           AND (
               EXISTS (SELECT 1 FROM agent_runtime_media_action_bindings binding
                        WHERE binding.action_id=action.id)
               OR EXISTS (
                   SELECT 1 FROM agent_runtime_prepared_media_action_bindings binding
                    WHERE binding.action_id=action.id
               )
           )
    ) OR EXISTS (
        SELECT 1 FROM agent_projection_outbox outbox
        JOIN agent_runtime_events event ON event.id=outbox.event_id
         WHERE outbox.status<>'delivered' AND (
             EXISTS (SELECT 1 FROM agent_runtime_media_action_bindings binding
                      WHERE binding.action_id IS NOT DISTINCT FROM event.action_id
                         OR binding.run_id IS NOT DISTINCT FROM event.run_id)
             OR EXISTS (
                 SELECT 1 FROM agent_runtime_prepared_media_action_bindings binding
                  WHERE binding.action_id IS NOT DISTINCT FROM event.action_id
                     OR binding.run_id IS NOT DISTINCT FROM event.run_id
             )
         )
    ) OR slot_release_in_flight THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_READINESS_ROLLBACK_IN_FLIGHT'
            USING ERRCODE='55000';
    END IF;
END $$;
CREATE OR REPLACE FUNCTION record_agent_runtime_media_projection_readiness_v1(
    p_worker_id TEXT,p_projection_revision TEXT,p_ready BOOLEAN,
    p_heartbeat_ttl_seconds INTEGER DEFAULT 30
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
BEGIN
    IF session_user<>'everydayai_projection_worker'
       OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'projection' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_SCOPE_REQUIRED'
            USING ERRCODE='42501';
    END IF;
    IF NULLIF(btrim(p_worker_id),'') IS NULL
       OR length(btrim(p_worker_id))>128
       OR NULLIF(btrim(p_projection_revision),'') IS NULL
       OR length(btrim(p_projection_revision))>128
       OR p_ready IS NULL
       OR p_heartbeat_ttl_seconds NOT BETWEEN 5 AND 300 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_PROJECTION_READINESS_INVALID'
            USING ERRCODE='22023';
    END IF;
    UPDATE agent_runtime_media_owner_readiness
       SET projection_owner_ready=p_ready,
           projection_worker_id=btrim(p_worker_id),
           projection_revision=btrim(p_projection_revision),
           projection_heartbeat_at=statement_timestamp(),
           projection_heartbeat_ttl_seconds=p_heartbeat_ttl_seconds,
           state_version=state_version+1,updated_at=clock_timestamp()
     WHERE singleton;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_READINESS_MISSING'
            USING ERRCODE='55000';
    END IF;
    RETURN _agent_runtime_media_owner_readiness_v1();
END;
$$;
REVOKE ALL ON FUNCTION
    record_agent_runtime_media_projection_readiness_v1(TEXT,TEXT,BOOLEAN,INTEGER)
FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
    everydayai_sync,everydayai,everydayai_agent_runtime_worker,
    everydayai_projection_worker,everydayai_authorization_worker,
    everydayai_sandbox_worker,everydayai_runtime_admin;
GRANT EXECUTE ON FUNCTION
    record_agent_runtime_media_projection_readiness_v1(TEXT,TEXT,BOOLEAN,INTEGER)
TO everydayai_projection_worker;
RESET ROLE;
