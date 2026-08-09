SET LOCAL ROLE everydayai_owner;
CREATE TABLE agent_runtime_scheduled_execution_profiles (
    scheduled_task_id UUID PRIMARY KEY
        REFERENCES scheduled_tasks(id) ON DELETE RESTRICT,
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    source_action_id UUID NOT NULL REFERENCES agent_actions(id) ON DELETE RESTRICT,
    source_run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE RESTRICT,
    agent_definition_id TEXT NOT NULL,
    agent_definition_revision TEXT NOT NULL,
    agent_definition_hash TEXT NOT NULL CHECK (agent_definition_hash ~ '^[0-9a-f]{64}$'),
    catalog_revision TEXT NOT NULL CHECK (catalog_revision ~ '^[0-9a-f]{64}$'),
    effective_toolset_hash TEXT NOT NULL CHECK (effective_toolset_hash ~ '^[0-9a-f]{64}$'),
    model_snapshot JSONB NOT NULL CHECK (jsonb_typeof(model_snapshot) = 'object'),
    toolset_snapshot JSONB NOT NULL CHECK (jsonb_typeof(toolset_snapshot) = 'object'),
    scope_snapshot JSONB NOT NULL CHECK (jsonb_typeof(scope_snapshot) = 'object'),
    channel TEXT NOT NULL CHECK (channel IN ('web', 'wecom')),
    budget_snapshot JSONB NOT NULL CHECK (jsonb_typeof(budget_snapshot) = 'object'),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    state_version BIGINT NOT NULL DEFAULT 1 CHECK (state_version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (length(btrim(agent_definition_id)) BETWEEN 1 AND 200),
    CHECK (length(btrim(agent_definition_revision)) BETWEEN 1 AND 200)
);
CREATE TABLE agent_runtime_scheduled_run_bindings (
    scheduled_run_id UUID PRIMARY KEY
        REFERENCES scheduled_task_runs(id) ON DELETE RESTRICT,
    scheduled_task_id UUID NOT NULL REFERENCES scheduled_tasks(id) ON DELETE RESTRICT,
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('legacy', 'runtime')),
    trigger_kind TEXT NOT NULL CHECK (trigger_kind IN ('scheduled', 'manual', 'retry')),
    trigger_key TEXT NOT NULL CHECK (length(btrim(trigger_key)) BETWEEN 1 AND 300),
    scheduled_for TIMESTAMPTZ,
    manual_request_id TEXT,
    task_revision BIGINT NOT NULL CHECK (task_revision >= 0),
    context_hash TEXT NOT NULL CHECK (context_hash ~ '^[0-9a-f]{64}$'),
    request_hash TEXT NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    tenant_kill_epoch BIGINT NOT NULL CHECK (tenant_kill_epoch >= 0),
    runtime_command_id UUID UNIQUE
        REFERENCES agent_session_commands(id) ON DELETE RESTRICT,
    runtime_run_id UUID UNIQUE REFERENCES agent_runs(id) ON DELETE RESTRICT,
    owner_status TEXT NOT NULL DEFAULT 'selected' CHECK (owner_status IN (
        'selected', 'submitted', 'runtime_claimed', 'running',
        'cancel_requested', 'reconcile_required', 'completed', 'failed', 'cancelled'
    )),
    state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (scheduled_task_id, trigger_kind, trigger_key),
    CHECK ((trigger_kind = 'manual') = (manual_request_id IS NOT NULL)),
    CHECK (manual_request_id IS NULL OR length(btrim(manual_request_id)) BETWEEN 1 AND 200),
    CHECK (owner_kind = 'runtime' OR (runtime_command_id IS NULL AND runtime_run_id IS NULL)),
    CHECK (runtime_run_id IS NULL OR runtime_command_id IS NOT NULL)
);
CREATE INDEX idx_runtime_scheduled_bindings_task
    ON agent_runtime_scheduled_run_bindings(scheduled_task_id, created_at DESC);
