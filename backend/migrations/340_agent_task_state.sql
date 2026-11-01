-- Logical-task facts share the existing Actor checkpoint transaction and fence.
SET LOCAL ROLE everydayai;

CREATE TABLE public.agent_task_states (
    root_task_id uuid PRIMARY KEY REFERENCES public.tasks(id) ON DELETE CASCADE,
    conversation_id uuid NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
    actor_user_id uuid NOT NULL,
    org_id uuid,
    owner_task_id uuid NOT NULL REFERENCES public.tasks(id),
    revision bigint NOT NULL CHECK (revision >= 0),
    state jsonb NOT NULL CHECK (jsonb_typeof(state) = 'object' AND state->>'version' = '1'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX agent_task_states_conversation ON public.agent_task_states(conversation_id, updated_at);

CREATE TABLE public.agent_task_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    root_task_id uuid NOT NULL REFERENCES public.agent_task_states(root_task_id) ON DELETE CASCADE,
    actor_user_id uuid NOT NULL,
    org_id uuid,
    dedupe_key text NOT NULL CHECK (length(dedupe_key) BETWEEN 1 AND 300),
    expected_revision bigint NOT NULL CHECK (expected_revision >= 0),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','queued','applied','obsolete')),
    continuation_task_id uuid REFERENCES public.tasks(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(root_task_id, dedupe_key)
);
CREATE INDEX agent_task_events_pending ON public.agent_task_events(created_at) WHERE status = 'pending';

ALTER TABLE public.agent_task_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_task_states FORCE ROW LEVEL SECURITY;
ALTER TABLE public.agent_task_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_task_events FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_task_state_scope ON public.agent_task_states
    USING (current_setting('app.access_kind', true) = 'runtime'
       AND actor_user_id = NULLIF(current_setting('app.actor_user_id', true),'')::uuid
       AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id', true),'')::uuid)
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime'
       AND actor_user_id = NULLIF(current_setting('app.actor_user_id', true),'')::uuid
       AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id', true),'')::uuid);
CREATE POLICY agent_task_event_scope ON public.agent_task_events
    USING (current_setting('app.access_kind', true) = 'runtime'
       AND actor_user_id = NULLIF(current_setting('app.actor_user_id', true),'')::uuid
       AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id', true),'')::uuid)
    WITH CHECK (current_setting('app.access_kind', true) = 'runtime'
       AND actor_user_id = NULLIF(current_setting('app.actor_user_id', true),'')::uuid
       AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id', true),'')::uuid
       AND EXISTS (SELECT 1 FROM public.agent_task_states s WHERE s.root_task_id=agent_task_events.root_task_id
                   AND s.actor_user_id=agent_task_events.actor_user_id
                   AND s.org_id IS NOT DISTINCT FROM agent_task_events.org_id));
REVOKE ALL ON public.agent_task_states, public.agent_task_events FROM PUBLIC;

-- A trusted Skill activation may ADD method obligations. It cannot edit any
-- existing requirement, method, gate, goal, evidence, effect or authority fact.
CREATE FUNCTION public.agent_task_method_extension_valid(p_old jsonb, p_new jsonb, p_skills jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE
    v_binding jsonb;
    v_requirement jsonb;
    v_gate jsonb;
    v_id text;
    v_count integer;
    v_old_bindings jsonb := COALESCE(p_old->'method_bindings','[]'::jsonb);
    v_new_bindings jsonb := COALESCE(p_new->'method_bindings','[]'::jsonb);
BEGIN
    IF p_old->>'status' IS DISTINCT FROM 'active'
       OR jsonb_typeof(v_new_bindings) IS DISTINCT FROM 'array'
       OR jsonb_typeof(p_new->'capability_gates') IS DISTINCT FROM 'array'
       OR jsonb_typeof(p_new->'applicable_method_ids') IS DISTINCT FROM 'array'
       OR jsonb_typeof(p_skills->'active') IS DISTINCT FROM 'array'
       OR jsonb_typeof(p_skills->'directory') IS DISTINCT FROM 'array'
       OR jsonb_array_length(v_new_bindings)<=jsonb_array_length(v_old_bindings)
       OR (p_old-ARRAY['revision','requirements','method_bindings','capability_gates','applicable_method_ids'])
          IS DISTINCT FROM (p_new-ARRAY['revision','requirements','method_bindings','capability_gates','applicable_method_ids'])
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_old->'requirements') old
                 WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_new->'requirements') new WHERE new=old))
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(v_old_bindings) old
                 WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(v_new_bindings) new WHERE new=old))
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(p_old->'capability_gates') old
                 WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_new->'capability_gates') new WHERE new=old))
       OR NOT ((p_new->'applicable_method_ids') @> (p_old->'applicable_method_ids'))
       OR (SELECT count(*)<>count(DISTINCT r->>'id') FROM jsonb_array_elements(p_new->'requirements') r)
       OR (SELECT count(*)<>count(DISTINCT b->>'skill_key') FROM jsonb_array_elements(v_new_bindings) b) THEN
        RETURN false;
    END IF;
    SELECT COALESCE(max((r->>'minimum')::integer),0) INTO v_count
      FROM jsonb_array_elements(p_old->'requirements') r WHERE r->>'artifact_kind'='image';
    IF v_count=0 THEN
        SELECT COALESCE(max((r->>'item_count')::integer),0) INTO v_count
          FROM jsonb_array_elements(p_old->'requirements') r WHERE r->>'artifact_kind' IN ('text','file');
    END IF;
    FOR v_binding IN SELECT b FROM jsonb_array_elements(v_new_bindings) b
                     WHERE NOT (v_old_bindings @> jsonb_build_array(b)) LOOP
        v_id := 'method.'||(v_binding->>'skill_key')||'.planning';
        IF v_binding->>'profile_id' NOT IN ('reference-prompts-v1','ecommerce-image-plan-v1')
           OR COALESCE(v_binding->>'contract_source','reviewed') NOT IN ('reviewed','declared')
           OR (COALESCE(v_binding->>'contract_source','reviewed')='reviewed'
               AND v_binding->'retire_on_complete' IS DISTINCT FROM to_jsonb(v_binding->>'profile_id'='reference-prompts-v1'))
           OR (COALESCE(v_binding->>'contract_source','reviewed')='reviewed'
               AND (v_binding->>'package_id',v_binding->>'skill_key',v_binding->>'revision',v_binding->>'body_sha256',v_binding->>'profile_id',v_binding->>'retire_on_complete')
                   NOT IN (('725d179e-a6fe-4282-9e8a-83876c336ff4','reference-image-prompts',
                            'vf540a7eb0eff4862b924d9af0f7032bd','dd85988f88e96ae446ff5588866b0d2641113d96b9a73bae332c947606e6e90e','reference-prompts-v1','true'),
                           ('4c7d133e-33e5-455a-9216-4b6cff0ce892','ecommerce-image-prompts',
                            'v5d3a01973e41404981324860769e3750','7f8e8de78b18105a2456a327e8bb9ee38edee9ed5d1ddae48a2e0a2c634df69e','ecommerce-image-plan-v1','false')))
           OR v_binding->'requirement_ids' IS DISTINCT FROM jsonb_build_array(v_id)
           OR v_binding->>'image_count' IS DISTINCT FROM
              (CASE WHEN v_count>0 THEN v_count WHEN v_binding->>'profile_id'='reference-prompts-v1' THEN 6 ELSE 1 END)::text
           OR NOT (p_new->'applicable_method_ids' @> jsonb_build_array(v_binding->>'skill_key'))
           OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p_skills->'active') a
                           JOIN jsonb_array_elements(p_skills->'directory') d ON d->>'skill_key'=a->>'skill_key'
                           WHERE a->>'skill_key'=v_binding->>'skill_key'
                             AND a->>'revision'=v_binding->>'revision'
                             AND a->>'body_sha256'=v_binding->>'body_sha256'
                             AND d->>'package_id'=v_binding->>'package_id'
                             AND d->>'revision'=a->>'revision'
                             AND ((COALESCE(v_binding->>'contract_source','reviewed')='reviewed'
                                   AND d->'catalog_metadata'->'task_stage' IS NULL)
                                  OR (v_binding->>'contract_source'='declared'
                                      AND d->'catalog_metadata'->'task_stage'->>'version'='1'
                                      AND d->'catalog_metadata'->'task_stage'->>'output_profile'=v_binding->>'profile_id'
                                      AND to_jsonb(d->'catalog_metadata'->'task_stage'->>'restriction_scope'='phase')
                                          =v_binding->'retire_on_complete'))) THEN
            RETURN false;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p_new->'requirements') r
                       WHERE r->>'id'=v_id AND r->>'source_message_id'=p_old->'source_message_ids'->>0
                         AND r->>'artifact_kind'='text' AND r->>'minimum'='1'
                         AND r->>'validator'=v_binding->>'profile_id') THEN
            RETURN false;
        END IF;
        FOREACH v_id IN ARRAY ARRAY['platform.generate_image','platform.image_agent'] LOOP
            IF NOT (p_new->'capability_gates' @> jsonb_build_array(jsonb_build_object(
                'capability',v_id,'requirement_ids',v_binding->'requirement_ids'))) THEN RETURN false; END IF;
        END LOOP;
    END LOOP;
    FOR v_requirement IN SELECT r FROM jsonb_array_elements(p_new->'requirements') r
                         WHERE NOT (p_old->'requirements' @> jsonb_build_array(r)) LOOP
        IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(v_new_bindings) b
                      WHERE NOT (v_old_bindings @> jsonb_build_array(b))
                        AND b->'requirement_ids' @> jsonb_build_array(v_requirement->>'id')) THEN RETURN false; END IF;
    END LOOP;
    FOR v_gate IN SELECT g FROM jsonb_array_elements(p_new->'capability_gates') g
                  WHERE NOT (p_old->'capability_gates' @> jsonb_build_array(g)) LOOP
        IF v_gate->>'capability' NOT IN ('platform.generate_image','platform.image_agent')
           OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(v_new_bindings) b
                         WHERE NOT (v_old_bindings @> jsonb_build_array(b))
                           AND b->'requirement_ids'=v_gate->'requirement_ids') THEN RETURN false; END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_new->'applicable_method_ids') k
              WHERE NOT (p_old->'applicable_method_ids' @> jsonb_build_array(k))
                AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(v_new_bindings) b
                               WHERE b->>'skill_key'=k#>>'{}')) THEN RETURN false; END IF;
    RETURN true;
