-- Image previews share the image lifecycle, with a trial destination and no chat message.
CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_image_trial
ON public.tasks ((request_params->'_media_request_v1'->'origin'->>'trial_id'))
WHERE type='image' AND request_params->'_media_request_v1'->'origin'->>'destination'='skill_trial';

DROP POLICY IF EXISTS skill_draft_trial_image_worker ON public.skill_draft_trial_runs;
DROP POLICY IF EXISTS skill_draft_trial_image_worker_read ON public.skill_draft_trial_runs;
CREATE POLICY skill_draft_trial_image_worker_read ON public.skill_draft_trial_runs FOR SELECT TO everydayai,everydayai_worker
USING (current_setting('app.access_kind',TRUE)='worker'
    AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',TRUE),'')::UUID
    AND EXISTS (SELECT 1 FROM public.tasks t WHERE t.user_id=actor_user_id AND t.org_id=skill_draft_trial_runs.org_id
        AND t.type='image' AND t.request_params->'_media_request_v1'->'origin'->>'destination'='skill_trial'
        AND t.request_params->'_media_request_v1'->'origin'->>'trial_id'=skill_draft_trial_runs.id::TEXT));
CREATE POLICY skill_draft_trial_image_worker ON public.skill_draft_trial_runs FOR UPDATE TO everydayai,everydayai_worker
USING (current_setting('app.access_kind',TRUE)='worker'
    AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',TRUE),'')::UUID
    AND EXISTS (SELECT 1 FROM public.tasks t WHERE t.user_id=actor_user_id AND t.org_id=skill_draft_trial_runs.org_id
        AND t.type='image' AND t.request_params->'_media_request_v1'->'origin'->>'destination'='skill_trial'
        AND t.request_params->'_media_request_v1'->'origin'->>'trial_id'=skill_draft_trial_runs.id::TEXT))
WITH CHECK (current_setting('app.access_kind',TRUE)='worker'
    AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',TRUE),'')::UUID
    AND EXISTS (SELECT 1 FROM public.tasks t WHERE t.user_id=actor_user_id AND t.org_id=skill_draft_trial_runs.org_id
        AND t.type='image' AND t.request_params->'_media_request_v1'->'origin'->>'destination'='skill_trial'
        AND t.request_params->'_media_request_v1'->'origin'->>'trial_id'=skill_draft_trial_runs.id::TEXT));

CREATE OR REPLACE FUNCTION public.accept_chat_image_trial(
    p_trial_id UUID,p_snapshot JSONB,p_result JSONB,p_org_id UUID
) RETURNS JSONB LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE
    v_actor UUID:=NULLIF(current_setting('app.actor_user_id',TRUE),'')::UUID;
    v_trial public.skill_draft_trial_runs%ROWTYPE; v_change public.change_sets%ROWTYPE;
    v_task public.tasks%ROWTYPE; v_conv public.conversations%ROWTYPE; v_id UUID:=gen_random_uuid();
    v_origin JSONB:=p_snapshot->'origin'; v_cost INTEGER:=(p_snapshot->>'estimated_credits')::INTEGER;