ALTER TABLE agent_runtime_scheduled_execution_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_execution_profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_run_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_run_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY runtime_scheduled_profiles_owner_all
    ON agent_runtime_scheduled_execution_profiles FOR ALL TO everydayai_owner
    USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY runtime_scheduled_bindings_owner_all
    ON agent_runtime_scheduled_run_bindings FOR ALL TO everydayai_owner
    USING (TRUE) WITH CHECK (TRUE);
REVOKE ALL ON TABLE
    agent_runtime_scheduled_execution_profiles,
    agent_runtime_scheduled_run_bindings
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker,
    everydayai_sync, everydayai, everydayai_agent_runtime_worker;

CREATE FUNCTION _agent_runtime_scheduled_owner_actor()
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF session_user <> 'everydayai_agent_runtime_worker'
       OR current_setting('app.access_kind', TRUE) IS DISTINCT FROM 'agent_runtime' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_SCOPE_REQUIRED'
            USING ERRCODE = '42501';
    END IF;
END;
$$;
CREATE FUNCTION _agent_runtime_scheduled_snapshot_safe(p_snapshot JSONB)
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog, public
AS $$
    WITH RECURSIVE nodes(value) AS (
        SELECT p_snapshot
        UNION ALL
        SELECT child.value
          FROM nodes parent
          CROSS JOIN LATERAL (
              SELECT object_item.value
                FROM jsonb_each(
                    CASE WHEN jsonb_typeof(parent.value) = 'object'
                         THEN parent.value ELSE '{}'::JSONB END
                ) object_item
              UNION ALL
              SELECT array_item.value
                FROM jsonb_array_elements(
                    CASE WHEN jsonb_typeof(parent.value) = 'array'
                         THEN parent.value ELSE '[]'::JSONB END
                ) array_item
          ) child
    )
    SELECT jsonb_typeof(p_snapshot) = 'object'
       AND pg_column_size(p_snapshot) <= 65536
       AND NOT EXISTS (
           SELECT 1
             FROM nodes node
             CROSS JOIN LATERAL jsonb_object_keys(
                 CASE WHEN jsonb_typeof(node.value) = 'object'
                      THEN node.value ELSE '{}'::JSONB END
             ) key_name
            WHERE key_name ~* '(^|_)(secret|token|password|api_?key|credential)($|_)'
       )
$$;
CREATE FUNCTION _agent_runtime_scheduled_identity_immutable()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_FACT_IMMUTABLE'
            USING ERRCODE = '55000';
    END IF;
    IF OLD.scheduled_run_id IS DISTINCT FROM NEW.scheduled_run_id
       OR OLD.scheduled_task_id IS DISTINCT FROM NEW.scheduled_task_id
       OR OLD.org_id IS DISTINCT FROM NEW.org_id
       OR OLD.user_id IS DISTINCT FROM NEW.user_id
       OR OLD.owner_kind IS DISTINCT FROM NEW.owner_kind
       OR OLD.trigger_kind IS DISTINCT FROM NEW.trigger_kind
       OR OLD.trigger_key IS DISTINCT FROM NEW.trigger_key
       OR OLD.scheduled_for IS DISTINCT FROM NEW.scheduled_for
       OR OLD.manual_request_id IS DISTINCT FROM NEW.manual_request_id
       OR OLD.task_revision IS DISTINCT FROM NEW.task_revision
       OR OLD.context_hash IS DISTINCT FROM NEW.context_hash
       OR OLD.request_hash IS DISTINCT FROM NEW.request_hash
       OR OLD.tenant_kill_epoch IS DISTINCT FROM NEW.tenant_kill_epoch
       OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_IDENTITY_IMMUTABLE'
            USING ERRCODE = '55000';
    END IF;
    IF NEW.state_version <= OLD.state_version THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_VERSION_INVALID'
            USING ERRCODE = '40001';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER runtime_scheduled_profile_immutable