END;
$$;
REVOKE ALL ON FUNCTION public.agent_task_method_extension_valid(jsonb,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_method_extension_valid(jsonb,jsonb,jsonb) TO everydayai;

CREATE FUNCTION public.save_agent_task_checkpoint(
    p_task_id uuid, p_execution_token uuid, p_safe_point text, p_state jsonb, p_expected_revision bigint
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
    v_root public.tasks%ROWTYPE;
    v_old public.agent_task_states%ROWTYPE;
    v_new jsonb := p_state->'_task_execution';
    v_root_id uuid;
    v_revision bigint;
    v_result jsonb;
BEGIN
    IF SESSION_USER <> 'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime' THEN
        RAISE EXCEPTION 'TASK_EXECUTION_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    IF v_new IS NULL OR jsonb_typeof(v_new) IS DISTINCT FROM 'object'
       OR v_new->>'version' IS DISTINCT FROM '1' OR p_expected_revision IS NULL OR p_expected_revision<0
       OR COALESCE(v_new->>'revision','') !~ '^[0-9]+$'
       OR jsonb_typeof(v_new->'requirements') IS DISTINCT FROM 'array'
       OR jsonb_array_length(v_new->'requirements') NOT BETWEEN 1 AND 32
       OR jsonb_typeof(v_new->'source_message_ids') IS DISTINCT FROM 'array'
       OR jsonb_array_length(v_new->'source_message_ids') NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'TASK_EXECUTION_STATE_INVALID' USING ERRCODE='22023';
    END IF;
    v_root_id := (v_new->>'root_task_id')::uuid;
    v_revision := (v_new->>'revision')::bigint;
    -- Same ordering for all attempts: current Actor first, logical-task row second.
    SELECT * INTO v_task FROM public.tasks WHERE id=p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.type <> 'chat' OR NOT (v_task.delivery_context @> '{"actor":true}') THEN
        RAISE EXCEPTION 'TASK_EXECUTION_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token THEN
        RETURN jsonb_build_object('outcome','ownership_lost');
    END IF;
    IF v_task.status <> 'running' THEN
        RETURN jsonb_build_object('outcome','terminal','status',v_task.status);
    END IF;
    IF v_task.lease_expires_at IS NULL OR v_task.lease_expires_at<=now() THEN
        RETURN jsonb_build_object('outcome','lease_expired');
    END IF;
    SELECT * INTO v_root FROM public.tasks WHERE id=v_root_id;
    IF NOT FOUND OR v_root.conversation_id IS DISTINCT FROM v_task.conversation_id
       OR v_root.user_id IS DISTINCT FROM v_task.user_id OR v_root.org_id IS DISTINCT FROM v_task.org_id
       OR v_new->>'conversation_id' IS DISTINCT FROM v_task.conversation_id::text
       OR v_new->>'actor_user_id' IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')
       OR v_task.user_id::text IS DISTINCT FROM v_new->>'actor_user_id'
       OR (v_new->>'org_id') IS DISTINCT FROM v_task.org_id::text
       OR v_task.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'TASK_EXECUTION_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_old FROM public.agent_task_states WHERE root_task_id=v_root_id FOR UPDATE;
    IF NOT FOUND THEN
        IF v_root_id<>p_task_id OR p_expected_revision<>0 THEN
            RAISE EXCEPTION 'TASK_EXECUTION_INITIAL_OWNER_INVALID' USING ERRCODE='42501';
        END IF;
    ELSE
        IF v_old.owner_task_id<>p_task_id THEN
            -- Ownership transfer requires the trusted continuation-enqueue protocol.
            IF NOT EXISTS (SELECT 1 FROM public.agent_task_events WHERE root_task_id=v_root_id
                AND continuation_task_id=p_task_id AND status='queued' AND expected_revision=v_old.revision) THEN
                RAISE EXCEPTION 'TASK_EXECUTION_OWNER_DENIED' USING ERRCODE='42501';
            END IF;
        END IF;
        IF v_old.state IS DISTINCT FROM v_new AND
           (v_old.revision<>p_expected_revision OR v_revision<=v_old.revision) THEN
            RAISE EXCEPTION 'TASK_EXECUTION_STALE' USING ERRCODE='40001';
        END IF;
        IF (v_old.state->'requirements',v_old.state->'source_message_ids',v_old.state->'original_goal',
            v_old.state->'requirements_revision',v_old.state->'branch_id',v_old.state->'capability_gates',
            v_old.state->'execution_identity',v_old.state->'method_bindings',
            v_old.state->'intent_action',v_old.state->'applicable_method_ids',v_old.state->'image_binding_version') IS DISTINCT FROM
           (v_new->'requirements',v_new->'source_message_ids',v_new->'original_goal',
            v_new->'requirements_revision',v_new->'branch_id',v_new->'capability_gates',
            v_new->'execution_identity',v_new->'method_bindings',
            v_new->'intent_action',v_new->'applicable_method_ids',v_new->'image_binding_version')
           AND public.agent_task_method_extension_valid(v_old.state,v_new,p_state->'skill_runtime') IS NOT TRUE THEN
            RAISE EXCEPTION 'TASK_EXECUTION_REQUIREMENTS_IMMUTABLE' USING ERRCODE='42501';
        END IF;
    END IF;
    IF NOT (v_new->'source_message_ids' @> jsonb_build_array(v_root.input_message_id::text))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(v_new->'source_message_ids') source(id)
           WHERE NOT EXISTS (SELECT 1 FROM public.messages m WHERE m.id::text=source.id
                 AND m.conversation_id=v_task.conversation_id AND m.role='user')) THEN
        RAISE EXCEPTION 'TASK_EXECUTION_INPUT_ANCHOR_INVALID' USING ERRCODE='42501';
    END IF;
    IF EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(v_old.state->'artifacts','[]'::jsonb)) old
              WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(v_new->'artifacts') new WHERE new=old))
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(v_old.state->'results','[]'::jsonb)) old
                 WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(v_new->'results') new WHERE new=old))
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(v_new->'artifacts') a
                 WHERE NOT (COALESCE(v_old.state->'artifacts','[]'::jsonb) @> jsonb_build_array(a))
                   AND a->>'delivery_task_id' IS DISTINCT FROM p_task_id::text)
       OR EXISTS(SELECT 1 FROM jsonb_array_elements(v_new->'results') r
                 WHERE NOT (COALESCE(v_old.state->'results','[]'::jsonb) @> jsonb_build_array(r))
                   AND r->>'completed_task_id' IS DISTINCT FROM p_task_id::text) THEN
        RAISE EXCEPTION 'TASK_ARTIFACT_DELIVERY_IMMUTABLE' USING ERRCODE='42501';
    END IF;
    v_result := public.save_generation_checkpoint(p_task_id,p_execution_token,p_safe_point,p_state);
    IF v_result->>'outcome' IS DISTINCT FROM 'saved' THEN RETURN v_result; END IF;
    INSERT INTO public.agent_task_states(root_task_id,conversation_id,actor_user_id,org_id,owner_task_id,revision,state)
      VALUES(v_root_id,v_task.conversation_id,v_task.user_id,v_task.org_id,p_task_id,v_revision,v_new)
      ON CONFLICT(root_task_id) DO UPDATE SET owner_task_id=EXCLUDED.owner_task_id,
          revision=EXCLUDED.revision,state=EXCLUDED.state,updated_at=now();
    RETURN v_result || jsonb_build_object('task_execution_revision',v_revision);
