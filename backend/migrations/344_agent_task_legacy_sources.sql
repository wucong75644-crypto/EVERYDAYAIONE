-- Read existing outputs under the current Actor's frozen history and authority.
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.read_agent_task_legacy_source(
    p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_expected_revision bigint,
    p_kind text,p_source_id uuid,p_source_revision bigint DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; source public.tasks%ROWTYPE;
    m public.messages%ROWTYPE; plan public.ecom_image_plans%ROWTYPE; content jsonb; output jsonb;
BEGIN
    SELECT * INTO t FROM public.tasks WHERE id=p_task_id;
    IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime'
       OR t.user_id::text IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')
       OR t.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
       OR t.type IS DISTINCT FROM 'chat' OR NOT(t.delivery_context @> '{"actor":true}') THEN
        RAISE EXCEPTION 'TASK_LEGACY_SOURCE_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    IF t.execution_token IS DISTINCT FROM p_execution_token OR t.status<>'running' THEN RETURN jsonb_build_object('outcome','ownership_lost'); END IF;
    IF t.lease_expires_at IS NULL OR t.lease_expires_at<=now() THEN RETURN jsonb_build_object('outcome','lease_expired'); END IF;
    SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id AND owner_task_id=p_task_id
       AND conversation_id=t.conversation_id AND actor_user_id=t.user_id AND org_id IS NOT DISTINCT FROM t.org_id;
    IF NOT FOUND OR s.revision<>p_expected_revision THEN RETURN jsonb_build_object('outcome','stale'); END IF;
    IF s.state->>'branch_id' IS NOT NULL OR t.execution_mode<>'serial' THEN
        RAISE EXCEPTION 'TASK_LEGACY_BRANCH_SOURCE_UNAVAILABLE' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=t.user_id AND status::text='active')
       OR (t.org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members member JOIN public.organizations org ON org.id=member.org_id
           WHERE member.user_id=t.user_id AND member.org_id=t.org_id AND member.status='active' AND org.status='active')) THEN
        RAISE EXCEPTION 'TASK_LEGACY_SOURCE_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    IF p_kind='discover' AND p_source_id IS NULL THEN
        RETURN jsonb_build_object('outcome','found','sources',COALESCE((SELECT jsonb_agg(item) FROM (
            SELECT jsonb_build_object('kind','legacy_message','source_id',msg.id,'preview',left(msg.content::text,250)) item,
                msg.context_revision AS revision,msg.id AS sort_id
              FROM public.messages msg JOIN public.tasks old ON old.assistant_message_id=msg.id
              WHERE msg.conversation_id=t.conversation_id AND msg.org_id IS NOT DISTINCT FROM t.org_id
                AND msg.role='assistant' AND msg.status='completed' AND msg.message_kind='conversation'
                AND msg.context_revision>0 AND msg.context_revision<=t.base_context_revision
                AND old.type='chat' AND old.status='completed' AND old.execution_mode='serial'
                AND old.user_id=t.user_id AND old.org_id IS NOT DISTINCT FROM t.org_id
                AND old.conversation_id=t.conversation_id
            UNION ALL
            SELECT jsonb_build_object('kind','ecom_plan','source_id',p.id,'source_revision',p.plan_revision,
                'image_count',p.image_count,'preview',left(p.stage_outputs::text,250)),msg.context_revision,p.id
              FROM public.ecom_image_plans p JOIN public.tasks old ON old.id=p.parent_task_id
              JOIN public.messages msg ON msg.id=old.assistant_message_id
              WHERE p.user_id=t.user_id AND p.org_id IS NOT DISTINCT FROM t.org_id
                AND p.conversation_id=t.conversation_id AND p.status='ready'
                AND old.type='chat' AND old.status='completed' AND old.execution_mode='serial'
                AND old.user_id=t.user_id AND old.org_id IS NOT DISTINCT FROM t.org_id
                AND old.conversation_id=t.conversation_id
                AND msg.conversation_id=t.conversation_id AND msg.org_id IS NOT DISTINCT FROM t.org_id
                AND msg.role='assistant' AND msg.status='completed' AND msg.message_kind='conversation'
                AND msg.context_revision>0 AND msg.context_revision<=t.base_context_revision
              ORDER BY revision DESC,sort_id LIMIT 20) found),'[]'::jsonb));
    END IF;
    IF p_kind='ecom_plan' THEN
        SELECT * INTO plan FROM public.ecom_image_plans WHERE id=p_source_id AND user_id=t.user_id
            AND org_id IS NOT DISTINCT FROM t.org_id AND conversation_id=t.conversation_id
            AND status='ready' AND plan_revision=p_source_revision;
        IF NOT FOUND THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
        SELECT * INTO source FROM public.tasks WHERE id=plan.parent_task_id;
        output:=jsonb_build_object('plan',jsonb_build_object('id',plan.id,'status',plan.status,
            'plan_revision',plan.plan_revision,'stage_outputs',plan.stage_outputs,'items',plan.items,'target_size',plan.target_size));
    ELSIF p_kind='legacy_message' AND p_source_revision IS NULL THEN
        SELECT * INTO source FROM public.tasks WHERE assistant_message_id=p_source_id AND type='chat'
            AND user_id=t.user_id AND org_id IS NOT DISTINCT FROM t.org_id AND conversation_id=t.conversation_id
            AND status='completed' ORDER BY created_at DESC LIMIT 1;
        output:='{}'::jsonb;
    ELSE RAISE EXCEPTION 'TASK_LEGACY_SOURCE_INPUT_INVALID' USING ERRCODE='22023'; END IF;
    IF source.id IS NULL OR source.user_id<>t.user_id OR source.org_id IS DISTINCT FROM t.org_id
       OR source.conversation_id<>t.conversation_id OR source.type<>'chat' OR source.execution_mode<>'serial' THEN
        RETURN jsonb_build_object('outcome','not_found');
    END IF;
    SELECT * INTO m FROM public.messages WHERE id=source.assistant_message_id AND conversation_id=t.conversation_id
        AND org_id IS NOT DISTINCT FROM t.org_id AND role='assistant' AND status='completed'
        AND message_kind='conversation' AND context_revision>0 AND context_revision<=t.base_context_revision;
    IF source.id<>t.id AND (source.status<>'completed' OR m.id IS NULL) THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
    IF p_kind='legacy_message' THEN
        BEGIN content:=m.content::jsonb;
        EXCEPTION WHEN invalid_text_representation THEN RETURN jsonb_build_object('outcome','not_found'); END;
        IF jsonb_typeof(content)<>'array' THEN RETURN jsonb_build_object('outcome','not_found'); END IF;
        output:=jsonb_build_object('parts',(SELECT COALESCE(jsonb_agg(jsonb_build_object('content_index',position-1,'text',part->>'text') ORDER BY position),'[]'::jsonb)
            FROM jsonb_array_elements(content) WITH ORDINALITY parts(part,position) WHERE part->>'type'='text'),'text',(SELECT string_agg(part->>'text',E'\n' ORDER BY position)
            FROM jsonb_array_elements(content) WITH ORDINALITY parts(part,position) WHERE part->>'type'='text'));
    END IF;
    RETURN output||jsonb_build_object('outcome','found','kind',p_kind,'source_id',p_source_id,
        'source_task_id',source.id,'source_revision',p_source_revision);
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_legacy_source(uuid,uuid,uuid,bigint,text,uuid,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_legacy_source(uuid,uuid,uuid,bigint,text,uuid,bigint) TO everydayai;
RESET ROLE;
