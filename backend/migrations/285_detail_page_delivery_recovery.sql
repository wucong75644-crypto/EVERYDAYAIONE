-- Durable page delivery retries. Provider IO and image settlement stay in the existing workers.
SET LOCAL ROLE everydayai_owner;
ALTER TABLE public.ecom_image_plans ADD COLUMN delivery_recovery JSONB NOT NULL DEFAULT '{}'
 CHECK(jsonb_typeof(delivery_recovery)='object');

CREATE FUNCTION public.lock_detail_delivery_plan(p_plan_id UUID)
RETURNS public.ecom_image_plans LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; d public.detail_projects%ROWTYPE; project UUID;
BEGIN
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime' THEN
  RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT project_id INTO project FROM public.ecom_image_plans WHERE id=p_plan_id;
 SELECT * INTO d FROM public.detail_projects WHERE id=project FOR UPDATE;
 SELECT * INTO p FROM public.ecom_image_plans WHERE id=p_plan_id FOR UPDATE;
 IF d.id IS NULL OR p.id IS NULL OR p.source_kind IS DISTINCT FROM 'detail_project'
  OR d.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
  OR d.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
  OR p.user_id IS DISTINCT FROM d.user_id OR p.org_id IS DISTINCT FROM d.org_id
  OR d.status='archived' OR d.run_state->>'run_id' IS DISTINCT FROM p.generation_run_id::text
  OR d.run_state->>'delivery_stopped'='true'
  OR p.model_settings->'delivery_policy' IS DISTINCT FROM '{"version":1,"retry_cost":"platform"}'::jsonb THEN
  RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 RETURN p;
END $$;

CREATE FUNCTION public.claim_detail_delivery_retry(p_plan_id UUID,p_key TEXT,p_source TEXT,p_token UUID,
 p_reason TEXT,p_blocked BOOLEAN DEFAULT FALSE)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; j JSONB; source public.tasks%ROWTYPE; latest UUID; delay INTEGER;
BEGIN
 p=public.lock_detail_delivery_plan(p_plan_id);
 IF p_token IS NULL OR p_blocked IS NULL OR p_reason IS NULL OR length(p_reason)>128
  OR p_source IS NULL OR length(p_source)>200 OR p_key IS NULL THEN
  RAISE EXCEPTION 'DETAIL_RETRY_INVALID' USING ERRCODE='22023'; END IF;
 IF p_key='plan' THEN
  IF p.status<>'failed' OR p.lease_expires_at>NOW()
   OR p_source IS DISTINCT FROM (COALESCE(p.recovery_state->>'window_task_id','initial')||':'||jsonb_array_length(p.stage_attempts)::text||':'||p.current_stage::text) THEN
   RETURN jsonb_build_object('outcome','stale'); END IF;
 ELSIF p_key='acceptance' THEN
  IF p.status<>'ready' OR p.recovery_state->'acceptance_error' IS NULL
   OR p_source IS DISTINCT FROM p.recovery_state->'acceptance_error'->>'attempt_id' THEN
   RETURN jsonb_build_object('outcome','stale'); END IF;
 ELSIF p_key ~ '^image:[0-9a-f-]{36}$' THEN
  SELECT * INTO source FROM public.tasks WHERE id=p_source::uuid;
  IF source.id IS NULL OR source.user_id IS DISTINCT FROM p.user_id OR source.org_id IS DISTINCT FROM p.org_id
   OR source.status<>'failed' OR source.request_params->'_media_lifecycle_v1'->>'phase' IS DISTINCT FROM 'published'
   OR source.request_params->'_media_request_v1'->'origin'->'plan_source'->>'plan_id' IS DISTINCT FROM p.id::text
   OR source.request_params->'_media_request_v1'->'origin'->>'generation_run_id' IS DISTINCT FROM p.generation_run_id::text
   OR p_key IS DISTINCT FROM 'image:'||(source.request_params->'_media_request_v1'->'origin'->'plan_source'->>'item_id') THEN
   RETURN jsonb_build_object('outcome','stale'); END IF;
  SELECT t.id INTO latest FROM public.tasks t
   WHERE t.user_id=p.user_id AND t.org_id IS NOT DISTINCT FROM p.org_id
   AND t.request_params->'_media_request_v1'->'origin'->'plan_source'->>'plan_id'=p.id::text
   AND t.request_params->'_media_request_v1'->'origin'->'plan_source'->>'item_id'=substring(p_key from 7)
   ORDER BY t.created_at DESC,t.id DESC LIMIT 1;
  IF latest IS DISTINCT FROM source.id THEN RETURN jsonb_build_object('outcome','stale'); END IF;
 ELSE RAISE EXCEPTION 'DETAIL_RETRY_INVALID' USING ERRCODE='22023'; END IF;
 j=COALESCE(p.delivery_recovery->p_key,'{}'::jsonb);
 IF p_blocked THEN
  j=j||jsonb_build_object('status','blocked','source',p_source,'reason',
   CASE WHEN j->>'source'=p_source AND j->>'status'='blocked' THEN COALESCE(j->>'reason',p_reason) ELSE p_reason END);
 ELSIF j->>'source' IS DISTINCT FROM p_source OR j->>'status'='blocked' THEN
  delay=LEAST(300,5*(2^LEAST(COALESCE((j->>'attempts')::int,0),6))::int);
  j=jsonb_build_object('status','waiting','source',p_source,'reason',p_reason,'request_id',gen_random_uuid(),
   'attempts',COALESCE((j->>'attempts')::int,0),'next_retry_at',NOW()+make_interval(secs=>delay));
 ELSIF j->>'status'='waiting' AND (j->>'next_retry_at')::timestamptz<=NOW()
  OR j->>'status'='running' AND (j->>'lease_expires_at')::timestamptz<=NOW() THEN
  j=j||jsonb_build_object('status','running','claim_token',p_token,'lease_expires_at',NOW()+interval '60 seconds',
   'attempts',COALESCE((j->>'attempts')::int,0)+1);
  UPDATE public.ecom_image_plans SET delivery_recovery=jsonb_set(delivery_recovery,ARRAY[p_key],j) WHERE id=p.id;
  RETURN j||jsonb_build_object('outcome','execute');
 END IF;
 UPDATE public.ecom_image_plans SET delivery_recovery=jsonb_set(delivery_recovery,ARRAY[p_key],j) WHERE id=p.id;
 RETURN j||jsonb_build_object('outcome','waiting');
