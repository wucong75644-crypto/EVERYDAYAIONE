-- Authenticated input review and requirement versions. No new business executor.
SET LOCAL ROLE everydayai;
CREATE TABLE public.agent_task_user_change_reviews(
 root_task_id uuid NOT NULL REFERENCES public.agent_task_states(root_task_id) ON DELETE CASCADE,
 event_id uuid NOT NULL REFERENCES public.conversation_control_events(id) ON DELETE CASCADE,
 relation text NOT NULL CHECK(relation IN ('continue','amend','unrelated')),
 requirements_revision bigint NOT NULL,
 source_sha256 text NOT NULL,
 review jsonb NOT NULL,
 projection jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(root_task_id,event_id)
);
ALTER TABLE public.agent_task_user_change_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_task_user_change_reviews FORCE ROW LEVEL SECURITY;
CREATE POLICY agent_task_user_change_review_scope ON public.agent_task_user_change_reviews
 USING(EXISTS(SELECT 1 FROM public.agent_task_states s WHERE s.root_task_id=agent_task_user_change_reviews.root_task_id))
 WITH CHECK(EXISTS(SELECT 1 FROM public.agent_task_states s WHERE s.root_task_id=agent_task_user_change_reviews.root_task_id
   AND s.actor_user_id=NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   AND s.org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
   AND current_setting('app.access_kind',true)='runtime'));
