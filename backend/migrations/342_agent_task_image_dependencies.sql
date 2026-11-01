-- Register the child/event in the ORIGINAL image-acceptance transaction.
-- Terminal readiness is read from the existing native child task, not copied
-- from a lossy parent-running notification. No extra billing or queue here.
SET LOCAL ROLE everydayai;
ALTER TABLE public.agent_task_events ADD COLUMN child_task_id uuid REFERENCES public.tasks(id) ON DELETE CASCADE;
ALTER TABLE public.agent_task_events ADD COLUMN operation_id text CHECK(operation_id ~ '^[a-f0-9]{64}$');
ALTER TABLE public.agent_task_events ADD CONSTRAINT agent_task_child_pair CHECK(
    (child_task_id IS NULL)=(operation_id IS NULL));
CREATE UNIQUE INDEX agent_task_one_child_per_operation ON public.agent_task_events(root_task_id,operation_id)
    WHERE child_task_id IS NOT NULL;
CREATE UNIQUE INDEX agent_task_one_dependency_per_child ON public.agent_task_events(child_task_id)
    WHERE child_task_id IS NOT NULL;
CREATE POLICY agent_task_child_terminal_worker_read ON public.agent_task_events FOR SELECT
    USING(current_setting('app.access_kind',true)='worker'
      AND org_id IS NOT DISTINCT FROM NULLIF(current_setting('app.org_id',true),'')::uuid
      AND child_task_id IS NOT NULL AND EXISTS(SELECT 1 FROM public.tasks child
        WHERE child.id=agent_task_events.child_task_id AND child.user_id=agent_task_events.actor_user_id
          AND child.org_id IS NOT DISTINCT FROM agent_task_events.org_id
          AND child.type='image' AND child.status IN ('completed','failed','cancelled')));

CREATE FUNCTION public.register_agent_image_dependency() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE
    s public.agent_task_states%ROWTYPE;
    origin jsonb;
    op jsonb;
    matches integer;
BEGIN
    IF NEW.type IS DISTINCT FROM 'image' THEN RETURN NEW; END IF;
    origin := NEW.request_params#>'{_media_request_v1,origin}';
    IF origin->>'parent_task_id' IS NULL THEN RETURN NEW; END IF;
    SELECT * INTO s FROM public.agent_task_states
      WHERE owner_task_id::text=origin->>'parent_task_id' FOR SHARE;
    IF NOT FOUND OR (jsonb_array_length(s.state->'method_bindings')=0 AND NOT EXISTS(
        SELECT 1 FROM jsonb_array_elements(s.state->'artifacts') a WHERE a->'validations' @> '["image.source.valid"]'::jsonb
          OR (s.state->>'image_binding_version' IN ('2','3') AND a->'validations' @> '["image.plan.valid"]'::jsonb))) THEN
        RETURN NEW; -- Old/unadapted requests keep their original acceptance.
    END IF;
    IF NEW.user_id IS DISTINCT FROM s.actor_user_id OR NEW.org_id IS DISTINCT FROM s.org_id
       OR NEW.conversation_id IS DISTINCT FROM s.conversation_id
       OR origin->>'actor_user_id' IS DISTINCT FROM s.actor_user_id::text
       OR origin->>'conversation_id' IS DISTINCT FROM s.conversation_id::text
       OR s.state->>'status' IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION 'TASK_IMAGE_DEPENDENCY_SCOPE_DENIED' USING ERRCODE='42501';
    END IF;
    SELECT count(*) INTO matches FROM jsonb_array_elements(s.state->'operations') item
     WHERE item->>'owner_task_id'=s.owner_task_id::text AND item->>'owner_turn_id'=origin->>'turn_id'
       AND item->>'invocation_id'=origin->>'tool_call_id' AND item->>'tool_name'='generate_image'
       AND item->>'capability'='platform.generate_image' AND item->>'status'='reserved';
    IF matches<>1 THEN
        RAISE EXCEPTION 'TASK_IMAGE_OPERATION_BINDING_REQUIRED' USING ERRCODE='42501';
    END IF;
    SELECT item INTO op FROM jsonb_array_elements(s.state->'operations') item
     WHERE item->>'owner_task_id'=s.owner_task_id::text AND item->>'owner_turn_id'=origin->>'turn_id'
       AND item->>'invocation_id'=origin->>'tool_call_id' AND item->>'tool_name'='generate_image'
       AND item->>'capability'='platform.generate_image' AND item->>'status'='reserved';
    INSERT INTO public.agent_task_events(root_task_id,actor_user_id,org_id,dedupe_key,expected_revision,payload,
        child_task_id,operation_id)
    VALUES(s.root_task_id,s.actor_user_id,s.org_id,'image-child:'||NEW.id::text,s.revision,
        jsonb_build_object('kind','image_child','version',1,'child_task_id',NEW.id,'operation_id',op->>'id',
            'requirements_revision',s.state->'requirements_revision','step_id',op->>'step_id'),NEW.id,op->>'id');
    -- Routing hint only; the event remains authoritative. Existing task scans
    -- can find these children without widening event-table tenant policies.
    UPDATE public.tasks SET request_params=request_params||jsonb_build_object('_agent_task_dependency_v1',
        jsonb_build_object('version',1,'pending',true,'root_task_id',s.root_task_id)) WHERE id=NEW.id;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.register_agent_image_dependency() FROM PUBLIC;
CREATE TRIGGER agent_task_image_dependency_after_accept AFTER INSERT ON public.tasks
    FOR EACH ROW EXECUTE FUNCTION public.register_agent_image_dependency();

CREATE FUNCTION public.guard_agent_child_identity() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
    IF OLD.child_task_id IS NOT NULL AND
       (NEW.child_task_id,NEW.operation_id,NEW.root_task_id,NEW.actor_user_id,NEW.org_id,NEW.dedupe_key,NEW.payload)
       IS DISTINCT FROM
       (OLD.child_task_id,OLD.operation_id,OLD.root_task_id,OLD.actor_user_id,OLD.org_id,OLD.dedupe_key,OLD.payload) THEN
        RAISE EXCEPTION 'TASK_CHILD_IDENTITY_IMMUTABLE' USING ERRCODE='42501';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.guard_agent_child_identity() FROM PUBLIC;
CREATE TRIGGER agent_task_child_identity BEFORE UPDATE ON public.agent_task_events
    FOR EACH ROW EXECUTE FUNCTION public.guard_agent_child_identity();
RESET ROLE;