END $$;

CREATE FUNCTION public.finish_detail_delivery_retry(p_plan_id UUID,p_key TEXT,p_token UUID,p_status TEXT,p_reason TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; j JSONB; delay INTEGER;
BEGIN
 p=public.lock_detail_delivery_plan(p_plan_id); j=p.delivery_recovery->p_key;
 IF p_status IS NULL OR p_status NOT IN ('submitted','waiting','blocked') OR p_reason IS NULL OR length(p_reason)>128 THEN
  RAISE EXCEPTION 'DETAIL_RETRY_INVALID' USING ERRCODE='22023'; END IF;
 IF j->>'status' IS DISTINCT FROM 'running' OR j->>'claim_token' IS DISTINCT FROM p_token::text
  OR (j->>'lease_expires_at')::timestamptz<=NOW() THEN RETURN FALSE; END IF;
 delay=LEAST(300,5*(2^LEAST(COALESCE((j->>'attempts')::int,0),6))::int);
 j=(j-'claim_token'-'lease_expires_at')||jsonb_build_object('status',p_status,'reason',p_reason,
  'next_retry_at',NOW()+make_interval(secs=>delay));
 UPDATE public.ecom_image_plans SET delivery_recovery=jsonb_set(delivery_recovery,ARRAY[p_key],j) WHERE id=p.id;
 RETURN TRUE;
END $$;

CREATE FUNCTION public.resume_detail_delivery_plan(p_plan_id UUID,p_token UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; j JSONB; state JSONB;
BEGIN
 p=public.lock_detail_delivery_plan(p_plan_id); j=p.delivery_recovery->'plan';
 IF p_token IS NULL OR j->>'status' IS DISTINCT FROM 'running'
  OR j->>'claim_token' IS DISTINCT FROM p_token::text OR (j->>'lease_expires_at')::timestamptz IS NULL
  OR (j->>'lease_expires_at')::timestamptz<=NOW() THEN
  RAISE EXCEPTION 'DETAIL_PLAN_NOT_RETRYABLE' USING ERRCODE='55000'; END IF;
 IF p.status='planning' AND p.recovery_state->>'window_task_id'=j->>'request_id' THEN
  RETURN jsonb_build_object('status','resumed'); END IF;
 IF p.status<>'failed' OR p.lease_expires_at>NOW() THEN
  RAISE EXCEPTION 'DETAIL_PLAN_NOT_RETRYABLE' USING ERRCODE='55000'; END IF;
 -- Keep uncertain provider usage uncertain. This new delivery window is explicitly platform-backed.
 state=jsonb_build_object('version',1,'window_task_id',j->>'request_id',
  'deadline',NOW()+make_interval(secs=>LEAST(1800,GREATEST(600,COALESCE((p.model_settings->'profile'->>'wall_seconds')::int,1200)))),
  'counts','{}'::jsonb,'attempts','{}'::jsonb,'resume_request_id',j->>'request_id',
  'previous_windows',COALESCE(p.recovery_state->'previous_windows','[]')||jsonb_build_array(
   (p.recovery_state-'previous_windows')||jsonb_build_object('retry_cost','platform')));
 UPDATE public.ecom_image_plans SET status='planning',lease_token=NULL,lease_expires_at=NULL,recovery_state=state,
  updated_at=NOW() WHERE id=p.id;
 UPDATE public.detail_projects SET status='analyzing',updated_at=NOW() WHERE id=p.project_id;
 RETURN jsonb_build_object('status','resumed');
END $$;

CREATE FUNCTION public.stop_detail_delivery_recovery(p_project_id UUID)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime' THEN
  RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 UPDATE public.detail_projects SET run_state=run_state||'{"delivery_stopped":true}'::jsonb,updated_at=NOW()
  WHERE id=p_project_id AND user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
  AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid AND status<>'archived';
 IF NOT FOUND THEN RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 RETURN TRUE;
END $$;

CREATE FUNCTION public.resume_detail_delivery_acceptance(p_plan_id UUID,p_token UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; j JSONB;
BEGIN
 p=public.lock_detail_delivery_plan(p_plan_id); j=p.delivery_recovery->'acceptance';
 IF p_token IS NULL OR p.status<>'ready' OR j->>'status' IS DISTINCT FROM 'running'
  OR j->>'claim_token' IS DISTINCT FROM p_token::text OR (j->>'lease_expires_at')::timestamptz IS NULL
  OR (j->>'lease_expires_at')::timestamptz<=NOW()
  OR (p.recovery_state->>'resume_request_id' IS DISTINCT FROM j->>'request_id'
   AND p.recovery_state->'acceptance_error'->>'attempt_id' IS DISTINCT FROM j->>'source') THEN
  RAISE EXCEPTION 'DETAIL_PLAN_NOT_RETRYABLE' USING ERRCODE='55000'; END IF;
 RETURN public.resume_detail_page_plan(p.project_id,p.id,(j->>'request_id')::uuid);
END $$;

REVOKE ALL ON FUNCTION public.lock_detail_delivery_plan(UUID),
 public.claim_detail_delivery_retry(UUID,TEXT,TEXT,UUID,TEXT,BOOLEAN),
 public.finish_detail_delivery_retry(UUID,TEXT,UUID,TEXT,TEXT),
 public.resume_detail_delivery_plan(UUID,UUID),public.resume_detail_delivery_acceptance(UUID,UUID),
 public.stop_detail_delivery_recovery(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.lock_detail_delivery_plan(UUID),
 public.claim_detail_delivery_retry(UUID,TEXT,TEXT,UUID,TEXT,BOOLEAN),
 public.finish_detail_delivery_retry(UUID,TEXT,UUID,TEXT,TEXT),
 public.resume_detail_delivery_plan(UUID,UUID),public.resume_detail_delivery_acceptance(UUID,UUID),
 public.stop_detail_delivery_recovery(UUID) TO everydayai;
RESET ROLE;

SET LOCAL ROLE everydayai_owner;
CREATE OR REPLACE FUNCTION public.finish_ecom_plan_attempt(
    p_plan_id UUID,p_lease_token UUID,p_stage INTEGER,p_attempt_id UUID,p_usage JSONB,
    p_outcome TEXT,p_output JSONB DEFAULT NULL,p_status TEXT DEFAULT 'planning',
    p_items JSONB DEFAULT NULL,p_reviews JSONB DEFAULT NULL,p_credits INTEGER DEFAULT 0
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; r public.ecom_image_plans%ROWTYPE;
        root_id UUID; a JSONB; final JSONB; fingerprint TEXT; saved JSONB;
BEGIN
    SELECT COALESCE(root_plan_id,id) INTO root_id FROM public.ecom_image_plans WHERE id=p_plan_id;
    SELECT * INTO r FROM public.ecom_image_plans WHERE id=root_id FOR UPDATE;
    SELECT * INTO p FROM public.ecom_image_plans WHERE id=p_plan_id FOR UPDATE;
    IF SESSION_USER <> 'everydayai' OR current_setting('app.access_kind',TRUE) <> 'runtime'
       OR p.id IS NULL OR r.id IS NULL OR r.user_id IS DISTINCT FROM p.user_id OR r.org_id IS DISTINCT FROM p.org_id
       OR r.conversation_id IS DISTINCT FROM p.conversation_id
       OR p.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',TRUE),'')::uuid
       OR p.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',TRUE),'')::uuid
       OR p_outcome IS NULL OR p_outcome NOT IN ('completed','validation_failed','rejected','uncertain') THEN
        RAISE EXCEPTION 'ECOM_PLAN_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    a=r.recovery_state->'attempts'->p_attempt_id::text;
    IF a IS NULL OR a->>'plan_id' IS DISTINCT FROM p.id::text OR (a->>'stage')::integer IS DISTINCT FROM p_stage THEN
        RAISE EXCEPTION 'ECOM_PLAN_ATTEMPT_CONFLICT' USING ERRCODE='22023';
    END IF;
    IF p.source_kind='detail_project'
       AND p.model_settings->'delivery_policy'='{"version":1,"retry_cost":"platform"}'::jsonb THEN
        IF p_outcome='completed' AND p_status='ready' AND p_output IS NOT NULL THEN
            -- Charge each valid stage once, only when the complete prompt set is delivered.
            SELECT p_credits+COALESCE(SUM((value->'usage'->>'user_credits')::integer),0)
              INTO p_credits FROM jsonb_array_elements(p.stage_attempts)
             WHERE value->>'status'='completed' AND (value->>'stage')::integer<p_stage;
            p_usage=COALESCE(p_usage,'{}')||jsonb_build_object('billing_party','user','delivered_stage_credits',p_credits);
        ELSE
            p_usage=COALESCE(p_usage,'{}')||jsonb_build_object('billing_party',
                CASE WHEN p_outcome='completed' AND p_output IS NOT NULL AND p_status='planning'
                  THEN 'pending_delivery' ELSE 'platform' END,
                'platform_credits_estimate',CASE WHEN p_outcome='validation_failed' THEN p_credits ELSE 0 END);
            p_credits=0;
        END IF;
    END IF;
    fingerprint=md5(jsonb_build_object('usage',p_usage,'outcome',p_outcome,'output',p_output,
        'status',p_status,'items',p_items,'reviews',p_reviews,'credits',p_credits)::text);
    IF a->>'outcome'<>'started' THEN
        IF a->>'fingerprint' IS DISTINCT FROM fingerprint THEN
            RAISE EXCEPTION 'ECOM_PLAN_ATTEMPT_CONFLICT' USING ERRCODE='22023';
        END IF;
        RETURN jsonb_build_object('outcome','replay','attempt_id',p_attempt_id);
    END IF;
    saved=public.save_ecom_image_plan_stage(p_plan_id,p_lease_token,p_stage,p_output,
        jsonb_build_object('attempt_id',p_attempt_id,'stage',p_stage,'status',p_outcome,'usage',p_usage),
        p_status,p_items,p_reviews,p_credits);
    final=a || jsonb_build_object('outcome',p_outcome,'fingerprint',fingerprint,'usage',p_usage,
        'user_credits',p_credits,'completed_at',NOW());
    UPDATE public.ecom_image_plans SET recovery_state=jsonb_set(recovery_state,
        ARRAY['attempts',p_attempt_id::text],final),updated_at=NOW() WHERE id=root_id;
    RETURN saved || jsonb_build_object('outcome','saved','attempt_id',p_attempt_id);
END $$;
RESET ROLE;

SET LOCAL ROLE everydayai;
CREATE OR REPLACE FUNCTION public.guard_detail_image_source()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE s JSONB:=NEW.request_params->'_media_request_v1'; o JSONB:=s->'origin';
 d public.detail_projects%ROWTYPE; p public.ecom_image_plans%ROWTYPE; item JSONB;
 prior public.tasks%ROWTYPE; n INTEGER; reserved INTEGER; j JSONB; latest UUID;
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
 IF o ? 'delivery_retry_token' THEN
  j=p.delivery_recovery->('image:'||(item->>'item_id'));
  SELECT t.id INTO latest FROM public.tasks t WHERE t.user_id=actor AND t.org_id IS NOT DISTINCT FROM org
   AND t.request_params->'_media_request_v1'->'origin'->'plan_source'->>'plan_id'=p.id::text
   AND t.request_params->'_media_request_v1'->'origin'->'plan_source'->>'item_id'=item->>'item_id'
   ORDER BY t.created_at DESC,t.id DESC LIMIT 1;
  IF d.run_state->>'delivery_stopped'='true'
   OR p.model_settings->'delivery_policy' IS DISTINCT FROM '{"version":1,"retry_cost":"platform"}'::jsonb
   OR j->>'status' IS DISTINCT FROM 'running' OR j->>'claim_token' IS DISTINCT FROM o->>'delivery_retry_token'
   OR (j->>'lease_expires_at')::timestamptz IS NULL OR (j->>'lease_expires_at')::timestamptz<=NOW()
   OR j->>'request_id' IS DISTINCT FROM o->>'retry_request_id'
   OR j->>'source' IS DISTINCT FROM o->>'retry_of_task_id' OR latest::text IS DISTINCT FROM j->>'source' THEN
   RAISE EXCEPTION 'DETAIL_REPLAY_DENIED' USING ERRCODE='42501'; END IF;
 END IF;
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
RESET ROLE;
