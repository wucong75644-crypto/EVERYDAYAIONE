-- Explicit page identities, stable task history and capacity-aware multi-task delivery.
SET LOCAL ROLE everydayai;
ALTER TABLE public.detail_projects ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT '' CHECK(length(title)<=120);
DROP INDEX public.uq_detail_projects_org_draft;
DROP INDEX public.uq_detail_projects_personal_draft;
CREATE INDEX IF NOT EXISTS detail_projects_owner_created ON public.detail_projects(user_id,org_id,created_at DESC,id DESC)
 WHERE status<>'archived';

CREATE FUNCTION public.create_detail_project(p_request_id UUID)
RETURNS UUID LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE actor UUID:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org UUID:=NULLIF(current_setting('app.org_id',true),'')::uuid; d public.detail_projects%ROWTYPE;
BEGIN
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime' OR actor IS NULL OR p_request_id IS NULL THEN
  RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 IF org IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members WHERE user_id=actor AND org_id=org AND status='active') THEN
  RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 INSERT INTO public.detail_projects(id,user_id,org_id,platform) VALUES(p_request_id,actor,org,'taobao') ON CONFLICT(id) DO NOTHING;
 SELECT * INTO d FROM public.detail_projects WHERE id=p_request_id AND user_id=actor AND org_id IS NOT DISTINCT FROM org;
 IF NOT FOUND THEN RAISE EXCEPTION 'DETAIL_PROJECT_DENIED' USING ERRCODE='42501'; END IF;
 RETURN d.id;