END;
$$;
REVOKE ALL ON FUNCTION public.save_agent_task_checkpoint(uuid,uuid,text,jsonb,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.save_agent_task_checkpoint(uuid,uuid,text,jsonb,bigint) TO everydayai;

-- Read only actual completed text deliverables visible in this Actor's frozen
-- history. Model arguments select a reference, never its scope or validator.
CREATE FUNCTION public.read_agent_task_text_artifacts(
    p_task_id uuid, p_execution_token uuid, p_logical_task_id uuid, p_expected_revision bigint,
    p_source_logical_task_id uuid DEFAULT NULL, p_artifact_id text DEFAULT NULL, p_limit integer DEFAULT 10
) RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE
    v_task public.tasks%ROWTYPE;
    v_current public.agent_task_states%ROWTYPE;
    v_items jsonb;
BEGIN
    IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime' THEN
        RAISE EXCEPTION 'TASK_ARTIFACT_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    IF p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 20
       OR ((p_source_logical_task_id IS NULL) <> (p_artifact_id IS NULL)) THEN
        RAISE EXCEPTION 'TASK_ARTIFACT_INPUT_INVALID' USING ERRCODE='22023';
    END IF;
    SELECT * INTO v_task FROM public.tasks WHERE id=p_task_id;
    IF NOT FOUND OR v_task.user_id::text IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')
       OR v_task.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
       OR v_task.type<>'chat' OR NOT(v_task.delivery_context @> '{"actor":true}') THEN
        RAISE EXCEPTION 'TASK_ARTIFACT_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    IF v_task.execution_token IS DISTINCT FROM p_execution_token OR v_task.status<>'running' THEN
        RETURN jsonb_build_object('outcome','ownership_lost');
    END IF;
    IF v_task.lease_expires_at IS NULL OR v_task.lease_expires_at<=now() THEN
        RETURN jsonb_build_object('outcome','lease_expired');
    END IF;
    SELECT * INTO v_current FROM public.agent_task_states WHERE root_task_id=p_logical_task_id
        AND owner_task_id=p_task_id AND conversation_id=v_task.conversation_id
        AND actor_user_id=v_task.user_id AND org_id IS NOT DISTINCT FROM v_task.org_id;
    IF NOT FOUND OR v_current.revision IS DISTINCT FROM p_expected_revision THEN
        RETURN jsonb_build_object('outcome','stale');
    END IF;
    SELECT COALESCE(jsonb_agg(item),'[]'::jsonb) INTO v_items FROM (
        SELECT jsonb_build_object('source_logical_task_id',s.root_task_id,
                   'source_goal',left(s.state->>'original_goal',500),
                   'artifact',CASE WHEN p_source_logical_task_id IS NULL THEN a-'inline_text' ELSE a END) item
        FROM public.agent_task_states s
        CROSS JOIN LATERAL jsonb_array_elements(s.state->'artifacts') a
        JOIN public.tasks source_task ON source_task.id::text=a->>'delivery_task_id'
        JOIN public.messages m ON m.id=source_task.assistant_message_id
        WHERE s.root_task_id<>v_current.root_task_id
          AND s.conversation_id=v_current.conversation_id AND s.actor_user_id=v_current.actor_user_id
          AND s.org_id IS NOT DISTINCT FROM v_current.org_id
          AND s.state->'execution_identity'=v_current.state->'execution_identity'
          AND s.state->'branch_id' IS NOT DISTINCT FROM v_current.state->'branch_id'
          AND source_task.user_id=v_current.actor_user_id AND source_task.conversation_id=v_current.conversation_id
          AND source_task.org_id IS NOT DISTINCT FROM v_current.org_id AND source_task.type='chat'
          AND source_task.status='completed'
          AND m.conversation_id=v_current.conversation_id AND m.role='assistant' AND m.status='completed'
          AND m.message_kind='conversation' AND m.context_revision>0
          AND m.context_revision<=v_task.base_context_revision
          AND a->>'kind'='text' AND jsonb_typeof(a->'inline_text')='string'
          AND EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'results') r
                     JOIN public.tasks completion_task ON completion_task.id::text=r->>'completed_task_id'
                     JOIN public.messages completion_message ON completion_message.id=completion_task.assistant_message_id
                     WHERE r->>'step_id'=a->>'step_id' AND r->'artifact_ids' @> jsonb_build_array(a->>'id')
                       AND completion_task.user_id=v_current.actor_user_id
                       AND completion_task.org_id IS NOT DISTINCT FROM v_current.org_id
                       AND completion_task.conversation_id=v_current.conversation_id
                       AND completion_task.type='chat' AND completion_task.status='completed'
                       AND completion_message.conversation_id=v_current.conversation_id
                       AND completion_message.role='assistant' AND completion_message.status='completed'
                       AND completion_message.message_kind='conversation' AND completion_message.context_revision>0
                       AND completion_message.context_revision<=v_task.base_context_revision)
          AND (p_source_logical_task_id IS NULL OR (s.root_task_id=p_source_logical_task_id AND a->>'id'=p_artifact_id))
        ORDER BY s.updated_at DESC, a->>'id' LIMIT p_limit
    ) eligible;
    RETURN jsonb_build_object('outcome','found','artifacts',v_items);
