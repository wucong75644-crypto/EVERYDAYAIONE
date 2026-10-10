-- History is retained. Finish any later run before restoring the old cumulative budget.
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM public.detail_projects d WHERE d.status IN ('analyzing','plan_ready','generating')
   AND (SELECT COUNT(DISTINCT p.generation_run_id) FROM public.ecom_image_plans p WHERE p.project_id=d.id)>1) THEN
  RAISE EXCEPTION 'DETAIL_HISTORY_ROLLBACK_ACTIVE'; END IF;
END $$;
SET LOCAL ROLE everydayai_owner;
CREATE OR REPLACE FUNCTION public.start_detail_page_run(p_project_id UUID,p_version INTEGER,p_request_id UUID,p_plans JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE d public.detail_projects%ROWTYPE; actor UUID:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org UUID:=NULLIF(current_setting('app.org_id',true),'')::uuid; entry JSONB;
BEGIN
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true)<>'runtime' OR actor IS NULL OR p_request_id IS NULL THEN
  RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO d FROM public.detail_projects WHERE id=p_project_id AND user_id=actor AND org_id IS NOT DISTINCT FROM org FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'DETAIL_PROJECT_DENIED' USING ERRCODE='42501'; END IF;
 IF d.run_state->>'request_id'=p_request_id::text THEN RETURN to_jsonb(d); END IF;
 IF d.status<>'draft' OR d.version<>p_version THEN RAISE EXCEPTION 'DETAIL_PROJECT_VERSION_CONFLICT' USING ERRCODE='55000'; END IF;
 IF jsonb_typeof(p_plans)<>'array' OR jsonb_array_length(p_plans)<>(CASE WHEN d.content_type='default' THEN 2 ELSE 1 END) THEN
  RAISE EXCEPTION 'DETAIL_PLAN_INVALID' USING ERRCODE='22023'; END IF;
 FOR entry IN SELECT value FROM jsonb_array_elements(p_plans) LOOP
  IF entry->>'project_id' IS DISTINCT FROM d.id::text OR entry->>'user_id' IS DISTINCT FROM actor::text
   OR entry->>'org_id' IS DISTINCT FROM org::text OR entry->>'generation_run_id' IS DISTINCT FROM p_request_id::text
   OR (entry->>'image_count')::int<>(CASE WHEN d.content_type='default' THEN 7 ELSE d.image_count END)
   OR entry->'model_settings'->>'model' IS DISTINCT FROM d.prompt_model THEN
   RAISE EXCEPTION 'DETAIL_PLAN_INVALID' USING ERRCODE='22023'; END IF;
  INSERT INTO public.ecom_image_plans(id,user_id,org_id,source_kind,project_id,generation_run_id,invocation_key,input_digest,
    status,current_stage,image_count,input_snapshot,prompt_versions,model_settings,target_size)
   VALUES((entry->>'id')::uuid,actor,org,'detail_project',d.id,p_request_id,entry->>'invocation_key',entry->>'input_digest',
    'planning',1,(entry->>'image_count')::int,entry->'input_snapshot',entry->'prompt_versions',entry->'model_settings',entry->'target_size');
 END LOOP;
 UPDATE public.detail_projects SET status='analyzing',version=version+1,updated_at=NOW(),
  run_state=jsonb_build_object('run_id',p_request_id,'request_id',p_request_id,'started_at',NOW()) WHERE id=d.id RETURNING * INTO d;
 RETURN to_jsonb(d);