END $$;
REVOKE ALL ON FUNCTION public.create_detail_project(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.create_detail_project(UUID) TO everydayai;

CREATE FUNCTION public.attach_detail_project_image_by_id(p_project_id UUID,p_workspace_path TEXT,p_category TEXT)
RETURNS TABLE(project_id UUID,project_version INTEGER,image_id UUID)
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE d public.detail_projects%ROWTYPE; actor UUID:=NULLIF(current_setting('app.actor_user_id',true),'')::uuid;
 org UUID:=NULLIF(current_setting('app.org_id',true),'')::uuid; n INTEGER; pos SMALLINT; image UUID;
BEGIN
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime' OR actor IS NULL THEN
  RAISE EXCEPTION 'DETAIL_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
 SELECT * INTO d FROM public.detail_projects WHERE id=p_project_id AND user_id=actor AND org_id IS NOT DISTINCT FROM org FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'DETAIL_PROJECT_DENIED' USING ERRCODE='42501'; END IF;
 IF d.status<>'draft' THEN RAISE EXCEPTION 'DETAIL_PROJECT_NOT_DRAFT' USING ERRCODE='55000'; END IF;
 IF p_workspace_path IS NULL OR length(p_workspace_path) NOT BETWEEN 1 AND 500 OR p_category IS NULL OR p_category NOT IN ('product','reference') THEN
  RAISE EXCEPTION 'DETAIL_IMAGE_INVALID_PATH' USING ERRCODE='22023'; END IF;
 IF EXISTS(SELECT 1 FROM public.detail_project_images i WHERE i.project_id=d.id AND i.workspace_path=p_workspace_path) THEN
  RAISE EXCEPTION 'DETAIL_IMAGE_DUPLICATE' USING ERRCODE='23505'; END IF;
 SELECT COUNT(*),COALESCE(MAX(sort_order)+1,0) INTO n,pos FROM public.detail_project_images i WHERE i.project_id=d.id;
 IF n>=9 THEN RAISE EXCEPTION 'DETAIL_IMAGE_LIMIT_EXCEEDED' USING ERRCODE='22023'; END IF;
 INSERT INTO public.detail_project_images(project_id,user_id,org_id,workspace_path,category,sort_order)
 VALUES(d.id,actor,org,p_workspace_path,p_category,pos) RETURNING id INTO image;
 UPDATE public.detail_projects SET version=version+1,updated_at=NOW() WHERE id=d.id RETURNING version INTO d.version;
 RETURN QUERY SELECT d.id,d.version,image;
END $$;
REVOKE ALL ON FUNCTION public.attach_detail_project_image_by_id(UUID,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.attach_detail_project_image_by_id(UUID,TEXT,TEXT) TO everydayai;

-- Migration 118 created this legacy function under the controlled migration session owner.
RESET ROLE;
CREATE OR REPLACE FUNCTION attach_detail_project_image(
    p_user_id UUID,
    p_org_id UUID,
    p_workspace_path TEXT,
    p_category TEXT
)
RETURNS TABLE(project_id UUID, project_version INTEGER, image_id UUID)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
    v_project_id UUID;
    v_version INTEGER;
    v_image_id UUID;
    v_count INTEGER;
    v_sort_order SMALLINT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM users WHERE id = p_user_id) THEN
        RAISE EXCEPTION 'DETAIL_PROJECT_USER_NOT_FOUND' USING ERRCODE = '23503';
    END IF;
    IF p_org_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM org_members member
         WHERE member.org_id = p_org_id
           AND member.user_id = p_user_id
           AND member.status = 'active'
    ) THEN
        RAISE EXCEPTION 'DETAIL_PROJECT_ORG_ACCESS_DENIED' USING ERRCODE = '42501';
    END IF;
    IF p_workspace_path IS NULL OR length(p_workspace_path) NOT BETWEEN 1 AND 500 THEN
        RAISE EXCEPTION 'DETAIL_IMAGE_INVALID_PATH' USING ERRCODE = '22023';
    END IF;
    IF p_category NOT IN ('product', 'reference') THEN
        RAISE EXCEPTION 'DETAIL_IMAGE_INVALID_CATEGORY' USING ERRCODE = '22023';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(p_user_id::text||COALESCE(p_org_id::text,''),118));
    IF (SELECT COUNT(*) FROM detail_projects WHERE user_id=p_user_id AND org_id IS NOT DISTINCT FROM p_org_id AND status='draft')>1 THEN
        RAISE EXCEPTION 'DETAIL_PROJECT_AMBIGUOUS' USING ERRCODE='55000';
    END IF;
    SELECT id, version
      INTO v_project_id, v_version
      FROM detail_projects
     WHERE user_id = p_user_id
       AND org_id IS NOT DISTINCT FROM p_org_id
       AND status = 'draft'
     FOR UPDATE;

    IF v_project_id IS NULL THEN
        BEGIN
            INSERT INTO detail_projects(user_id, org_id)
            VALUES (p_user_id, p_org_id)
            RETURNING id, version INTO v_project_id, v_version;
        EXCEPTION WHEN unique_violation THEN
            SELECT id, version
              INTO v_project_id, v_version
              FROM detail_projects
             WHERE user_id = p_user_id
               AND org_id IS NOT DISTINCT FROM p_org_id
               AND status = 'draft'
             FOR UPDATE;
        END;
    END IF;

    IF v_project_id IS NULL THEN
        RAISE EXCEPTION 'DETAIL_PROJECT_CREATE_FAILED' USING ERRCODE = 'P0001';
    END IF;

    IF EXISTS (
        SELECT 1 FROM detail_project_images image
         WHERE image.project_id = v_project_id
           AND image.workspace_path = p_workspace_path
    ) THEN
        RAISE EXCEPTION 'DETAIL_IMAGE_DUPLICATE' USING ERRCODE = '23505';
    END IF;

    SELECT COUNT(*), COALESCE(MAX(sort_order) + 1, 0)::SMALLINT
      INTO v_count, v_sort_order
      FROM detail_project_images image
     WHERE image.project_id = v_project_id;

    IF v_count >= 9 THEN
        RAISE EXCEPTION 'DETAIL_IMAGE_LIMIT_EXCEEDED' USING ERRCODE = '22023';
    END IF;

    INSERT INTO detail_project_images(
        project_id, user_id, org_id, workspace_path, category, sort_order
    ) VALUES (
        v_project_id, p_user_id, p_org_id, p_workspace_path, p_category, v_sort_order
    ) RETURNING id INTO v_image_id;

    UPDATE detail_projects
       SET version = version + 1, updated_at = NOW()
     WHERE id = v_project_id
     RETURNING version INTO v_version;

    RETURN QUERY SELECT v_project_id, v_version, v_image_id;