BEGIN
    IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',TRUE) IS DISTINCT FROM 'runtime_admin'
       OR NULLIF(current_setting('app.org_id',TRUE),'')::UUID IS DISTINCT FROM p_org_id
       OR p_org_id IS NULL OR v_actor IS NULL THEN
        RAISE EXCEPTION 'CHAT_IMAGE_TRIAL_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_trial FROM public.skill_draft_trial_runs WHERE id=p_trial_id AND org_id=p_org_id
        AND actor_user_id=v_actor FOR UPDATE;
    IF NOT FOUND OR v_trial.mode<>'image'
       OR v_origin->>'trial_id' IS DISTINCT FROM p_trial_id::TEXT
       OR v_origin->>'destination' IS DISTINCT FROM 'skill_trial'
       OR v_origin->>'actor_user_id' IS DISTINCT FROM v_actor::TEXT
       OR v_origin->>'workspace_owner_id' IS DISTINCT FROM v_actor::TEXT
       OR v_origin->>'org_id' IS DISTINCT FROM p_org_id::TEXT
       OR v_origin->>'context_scope' IS DISTINCT FROM 'user'
       OR v_origin->>'candidate_revision' IS DISTINCT FROM v_trial.candidate_revision::TEXT
       OR v_origin->>'content_sha256' IS DISTINCT FROM v_trial.content_sha256
       OR v_origin->>'change_set_id' IS DISTINCT FROM v_trial.change_set_id::TEXT
       OR p_snapshot->>'model' IS DISTINCT FROM v_trial.model_id THEN
        RAISE EXCEPTION 'CHAT_IMAGE_TRIAL_DENIED' USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_task FROM public.tasks WHERE type='image'
        AND request_params->'_media_request_v1'->'origin'->>'trial_id'=p_trial_id::TEXT;
    IF FOUND THEN
        IF v_task.request_params->'_media_request_v1' IS DISTINCT FROM p_snapshot THEN
            RAISE EXCEPTION 'CHAT_IMAGE_TRIAL_CONFLICT' USING ERRCODE='22023';
        END IF;
        RETURN jsonb_build_object('outcome','replay','task_id',v_task.id,'submission_state',v_task.request_params->'_media_lifecycle_v1'->>'phase');
    END IF;
    IF v_trial.status<>'running' OR p_snapshot->>'schema_version' IS DISTINCT FROM '1'
       OR p_snapshot->>'num_images' IS DISTINCT FROM '1' OR v_cost IS NULL OR v_cost NOT BETWEEN 1 AND 200
       OR COALESCE(p_snapshot->>'mode','') NOT IN ('text_to_image','image_to_image')
       OR NULLIF(BTRIM(p_snapshot->>'prompt'),'') IS NULL
       OR COALESCE(jsonb_typeof(p_snapshot->'references'),'')<>'array'
       OR pg_column_size(p_snapshot)>128000 OR p_result IS NULL OR jsonb_typeof(p_result)<>'object' THEN
        RAISE EXCEPTION 'CHAT_IMAGE_TRIAL_REQUEST_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO v_change FROM public.change_sets WHERE id=v_trial.change_set_id AND org_id=p_org_id FOR SHARE;
    IF NOT FOUND OR v_change.status<>'awaiting_approval' OR v_change.expires_at<=NOW()
       OR v_change.created_by IS DISTINCT FROM v_actor::TEXT OR v_change.resource_type<>'skill_draft'
       OR v_change.revision IS DISTINCT FROM v_trial.candidate_revision
       OR v_change.proposed_snapshot->>'content_sha256' IS DISTINCT FROM v_trial.content_sha256
       OR NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.users u ON u.id=m.user_id
           JOIN public.organizations o ON o.id=m.org_id WHERE m.org_id=p_org_id AND m.user_id=v_actor
           AND m.status='active' AND m.role IN ('owner','admin') AND u.status='active' AND o.status='active') THEN
        RAISE EXCEPTION 'CHAT_IMAGE_TRIAL_CANDIDATE_UNAVAILABLE' USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_conv FROM public.conversations WHERE id=(v_origin->>'conversation_id')::UUID;
    IF NOT FOUND OR v_conv.user_id IS DISTINCT FROM v_actor OR v_conv.org_id IS DISTINCT FROM p_org_id
       OR COALESCE(v_conv.scope_type,'user')<>'user' THEN
        RAISE EXCEPTION 'CHAT_IMAGE_TRIAL_CONVERSATION_DENIED' USING ERRCODE='42501';
    END IF;
    INSERT INTO public.tasks(id,user_id,org_id,conversation_id,type,status,model_id,batch_id,image_index,request_params)
        VALUES(v_id,v_actor,p_org_id,v_conv.id,'image','pending',p_snapshot->>'model',v_id::TEXT,0,
            jsonb_build_object('_media_request_v1',p_snapshot,'_media_lifecycle_v1',jsonb_build_object('phase','queued','accepted_at',NOW())));
    UPDATE public.skill_draft_trial_runs SET result=p_result || jsonb_build_object('image_task_id',v_id,'status','running','submission_state','queued') WHERE id=p_trial_id;
    RETURN jsonb_build_object('outcome','accepted','task_id',v_id,'submission_state','queued');
END;
$$;
REVOKE ALL ON FUNCTION public.accept_chat_image_trial(UUID,JSONB,JSONB,UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.accept_chat_image_trial(UUID,JSONB,JSONB,UUID) TO everydayai;
