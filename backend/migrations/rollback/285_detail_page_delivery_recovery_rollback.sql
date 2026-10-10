-- Destructive schema rollback is allowed only before the first opted-in delivery run.
-- Once used, retain 285 and use a compatible application rollback; never discard billing/recovery history.
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM public.ecom_image_plans WHERE model_settings ? 'delivery_policy') THEN
  RAISE EXCEPTION 'DETAIL_DELIVERY_ROLLBACK_REQUIRES_COMPATIBLE_APPLICATION';
 END IF;
END $$;
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

DROP FUNCTION public.resume_detail_delivery_acceptance(UUID,UUID);
DROP FUNCTION public.resume_detail_delivery_plan(UUID,UUID);
DROP FUNCTION public.finish_detail_delivery_retry(UUID,TEXT,UUID,TEXT,TEXT);
DROP FUNCTION public.claim_detail_delivery_retry(UUID,TEXT,TEXT,UUID,TEXT,BOOLEAN);
DROP FUNCTION public.lock_detail_delivery_plan(UUID);
DROP FUNCTION public.stop_detail_delivery_recovery(UUID);
RESET ROLE;
SET LOCAL ROLE everydayai;
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

RESET ROLE;
SET LOCAL ROLE everydayai_owner;
ALTER TABLE public.ecom_image_plans DROP COLUMN delivery_recovery;
RESET ROLE;
