-- Formatting recovery has no provider attempt and cannot rewrite a sealed receipt.
SET LOCAL ROLE everydayai_owner;
CREATE FUNCTION public.lock_ecom_format_plan(p_plan_id UUID)
RETURNS public.ecom_image_plans LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; d public.detail_projects%ROWTYPE; project UUID; root UUID;
BEGIN
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime' THEN
  RAISE EXCEPTION 'ECOM_PLAN_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT project_id,COALESCE(root_plan_id,id) INTO project,root FROM public.ecom_image_plans WHERE id=p_plan_id;
 SELECT * INTO d FROM public.detail_projects WHERE id=project FOR UPDATE;
 PERFORM 1 FROM public.ecom_image_plans WHERE id=root FOR UPDATE;
 SELECT * INTO p FROM public.ecom_image_plans WHERE id=p_plan_id FOR UPDATE;
 IF p.id IS NULL OR d.id IS NULL OR p.source_kind IS DISTINCT FROM 'detail_project'
  OR p.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
  OR p.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
  OR d.user_id IS DISTINCT FROM p.user_id OR d.org_id IS DISTINCT FROM p.org_id
  OR d.status='archived' OR d.run_state->>'run_id' IS DISTINCT FROM p.generation_run_id::text
  OR d.run_state->>'delivery_stopped'='true' THEN
  RAISE EXCEPTION 'ECOM_PLAN_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 RETURN p;
END $$;

