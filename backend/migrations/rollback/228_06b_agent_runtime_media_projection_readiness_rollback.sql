SET LOCAL ROLE everydayai_owner;
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