END;
$$;
REVOKE ALL ON FUNCTION public.read_agent_task_text_artifacts(uuid,uuid,uuid,bigint,uuid,text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_text_artifacts(uuid,uuid,uuid,bigint,uuid,text,integer) TO everydayai;

-- Protect ALL writers, including the legacy checkpoint RPC. Checks are deferred
-- so both projections can be updated in either order inside the same transaction.
CREATE FUNCTION public.guard_agent_task_checkpoint() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE
    v_task_id uuid;
    v_root_id uuid;
    v_state jsonb;
    v_logical public.agent_task_states%ROWTYPE;
BEGIN
    IF TG_TABLE_NAME='agent_task_states' THEN
        IF TG_OP='DELETE' THEN
            IF EXISTS(SELECT 1 FROM public.conversation_turn_checkpoints
                WHERE state->'_task_execution'->>'root_task_id'=OLD.root_task_id::text) THEN
                RAISE EXCEPTION 'TASK_EXECUTION_STATE_DELETE_DENIED' USING ERRCODE='22023';
            END IF;
            RETURN NULL;
        END IF;
        SELECT * INTO v_logical FROM public.agent_task_states WHERE root_task_id=NEW.root_task_id;
        IF NOT FOUND THEN RETURN NULL; END IF;
        v_task_id := v_logical.owner_task_id;
    ELSE
        IF TG_OP='UPDATE' AND NEW.state IS NOT DISTINCT FROM OLD.state THEN RETURN NULL; END IF;
        v_task_id := CASE WHEN TG_OP='DELETE' THEN OLD.task_id ELSE NEW.task_id END;
    END IF;
    SELECT state INTO v_state FROM public.conversation_turn_checkpoints WHERE task_id=v_task_id;
    IF v_state IS NULL OR NOT (v_state ? '_task_execution') THEN
        IF EXISTS(SELECT 1 FROM public.agent_task_states WHERE owner_task_id=v_task_id) THEN
            RAISE EXCEPTION 'TASK_EXECUTION_DOWNGRADE_DENIED' USING ERRCODE='22023';
        END IF;
        RETURN NULL; -- Never-enabled legacy checkpoints retain their behavior.
    END IF;
    IF jsonb_typeof(v_state->'_task_execution') IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'TASK_EXECUTION_STATE_INVALID' USING ERRCODE='22023';
    END IF;
    v_root_id := (v_state->'_task_execution'->>'root_task_id')::uuid;
    SELECT * INTO v_logical FROM public.agent_task_states WHERE root_task_id=v_root_id;
    IF NOT FOUND OR v_logical.owner_task_id<>v_task_id
       OR v_logical.state IS DISTINCT FROM v_state->'_task_execution' THEN
        RAISE EXCEPTION 'TASK_EXECUTION_CHECKPOINT_INCONSISTENT' USING ERRCODE='22023';
    END IF;
    RETURN NULL;
END;
$$;
GRANT EXECUTE ON FUNCTION public.guard_agent_task_checkpoint() TO everydayai_owner;
-- Existing installations can have either of the two controlled schema owners.
-- Use the actual owner for trigger DDL; never broaden runtime table privileges.
DO $$ DECLARE v_owner text; BEGIN
    SELECT pg_get_userbyid(relowner) INTO v_owner FROM pg_class
     WHERE oid='public.conversation_turn_checkpoints'::regclass;
    IF v_owner NOT IN ('everydayai','everydayai_owner') THEN
        RAISE EXCEPTION 'TASK_EXECUTION_CHECKPOINT_OWNER_UNSUPPORTED';
    END IF;
    EXECUTE format('SET LOCAL ROLE %I',v_owner);
END $$;
CREATE CONSTRAINT TRIGGER agent_task_checkpoint_consistency
    AFTER INSERT OR UPDATE OR DELETE ON public.conversation_turn_checkpoints
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.guard_agent_task_checkpoint();
SET LOCAL ROLE everydayai;
CREATE CONSTRAINT TRIGGER agent_task_state_consistency
    AFTER INSERT OR UPDATE OR DELETE ON public.agent_task_states
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.guard_agent_task_checkpoint();
REVOKE ALL ON FUNCTION public.guard_agent_task_checkpoint() FROM PUBLIC;
RESET ROLE;
