-- A page image belongs to a frozen project plan, not to a synthetic conversation.
SET LOCAL ROLE everydayai;
ALTER TABLE public.tasks ALTER COLUMN conversation_id DROP NOT NULL;
ALTER TABLE public.tasks ADD CONSTRAINT tasks_conversation_source_check CHECK (
 conversation_id IS NOT NULL OR (type='image' AND
  request_params->'_media_request_v1'->'origin'->>'destination'='detail_project') IS TRUE
);

CREATE OR REPLACE FUNCTION public.guard_detail_image_source()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE s JSONB:=NEW.request_params->'_media_request_v1'; o JSONB:=s->'origin';
 d public.detail_projects%ROWTYPE; p public.ecom_image_plans%ROWTYPE; item JSONB;
 prior public.tasks%ROWTYPE; n INTEGER; reserved INTEGER;
 actor UUID:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org UUID:=NULLIF(current_setting('app.org_id',true),'')::uuid;
BEGIN
 IF TG_OP='UPDATE' THEN
  -- Completed tasks can outlive the project's current run. Their frozen source
  -- is immutable; normal claim/publish may only update lifecycle fields.
  IF OLD.request_params->'_media_request_v1'->'origin'->>'destination'='detail_project' THEN
   IF s IS DISTINCT FROM OLD.request_params->'_media_request_v1'
    OR NEW.conversation_id IS DISTINCT FROM OLD.conversation_id OR NEW.user_id IS DISTINCT FROM OLD.user_id
    OR NEW.org_id IS DISTINCT FROM OLD.org_id OR NEW.type IS DISTINCT FROM OLD.type
    OR NEW.delivery_context IS DISTINCT FROM OLD.delivery_context
    OR NEW.model_id IS DISTINCT FROM OLD.model_id OR NEW.assistant_message_id IS DISTINCT FROM OLD.assistant_message_id
    OR NEW.batch_id IS DISTINCT FROM OLD.batch_id OR NEW.image_index IS DISTINCT FROM OLD.image_index THEN
    RAISE EXCEPTION 'DETAIL_IMAGE_SOURCE_IMMUTABLE' USING ERRCODE='42501'; END IF;
   RETURN NEW;
  ELSIF o->>'destination'='detail_project' THEN
   RAISE EXCEPTION 'DETAIL_IMAGE_SOURCE_DENIED' USING ERRCODE='42501';
  END IF;
  RETURN NEW;
 END IF;
 IF o->>'destination' IS DISTINCT FROM 'detail_project' THEN RETURN NEW; END IF;
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime'
  OR actor IS NULL OR NEW.user_id IS DISTINCT FROM actor OR NEW.org_id IS DISTINCT FROM org
  OR NEW.type IS DISTINCT FROM 'image' OR NEW.conversation_id IS NOT NULL
  OR NEW.status IS DISTINCT FROM 'pending' OR NEW.request_params->'_media_lifecycle_v1'->>'phase' IS DISTINCT FROM 'queued'
  OR COALESCE(NEW.delivery_context,'{}')<>'{}' OR NEW.assistant_message_id IS NOT NULL THEN
  RAISE EXCEPTION 'DETAIL_IMAGE_SOURCE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO d FROM public.detail_projects WHERE id=(o->>'project_id')::uuid
  AND user_id=actor AND org_id IS NOT DISTINCT FROM org FOR UPDATE;
 SELECT * INTO p FROM public.ecom_image_plans WHERE id=(o->'plan_source'->>'plan_id')::uuid
  AND project_id=d.id AND source_kind='detail_project' AND user_id=actor AND org_id IS NOT DISTINCT FROM org;
 SELECT value INTO item FROM jsonb_array_elements(p.items) WHERE value->>'item_id'=o->'plan_source'->>'item_id';
 IF d.id IS NULL OR p.id IS NULL OR p.status IS DISTINCT FROM 'ready' OR item IS NULL OR d.status='archived'
  OR jsonb_typeof(o) IS DISTINCT FROM 'object' OR NOT(o ? 'org_id')
  OR jsonb_typeof(o->'plan_source') IS DISTINCT FROM 'object'
  OR d.run_state->>'run_id' IS DISTINCT FROM p.generation_run_id::text
  OR o->>'generation_run_id' IS DISTINCT FROM p.generation_run_id::text
  OR o->>'actor_user_id' IS DISTINCT FROM actor::text OR o->>'workspace_owner_id' IS DISTINCT FROM actor::text
  OR o->>'org_id' IS DISTINCT FROM org::text OR o->>'context_scope' IS DISTINCT FROM 'user'
  OR o ?| ARRAY['conversation_id','parent_task_id','input_message_id','base_context_revision','trial_id']
  OR o->'plan_source'->>'revision' IS DISTINCT FROM p.plan_revision::text
  OR o->'plan_source'->>'request_text_sha256' IS DISTINCT FROM item->>'request_text_sha256'
  OR s->>'schema_version' IS DISTINCT FROM '1' OR s->>'num_images' IS DISTINCT FROM '1'
  OR (s->>'mode' IN ('text_to_image','image_to_image')) IS DISTINCT FROM TRUE
  OR s->>'prompt' IS NULL OR s->>'prompt' IS DISTINCT FROM item->>'request_text'
  OR s->>'prompt_sha256' IS DISTINCT FROM item->>'request_text_sha256'
  OR encode(public.digest(s->>'prompt','sha256'),'hex') IS DISTINCT FROM s->>'prompt_sha256'
  OR jsonb_typeof(s->'references') IS DISTINCT FROM 'array'
  OR s->'references' IS DISTINCT FROM p.input_snapshot->'resolved_references'
  OR s->>'aspect_ratio' IS NULL OR s->>'aspect_ratio' IS DISTINCT FROM item->>'aspect_ratio'
  OR s->>'resolution' IS NULL OR s->>'resolution' IS DISTINCT FROM p.target_size->>'resolution'
  OR (s->>'request_hash' ~ '^[0-9a-f]{64}$') IS DISTINCT FROM TRUE
  OR (s->>'estimated_credits')::int IS NULL OR (s->>'estimated_credits')::int NOT BETWEEN 1 AND 300
  OR NEW.model_id IS DISTINCT FROM s->>'model' OR s->>'model' IS NULL
  OR pg_column_size(s)>131072 THEN
  RAISE EXCEPTION 'DETAIL_IMAGE_SOURCE_DENIED' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=actor AND status='active')
  OR (org IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations g ON g.id=m.org_id
   WHERE m.user_id=actor AND m.org_id=org AND m.status='active' AND g.status='active')) THEN
  RAISE EXCEPTION 'DETAIL_IDENTITY_DENIED' USING ERRCODE='42501'; END IF;
 IF EXISTS(SELECT 1 FROM public.tasks t WHERE t.type='image' AND t.user_id=actor AND t.org_id IS NOT DISTINCT FROM org
  AND t.request_params->'_media_request_v1'->'origin'->>'project_id'=d.id::text
  AND t.request_params->'_media_request_v1'->'origin'->'plan_source'->>'item_id'=item->>'item_id'
  AND COALESCE(t.request_params->'_media_request_v1'->'origin'->>'retry_request_id','initial')=COALESCE(o->>'retry_request_id','initial')) THEN
  RAISE EXCEPTION 'DETAIL_IMAGE_DUPLICATE' USING ERRCODE='23505'; END IF;
 IF o ? 'retry_request_id' THEN
  SELECT * INTO prior FROM public.tasks WHERE id=(o->>'retry_of_task_id')::uuid AND type='image'
   AND user_id=actor AND org_id IS NOT DISTINCT FROM org;
  IF prior.id IS NULL OR NULLIF(o->>'retry_request_id','') IS NULL
   OR prior.request_params->'_media_lifecycle_v1'->>'phase' IS DISTINCT FROM 'published'
   OR prior.request_params->'_media_request_v1'->'origin'->>'project_id' IS DISTINCT FROM d.id::text
   OR prior.request_params->'_media_request_v1'->'origin'->'plan_source' IS DISTINCT FROM o->'plan_source'
   OR prior.request_params->'_media_request_v1'->>'prompt' IS DISTINCT FROM s->>'prompt'
   OR prior.request_params->'_media_request_v1'->'references' IS DISTINCT FROM s->'references'
   OR prior.request_params->'_media_request_v1'->>'model' IS DISTINCT FROM s->>'model'
   OR prior.request_params->'_media_request_v1'->>'estimated_credits' IS DISTINCT FROM s->>'estimated_credits' THEN
   RAISE EXCEPTION 'DETAIL_REPLAY_DENIED' USING ERRCODE='42501'; END IF;
 ELSE
  SELECT COUNT(*),COALESCE(SUM((t.request_params->'_media_request_v1'->>'estimated_credits')::int),0) INTO n,reserved
   FROM public.tasks t WHERE t.type='image' AND t.user_id=actor AND t.org_id IS NOT DISTINCT FROM org
   AND t.request_params->'_media_request_v1'->'origin'->>'project_id'=d.id::text
   AND NOT(t.request_params->'_media_request_v1'->'origin' ? 'retry_request_id');
  IF n>=COALESCE((p.model_settings->'image_budget'->>'max_requests')::int,15)
   OR reserved+(s->>'estimated_credits')::int>COALESCE((p.model_settings->'image_budget'->>'max_credits')::int,300) THEN
   RAISE EXCEPTION 'DETAIL_IMAGE_BUDGET_EXCEEDED' USING ERRCODE='54000'; END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_detail_image_source() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.guard_detail_image_source() TO everydayai,everydayai_worker;