END;
$$;


GRANT EXECUTE ON FUNCTION public.attach_detail_project_image(UUID,UUID,TEXT,TEXT) TO everydayai;
RESET ROLE;
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

CREATE OR REPLACE FUNCTION public.reserve_ecom_plan_attempt(
    p_plan_id UUID,p_lease_token UUID,p_attempt_id UUID,p_stage INTEGER,p_wall_seconds INTEGER DEFAULT 600
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; r public.ecom_image_plans%ROWTYPE; t public.tasks%ROWTYPE;
        root_id UUID; state JSONB; attempts JSONB; n INTEGER; reset_window BOOLEAN;
BEGIN
    SELECT COALESCE(root_plan_id,id) INTO root_id FROM public.ecom_image_plans WHERE id=p_plan_id;
    SELECT * INTO r FROM public.ecom_image_plans WHERE id=root_id FOR UPDATE;
    SELECT * INTO p FROM public.ecom_image_plans WHERE id=p_plan_id FOR UPDATE;
    SELECT * INTO t FROM public.tasks WHERE id=p.parent_task_id;
    IF SESSION_USER <> 'everydayai' OR current_setting('app.access_kind',TRUE) <> 'runtime'
       OR p.id IS NULL OR r.id IS NULL OR r.user_id IS DISTINCT FROM p.user_id OR r.org_id IS DISTINCT FROM p.org_id
       OR r.conversation_id IS DISTINCT FROM p.conversation_id
       OR p.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',TRUE),'')::uuid
       OR p.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',TRUE),'')::uuid
       OR (p.source_kind='chat' AND (t.id IS NULL OR t.status <> 'running')) OR p_lease_token IS NULL OR p.lease_token IS DISTINCT FROM p_lease_token
       OR p.lease_expires_at IS NULL OR p.lease_expires_at < NOW() OR p.status <> 'planning' OR p.current_stage IS DISTINCT FROM p_stage
       OR p_stage IS NULL OR p_attempt_id IS NULL OR p_wall_seconds IS NULL OR p_wall_seconds NOT BETWEEN 1 AND (CASE WHEN p.source_kind='detail_project' THEN 1800 ELSE 600 END) THEN
        RAISE EXCEPTION 'ECOM_PLAN_LEASE_LOST' USING ERRCODE='55000';
    END IF;
    state=r.recovery_state;
    reset_window=COALESCE((t.request_params->'_ecom_workflow'->>'reset_window')::boolean,false)
        AND t.request_params->'_ecom_workflow'->>'window_task_id'=t.id::text
        AND state->>'window_task_id' IS DISTINCT FROM t.id::text;
    IF state->>'window_task_id' IS NULL AND jsonb_array_length(r.stage_attempts)>0 AND NOT reset_window THEN
        RAISE EXCEPTION 'ECOM_PLAN_EXECUTION_UNCERTAIN' USING ERRCODE='55000';
    END IF;
    IF state->>'window_task_id' IS NULL OR reset_window THEN
        state=jsonb_build_object('version',1,'window_task_id',COALESCE(t.id,p.generation_run_id),'deadline',NOW()+make_interval(secs=>p_wall_seconds),
            'counts','{}'::jsonb,'attempts','{}'::jsonb);
    END IF;
    -- Only an unstarted page window may begin its clock on actual reservation.
    IF p.source_kind='detail_project' AND state->>'pending_start'='true'
       AND state->>'deadline' IS NULL AND COALESCE(state->'attempts','{}')='{}'::jsonb THEN
        state=(state-'pending_start')||jsonb_build_object('deadline',NOW()+make_interval(secs=>p_wall_seconds));
    END IF;
    attempts=COALESCE(state->'attempts','{}'::jsonb);
    IF attempts ? p_attempt_id::text THEN
        RETURN jsonb_build_object('outcome','replay','attempt',attempts->p_attempt_id::text);
    END IF;
    IF EXISTS(SELECT 1 FROM jsonb_each(attempts) x WHERE x.value->>'outcome' IN ('started','uncertain')) THEN
        RAISE EXCEPTION 'ECOM_PLAN_EXECUTION_UNCERTAIN' USING ERRCODE='55000';
    END IF;
    IF (state->>'deadline')::timestamptz <= NOW() THEN
        RAISE EXCEPTION 'ECOM_PLAN_PARENT_BUDGET_EXHAUSTED' USING ERRCODE='55000';
    END IF;
    n=COALESCE((state->'counts'->>p_stage::text)::integer,0);
    IF n>=3 THEN RAISE EXCEPTION 'ECOM_PLAN_RETRY_EXHAUSTED' USING ERRCODE='55000'; END IF;
    state=jsonb_set(state,ARRAY['counts',p_stage::text],to_jsonb(n+1));
    state=jsonb_set(state,ARRAY['attempts',p_attempt_id::text],jsonb_build_object(
        'stage',p_stage,'plan_id',p.id,'outcome','started','started_at',NOW(),'ordinal',n+1));
    UPDATE public.ecom_image_plans SET recovery_state=state,updated_at=NOW() WHERE id=root_id;
    RETURN jsonb_build_object('outcome','execute','attempt_id',p_attempt_id,'ordinal',n+1,
        'remaining_attempts',2-n,'deadline',state->>'deadline');
END $$;

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
   'pending_start',true,
   'counts','{}'::jsonb,'attempts','{}'::jsonb,'resume_request_id',p_request_id,
   'previous_windows',COALESCE(p.recovery_state->'previous_windows','[]')||jsonb_build_array(p.recovery_state-'previous_windows'));
  UPDATE public.ecom_image_plans SET status='planning',lease_token=NULL,lease_expires_at=NULL,recovery_state=state,updated_at=NOW() WHERE id=p.id;
 ELSE RAISE EXCEPTION 'DETAIL_PLAN_NOT_RETRYABLE' USING ERRCODE='55000'; END IF;
 UPDATE public.detail_projects SET status='analyzing',updated_at=NOW() WHERE id=d.id;
 RETURN jsonb_build_object('status','resumed');