BEFORE UPDATE OR DELETE ON agent_runtime_scheduled_execution_profiles
FOR EACH ROW EXECUTE FUNCTION _runtime_scheduler_immutable_fact();
CREATE TRIGGER runtime_scheduled_binding_identity_immutable
BEFORE UPDATE OR DELETE ON agent_runtime_scheduled_run_bindings
FOR EACH ROW EXECUTE FUNCTION _agent_runtime_scheduled_identity_immutable();
CREATE FUNCTION create_agent_runtime_scheduled_execution_profile_v1(
    p_task_id UUID, p_org_id UUID, p_user_id UUID,
    p_source_action_id UUID, p_source_run_id UUID,
    p_agent_definition_id TEXT, p_agent_definition_revision TEXT,
    p_agent_definition_hash TEXT, p_catalog_revision TEXT,
    p_effective_toolset_hash TEXT, p_model_snapshot JSONB,
    p_scope_snapshot JSONB, p_channel TEXT, p_budget_snapshot JSONB,
    p_request_hash TEXT, p_expected_state_version BIGINT DEFAULT 0
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task scheduled_tasks%ROWTYPE;
    v_action agent_actions%ROWTYPE;
    v_existing agent_runtime_scheduled_execution_profiles%ROWTYPE;
    v_definition agent_runtime_definition_facts%ROWTYPE;
    v_toolset agent_runtime_effective_toolset_facts%ROWTYPE;
BEGIN
    PERFORM _agent_runtime_scheduled_owner_actor();
    IF p_expected_state_version <> 0
       OR p_request_hash !~ '^[0-9a-f]{64}$'
       OR NOT _agent_runtime_scheduled_snapshot_safe(p_model_snapshot)
       OR NOT _agent_runtime_scheduled_snapshot_safe(p_scope_snapshot)
       OR NOT _agent_runtime_scheduled_snapshot_safe(p_budget_snapshot)
       OR p_scope_snapshot->>'scope_kind' IS DISTINCT FROM 'user'
       OR p_scope_snapshot->>'scope_id' IS DISTINCT FROM p_user_id::TEXT
       OR p_channel NOT IN ('web', 'wecom') THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_PROFILE_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_task FROM scheduled_tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.org_id IS DISTINCT FROM p_org_id
       OR v_task.user_id IS DISTINCT FROM p_user_id THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_PROFILE_TENANT_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_action FROM agent_actions WHERE id = p_source_action_id;
    IF NOT FOUND OR v_action.run_id IS DISTINCT FROM p_source_run_id
       OR v_action.org_id IS DISTINCT FROM p_org_id
       OR v_action.user_id IS DISTINCT FROM p_user_id
       OR v_task.runtime_action_id IS DISTINCT FROM p_source_action_id
       OR NOT EXISTS (
           SELECT 1
             FROM agent_runtime_scheduler_operation_intents intent
             JOIN agent_runtime_scheduler_operation_receipts receipt
               ON receipt.intent_id = intent.id AND receipt.outcome = 'committed'
            WHERE intent.task_id = p_task_id
              AND intent.org_id = p_org_id
              AND intent.user_id = p_user_id
              AND intent.run_id = p_source_run_id
              AND intent.action_id = p_source_action_id
              AND intent.operation = 'create'
       ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_PROFILE_SOURCE_INVALID'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_definition
      FROM agent_runtime_definition_facts
     WHERE agent_key = p_agent_definition_id
       AND definition_revision = p_agent_definition_revision
       AND definition_hash = p_agent_definition_hash
       AND catalog_revision = p_catalog_revision
       AND enabled_for_new_ingress AND recoverable;
    SELECT * INTO v_toolset
      FROM agent_runtime_effective_toolset_facts
     WHERE agent_key = p_agent_definition_id
       AND definition_revision = p_agent_definition_revision
       AND catalog_revision = p_catalog_revision
       AND scope_kind = 'user' AND channel = p_channel
       AND gate_state = 'enabled'
       AND effective_toolset_hash = p_effective_toolset_hash
       AND enabled_for_new_ingress AND recoverable;
    IF v_definition.agent_key IS NULL OR v_toolset.agent_key IS NULL
       OR jsonb_typeof(v_toolset.toolset_document->'tools') IS DISTINCT FROM 'array'
       OR EXISTS (
           SELECT 1 FROM jsonb_array_elements(v_toolset.toolset_document->'tools') tool
            WHERE tool->>'safety_level' IS DISTINCT FROM 'safe'
               OR tool->>'side_effect' IS DISTINCT FROM 'none'
               OR tool->>'authorization_requirement' IS DISTINCT FROM 'none'
       ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_TOOLSET_NOT_UNATTENDED_SAFE'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_existing
      FROM agent_runtime_scheduled_execution_profiles
     WHERE scheduled_task_id = p_task_id;
    IF FOUND THEN
        IF v_existing.org_id IS DISTINCT FROM p_org_id
           OR v_existing.user_id IS DISTINCT FROM p_user_id
           OR v_existing.source_action_id IS DISTINCT FROM p_source_action_id
           OR v_existing.source_run_id IS DISTINCT FROM p_source_run_id
           OR v_existing.agent_definition_id IS DISTINCT FROM p_agent_definition_id
           OR v_existing.agent_definition_revision IS DISTINCT FROM p_agent_definition_revision
           OR v_existing.agent_definition_hash IS DISTINCT FROM p_agent_definition_hash
           OR v_existing.catalog_revision IS DISTINCT FROM p_catalog_revision
           OR v_existing.effective_toolset_hash IS DISTINCT FROM p_effective_toolset_hash
           OR v_existing.model_snapshot IS DISTINCT FROM p_model_snapshot
           OR v_existing.scope_snapshot IS DISTINCT FROM p_scope_snapshot
           OR v_existing.channel IS DISTINCT FROM p_channel
           OR v_existing.budget_snapshot IS DISTINCT FROM p_budget_snapshot
           OR v_existing.request_hash IS DISTINCT FROM p_request_hash THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_PROFILE_IDEMPOTENCY_CONFLICT'
                USING ERRCODE = '40001';
        END IF;
        RETURN jsonb_build_object('outcome', 'already_exists', 'profile', to_jsonb(v_existing));
    END IF;
    INSERT INTO agent_runtime_scheduled_execution_profiles(
        scheduled_task_id, org_id, user_id, source_action_id, source_run_id,
        agent_definition_id, agent_definition_revision, agent_definition_hash,
        catalog_revision, effective_toolset_hash, model_snapshot, toolset_snapshot,
        scope_snapshot, channel, budget_snapshot, request_hash
    ) VALUES (
        p_task_id, p_org_id, p_user_id, p_source_action_id, p_source_run_id,
        p_agent_definition_id, p_agent_definition_revision, p_agent_definition_hash,
        p_catalog_revision, p_effective_toolset_hash, p_model_snapshot,
        v_toolset.toolset_document, p_scope_snapshot, p_channel, p_budget_snapshot,
        p_request_hash
    ) RETURNING * INTO v_existing;
    RETURN jsonb_build_object('outcome', 'created', 'profile', to_jsonb(v_existing));
END;
$$;
CREATE FUNCTION read_agent_runtime_scheduled_execution_profile_v1(
    p_task_id UUID, p_org_id UUID, p_user_id UUID
) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE v_profile agent_runtime_scheduled_execution_profiles%ROWTYPE;
BEGIN
    PERFORM _agent_runtime_scheduled_owner_actor();
    SELECT * INTO v_profile FROM agent_runtime_scheduled_execution_profiles
     WHERE scheduled_task_id = p_task_id AND org_id = p_org_id AND user_id = p_user_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome', 'not_found', 'owner_kind', 'legacy'); END IF;
    RETURN jsonb_build_object('outcome', 'found', 'owner_kind', 'runtime', 'profile', to_jsonb(v_profile));
END;
$$;
CREATE FUNCTION select_agent_runtime_scheduled_run_owner_v1(
    p_task_id UUID, p_scheduled_run_id UUID, p_org_id UUID, p_user_id UUID,
    p_trigger_kind TEXT, p_trigger_key TEXT, p_scheduled_for TIMESTAMPTZ,
    p_manual_request_id TEXT, p_task_revision BIGINT,
    p_context_hash TEXT, p_request_hash TEXT, p_tenant_kill_epoch BIGINT
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_task scheduled_tasks%ROWTYPE;
    v_binding agent_runtime_scheduled_run_bindings%ROWTYPE;
    v_owner TEXT;
BEGIN
    PERFORM _agent_runtime_scheduled_owner_actor();
    IF p_trigger_kind NOT IN ('scheduled', 'manual', 'retry')
       OR (p_trigger_kind = 'manual') IS DISTINCT FROM (p_manual_request_id IS NOT NULL)
       OR NULLIF(btrim(p_trigger_key), '') IS NULL
       OR p_context_hash !~ '^[0-9a-f]{64}$'
       OR p_request_hash !~ '^[0-9a-f]{64}$'
       OR p_task_revision < 0 OR p_tenant_kill_epoch < 0 THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_ARGUMENT_INVALID'
            USING ERRCODE = '22023';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'scheduled-trigger-owner:' || p_task_id::TEXT || ':' || p_trigger_kind || ':' || btrim(p_trigger_key), 0));
    SELECT * INTO v_task FROM scheduled_tasks WHERE id = p_task_id FOR UPDATE;
    IF NOT FOUND OR v_task.org_id IS DISTINCT FROM p_org_id
       OR v_task.user_id IS DISTINCT FROM p_user_id
       OR v_task.runtime_state_version IS DISTINCT FROM p_task_revision
       OR NOT EXISTS (
           SELECT 1 FROM scheduled_task_runs run
            WHERE run.id = p_scheduled_run_id AND run.task_id = p_task_id
              AND run.org_id = p_org_id
       ) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_BINDING_INVALID'
            USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_binding FROM agent_runtime_scheduled_run_bindings
     WHERE scheduled_run_id = p_scheduled_run_id
        OR (scheduled_task_id=p_task_id AND trigger_kind=p_trigger_kind
            AND trigger_key=btrim(p_trigger_key))
     ORDER BY (scheduled_run_id=p_scheduled_run_id) DESC LIMIT 1;
    IF FOUND THEN
        IF v_binding.scheduled_task_id IS DISTINCT FROM p_task_id
           OR v_binding.org_id IS DISTINCT FROM p_org_id
           OR v_binding.user_id IS DISTINCT FROM p_user_id
           OR v_binding.trigger_kind IS DISTINCT FROM p_trigger_kind
           OR v_binding.trigger_key IS DISTINCT FROM btrim(p_trigger_key)
           OR v_binding.scheduled_for IS DISTINCT FROM p_scheduled_for
           OR v_binding.manual_request_id IS DISTINCT FROM p_manual_request_id
           OR v_binding.task_revision IS DISTINCT FROM p_task_revision
           OR v_binding.context_hash IS DISTINCT FROM p_context_hash
           OR v_binding.request_hash IS DISTINCT FROM p_request_hash
           OR v_binding.tenant_kill_epoch IS DISTINCT FROM p_tenant_kill_epoch THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_IDEMPOTENCY_CONFLICT'
                USING ERRCODE = '40001';
        END IF;
        RETURN jsonb_build_object('outcome', 'already_selected', 'binding', to_jsonb(v_binding));
    END IF;
    v_owner := CASE WHEN EXISTS (
        SELECT 1 FROM agent_runtime_scheduled_execution_profiles profile
         WHERE profile.scheduled_task_id = p_task_id AND profile.org_id = p_org_id
           AND profile.user_id = p_user_id
    ) THEN 'runtime' ELSE 'legacy' END;
    INSERT INTO agent_runtime_scheduled_run_bindings(
        scheduled_run_id, scheduled_task_id, org_id, user_id, owner_kind,
        trigger_kind, trigger_key, scheduled_for, manual_request_id,
        task_revision, context_hash, request_hash, tenant_kill_epoch
    ) VALUES (
        p_scheduled_run_id, p_task_id, p_org_id, p_user_id, v_owner,
        p_trigger_kind, btrim(p_trigger_key), p_scheduled_for, p_manual_request_id,
        p_task_revision, p_context_hash, p_request_hash, p_tenant_kill_epoch
    ) RETURNING * INTO v_binding;
    RETURN jsonb_build_object('outcome', 'selected', 'binding', to_jsonb(v_binding));
END;
$$;
CREATE FUNCTION read_agent_runtime_scheduled_run_owner_v1(
    p_task_id UUID, p_scheduled_run_id UUID, p_org_id UUID, p_user_id UUID
) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE v_binding agent_runtime_scheduled_run_bindings%ROWTYPE;
BEGIN
    PERFORM _agent_runtime_scheduled_owner_actor();
    IF NOT EXISTS (SELECT 1 FROM scheduled_tasks WHERE id=p_task_id AND org_id=p_org_id AND user_id=p_user_id)
       OR NOT EXISTS (SELECT 1 FROM scheduled_task_runs WHERE id=p_scheduled_run_id AND task_id=p_task_id AND org_id=p_org_id) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_TENANT_MISMATCH' USING ERRCODE='42501';
    END IF;
    SELECT * INTO v_binding FROM agent_runtime_scheduled_run_bindings
     WHERE scheduled_run_id = p_scheduled_run_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('outcome','defaulted','owner_kind','legacy'); END IF;
    RETURN jsonb_build_object('outcome','found','owner_kind',v_binding.owner_kind,'binding',to_jsonb(v_binding));
END;
$$;
CREATE FUNCTION bind_agent_runtime_scheduled_run_runtime_v1(
    p_scheduled_run_id UUID, p_runtime_command_id UUID, p_runtime_run_id UUID,
    p_expected_state_version BIGINT
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_binding agent_runtime_scheduled_run_bindings%ROWTYPE;
    v_command agent_session_commands%ROWTYPE;
    v_run agent_runs%ROWTYPE;
BEGIN
    PERFORM _agent_runtime_scheduled_owner_actor();
    SELECT * INTO v_binding FROM agent_runtime_scheduled_run_bindings
     WHERE scheduled_run_id = p_scheduled_run_id FOR UPDATE;
    IF NOT FOUND OR v_binding.owner_kind <> 'runtime' THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_RUN_NOT_RUNTIME_OWNED' USING ERRCODE='42501';
    END IF;
    IF v_binding.runtime_command_id IS NOT NULL THEN
        IF v_binding.runtime_command_id IS DISTINCT FROM p_runtime_command_id
           OR v_binding.runtime_run_id IS DISTINCT FROM p_runtime_run_id THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_RUNTIME_BINDING_CONFLICT' USING ERRCODE='40001';
        END IF;
        RETURN jsonb_build_object('outcome','already_bound','binding',to_jsonb(v_binding));
    END IF;
    IF v_binding.state_version IS DISTINCT FROM p_expected_state_version THEN
        RETURN jsonb_build_object('outcome','stale_version','state_version',v_binding.state_version);
    END IF;
    SELECT * INTO v_command FROM agent_session_commands WHERE id=p_runtime_command_id;
    IF NOT FOUND OR v_command.org_id IS DISTINCT FROM v_binding.org_id
       OR v_command.user_id IS DISTINCT FROM v_binding.user_id THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_COMMAND_BINDING_INVALID' USING ERRCODE='42501';
    END IF;
    IF p_runtime_run_id IS NOT NULL THEN
        SELECT * INTO v_run FROM agent_runs WHERE id=p_runtime_run_id;
        IF NOT FOUND OR v_run.command_id IS DISTINCT FROM p_runtime_command_id
           OR v_run.org_id IS DISTINCT FROM v_binding.org_id
           OR v_run.user_id IS DISTINCT FROM v_binding.user_id THEN
            RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_RUN_BINDING_INVALID' USING ERRCODE='42501';
        END IF;
    END IF;
    UPDATE agent_runtime_scheduled_run_bindings
       SET runtime_command_id=p_runtime_command_id, runtime_run_id=p_runtime_run_id,
           owner_status=CASE WHEN p_runtime_run_id IS NULL THEN 'submitted' ELSE 'runtime_claimed' END,
           state_version=state_version+1, updated_at=clock_timestamp()
     WHERE scheduled_run_id=p_scheduled_run_id RETURNING * INTO v_binding;
    RETURN jsonb_build_object('outcome','bound','binding',to_jsonb(v_binding));
END;
$$;

CREATE FUNCTION assert_agent_runtime_scheduled_run_owner_v1(
    p_task_id UUID, p_scheduled_run_id UUID, p_expected_owner TEXT
) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE v_owner TEXT;
BEGIN
    PERFORM _agent_runtime_scheduled_owner_actor();
    IF p_expected_owner NOT IN ('legacy','runtime') THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_EXPECTED_OWNER_INVALID' USING ERRCODE='22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM scheduled_task_runs
                    WHERE id=p_scheduled_run_id AND task_id=p_task_id) THEN
        RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_OWNER_BINDING_INVALID' USING ERRCODE='42501';
    END IF;
    SELECT owner_kind INTO v_owner FROM agent_runtime_scheduled_run_bindings
     WHERE scheduled_run_id=p_scheduled_run_id AND scheduled_task_id=p_task_id;
    v_owner := COALESCE(v_owner,'legacy');
    IF v_owner IS DISTINCT FROM p_expected_owner THEN
        RAISE EXCEPTION 'SCHEDULED_RUN_%_OWNED', upper(v_owner) USING ERRCODE='42501';
    END IF;
    RETURN jsonb_build_object('outcome','allowed','owner_kind',v_owner);
END;
$$;

REVOKE ALL ON FUNCTION
    _agent_runtime_scheduled_owner_actor(),
    _agent_runtime_scheduled_snapshot_safe(JSONB),
    _agent_runtime_scheduled_identity_immutable(),
    create_agent_runtime_scheduled_execution_profile_v1(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB,TEXT,BIGINT),
    read_agent_runtime_scheduled_execution_profile_v1(UUID,UUID,UUID),
    select_agent_runtime_scheduled_run_owner_v1(UUID,UUID,UUID,UUID,TEXT,TEXT,TIMESTAMPTZ,TEXT,BIGINT,TEXT,TEXT,BIGINT),
    read_agent_runtime_scheduled_run_owner_v1(UUID,UUID,UUID,UUID),
    bind_agent_runtime_scheduled_run_runtime_v1(UUID,UUID,UUID,BIGINT),
    assert_agent_runtime_scheduled_run_owner_v1(UUID,UUID,TEXT)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker,
    everydayai_sync, everydayai, everydayai_agent_runtime_worker;

GRANT EXECUTE ON FUNCTION
    create_agent_runtime_scheduled_execution_profile_v1(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB,TEXT,BIGINT),
    read_agent_runtime_scheduled_execution_profile_v1(UUID,UUID,UUID),
    select_agent_runtime_scheduled_run_owner_v1(UUID,UUID,UUID,UUID,TEXT,TEXT,TIMESTAMPTZ,TEXT,BIGINT,TEXT,TEXT,BIGINT),
    read_agent_runtime_scheduled_run_owner_v1(UUID,UUID,UUID,UUID),
    bind_agent_runtime_scheduled_run_runtime_v1(UUID,UUID,UUID,BIGINT),
    assert_agent_runtime_scheduled_run_owner_v1(UUID,UUID,TEXT)
TO everydayai_agent_runtime_worker;

RESET ROLE;
