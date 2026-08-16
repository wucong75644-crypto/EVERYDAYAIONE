-- Audited, atomic production activation for the frozen image-only v13 release.
SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION get_agent_runtime_media_admin_context_v1()
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE selected_user UUID; selected_org UUID;
BEGIN
  IF session_user<>'everydayai_runtime_admin'
     OR current_setting('app.access_kind',true)<>'runtime_admin' THEN
    RAISE EXCEPTION 'RUNTIME_ADMIN_REQUIRED' USING ERRCODE='42501';
  END IF;
  SELECT u.id,COALESCE(u.current_org_id,member.org_id)
    INTO selected_user,selected_org
    FROM users u
    LEFT JOIN LATERAL (
      SELECT om.org_id FROM org_members om
       WHERE om.user_id=u.id AND om.status='active'
       ORDER BY om.org_id LIMIT 1
    ) member ON TRUE
    JOIN organizations o ON o.id=COALESCE(u.current_org_id,member.org_id)
   WHERE u.role::text='super_admin' AND u.status::text='active'
     AND o.status::text='active'
   ORDER BY u.id LIMIT 1;
  IF selected_user IS NULL OR selected_org IS NULL THEN
    RAISE EXCEPTION 'RUNTIME_ADMIN_CONTEXT_MISSING' USING ERRCODE='55000';
  END IF;
  RETURN jsonb_build_object(
    'actor_user_id',selected_user,'org_id',selected_org,
    'readiness',_agent_runtime_media_owner_readiness_v1(),
    'image_ingress_enabled',COALESCE((SELECT enabled_for_new_ingress
      FROM agent_runtime_definition_facts
     WHERE agent_key='everydayai-default' AND definition_revision='v13'),FALSE));
END $$;

CREATE FUNCTION set_agent_runtime_media_production_state_v1(
 p_request_id UUID,p_actor_user_id UUID,p_org_id UUID,
 p_expected_state_version BIGINT,p_image_ingress_enabled BOOLEAN,
 p_runtime_enabled BOOLEAN,p_provider_probe_passed BOOLEAN,
 p_production_ready BOOLEAN,p_reason TEXT
) RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
DECLARE prior agent_runtime_admin_audit%ROWTYPE;
  control agent_runtime_media_owner_readiness%ROWTYPE;
  actor UUID:=p_actor_user_id; org UUID:=p_org_id; result JSONB;
  payload JSONB;
BEGIN
  IF session_user<>'everydayai_runtime_admin'
     OR current_setting('app.access_kind',true)<>'runtime_admin'
     OR actor IS NULL OR org IS NULL THEN
    RAISE EXCEPTION 'RUNTIME_ADMIN_REQUIRED' USING ERRCODE='42501';
  END IF;
  PERFORM set_config('app.actor_user_id',actor::text,true);
  PERFORM set_config('app.org_id',org::text,true);
  IF NOT tenant_platform_admin() THEN
    RAISE EXCEPTION 'RUNTIME_ADMIN_REQUIRED' USING ERRCODE='42501';
  END IF;
  IF length(btrim(p_reason)) NOT BETWEEN 1 AND 500
     OR p_expected_state_version<0
     OR (p_production_ready AND (NOT p_runtime_enabled OR NOT p_provider_probe_passed))
     OR (p_image_ingress_enabled AND NOT p_production_ready) THEN
    RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_ACTIVATION_INVALID' USING ERRCODE='22023';
  END IF;
  payload:=jsonb_build_object(
    'actor_user_id',actor,'org_id',org,
    'expected_state_version',p_expected_state_version,
    'image_ingress_enabled',p_image_ingress_enabled,
    'runtime_enabled',p_runtime_enabled,
    'provider_probe_passed',p_provider_probe_passed,
    'production_ready',p_production_ready);
  SELECT * INTO prior FROM agent_runtime_admin_audit WHERE request_id=p_request_id;
  IF FOUND THEN
    IF prior.actor_user_id<>actor OR prior.org_id<>org
       OR prior.operation<>'set_media_production_state'
       OR prior.reason<>btrim(p_reason) OR prior.request_payload<>payload THEN
      RETURN jsonb_build_object('outcome','idempotency_conflict');
    END IF;
    RETURN prior.result_payload||jsonb_build_object('outcome','already_applied');
  END IF;
  SELECT * INTO control FROM agent_runtime_media_owner_readiness
   WHERE singleton FOR UPDATE;
  IF control.singleton IS NULL THEN
    RAISE EXCEPTION 'AGENT_RUNTIME_MEDIA_READINESS_MISSING' USING ERRCODE='55000';
  END IF;
  IF control.state_version<>p_expected_state_version THEN
    RETURN jsonb_build_object('outcome','stale_version','state_version',control.state_version);
  END IF;
  PERFORM set_agent_runtime_definition_ingress_enabled(
    'everydayai-default','v13',p_image_ingress_enabled);
  UPDATE agent_runtime_media_owner_readiness SET
    runtime_enabled=p_runtime_enabled,
    provider_probe_passed=p_provider_probe_passed,
    production_ready=p_production_ready,
    state_version=state_version+1,updated_at=clock_timestamp()
   WHERE singleton;
  result:=jsonb_build_object(
    'outcome','applied','image_ingress_enabled',p_image_ingress_enabled,
    'readiness',_agent_runtime_media_owner_readiness_v1());
  INSERT INTO agent_runtime_admin_audit(
    request_id,actor_user_id,org_id,operation,reason,request_payload,result_payload)
  VALUES(p_request_id,actor,org,'set_media_production_state',btrim(p_reason),payload,result);
  RETURN result;
END $$;

REVOKE ALL ON FUNCTION get_agent_runtime_media_admin_context_v1(),
 set_agent_runtime_media_production_state_v1(UUID,UUID,UUID,BIGINT,BOOLEAN,BOOLEAN,BOOLEAN,BOOLEAN,TEXT)
 FROM PUBLIC,everydayai_runtime,everydayai_worker,everydayai;
GRANT EXECUTE ON FUNCTION get_agent_runtime_media_admin_context_v1(),
 set_agent_runtime_media_production_state_v1(UUID,UUID,UUID,BIGINT,BOOLEAN,BOOLEAN,BOOLEAN,BOOLEAN,TEXT)
 TO everydayai_runtime_admin;

RESET ROLE;