END $$;

CREATE OR REPLACE FUNCTION public.resume_detail_delivery_plan(p_plan_id UUID,p_token UUID)
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
  'pending_start',true,
  'counts','{}'::jsonb,'attempts','{}'::jsonb,'resume_request_id',j->>'request_id',
  'previous_windows',COALESCE(p.recovery_state->'previous_windows','[]')||jsonb_build_array(
   (p.recovery_state-'previous_windows')||jsonb_build_object('retry_cost','platform')));
 UPDATE public.ecom_image_plans SET status='planning',lease_token=NULL,lease_expires_at=NULL,recovery_state=state,
  updated_at=NOW() WHERE id=p.id;
 UPDATE public.detail_projects SET status='analyzing',updated_at=NOW() WHERE id=p.project_id;
 RETURN jsonb_build_object('status','resumed');
END $$;

CREATE OR REPLACE FUNCTION public.scan_detail_page_work(p_org_id UUID DEFAULT NULL,p_limit INTEGER DEFAULT 20)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE rows JSONB;
BEGIN
 PERFORM public.assert_chat_image_worker(p_org_id);
 IF p_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'DETAIL_SCAN_INVALID'; END IF;
 SELECT COALESCE(jsonb_agg(to_jsonb(d)),'[]') INTO rows FROM(
 SELECT ranked.* FROM (
  SELECT d.*,row_number() OVER(PARTITION BY user_id ORDER BY updated_at,id) AS owner_turn
  FROM public.detail_projects d WHERE org_id IS NOT DISTINCT FROM p_org_id AND status IN ('analyzing','plan_ready','generating')
 ) ranked ORDER BY owner_turn,updated_at,id LIMIT p_limit) d;
 RETURN rows;
END $$;
RESET ROLE;