END $$;
CREATE OR REPLACE FUNCTION public.accept_detail_page_image(p_project_id UUID,p_plan_id UUID,p_item_id UUID,p_snapshot JSONB,p_org_id UUID DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE d public.detail_projects%ROWTYPE; plan public.ecom_image_plans%ROWTYPE; item JSONB; origin JSONB:=p_snapshot->'origin';
 actor UUID:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid; prior public.tasks%ROWTYPE;
 source public.tasks%ROWTYPE; task_id UUID:=gen_random_uuid(); cost INTEGER:=(p_snapshot->>'estimated_credits')::int;
 n INTEGER; reserved INTEGER;
BEGIN
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true)<>'runtime' OR actor IS NULL
 OR NULLIF(current_setting('app.org_id',true),'')::uuid IS DISTINCT FROM p_org_id THEN
  RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO d FROM public.detail_projects WHERE id=p_project_id AND user_id=actor AND org_id IS NOT DISTINCT FROM p_org_id FOR UPDATE;
 SELECT * INTO plan FROM public.ecom_image_plans WHERE id=p_plan_id AND project_id=p_project_id AND user_id=actor AND org_id IS NOT DISTINCT FROM p_org_id;
 SELECT value INTO item FROM jsonb_array_elements(plan.items) WHERE value->>'item_id'=p_item_id::text;
 IF d.id IS NULL OR plan.id IS NULL OR plan.status<>'ready' OR item IS NULL
 OR d.run_state->>'run_id' IS DISTINCT FROM plan.generation_run_id::text
 OR origin->>'generation_run_id' IS DISTINCT FROM plan.generation_run_id::text
 OR origin->>'project_id' IS DISTINCT FROM d.id::text OR origin->>'destination' IS DISTINCT FROM 'detail_project'
 OR origin->>'actor_user_id' IS DISTINCT FROM actor::text OR origin->>'workspace_owner_id' IS DISTINCT FROM actor::text
 OR origin->>'org_id' IS DISTINCT FROM p_org_id::text OR origin->>'context_scope' IS DISTINCT FROM 'user'
 OR origin ?| ARRAY['conversation_id','parent_task_id','input_message_id','base_context_revision','trial_id']
 OR origin->'plan_source'->>'plan_id' IS DISTINCT FROM plan.id::text
 OR origin->'plan_source'->>'item_id' IS DISTINCT FROM p_item_id::text
 OR origin->'plan_source'->>'revision' IS DISTINCT FROM plan.plan_revision::text
 OR origin->'plan_source'->>'request_text_sha256' IS DISTINCT FROM item->>'request_text_sha256'
 OR p_snapshot->>'schema_version' IS DISTINCT FROM '1' OR p_snapshot->>'num_images' IS DISTINCT FROM '1'
 OR p_snapshot->>'prompt' IS DISTINCT FROM item->>'request_text'
 OR p_snapshot->>'prompt_sha256' IS DISTINCT FROM item->>'request_text_sha256'
 OR p_snapshot->'references' IS DISTINCT FROM plan.input_snapshot->'resolved_references'
 OR p_snapshot->>'aspect_ratio' IS DISTINCT FROM item->>'aspect_ratio'
 OR p_snapshot->>'resolution' IS DISTINCT FROM plan.target_size->>'resolution'
 OR p_snapshot->>'request_hash' !~ '^[0-9a-f]{64}$' OR cost IS NULL OR cost NOT BETWEEN 1 AND 300
 OR pg_column_size(p_snapshot)>131072 THEN
  RAISE EXCEPTION 'DETAIL_IMAGE_SOURCE_DENIED' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=actor AND status='active')
 OR (p_org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations o ON o.id=m.org_id
 WHERE m.user_id=actor AND m.org_id=p_org_id AND m.status='active' AND o.status='active')) THEN
  RAISE EXCEPTION 'DETAIL_IDENTITY_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO prior FROM public.tasks WHERE type='image' AND user_id=actor AND org_id IS NOT DISTINCT FROM p_org_id
 AND request_params->'_media_request_v1'->'origin'->>'project_id'=d.id::text
 AND request_params->'_media_request_v1'->'origin'->'plan_source'->>'item_id'=p_item_id::text
 AND COALESCE(request_params->'_media_request_v1'->'origin'->>'retry_request_id','initial')=COALESCE(origin->>'retry_request_id','initial');
 IF FOUND THEN RETURN jsonb_build_object('outcome','replay','task_id',prior.id,'message_id',NULL,'submission_state',prior.request_params->'_media_lifecycle_v1'->>'phase'); END IF;
 IF origin ? 'retry_request_id' THEN
  SELECT * INTO source FROM public.tasks WHERE id=(origin->>'retry_of_task_id')::uuid AND user_id=actor AND org_id IS NOT DISTINCT FROM p_org_id;
  IF source.id IS NULL OR source.request_params->'_media_lifecycle_v1'->>'phase'<>'published'
  OR source.request_params->'_media_request_v1'->'origin'->>'project_id' IS DISTINCT FROM d.id::text
  OR source.request_params->'_media_request_v1'->>'prompt' IS DISTINCT FROM p_snapshot->>'prompt'
  OR source.request_params->'_media_request_v1'->'references' IS DISTINCT FROM p_snapshot->'references'
  OR source.request_params->'_media_request_v1'->>'model' IS DISTINCT FROM p_snapshot->>'model'
  OR source.request_params->'_media_request_v1'->>'estimated_credits' IS DISTINCT FROM p_snapshot->>'estimated_credits' THEN
   RAISE EXCEPTION 'DETAIL_REPLAY_DENIED' USING ERRCODE='42501'; END IF;
 ELSE
  SELECT COUNT(*),COALESCE(SUM((request_params->'_media_request_v1'->>'estimated_credits')::int),0) INTO n,reserved
   FROM public.tasks WHERE type='image' AND user_id=actor AND org_id IS NOT DISTINCT FROM p_org_id
   AND request_params->'_media_request_v1'->'origin'->>'project_id'=d.id::text
   AND NOT(request_params->'_media_request_v1'->'origin' ? 'retry_request_id');
  IF n>=COALESCE((plan.model_settings->'image_budget'->>'max_requests')::int,15) OR reserved+cost>COALESCE((plan.model_settings->'image_budget'->>'max_credits')::int,300) THEN RAISE EXCEPTION 'DETAIL_IMAGE_BUDGET_EXCEEDED' USING ERRCODE='54000'; END IF;
 END IF;
 INSERT INTO public.tasks(id,user_id,org_id,type,status,model_id,batch_id,image_index,request_params)
 VALUES(task_id,actor,p_org_id,'image','pending',p_snapshot->>'model',plan.generation_run_id::text,(item->>'position')::int-1,
 jsonb_build_object('_media_request_v1',p_snapshot,'_media_lifecycle_v1',jsonb_build_object('phase','queued','attempt',0),
 'prompt',p_snapshot->>'prompt','model',p_snapshot->>'model','num_images',1));
 UPDATE public.detail_projects SET status='generating',updated_at=NOW() WHERE id=d.id;
 RETURN jsonb_build_object('outcome','accepted','task_id',task_id,'message_id',NULL,'submission_state','queued');
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