CREATE FUNCTION public.agent_task_requirement_projection(p_state jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('requirements',p_state->'requirements','requirements_revision',p_state->'requirements_revision',
 'requirements_history',COALESCE(p_state->'requirements_history','[]'::jsonb),'capability_gates',p_state->'capability_gates',
 'method_bindings',p_state->'method_bindings','applicable_method_ids',p_state->'applicable_method_ids','intent_action',p_state->'intent_action')
$$;
REVOKE ALL ON FUNCTION public.agent_task_requirement_projection(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_requirement_projection(jsonb) TO everydayai,everydayai_owner;
CREATE FUNCTION public.agent_task_unreviewed_changes(p_root_task_id uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT COALESCE(jsonb_agg(jsonb_build_object('event_id',e.id,'owner_task_id',e.task_id,'message',btrim(e.payload->>'message'),
 'message_sha256',encode(sha256(convert_to(btrim(e.payload->>'message'),'UTF8')),'hex')) ORDER BY e.event_sequence,e.id),'[]'::jsonb)
 FROM public.agent_task_states s JOIN public.tasks t ON t.user_id=s.actor_user_id AND t.org_id IS NOT DISTINCT FROM s.org_id
   AND t.conversation_id=s.conversation_id AND (t.id=s.root_task_id OR t.delivery_context->>'logical_task_id'=s.root_task_id::text)
 JOIN public.conversation_control_events e ON e.task_id=t.id AND e.conversation_id=s.conversation_id AND e.turn_id IS NOT DISTINCT FROM t.turn_id
 WHERE s.root_task_id=p_root_task_id AND e.event_type='steer' AND e.status IN ('pending','applied')
   AND NOT EXISTS(SELECT 1 FROM public.agent_task_user_change_reviews r WHERE r.root_task_id=s.root_task_id AND r.event_id=e.id)
$$;
REVOKE ALL ON FUNCTION public.agent_task_unreviewed_changes(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_unreviewed_changes(uuid) TO everydayai;
CREATE FUNCTION public.read_agent_task_user_change_pending(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE;
BEGIN
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id;
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id;
 IF SESSION_USER<>'everydayai' OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime'
   OR t.id IS NULL OR s.owner_task_id IS DISTINCT FROM t.id OR t.user_id IS DISTINCT FROM s.actor_user_id
   OR t.user_id IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')::uuid
   OR t.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
   OR s.org_id IS DISTINCT FROM t.org_id OR s.conversation_id IS DISTINCT FROM t.conversation_id
   OR t.execution_token IS DISTINCT FROM p_execution_token OR t.status IS DISTINCT FROM 'running'
   OR t.lease_expires_at IS NULL OR t.lease_expires_at<=now()
   OR NOT EXISTS(SELECT 1 FROM public.users WHERE id=t.user_id AND status::text='active')
   OR (t.org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations o ON o.id=m.org_id
     WHERE m.user_id=t.user_id AND m.org_id=t.org_id AND m.status='active' AND o.status='active')) THEN
   RAISE EXCEPTION 'TASK_REVISION_SCOPE_DENIED' USING ERRCODE='42501';
 END IF;
 RETURN jsonb_build_object('outcome','found','events',public.agent_task_unreviewed_changes(s.root_task_id));
END $$;
REVOKE ALL ON FUNCTION public.read_agent_task_user_change_pending(uuid,uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.read_agent_task_user_change_pending(uuid,uuid,uuid) TO everydayai;
-- Existing dynamic activation retains its method-extension guard after a
-- requirement revision. Normalize only the current host IDs for that guard;
-- archive, steps, receipts and execution facts remain untouched.
CREATE FUNCTION public.agent_task_unprefix_revision_state(p_state jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE prefix text:='r'||(p_state->>'requirements_revision')||'.'; key text; normalized jsonb;
BEGIN
 IF NOT p_state ? 'requirements_history' THEN RETURN p_state; END IF;
 SELECT COALESCE(jsonb_agg(r||jsonb_build_object('id',CASE WHEN starts_with(r->>'id',prefix) THEN substr(r->>'id',length(prefix)+1) ELSE r->>'id' END)),'[]')
   INTO normalized FROM jsonb_array_elements(p_state->'requirements') r;
 p_state:=jsonb_set(p_state,'{requirements}',normalized);
 FOREACH key IN ARRAY ARRAY['capability_gates','method_bindings'] LOOP
   SELECT COALESCE(jsonb_agg(r||jsonb_build_object('requirement_ids',(SELECT jsonb_agg(CASE WHEN starts_with(id#>>'{}',prefix)
       THEN substr(id#>>'{}',length(prefix)+1) ELSE id#>>'{}' END) FROM jsonb_array_elements(r->'requirement_ids') id))),'[]')
     INTO normalized FROM jsonb_array_elements(p_state->key) r;
   p_state:=jsonb_set(p_state,ARRAY[key],normalized);
 END LOOP;
 RETURN p_state;
END $$;
REVOKE ALL ON FUNCTION public.agent_task_unprefix_revision_state(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_unprefix_revision_state(jsonb) TO everydayai,everydayai_owner;
ALTER FUNCTION public.agent_task_method_extension_valid(jsonb,jsonb,jsonb) RENAME TO agent_task_method_extension_valid_before364;
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
    p_old:=public.agent_task_unprefix_revision_state(p_old);
    p_new:=public.agent_task_unprefix_revision_state(p_new);
    v_old_bindings:=COALESCE(p_old->'method_bindings','[]'::jsonb);
    v_new_bindings:=COALESCE(p_new->'method_bindings','[]'::jsonb);
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
    SELECT COALESCE(sum((r->>'minimum')::integer),0) INTO v_count
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
GRANT EXECUTE ON FUNCTION public.agent_task_method_extension_valid(jsonb,jsonb,jsonb) TO everydayai,everydayai_owner;
CREATE FUNCTION public.guard_agent_task_requirement_revision() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE before_projection jsonb; after_projection jsonb;
BEGIN
 before_projection:=public.agent_task_requirement_projection(OLD.state);
 after_projection:=public.agent_task_requirement_projection(NEW.state);
 IF before_projection=after_projection THEN RETURN NEW; END IF;
 IF COALESCE(OLD.state->'requirements_history','[]'::jsonb)=COALESCE(NEW.state->'requirements_history','[]'::jsonb)
   AND OLD.state->'requirements_revision'=NEW.state->'requirements_revision'
   AND public.agent_task_method_extension_valid(OLD.state,NEW.state,(SELECT state->'skill_runtime' FROM public.conversation_turn_checkpoints WHERE task_id=NEW.owner_task_id)) THEN
   RETURN NEW;
 END IF;
 IF NOT EXISTS(SELECT 1 FROM public.agent_task_user_change_reviews r WHERE r.root_task_id=NEW.root_task_id
   AND r.relation='amend' AND r.requirements_revision=(NEW.state->>'requirements_revision')::bigint AND r.projection=after_projection) THEN
   RAISE EXCEPTION 'TASK_REQUIREMENT_REVISION_UNAUTHORIZED' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_agent_task_requirement_revision() FROM PUBLIC;
CREATE TRIGGER agent_task_requirement_revision_guard BEFORE UPDATE OF state ON public.agent_task_states
 FOR EACH ROW EXECUTE FUNCTION public.guard_agent_task_requirement_revision();
-- Rebuild the host-owned projection; a matching observer never authorizes an
-- extra user delivery, arbitrary profile, omitted prerequisite or new pin.
CREATE FUNCTION public.agent_task_revision_projection_valid(p_old jsonb,p_new jsonb,p_proposal jsonb,p_skills jsonb,p_previous_skills jsonb)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE reqs jsonb:='[]'; gates jsonb:='[]'; bindings jsonb:='[]'; ids jsonb:=p_proposal->'applicable_method_ids';
 r jsonb; a jsonb; d jsonb; b jsonb; profile text; retire boolean; source text; rid text;
 prefix text:='r'||(p_new->>'requirements_revision')||'.'; images integer:=0; content_count integer:=0;
 message_id jsonb:=p_old->'source_message_ids'->0; action text:=p_proposal->>'action'; key text;
BEGIN
 IF jsonb_typeof(p_proposal->'requirements') IS DISTINCT FROM 'array' OR jsonb_array_length(p_proposal->'requirements') NOT BETWEEN 1 AND 24
    OR jsonb_typeof(ids) IS DISTINCT FROM 'array' OR jsonb_array_length(ids)>4
    OR action NOT IN ('plan','execute_ready','execute_prompt','edit','explain','other') THEN RETURN false; END IF;
 FOR r IN SELECT value FROM jsonb_array_elements(p_proposal->'requirements') LOOP
   b:=jsonb_build_object('id',prefix||'user.'||(r->>'id'),'source_message_id',message_id,'description',r->'description',
       'artifact_kind',r->'artifact_kind','minimum',r->'minimum','validator','artifact.exists');
   IF r->'item_count' IS NOT NULL AND r->'item_count'<>'null'::jsonb THEN b:=b||jsonb_build_object('item_count',r->'item_count'); END IF;
   reqs:=reqs||jsonb_build_array(b);
   IF r->>'artifact_kind'='image' THEN images:=images+(r->>'minimum')::int; END IF;
   IF r->>'artifact_kind' IN ('text','file') THEN content_count:=greatest(content_count,COALESCE((r->>'item_count')::int,0)); END IF;
 END LOOP;
 IF images>0 THEN
   rid:=prefix||CASE WHEN action IN ('execute_ready','execute_prompt') THEN 'host.image_source' ELSE 'host.image_plan' END;
   reqs:=reqs||jsonb_build_array(jsonb_build_object('id',rid,'source_message_id',message_id,'minimum',1,'artifact_kind','text',
       'description',CASE WHEN action IN ('execute_ready','execute_prompt') THEN '核验并读取要执行的原图片方案或最终提示词' ELSE '保存完整逐图制作方案和提示词，供后续原图绑定及执行' END,
       'validator',CASE WHEN action IN ('execute_ready','execute_prompt') THEN 'image.source.valid' ELSE 'image.plan.valid' END));
   gates:=jsonb_build_array(jsonb_build_object('capability','platform.generate_image','requirement_ids',jsonb_build_array(rid)),
       jsonb_build_object('capability','platform.image_agent','requirement_ids',jsonb_build_array(rid)));
 END IF;
 IF (p_skills IS NULL OR p_skills='null'::jsonb) IS DISTINCT FROM (p_previous_skills IS NULL OR p_previous_skills='null'::jsonb) THEN RETURN false; END IF;
 IF p_skills IS NOT NULL AND p_skills<>'null'::jsonb THEN
   IF p_skills->'directory' IS DISTINCT FROM p_previous_skills->'directory'
      OR p_skills->'manual_skill_id' IS DISTINCT FROM p_previous_skills->'manual_skill_id'
      OR p_skills->'session_skill_ids' IS DISTINCT FROM p_previous_skills->'session_skill_ids' THEN RETURN false; END IF;
   -- Revision apply only reopens phases. Removing participants must go through
   -- the original protected Skill control and its own confirmed checkpoint.
   IF (SELECT jsonb_agg(jsonb_build_array(x->'skill_key',x->'revision',x->'body_sha256') ORDER BY x->>'skill_key')
         FROM jsonb_array_elements(COALESCE(p_skills->'active','[]')||COALESCE(p_skills->'resident','[]')) x)
      IS DISTINCT FROM
      (SELECT jsonb_agg(jsonb_build_array(x->'skill_key',x->'revision',x->'body_sha256') ORDER BY x->>'skill_key')
         FROM jsonb_array_elements(COALESCE(p_previous_skills->'active','[]')||COALESCE(p_previous_skills->'resident','[]')) x) THEN RETURN false; END IF;
   FOR a IN SELECT value FROM jsonb_array_elements(p_skills->'active') LOOP
     IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(p_previous_skills->'active','[]')||COALESCE(p_previous_skills->'resident','[]')) prior
        WHERE prior->>'skill_key'=a->>'skill_key' AND prior->'revision'=a->'revision' AND prior->'body_sha256'=a->'body_sha256') THEN RETURN false; END IF;
   END LOOP;
   IF action='plan' THEN
     FOR key IN SELECT value#>>'{}' FROM jsonb_array_elements(COALESCE(p_skills->'session_skill_ids','[]')||
           CASE WHEN p_skills->>'manual_skill_id' IS NULL THEN '[]'::jsonb ELSE jsonb_build_array(p_skills->>'manual_skill_id') END) LOOP
       IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_skills->'active') active_row WHERE active_row->>'skill_key'=key) AND NOT ids @> jsonb_build_array(key) THEN ids:=ids||jsonb_build_array(key); END IF;
     END LOOP;
   END IF;
   FOR key IN SELECT value#>>'{}' FROM jsonb_array_elements(ids) LOOP
     IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(p_skills->'active') active_row WHERE active_row->>'skill_key'=key) THEN RETURN false; END IF;
   END LOOP;
   FOR a IN SELECT value FROM jsonb_array_elements(p_skills->'active') LOOP
     SELECT value INTO d FROM jsonb_array_elements(p_skills->'directory') WHERE value->>'skill_key'=a->>'skill_key';
     profile:=NULL;retire:=NULL;source:='reviewed';
     IF NULLIF(d#>'{catalog_metadata,task_stage}','null'::jsonb) IS NOT NULL THEN
       IF d#>>'{catalog_metadata,task_stage,version}' IS DISTINCT FROM '1' THEN RETURN false; END IF;
       profile:=d#>>'{catalog_metadata,task_stage,output_profile}';retire:=d#>>'{catalog_metadata,task_stage,restriction_scope}'='phase';source:='declared';
     ELSIF (d->>'package_id',a->>'skill_key',a->>'revision',a->>'body_sha256')=
       ('725d179e-a6fe-4282-9e8a-83876c336ff4','reference-image-prompts','vf540a7eb0eff4862b924d9af0f7032bd','dd85988f88e96ae446ff5588866b0d2641113d96b9a73bae332c947606e6e90e') THEN profile:='reference-prompts-v1';retire:=true;
     ELSIF (d->>'package_id',a->>'skill_key',a->>'revision',a->>'body_sha256')=
       ('4c7d133e-33e5-455a-9216-4b6cff0ce892','ecommerce-image-prompts','v5d3a01973e41404981324860769e3750','7f8e8de78b18105a2456a327e8bb9ee38edee9ed5d1ddae48a2e0a2c634df69e') THEN profile:='ecommerce-image-plan-v1';retire:=false;
     END IF;
     IF profile IS NULL THEN CONTINUE; END IF;
     IF NOT (images>0 AND (action NOT IN ('execute_ready','execute_prompt') OR ids @> jsonb_build_array(a->>'skill_key')))
        AND NOT (action='plan' AND ids @> jsonb_build_array(a->>'skill_key')) THEN CONTINUE; END IF;
     IF profile NOT IN ('reference-prompts-v1','ecommerce-image-plan-v1') THEN RETURN false; END IF;
     key:=a->>'skill_key';rid:=prefix||'method.'||key||'.planning';
     IF NOT ids @> jsonb_build_array(key) THEN ids:=ids||jsonb_build_array(key); END IF;
     reqs:=reqs||jsonb_build_array(jsonb_build_object('id',rid,'source_message_id',message_id,'description','完成所选方法的参考/商品分析、整套视觉与逐图完整提示词规划',
         'artifact_kind','text','minimum',1,'validator',profile));
     bindings:=bindings||jsonb_build_array(jsonb_build_object('profile_id',profile,'package_id',d->'package_id','skill_key',key,'revision',a->'revision',
         'body_sha256',a->'body_sha256','requirement_ids',jsonb_build_array(rid),'image_count',CASE WHEN images>0 THEN images WHEN content_count>0 THEN content_count WHEN profile='reference-prompts-v1' THEN 6 ELSE 1 END,
         'retire_on_complete',retire,'contract_source',source));
     gates:=gates||jsonb_build_array(jsonb_build_object('capability','platform.generate_image','requirement_ids',jsonb_build_array(rid)),
         jsonb_build_object('capability','platform.image_agent','requirement_ids',jsonb_build_array(rid)));
   END LOOP;
 ELSIF ids<>'[]'::jsonb THEN RETURN false;
 END IF;
 -- Collection order is immaterial; multiplicity and every field are exact.
 RETURN (SELECT jsonb_agg(x ORDER BY x::text) FROM jsonb_array_elements(reqs) x) IS NOT DISTINCT FROM
        (SELECT jsonb_agg(x ORDER BY x::text) FROM jsonb_array_elements(p_new->'requirements') x)
   AND (SELECT jsonb_agg(x ORDER BY x::text) FROM jsonb_array_elements(gates) x) IS NOT DISTINCT FROM
        (SELECT jsonb_agg(x ORDER BY x::text) FROM jsonb_array_elements(p_new->'capability_gates') x)
   AND (SELECT jsonb_agg(x ORDER BY x::text) FROM jsonb_array_elements(bindings) x) IS NOT DISTINCT FROM
        (SELECT jsonb_agg(x ORDER BY x::text) FROM jsonb_array_elements(p_new->'method_bindings') x)
   AND (SELECT jsonb_agg(x ORDER BY x::text) FROM jsonb_array_elements(ids) x) IS NOT DISTINCT FROM
        (SELECT jsonb_agg(x ORDER BY x::text) FROM jsonb_array_elements(p_new->'applicable_method_ids') x);
END $$;
REVOKE ALL ON FUNCTION public.agent_task_revision_projection_valid(jsonb,jsonb,jsonb,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.agent_task_revision_projection_valid(jsonb,jsonb,jsonb,jsonb,jsonb) TO everydayai;

CREATE FUNCTION public.apply_agent_task_requirement_revision(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,
 p_expected_revision bigint,p_review jsonb,p_payload jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE t public.tasks%ROWTYPE; s public.agent_task_states%ROWTYPE; cp jsonb; next_state jsonb:=p_payload->'_task_execution';
 relation text:=p_review#>>'{pending,relation}'; sources jsonb:=p_review->'events'; e jsonb; witness jsonb; history jsonb; archived jsonb;
 requirement jsonb; original jsonb; version bigint; rid text; method jsonb; saved jsonb; directive text; evidence_text text;
BEGIN
 PERFORM 1 FROM public.conversations WHERE id=(SELECT conversation_id FROM public.tasks WHERE id=p_task_id) FOR UPDATE;
 SELECT * INTO t FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 PERFORM public.read_agent_task_user_change_pending(p_task_id,p_execution_token,p_logical_task_id);
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id FOR UPDATE;
 SELECT state INTO cp FROM public.conversation_turn_checkpoints WHERE task_id=t.id FOR UPDATE;
 IF s.revision<>p_expected_revision THEN RETURN jsonb_build_object('outcome','stale'); END IF;
 IF s.state->>'image_binding_version' IS DISTINCT FROM '3' THEN RAISE EXCEPTION 'TASK_REVISION_NOT_SUPPORTED'; END IF;
 IF cp->'_task_execution' IS DISTINCT FROM s.state OR relation NOT IN ('continue','amend','unrelated') OR relation IS NULL
    OR p_review#>>'{observation,relation}' IS DISTINCT FROM relation
    OR jsonb_typeof(sources) IS DISTINCT FROM 'array' OR jsonb_array_length(sources) NOT BETWEEN 1 AND 32
    OR jsonb_typeof(p_review#>'{observation,issues}') IS DISTINCT FROM 'array' OR p_review#>'{observation,issues}'<>'[]'::jsonb
    OR p_review->'base_projection' IS DISTINCT FROM public.agent_task_requirement_projection(s.state)
    OR p_payload->'messages' IS NULL OR jsonb_typeof(p_payload->'messages') IS DISTINCT FROM 'array'
    OR next_state->'revision' IS DISTINCT FROM to_jsonb(s.revision+1)
    OR next_state->>'status' IS DISTINCT FROM 'active' THEN
    RAISE EXCEPTION 'TASK_REVISION_REVIEW_UNCONFIRMED' USING ERRCODE='42501';
 END IF;
 FOR e IN SELECT value FROM jsonb_array_elements(sources) LOOP
   SELECT value INTO witness FROM jsonb_array_elements(public.agent_task_unreviewed_changes(s.root_task_id)) WHERE value->>'event_id'=e->>'event_id';
   IF witness IS NULL OR witness IS DISTINCT FROM e OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(cp#>'{_task_user_changes,items}') c
     WHERE c->>'event_id'=e->>'event_id' AND (c-'delivered')=(e)) THEN
     RAISE EXCEPTION 'TASK_REVISION_SOURCE_CHANGED' USING ERRCODE='42501'; END IF;
 END LOOP;
 IF (next_state-ARRAY['revision','status','requirements_revision','requirements_history','requirements','steps','capability_gates','method_bindings','applicable_method_ids','intent_action'])
    IS DISTINCT FROM (s.state-ARRAY['revision','status','requirements_revision','requirements_history','requirements','steps','capability_gates','method_bindings','applicable_method_ids','intent_action']) THEN
    RAISE EXCEPTION 'TASK_REVISION_FACTS_CHANGED' USING ERRCODE='42501'; END IF;
 version:=(s.state->>'requirements_revision')::bigint;
 IF relation<>'amend' THEN
   IF public.agent_task_requirement_projection(next_state) IS DISTINCT FROM public.agent_task_requirement_projection(s.state)
      OR next_state->'steps' IS DISTINCT FROM s.state->'steps' THEN RAISE EXCEPTION 'TASK_REVISION_FACTS_CHANGED'; END IF;
 ELSE
   version:=version+1;
   history:=COALESCE(s.state->'requirements_history','[]'::jsonb);
   archived:=jsonb_build_object('number',version,'event_id',sources->-1->'event_id','source_owner_task_id',sources->-1->'owner_task_id',
     'requirements',s.state->'requirements','steps',s.state->'steps','capability_gates',s.state->'capability_gates',
     'method_bindings',s.state->'method_bindings','applicable_method_ids',s.state->'applicable_method_ids','intent_action',s.state->'intent_action');
   IF next_state->'requirements_revision' IS DISTINCT FROM to_jsonb(version) OR next_state->'steps'<>'[]'::jsonb
     OR jsonb_array_length(next_state->'requirements_history')<>jsonb_array_length(history)+1
     OR (next_state->'requirements_history'->-1)-ARRAY['message','message_sha256'] IS DISTINCT FROM archived
     OR EXISTS(SELECT 1 FROM jsonb_array_elements(history) WITH ORDINALITY h(value,n) WHERE next_state->'requirements_history'->((n-1)::int) IS DISTINCT FROM value)
     OR next_state->>'intent_action' IS DISTINCT FROM p_review#>>'{pending,proposal,action}' THEN
       RAISE EXCEPTION 'TASK_REVISION_HISTORY_INVALID' USING ERRCODE='42501'; END IF;
   SELECT string_agg(value->>'message',E'\n' ORDER BY n) INTO directive FROM jsonb_array_elements(sources) WITH ORDINALITY x(value,n);
   IF next_state#>>'{requirements_history,-1,message}' IS DISTINCT FROM directive
     OR next_state#>>'{requirements_history,-1,message_sha256}' IS DISTINCT FROM encode(sha256(convert_to(directive,'UTF8')),'hex') THEN
     RAISE EXCEPTION 'TASK_REVISION_SOURCE_CHANGED'; END IF;
   evidence_text:=(s.state->>'original_goal')||E'\n'||directive||E'\n'||COALESCE((SELECT string_agg(v->>'message',E'\n') FROM jsonb_array_elements(history) v),'');
   IF p_review#>>'{observation,requirements,action}' IS DISTINCT FROM p_review#>>'{pending,proposal,action}'
      OR jsonb_typeof(p_review#>'{observation,requirements,deliveries}') IS DISTINCT FROM 'array'
      OR jsonb_array_length(p_review#>'{observation,requirements,deliveries}') NOT BETWEEN 1 AND 24
      OR COALESCE(p_review#>'{observation,requirements,uncertainty}','[]'::jsonb)<>'[]'::jsonb THEN
     RAISE EXCEPTION 'TASK_REVISION_OBSERVATION_INVALID'; END IF;
   IF EXISTS(SELECT 1 FROM jsonb_array_elements(p_review#>'{observation,requirements,deliveries}') delivery
      WHERE jsonb_typeof(delivery->'evidence') IS DISTINCT FROM 'array' OR jsonb_array_length(delivery->'evidence') NOT BETWEEN 1 AND 4
         OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(delivery->'evidence') quote
              WHERE length(btrim(quote))=0 OR position(quote in evidence_text)=0)) THEN
     RAISE EXCEPTION 'TASK_REVISION_OBSERVATION_INVALID'; END IF;
   IF EXISTS(
      WITH observed AS (SELECT d->>'artifact_kind' kind,sum((d->>'minimum')::int) amount
           FROM jsonb_array_elements(p_review#>'{observation,requirements,deliveries}') d GROUP BY 1),
      proposed AS (SELECT d->>'artifact_kind' kind,sum((d->>'minimum')::int) amount
           FROM jsonb_array_elements(p_review#>'{pending,proposal,requirements}') d GROUP BY 1)
      SELECT 1 FROM observed o FULL JOIN proposed p USING(kind)
       WHERE COALESCE(p.amount,0)<COALESCE(o.amount,0)
          OR (COALESCE(o.kind,p.kind)<>'text' AND p.amount IS DISTINCT FROM o.amount)) THEN
     RAISE EXCEPTION 'TASK_REVISION_OBSERVATION_INVALID'; END IF;
   IF public.agent_task_revision_projection_valid(s.state,next_state,p_review#>'{pending,proposal}',
         COALESCE(p_payload->'skill_runtime',cp->'skill_runtime'),cp->'skill_runtime') IS DISTINCT FROM true THEN
     RAISE EXCEPTION 'TASK_REVISION_REQUIREMENTS_INVALID' USING ERRCODE='42501';
   END IF;
 END IF;
 FOR e IN SELECT value FROM jsonb_array_elements(sources) LOOP
   INSERT INTO public.agent_task_user_change_reviews(root_task_id,event_id,relation,requirements_revision,source_sha256,review,projection)
    VALUES(s.root_task_id,(e->>'event_id')::uuid,relation,version,e->>'message_sha256',p_review,public.agent_task_requirement_projection(next_state));
 END LOOP;
 -- Use the original checkpoint writer, retaining composition/context checks.
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(cp->'messages') WITH ORDINALITY x(value,n)
   WHERE p_payload->'messages'->((n-1)::int) IS DISTINCT FROM value) THEN RAISE EXCEPTION 'TASK_REVISION_CONTEXT_CHANGED'; END IF;
 p_payload:=(cp||p_payload)-ARRAY['checkpoint_kind','result_content','usage','credits_cost','tool_digest'];
 saved:=public.save_generation_checkpoint(t.id,p_execution_token,'after_tool',p_payload);
 IF saved->>'outcome' IS DISTINCT FROM 'saved' THEN RAISE EXCEPTION 'TASK_REVISION_CHECKPOINT_UNCONFIRMED'; END IF;
 UPDATE public.agent_task_states SET state=next_state,revision=(next_state->>'revision')::bigint,updated_at=now() WHERE root_task_id=s.root_task_id;
 RETURN jsonb_build_object('outcome','saved','state',next_state,'payload',p_payload);
END $$;
REVOKE ALL ON FUNCTION public.apply_agent_task_requirement_revision(uuid,uuid,uuid,bigint,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.apply_agent_task_requirement_revision(uuid,uuid,uuid,bigint,jsonb,jsonb) TO everydayai;
RESET ROLE;
SET LOCAL ROLE everydayai;
CREATE FUNCTION public.check_agent_task_revision_gate(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_requirements_revision bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE s public.agent_task_states%ROWTYPE; checked jsonb;
BEGIN
 checked:=public.read_agent_task_user_change_pending(p_task_id,p_execution_token,p_logical_task_id);
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id FOR SHARE;
 IF (s.state->>'requirements_revision')::bigint IS DISTINCT FROM p_requirements_revision THEN RETURN jsonb_build_object('outcome','stale'); END IF;
 RETURN jsonb_build_object('outcome',CASE WHEN jsonb_array_length(checked->'events')>0 THEN 'user_change_pending' ELSE 'ready' END);
END $$;
REVOKE ALL ON FUNCTION public.check_agent_task_revision_gate(uuid,uuid,uuid,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.check_agent_task_revision_gate(uuid,uuid,uuid,bigint) TO everydayai;
ALTER FUNCTION public.begin_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text) RENAME TO begin_agent_task_operation_before364;
CREATE FUNCTION public.begin_agent_task_operation(p_task_id uuid,p_execution_token uuid,p_logical_task_id uuid,p_expected_revision bigint,
 p_step_id text,p_capability text,p_effect_key text,p_effect_version integer,p_tool_call_id text,p_tool_name text,p_args_hash text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE s public.agent_task_states%ROWTYPE; checked jsonb;
BEGIN
 PERFORM 1 FROM public.tasks WHERE id=p_task_id FOR UPDATE;
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id;
 IF s.state->>'image_binding_version' IS DISTINCT FROM '3' THEN
   RETURN public.begin_agent_task_operation_before364(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_step_id,p_capability,p_effect_key,p_effect_version,p_tool_call_id,p_tool_name,p_args_hash);
 END IF;
 checked:=public.read_agent_task_user_change_pending(p_task_id,p_execution_token,p_logical_task_id);
 IF jsonb_array_length(checked->'events')>0 THEN RETURN jsonb_build_object('outcome','user_change_pending'); END IF;
 SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_logical_task_id FOR UPDATE;
 IF s.state ? 'requirements_history' AND EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'operations') op
   WHERE op->>'status' IN ('reserved','accepted','uncertain') AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'steps') step WHERE step->>'id'=op->>'step_id')) THEN
   RETURN jsonb_build_object('outcome','previous_effect_unresolved');
 END IF;
 RETURN public.begin_agent_task_operation_before364(p_task_id,p_execution_token,p_logical_task_id,p_expected_revision,p_step_id,p_capability,p_effect_key,p_effect_version,p_tool_call_id,p_tool_name,p_args_hash);
END $$;
REVOKE ALL ON FUNCTION public.begin_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.begin_agent_task_operation(uuid,uuid,uuid,bigint,text,text,text,integer,text,text,text) TO everydayai;
-- Guard late native mutation markers as well as the earlier reservation.
CREATE FUNCTION public.guard_agent_task_revision_dispatch() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE s public.agent_task_states%ROWTYPE; op jsonb;
BEGIN
 IF TG_TABLE_NAME='agent_task_native_writes' AND NEW.phase='dispatched' AND OLD.phase='prepared' THEN
   SELECT * INTO s FROM public.agent_task_states WHERE owner_task_id=NEW.owner_task_id FOR SHARE;
   SELECT value INTO op FROM jsonb_array_elements(s.state->'operations') WHERE value->>'id'=NEW.operation_id;
   IF s.root_task_id IS NOT NULL AND s.state->>'image_binding_version'='3' AND (jsonb_array_length(public.agent_task_unreviewed_changes(s.root_task_id))>0
     OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s.state->'steps') step WHERE step->>'id'=op->>'step_id')) THEN
     RAISE EXCEPTION 'TASK_USER_CHANGE_REVIEW_REQUIRED' USING ERRCODE='42501';
   END IF;
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_agent_task_revision_dispatch() FROM PUBLIC;
DO $$ BEGIN
 IF to_regclass('public.agent_task_native_writes') IS NOT NULL THEN
   CREATE TRIGGER agent_task_revision_native_dispatch BEFORE UPDATE OF phase ON public.agent_task_native_writes FOR EACH ROW EXECUTE FUNCTION public.guard_agent_task_revision_dispatch();
 END IF;
END $$;
RESET ROLE;

-- Native results from retired plans remain verifiable historical facts.
SET LOCAL ROLE everydayai;
CREATE OR REPLACE FUNCTION public.enqueue_agent_task_continuation(p_root_task_id uuid,p_expected_revision bigint,p_event_id uuid,p_source_kind text)
RETURNS jsonb LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE
    s public.agent_task_states%ROWTYPE;
    owner public.tasks%ROWTYPE;
    conv public.conversations%ROWTYPE;
    v_output_id uuid := gen_random_uuid();
    v_task_id uuid := gen_random_uuid();
    selected public.agent_task_events%ROWTYPE;
    params jsonb;
BEGIN
    SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_root_task_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'TASK_CONTINUATION_SCOPE_DENIED' USING ERRCODE='42501'; END IF;
    SELECT * INTO conv FROM public.conversations WHERE id=s.conversation_id FOR UPDATE;
    SELECT * INTO owner FROM public.tasks WHERE id=s.owner_task_id FOR UPDATE;
    SELECT * INTO s FROM public.agent_task_states WHERE root_task_id=p_root_task_id FOR UPDATE;
    SELECT * INTO selected FROM public.agent_task_events WHERE id=p_event_id AND root_task_id=p_root_task_id FOR UPDATE;
    IF NOT FOUND OR p_source_kind IS NULL OR p_source_kind NOT IN ('image','scheduled','video')
       OR (p_source_kind='image' AND selected.child_task_id IS NULL)
       OR (p_source_kind IN ('scheduled','video') AND (selected.child_task_id IS NOT NULL OR selected.payload->>'source_kind' IS DISTINCT FROM p_source_kind))
       OR selected.actor_user_id<>s.actor_user_id
       OR selected.org_id IS DISTINCT FROM s.org_id OR owner.user_id<>s.actor_user_id
       OR owner.org_id IS DISTINCT FROM s.org_id OR conv.user_id<>s.actor_user_id
       OR conv.org_id IS DISTINCT FROM s.org_id OR conv.scope_type IS DISTINCT FROM 'user'
       OR current_setting('app.access_kind',true) IS DISTINCT FROM 'runtime'
       OR s.actor_user_id::text IS DISTINCT FROM NULLIF(current_setting('app.actor_user_id',true),'')
       OR s.org_id IS DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid THEN
        RAISE EXCEPTION 'TASK_CONTINUATION_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    IF selected.status IN ('queued','applied') THEN
        RETURN jsonb_build_object('outcome','already_queued','task_id',selected.continuation_task_id);
    END IF;
    IF selected.status<>'pending' THEN RETURN jsonb_build_object('outcome','obsolete'); END IF;
    IF s.revision IS DISTINCT FROM p_expected_revision THEN RETURN jsonb_build_object('outcome','stale'); END IF;
    IF owner.status IS DISTINCT FROM 'completed'
       OR (p_source_kind='image' AND s.state->>'status' IS DISTINCT FROM 'waiting_dependencies')
       OR (p_source_kind IN ('scheduled','video') AND s.state->>'status' NOT IN ('waiting_approval','waiting_dependencies')) THEN
        RETURN jsonb_build_object('outcome','waiting');
    END IF;
    IF EXISTS(SELECT 1 FROM public.agent_task_events e WHERE e.root_task_id=p_root_task_id AND e.status='queued') THEN
        RETURN jsonb_build_object('outcome','waiting'); END IF;
    IF p_source_kind='scheduled' THEN
        IF public.agent_task_scheduled_terminal_fact(p_root_task_id,selected.payload->>'operation_id') IS NULL THEN
            RETURN jsonb_build_object('outcome','waiting'); END IF;
    END IF;
    IF p_source_kind='video' THEN
        IF public.agent_task_video_terminal_fact(p_root_task_id,selected.payload->>'operation_id') IS NULL THEN
            RETURN jsonb_build_object('outcome','waiting'); END IF;
    END IF;
    IF NOT EXISTS(SELECT 1 FROM public.users WHERE id=s.actor_user_id AND status::text='active')
       OR (s.org_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.org_members m JOIN public.organizations o ON o.id=m.org_id
           WHERE m.user_id=s.actor_user_id AND m.org_id=s.org_id AND m.status='active' AND o.status='active')) THEN
        RETURN jsonb_build_object('outcome','scope_changed');
    END IF;
    -- New input takes precedence. Preserve the old event; never reinterpret it
    -- as authorization for a new goal or append stale work over a new turn.
    IF EXISTS(SELECT 1 FROM public.tasks later WHERE later.conversation_id=s.conversation_id
       AND later.type='chat' AND later.queue_sequence>GREATEST(owner.queue_sequence,COALESCE((owner.delivery_context->>'agent_resume_sequence')::bigint,owner.queue_sequence))
       AND later.input_message_id<>owner.input_message_id
       AND (later.delivery_context->>'input_kind') IS DISTINCT FROM 'agent_task_continuation')
       OR EXISTS(SELECT 1 FROM public.messages newer JOIN public.messages source ON source.id=owner.input_message_id
         WHERE newer.conversation_id=s.conversation_id AND newer.role='user'
           AND NOT (s.state->'source_message_ids' @> jsonb_build_array(newer.id::text)) AND newer.created_at>GREATEST(source.created_at,COALESCE((owner.delivery_context->>'agent_resume_at')::timestamptz,source.created_at))) THEN
        RETURN jsonb_build_object('outcome','new_input_pending');
    END IF;
    IF p_source_kind='image' AND EXISTS(SELECT 1 FROM public.agent_task_events e JOIN public.tasks child ON child.id=e.child_task_id
        WHERE e.root_task_id=p_root_task_id AND e.status='pending'
          AND (child.status NOT IN ('completed','failed','cancelled')
               OR (child.status='completed' AND child.request_params#>>'{_media_lifecycle_v1,phase}' IS DISTINCT FROM 'published'))) THEN
        RETURN jsonb_build_object('outcome','waiting');
    END IF;
    params := COALESCE(owner.request_params,'{}'::jsonb)-ARRAY['_image_sources_v1','_agent_task_scheduled_v1','_agent_task_video_wait_v1'];
    INSERT INTO public.messages(id,conversation_id,org_id,role,content,status,turn_id,reply_to_message_id)
      VALUES(v_output_id,s.conversation_id,s.org_id,'assistant','[]','pending',owner.turn_id,owner.input_message_id);
    INSERT INTO public.tasks(id,user_id,org_id,conversation_id,type,status,model_id,assistant_message_id,
        input_message_id,turn_id,execution_mode,request_params,delivery_context)
      VALUES(v_task_id,s.actor_user_id,s.org_id,s.conversation_id,'chat','pending',owner.model_id,v_output_id,
        owner.input_message_id,owner.turn_id,owner.execution_mode,params,
        (COALESCE(owner.delivery_context,'{}'::jsonb)-'source_kind')||jsonb_build_object('actor',true,
          'input_kind','agent_task_continuation','logical_task_id',p_root_task_id,'event_id',p_event_id,
          'source_revision',s.revision,'source_owner_task_id',owner.id)
          ||CASE WHEN p_source_kind IN ('scheduled','video') THEN jsonb_build_object('source_kind',p_source_kind) ELSE '{}'::jsonb END);
    INSERT INTO public.task_attachment_refs(org_id,task_id,turn_id,input_message_id,attachment_id,attachment_set_id)
      SELECT r.org_id,v_task_id,r.turn_id,r.input_message_id,r.attachment_id,r.attachment_set_id
        FROM public.task_attachment_refs r WHERE r.task_id=owner.id;
    UPDATE public.agent_task_events SET status='queued',continuation_task_id=v_task_id,expected_revision=s.revision
      WHERE id=p_event_id AND root_task_id=p_root_task_id AND status='pending' AND p_source_kind IN ('scheduled','video');
    UPDATE public.agent_task_events SET status='queued',continuation_task_id=v_task_id,expected_revision=s.revision
      WHERE root_task_id=p_root_task_id AND status='pending' AND child_task_id IS NOT NULL AND p_source_kind='image';
    RETURN jsonb_build_object('outcome','queued','task_id',v_task_id,'message_id',v_output_id);
END $$;
REVOKE ALL ON FUNCTION public.enqueue_agent_task_continuation(uuid,bigint,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.enqueue_agent_task_continuation(uuid,bigint,uuid,text) TO everydayai;

RESET ROLE;
