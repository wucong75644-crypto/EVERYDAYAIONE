-- Version-gated positional budgets and atomic successful prose receipts. No historical task reinterpretation.
SET LOCAL ROLE everydayai_owner;
CREATE OR REPLACE FUNCTION public.reserve_ecom_plan_attempt(
    p_plan_id UUID,p_lease_token UUID,p_attempt_id UUID,p_stage INTEGER,p_wall_seconds INTEGER,p_position INTEGER
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; r public.ecom_image_plans%ROWTYPE; t public.tasks%ROWTYPE;
        root_id UUID; state JSONB; attempts JSONB; n INTEGER; reset_window BOOLEAN; count_key TEXT; position INTEGER;
BEGIN
    -- Use the project → root → plan order for every concurrent position receipt.
    IF p_stage=3 AND EXISTS(SELECT 1 FROM public.ecom_image_plans WHERE id=p_plan_id
       AND source_kind='detail_project' AND prompt_versions->>'stage_three_delivery_version'='ecom-prompts.v5') THEN
        PERFORM public.lock_ecom_format_plan(p_plan_id);
    END IF;
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
    IF (state->>'deadline')::timestamptz <= NOW() THEN
        RAISE EXCEPTION 'ECOM_PLAN_PARENT_BUDGET_EXHAUSTED' USING ERRCODE='55000';
    END IF;
    count_key=p_stage::text;
    IF p_stage=3 AND p.prompt_versions->>'stage_three_delivery_version'='ecom-prompts.v5' THEN
        position=COALESCE(p_position,(p.stage_drafts->'3'->>'current_position')::integer);
        IF position IS NULL OR position NOT BETWEEN 0 AND p.image_count
           OR p.stage_drafts->'3'->>'delivery_version' IS DISTINCT FROM 'ecom-prompts.v5'
           OR (position>0 AND p.stage_drafts->'3'->'positions'->position::text->>'prompt' IS NOT NULL)
           OR (position=0 AND (p_position IS DISTINCT FROM 0 OR NOT EXISTS (
               SELECT 1 FROM jsonb_each(p.stage_drafts->'3'->'positions') d
               WHERE jsonb_typeof(d.value->'review_signal')='object')
               OR EXISTS (SELECT 1 FROM jsonb_each(p.stage_drafts->'3'->'positions') d
               WHERE d.value->>'prompt' IS NULL))) THEN
            RAISE EXCEPTION 'ECOM_PLAN_POSITION_INVALID' USING ERRCODE='22023';
        END IF;
        count_key='3:'||position::text;
    ELSIF p_position IS NOT NULL THEN
        RAISE EXCEPTION 'ECOM_PLAN_POSITION_INVALID' USING ERRCODE='22023';
    END IF;
    IF EXISTS(SELECT 1 FROM jsonb_each(attempts) x
              WHERE x.value->>'outcome' IN ('started','uncertain')
              AND (position IS NULL OR position=0 OR x.value->>'position' IS NULL
                   OR x.value->>'position' IN (position::text,'0'))) THEN
        RAISE EXCEPTION 'ECOM_PLAN_EXECUTION_UNCERTAIN' USING ERRCODE='55000';
    END IF;
    n=COALESCE((state->'counts'->>count_key)::integer,0);
    IF n>=3 THEN RAISE EXCEPTION 'ECOM_PLAN_RETRY_EXHAUSTED' USING ERRCODE='55000'; END IF;
    state=jsonb_set(state,ARRAY['counts',count_key],to_jsonb(n+1));
    state=jsonb_set(state,ARRAY['attempts',p_attempt_id::text],jsonb_build_object(
        'stage',p_stage,'plan_id',p.id,'outcome','started','started_at',NOW(),'ordinal',n+1,'position',position));
    UPDATE public.ecom_image_plans SET recovery_state=state,updated_at=NOW() WHERE id=root_id;
    RETURN jsonb_build_object('outcome','execute','attempt_id',p_attempt_id,'ordinal',n+1,
        'remaining_attempts',2-n,'deadline',state->>'deadline');
END $$;

REVOKE ALL ON FUNCTION public.reserve_ecom_plan_attempt(UUID,UUID,UUID,INTEGER,INTEGER,INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.reserve_ecom_plan_attempt(UUID,UUID,UUID,INTEGER,INTEGER,INTEGER) TO everydayai;

CREATE OR REPLACE FUNCTION public.reserve_ecom_plan_attempt(
    p_plan_id UUID,p_lease_token UUID,p_attempt_id UUID,p_stage INTEGER,p_wall_seconds INTEGER DEFAULT 600
) RETURNS JSONB LANGUAGE sql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
    SELECT public.reserve_ecom_plan_attempt(p_plan_id,p_lease_token,p_attempt_id,p_stage,p_wall_seconds,NULL)
$$;

CREATE OR REPLACE FUNCTION public.finish_ecom_plan_attempt(
    p_plan_id UUID,p_lease_token UUID,p_stage INTEGER,p_attempt_id UUID,p_usage JSONB,
    p_outcome TEXT,p_output JSONB DEFAULT NULL,p_status TEXT DEFAULT 'planning',
    p_items JSONB DEFAULT NULL,p_reviews JSONB DEFAULT NULL,p_credits INTEGER DEFAULT 0
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; r public.ecom_image_plans%ROWTYPE;
        root_id UUID; a JSONB; final JSONB; fingerprint TEXT; saved JSONB;
BEGIN
    -- Use the project → root → plan order for every concurrent position receipt.
    IF p_stage=3 AND EXISTS(SELECT 1 FROM public.ecom_image_plans WHERE id=p_plan_id
       AND source_kind='detail_project' AND prompt_versions->>'stage_three_delivery_version'='ecom-prompts.v5') THEN
        PERFORM public.lock_ecom_format_plan(p_plan_id);
    END IF;
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
                CASE WHEN p_outcome='completed' AND p_status='planning' AND (p_output IS NOT NULL OR
                    (p.prompt_versions->>'stage_three_delivery_version'='ecom-prompts.v5'
                     AND p_stage=3 AND p_usage->>'position_usable'='true'))
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

CREATE FUNCTION public.finish_ecom_position_attempt(p_plan_id UUID,p_lease_token UUID,p_attempt_id UUID,
 p_usage JSONB,p_expected_draft JSONB,p_draft JSONB)
RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE p public.ecom_image_plans%ROWTYPE; result JSONB; position TEXT; fingerprint TEXT; a JSONB;
BEGIN
 p=public.lock_ecom_format_plan(p_plan_id);
 position=p_usage->>'position';
 IF p.prompt_versions->>'stage_three_delivery_version' IS DISTINCT FROM 'ecom-prompts.v5'
  OR p_draft->>'delivery_version' IS DISTINCT FROM 'ecom-prompts.v5'
  OR p_draft->>'format_version' IS DISTINCT FROM 'ecom-format.v1'
  OR position IS NULL OR position!~'^[1-9][0-9]*$'
  OR position::integer NOT BETWEEN 1 AND p.image_count
  OR (p_draft->'positions')-position IS DISTINCT FROM (p_expected_draft->'positions')-position THEN
  RAISE EXCEPTION 'ECOM_PLAN_POSITION_INVALID' USING ERRCODE='22023'; END IF;
 SELECT recovery_state->'attempts'->p_attempt_id::text INTO a FROM public.ecom_image_plans WHERE id=COALESCE(p.root_plan_id,p.id);
 IF a->>'position' IS DISTINCT FROM position THEN
  RAISE EXCEPTION 'ECOM_PLAN_ATTEMPT_CONFLICT' USING ERRCODE='22023'; END IF;
 fingerprint=md5(p_draft::text);
 -- Exact receipt replay remains idempotent; it must not overwrite a newer draft.
 IF a->>'outcome'='completed' THEN
  IF a->'usage'->>'position_draft_fingerprint' IS DISTINCT FROM fingerprint
   OR a->'usage' IS DISTINCT FROM p_usage||jsonb_build_object('position_draft_fingerprint',fingerprint,
    'position_usable',p_draft->'positions'->position->>'prompt' IS NOT NULL,
    'billing_party',CASE WHEN p_draft->'positions'->position->>'prompt' IS NOT NULL THEN 'pending_delivery' ELSE 'platform' END,
    'platform_credits_estimate',0) THEN
   RAISE EXCEPTION 'ECOM_PLAN_ATTEMPT_CONFLICT' USING ERRCODE='22023'; END IF;
  RETURN jsonb_build_object('outcome','replay');
 END IF;
 IF p.stage_drafts->'3' IS DISTINCT FROM p_expected_draft THEN
  RAISE EXCEPTION 'ECOM_PLAN_DRAFT_CONFLICT' USING ERRCODE='22023'; END IF;
 IF p_draft->'positions'->position->>'prompt' IS NULL AND jsonb_typeof(p_draft->'positions'->position->'questions') IS DISTINCT FROM 'array'
  AND jsonb_typeof(p_draft->'questions') IS DISTINCT FROM 'array' THEN
  RAISE EXCEPTION 'ECOM_PLAN_POSITION_INVALID' USING ERRCODE='22023'; END IF;
 result=public.finish_ecom_plan_attempt(p.id,p_lease_token,3,p_attempt_id,
  p_usage||jsonb_build_object('position_draft_fingerprint',fingerprint,
   'position_usable',p_draft->'positions'->position->>'prompt' IS NOT NULL),'completed',NULL,'planning',NULL,NULL,0);
 UPDATE public.ecom_image_plans SET stage_drafts=jsonb_set(stage_drafts,ARRAY['3'],p_draft),updated_at=NOW() WHERE id=p.id;
 RETURN result;
END $$;
REVOKE ALL ON FUNCTION public.finish_ecom_position_attempt(UUID,UUID,UUID,JSONB,JSONB,JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.finish_ecom_position_attempt(UUID,UUID,UUID,JSONB,JSONB,JSONB) TO everydayai;

CREATE OR REPLACE FUNCTION public.complete_ecom_plan_local_draft(p_plan_id UUID,p_lease_token UUID,p_stage INTEGER,
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
   AND ((value->>'stage')::integer<p_stage OR
    (p.prompt_versions->>'stage_three_delivery_version'='ecom-prompts.v5'
     AND (value->>'stage')::integer=3 AND value->'usage'->>'position' IS NOT NULL
     AND value->>'attempt_id' IN (SELECT x.value->>'attempt_id'
       FROM jsonb_each(p_expected_draft->'positions') x)));
 END IF;
 saved=public.save_ecom_image_plan_stage(p.id,p_lease_token,p_stage,p_output,NULL,p_status,p_items,p_reviews,credits);
 -- Local audit is separate from provider receipts and bears zero new model usage.
 UPDATE public.ecom_image_plans SET stage_drafts=jsonb_set(stage_drafts,ARRAY[p_stage::text,'local_completion'],
  jsonb_build_object('fingerprint',fingerprint,'validator_version',p_validator_version,
   'draft_fingerprint',md5(p_expected_draft::text),'output_fingerprint',md5(p_output::text),
   'delivered_stage_credits',credits,'provider_calls',0,'completed_at',NOW())) WHERE id=p.id;
 RETURN saved||jsonb_build_object('outcome','saved');
END $$;
RESET ROLE;