CREATE FUNCTION public.persist_ecom_plan_format_state(p_plan_id UUID,p_lease_token UUID,p_stage INTEGER,
 p_expected_draft JSONB,p_draft JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE;
BEGIN
 p=public.lock_ecom_format_plan(p_plan_id);
 IF p_stage NOT BETWEEN 1 AND 3 OR p_stage IS NULL OR jsonb_typeof(p_draft) IS DISTINCT FROM 'object'
  OR p_draft->>'format_version' IS DISTINCT FROM 'ecom-format.v1' THEN
  RAISE EXCEPTION 'ECOM_PLAN_DRAFT_INVALID' USING ERRCODE='22023'; END IF;
 IF p.status IS DISTINCT FROM 'planning' OR p.current_stage IS DISTINCT FROM p_stage
  OR p.lease_token IS DISTINCT FROM p_lease_token OR p_lease_token IS NULL
  OR p.lease_expires_at IS NULL OR p.lease_expires_at<=NOW() THEN
  RAISE EXCEPTION 'ECOM_PLAN_LEASE_LOST' USING ERRCODE='55000'; END IF;
 IF p.stage_drafts->p_stage::text IS DISTINCT FROM p_expected_draft THEN
  RAISE EXCEPTION 'ECOM_PLAN_DRAFT_CONFLICT' USING ERRCODE='22023'; END IF;
 UPDATE public.ecom_image_plans SET stage_drafts=jsonb_set(stage_drafts,ARRAY[p_stage::text],p_draft),
  updated_at=NOW() WHERE id=p.id;
 RETURN jsonb_build_object('outcome','saved','draft_fingerprint',md5(p_draft::text));
END $$;

CREATE FUNCTION public.complete_ecom_plan_local_draft(p_plan_id UUID,p_lease_token UUID,p_stage INTEGER,
 p_expected_draft JSONB,p_validator_version TEXT,p_output JSONB,p_status TEXT,
 p_items JSONB DEFAULT NULL,p_reviews JSONB DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; r public.ecom_image_plans%ROWTYPE;
 fingerprint TEXT; prior JSONB; saved JSONB; credits INTEGER:=0;
BEGIN
 p=public.lock_ecom_format_plan(p_plan_id);
 SELECT * INTO r FROM public.ecom_image_plans WHERE id=COALESCE(p.root_plan_id,p.id);
 IF p_stage IS NULL OR p_stage NOT BETWEEN 1 AND 3 OR p_output IS NULL
  OR p_validator_version IS DISTINCT FROM 'ecom-format.v1'
  OR jsonb_typeof(p_expected_draft) IS DISTINCT FROM 'object'
  OR p_status IS NULL OR NOT ((p_stage<3 AND p_status IN ('planning','needs_input','insufficient'))
    OR (p_stage=3 AND p_status IN ('ready','needs_input')))
  OR (p_status='ready' AND (jsonb_typeof(p_items) IS DISTINCT FROM 'array'
    OR jsonb_array_length(p_items) IS DISTINCT FROM p.image_count OR p_items IS DISTINCT FROM p_output->'images'))
  OR (p_stage<3 AND (p_items IS NOT NULL OR p_reviews IS NOT NULL)) THEN
  RAISE EXCEPTION 'ECOM_PLAN_DRAFT_INVALID' USING ERRCODE='22023'; END IF;
 fingerprint=md5(jsonb_build_object('draft',p_expected_draft,'validator',p_validator_version,
  'output',p_output,'status',p_status,'items',p_items,'reviews',p_reviews)::text);
 prior=p.stage_drafts->p_stage::text->'local_completion';
 IF prior IS NOT NULL THEN
  IF prior->>'fingerprint' IS DISTINCT FROM fingerprint OR p.stage_outputs->p_stage::text IS DISTINCT FROM p_output THEN
   RAISE EXCEPTION 'ECOM_PLAN_DRAFT_CONFLICT' USING ERRCODE='22023'; END IF;
  RETURN jsonb_build_object('outcome','replay');
 END IF;
 IF p.status IS DISTINCT FROM 'planning' OR p.current_stage IS DISTINCT FROM p_stage
  OR p.lease_token IS DISTINCT FROM p_lease_token OR p_lease_token IS NULL
  OR p.lease_expires_at IS NULL OR p.lease_expires_at<=NOW() THEN
  RAISE EXCEPTION 'ECOM_PLAN_LEASE_LOST' USING ERRCODE='55000'; END IF;
 IF p.stage_drafts->p_stage::text IS DISTINCT FROM p_expected_draft THEN
  RAISE EXCEPTION 'ECOM_PLAN_DRAFT_CONFLICT' USING ERRCODE='22023'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_each(COALESCE(r.recovery_state->'attempts','{}')) a
  WHERE a.value->>'plan_id'=p.id::text AND a.value->>'outcome' IN ('started','uncertain')) THEN
  RAISE EXCEPTION 'ECOM_PLAN_EXECUTION_UNCERTAIN' USING ERRCODE='55000'; END IF;
 IF p_status='ready' AND p.model_settings->'delivery_policy'='{"version":1,"retry_cost":"platform"}'::jsonb THEN
  SELECT COALESCE(SUM((value->'usage'->>'user_credits')::integer),0) INTO credits
   FROM jsonb_array_elements(p.stage_attempts) WHERE value->>'status'='completed'
   AND (value->>'stage')::integer<p_stage;
 END IF;
 saved=public.save_ecom_image_plan_stage(p.id,p_lease_token,p_stage,p_output,NULL,p_status,p_items,p_reviews,credits);
 -- Local audit is separate from provider receipts and bears zero new model usage.
 UPDATE public.ecom_image_plans SET stage_drafts=jsonb_set(stage_drafts,ARRAY[p_stage::text,'local_completion'],
  jsonb_build_object('fingerprint',fingerprint,'validator_version',p_validator_version,
   'draft_fingerprint',md5(p_expected_draft::text),'output_fingerprint',md5(p_output::text),
   'delivered_stage_credits',credits,'provider_calls',0,'completed_at',NOW())) WHERE id=p.id;
 RETURN saved||jsonb_build_object('outcome','saved');
END $$;
REVOKE ALL ON FUNCTION public.lock_ecom_format_plan(UUID),
 public.persist_ecom_plan_format_state(UUID,UUID,INTEGER,JSONB,JSONB),
 public.complete_ecom_plan_local_draft(UUID,UUID,INTEGER,JSONB,TEXT,JSONB,TEXT,JSONB,JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.lock_ecom_format_plan(UUID),
 public.persist_ecom_plan_format_state(UUID,UUID,INTEGER,JSONB,JSONB),
 public.complete_ecom_plan_local_draft(UUID,UUID,INTEGER,JSONB,TEXT,JSONB,TEXT,JSONB,JSONB) TO everydayai;
RESET ROLE;