CREATE TRIGGER guard_detail_image_source BEFORE INSERT OR UPDATE OF request_params,conversation_id,user_id,org_id,type,delivery_context,model_id,assistant_message_id,batch_id,image_index
 ON public.tasks FOR EACH ROW EXECUTE FUNCTION public.guard_detail_image_source();

RESET ROLE;
SET LOCAL ROLE everydayai_owner;
CREATE OR REPLACE FUNCTION public.renew_detail_page_plan(p_plan_id UUID,p_lease_token UUID)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime'
  OR p_lease_token IS NULL THEN RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 UPDATE public.ecom_image_plans p SET lease_expires_at=NOW()+interval '600 seconds'
  WHERE p.id=p_plan_id AND p.source_kind='detail_project' AND p.status='planning'
  AND p.lease_token=p_lease_token AND p.lease_expires_at>NOW()
  AND p.user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
  AND p.org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
  AND EXISTS(SELECT 1 FROM public.detail_projects d WHERE d.id=p.project_id AND d.user_id=p.user_id
   AND d.org_id IS NOT DISTINCT FROM p.org_id AND d.run_state->>'run_id'=p.generation_run_id::text
   AND d.status IN ('analyzing','plan_ready','generating'));
 RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION public.renew_detail_page_plan(UUID,UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.renew_detail_page_plan(UUID,UUID) TO everydayai;

-- This is permission for a new, explicit user attempt, never automatic replay.
CREATE OR REPLACE FUNCTION public.detail_attempt_is_closed_timeout(a JSONB)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public AS $$
 SELECT COALESCE(a->>'outcome'='uncertain' AND NULLIF(a->>'completed_at','') IS NOT NULL
  AND a->'usage'->>'error_type'='ModelGatewayTimeoutError' AND a->'usage'->>'error_code'='MODEL_TIMEOUT'
  AND (a->'usage'->'local_request_closed'='true'::jsonb
   -- Legacy _call finishes the timeout, closes in finally, then marks the plan
   -- failed. Failed + expired lease is checked by the caller, not inferred here.
   OR NOT(a->'usage' ? 'local_request_closed')),FALSE)
$$;
REVOKE ALL ON FUNCTION public.detail_attempt_is_closed_timeout(JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.detail_attempt_is_closed_timeout(JSONB) TO everydayai;

CREATE OR REPLACE FUNCTION public.resume_detail_page_plan(p_project_id UUID,p_plan_id UUID,p_request_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; d public.detail_projects%ROWTYPE; state JSONB;
BEGIN
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime' OR p_request_id IS NULL THEN RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO d FROM public.detail_projects WHERE id=p_project_id AND user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
  AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid FOR UPDATE;
 SELECT * INTO p FROM public.ecom_image_plans WHERE id=p_plan_id AND project_id=p_project_id FOR UPDATE;
 IF d.id IS NULL OR p.id IS NULL OR p.source_kind<>'detail_project' OR p.user_id<>d.user_id
  OR p.org_id IS DISTINCT FROM d.org_id OR d.status='archived' OR d.run_state->>'run_id' IS DISTINCT FROM p.generation_run_id::text THEN
  RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 IF p.recovery_state->>'resume_request_id'=p_request_id::text THEN RETURN to_jsonb(p); END IF;
 IF p.status='ready' THEN
  UPDATE public.ecom_image_plans SET recovery_state=(recovery_state-'acceptance_error')||jsonb_build_object('resume_request_id',p_request_id),updated_at=NOW() WHERE id=p.id;
 ELSIF p.status='failed' AND (p.lease_expires_at IS NULL OR p.lease_expires_at<=NOW()) THEN
  IF EXISTS(SELECT 1 FROM jsonb_each(COALESCE(p.recovery_state->'attempts','{}')) a
   WHERE a.value->>'outcome'='started' OR (a.value->>'outcome'='uncertain' AND NOT public.detail_attempt_is_closed_timeout(a.value))) THEN
   RAISE EXCEPTION 'ECOM_PLAN_EXECUTION_UNCERTAIN' USING ERRCODE='55000'; END IF;
  state=jsonb_build_object('version',1,'window_task_id',p_request_id,
   'deadline',NOW()+make_interval(secs=>LEAST(1800,GREATEST(600,COALESCE((p.model_settings->'profile'->>'wall_seconds')::int,1200)))),
   'counts','{}'::jsonb,'attempts','{}'::jsonb,'resume_request_id',p_request_id,
   'previous_windows',COALESCE(p.recovery_state->'previous_windows','[]')||jsonb_build_array(p.recovery_state-'previous_windows'));
  UPDATE public.ecom_image_plans SET status='planning',lease_token=NULL,lease_expires_at=NULL,recovery_state=state,updated_at=NOW() WHERE id=p.id;
 ELSE RAISE EXCEPTION 'DETAIL_PLAN_NOT_RETRYABLE' USING ERRCODE='55000'; END IF;
 UPDATE public.detail_projects SET status='analyzing',updated_at=NOW() WHERE id=d.id;
 RETURN jsonb_build_object('status','resumed');
END $$;
REVOKE ALL ON FUNCTION public.resume_detail_page_plan(UUID,UUID,UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.resume_detail_page_plan(UUID,UUID,UUID) TO everydayai;
RESET ROLE;
