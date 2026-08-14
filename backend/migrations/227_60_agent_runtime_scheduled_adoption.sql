-- 227_60: Historical scheduled-task adoption provenance and Runtime profile.
-- This migration never creates a normal Session/Command/Run/Action/Attempt.

SET LOCAL ROLE everydayai_owner;

-- Adoption facts are executable Runtime ownership facts, but have no ordinary
-- source Action/Attempt/Run. Keep those provenance columns empty explicitly.
ALTER TABLE agent_runtime_scheduled_execution_profiles
    ALTER COLUMN source_action_id DROP NOT NULL,
    ALTER COLUMN source_attempt_id DROP NOT NULL,
    ALTER COLUMN source_run_id DROP NOT NULL;
ALTER TABLE agent_runtime_scheduled_execution_profiles
    ADD CONSTRAINT runtime_scheduled_profile_source_shape_check CHECK(
        (source_action_id IS NULL AND source_attempt_id IS NULL AND source_run_id IS NULL)
        OR (source_action_id IS NOT NULL AND source_attempt_id IS NOT NULL AND source_run_id IS NOT NULL)
    );

DROP TRIGGER IF EXISTS runtime_scheduled_profile_immutable
    ON agent_runtime_scheduled_execution_profiles;
CREATE FUNCTION _agent_runtime_scheduled_profile_immutable() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND current_user = 'everydayai_owner'
       AND current_setting('app.agent_runtime_scheduled_adoption_rollback', true) = '1'
       AND OLD.source_action_id IS NULL
       AND OLD.source_attempt_id IS NULL
       AND OLD.source_run_id IS NULL THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'RUNTIME_SCHEDULER_FACT_IMMUTABLE' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER runtime_scheduled_profile_immutable
    BEFORE UPDATE OR DELETE ON agent_runtime_scheduled_execution_profiles
    FOR EACH ROW EXECUTE FUNCTION _agent_runtime_scheduled_profile_immutable();

CREATE TABLE agent_runtime_scheduled_adoption_provenance(
    adoption_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scheduled_task_id UUID NOT NULL UNIQUE REFERENCES scheduled_tasks(id) ON DELETE RESTRICT,
    adoption_request_id UUID NOT NULL,
    org_id UUID NOT NULL,
    user_id UUID NOT NULL,
    prior_status TEXT NOT NULL CHECK(prior_status IN ('active','paused','error')),
    task_semantics_hash TEXT NOT NULL CHECK(task_semantics_hash ~ '^[0-9a-f]{64}$'),
    delivery_target_hash TEXT NOT NULL CHECK(delivery_target_hash ~ '^[0-9a-f]{64}$'),
    provenance_kind TEXT NOT NULL DEFAULT 'historical_scheduled_task_adoption'
        CHECK(provenance_kind = 'historical_scheduled_task_adoption'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE agent_runtime_scheduled_adoption_profiles(
    adoption_id UUID PRIMARY KEY REFERENCES agent_runtime_scheduled_adoption_provenance(adoption_id)
        ON DELETE RESTRICT,
    scheduled_task_id UUID NOT NULL UNIQUE REFERENCES scheduled_tasks(id) ON DELETE RESTRICT,
    org_id UUID NOT NULL,
    user_id UUID NOT NULL,
    agent_definition_id TEXT NOT NULL,
    agent_definition_revision TEXT NOT NULL,
    agent_definition_hash TEXT NOT NULL CHECK(agent_definition_hash ~ '^[0-9a-f]{64}$'),
    catalog_revision TEXT NOT NULL CHECK(catalog_revision ~ '^[0-9a-f]{64}$'),
    source_effective_toolset_hash TEXT NOT NULL CHECK(source_effective_toolset_hash ~ '^[0-9a-f]{64}$'),
    effective_toolset_hash TEXT NOT NULL CHECK(effective_toolset_hash ~ '^[0-9a-f]{64}$'),
    model_snapshot JSONB NOT NULL CHECK(jsonb_typeof(model_snapshot) = 'object'),
    toolset_snapshot JSONB NOT NULL CHECK(jsonb_typeof(toolset_snapshot) = 'object'),
    scope_snapshot JSONB NOT NULL CHECK(jsonb_typeof(scope_snapshot) = 'object'),
    channel TEXT NOT NULL CHECK(channel IN ('web','wecom')),
    budget_snapshot JSONB NOT NULL CHECK(jsonb_typeof(budget_snapshot) = 'object'),
    provider_key TEXT NOT NULL,
    capability_key TEXT NOT NULL,
    provider_revision TEXT NOT NULL,
    capability_revision TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
    state_version BIGINT NOT NULL DEFAULT 1 CHECK(state_version = 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE agent_runtime_scheduled_adoption_provenance ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_adoption_provenance FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_adoption_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_adoption_profiles FORCE ROW LEVEL SECURITY;
CREATE POLICY scheduled_adoption_provenance_owner_all ON agent_runtime_scheduled_adoption_provenance
    FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
CREATE POLICY scheduled_adoption_profiles_owner_all ON agent_runtime_scheduled_adoption_profiles
    FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);

CREATE FUNCTION _agent_runtime_scheduled_adoption_immutable() RETURNS TRIGGER
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF current_user = 'everydayai_owner'
       AND current_setting('app.agent_runtime_scheduled_adoption_rollback', true) = '1' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'AGENT_RUNTIME_SCHEDULED_ADOPTION_FACT_IMMUTABLE' USING ERRCODE = '55000';
END;
$$;
CREATE TRIGGER scheduled_adoption_provenance_immutable
    BEFORE UPDATE OR DELETE ON agent_runtime_scheduled_adoption_provenance
    FOR EACH ROW EXECUTE FUNCTION _agent_runtime_scheduled_adoption_immutable();
CREATE TRIGGER scheduled_adoption_profile_immutable
    BEFORE UPDATE OR DELETE ON agent_runtime_scheduled_adoption_profiles
    FOR EACH ROW EXECUTE FUNCTION _agent_runtime_scheduled_adoption_immutable();

CREATE FUNCTION adopt_agent_runtime_scheduled_tasks_v1(
    p_facts JSONB, p_adoption_request_id UUID DEFAULT gen_random_uuid()
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
    task scheduled_tasks%ROWTYPE;
    facts JSONB;
    candidate_count INTEGER;
    fact_count INTEGER;
    expected_task_hash TEXT;
    expected_target_hash TEXT;
    adoption_id UUID;
    applied INTEGER := 0;
    already_adopted INTEGER := 0;
BEGIN
    IF current_user <> 'everydayai_owner' THEN
        RAISE EXCEPTION 'SCHEDULED_ADOPTION_OWNER_REQUIRED' USING ERRCODE = '42501';
    END IF;
    IF jsonb_typeof(p_facts) IS DISTINCT FROM 'object' OR p_adoption_request_id IS NULL THEN
        RAISE EXCEPTION 'SCHEDULED_ADOPTION_FACTS_INVALID' USING ERRCODE = '22023';
    END IF;

    SELECT count(*)::INTEGER INTO candidate_count
    FROM scheduled_tasks AS scheduled_task_row
    LEFT JOIN agent_runtime_scheduled_adoption_profiles profile
      ON profile.scheduled_task_id = scheduled_task_row.id
    LEFT JOIN agent_runtime_scheduled_execution_profiles runtime_profile
      ON runtime_profile.scheduled_task_id = scheduled_task_row.id
    WHERE profile.scheduled_task_id IS NULL
      AND runtime_profile.scheduled_task_id IS NULL;
    SELECT count(*)::INTEGER INTO fact_count FROM jsonb_object_keys(p_facts);
    IF fact_count <> candidate_count THEN
        RAISE EXCEPTION 'SCHEDULED_ADOPTION_FACT_SET_INCOMPLETE' USING ERRCODE = '55000';
    END IF;

    FOR task IN
        SELECT scheduled_task_row.* FROM scheduled_tasks AS scheduled_task_row
        LEFT JOIN agent_runtime_scheduled_adoption_profiles profile
          ON profile.scheduled_task_id = scheduled_task_row.id
        LEFT JOIN agent_runtime_scheduled_execution_profiles runtime_profile
          ON runtime_profile.scheduled_task_id = scheduled_task_row.id
        WHERE profile.scheduled_task_id IS NULL
          AND runtime_profile.scheduled_task_id IS NULL
        ORDER BY scheduled_task_row.id FOR UPDATE OF scheduled_task_row
    LOOP
        IF task.status NOT IN ('active','paused','error') THEN
            RAISE EXCEPTION 'SCHEDULED_ADOPTION_TASK_STATUS_BLOCKED' USING ERRCODE = '55000';
        END IF;
        IF task.status = 'running' OR EXISTS(
            SELECT 1 FROM scheduled_task_runs run
            WHERE run.task_id = task.id AND run.status = 'running'
        ) THEN
            RAISE EXCEPTION 'SCHEDULED_ADOPTION_TASK_IN_FLIGHT' USING ERRCODE = '55000';
        END IF;
        IF task.runtime_action_id IS NOT NULL OR task.runtime_attempt_id IS NOT NULL
           OR task.runtime_request_hash IS NOT NULL OR task.runtime_idempotency_key IS NOT NULL THEN
            RAISE EXCEPTION 'SCHEDULED_ADOPTION_PARTIAL_RUNTIME_FACTS' USING ERRCODE = '55000';
        END IF;
        IF task.status = 'active' AND NOT (
            NULLIF(btrim(task.name), '') IS NOT NULL
            AND NULLIF(task.prompt, '') IS NOT NULL
            AND EXISTS(SELECT 1 FROM pg_timezone_names zone WHERE zone.name = task.timezone)
            AND task.schedule_type IN ('once','daily','weekly','monthly','cron')
            AND ((task.schedule_type = 'once' AND task.run_at IS NOT NULL)
                 OR (task.schedule_type <> 'once' AND NULLIF(btrim(task.cron_expr), '') IS NOT NULL))
            AND task.next_run_at IS NOT NULL
            AND _agent_runtime_scheduled_adoption_target_shape(task.push_target)
        ) THEN
            RAISE EXCEPTION 'SCHEDULED_ADOPTION_TASK_INVALID' USING ERRCODE = '55000';
        END IF;

        facts := p_facts -> task.id::TEXT;
        IF jsonb_typeof(facts) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'SCHEDULED_ADOPTION_FACT_MISSING' USING ERRCODE = '55000';
        END IF;
        expected_task_hash := encode(digest(convert_to((jsonb_build_array(
            task.id,task.org_id,task.user_id,task.name,task.prompt,task.timezone,
            task.push_target,task.template_file,task.max_credits,task.retry_count,
            task.timeout_sec,task.schedule_type,task.cron_expr,
            CASE WHEN task.run_at IS NULL THEN NULL ELSE
                to_char(task.run_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS') || '+00:00' END,
            task.weekdays,task.day_of_month,
            CASE WHEN task.next_run_at IS NULL THEN NULL ELSE
                to_char(task.next_run_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS') || '+00:00' END,
            task.last_summary))::TEXT, 'UTF8'),'sha256'),'hex');
        expected_target_hash := encode(digest(convert_to(
            jsonb_build_array(task.push_target)::TEXT, 'UTF8'),'sha256'),'hex');
        IF facts->>'task_semantics_hash' IS DISTINCT FROM expected_task_hash
           OR facts->>'delivery_target_hash' IS DISTINCT FROM expected_target_hash
           OR NULLIF(facts->>'agent_definition_id','') IS NULL
           OR NULLIF(facts->>'agent_definition_revision','') IS NULL
           OR (facts->>'agent_definition_hash') !~ '^[0-9a-f]{64}$'
           OR (facts->>'catalog_revision') !~ '^[0-9a-f]{64}$'
           OR (facts->>'source_effective_toolset_hash') !~ '^[0-9a-f]{64}$'
           OR (facts->>'effective_toolset_hash') !~ '^[0-9a-f]{64}$'
           OR jsonb_typeof(facts->'model_snapshot') IS DISTINCT FROM 'object'
           OR jsonb_typeof(facts->'toolset_snapshot') IS DISTINCT FROM 'object'
           OR jsonb_typeof(facts->'scope_snapshot') IS DISTINCT FROM 'object'
           OR jsonb_typeof(facts->'budget_snapshot') IS DISTINCT FROM 'object'
           OR facts->>'channel' NOT IN ('web','wecom')
           OR NULLIF(facts->>'provider_key','') IS NULL
           OR NULLIF(facts->>'capability_key','') IS NULL
           OR NULLIF(facts->>'provider_revision','') IS NULL
           OR NULLIF(facts->>'capability_revision','') IS NULL
           OR (facts->>'request_hash') !~ '^[0-9a-f]{64}$'
           OR NOT _agent_runtime_scheduled_snapshot_safe(facts->'model_snapshot')
           OR NOT _agent_runtime_scheduled_snapshot_safe(facts->'toolset_snapshot')
           OR NOT _agent_runtime_scheduled_snapshot_safe(facts->'scope_snapshot')
           OR NOT _agent_runtime_scheduled_snapshot_safe(facts->'budget_snapshot') THEN
            RAISE EXCEPTION 'SCHEDULED_ADOPTION_FACT_INCOMPLETE' USING ERRCODE = '55000';
        END IF;
        INSERT INTO agent_runtime_scheduled_adoption_provenance(
            scheduled_task_id,adoption_request_id,org_id,user_id,prior_status,
            task_semantics_hash,delivery_target_hash
        ) VALUES(
            task.id,p_adoption_request_id,task.org_id,task.user_id,task.status,
            expected_task_hash,expected_target_hash
        ) RETURNING agent_runtime_scheduled_adoption_provenance.adoption_id INTO adoption_id;
        INSERT INTO agent_runtime_scheduled_adoption_profiles(
            adoption_id,scheduled_task_id,org_id,user_id,agent_definition_id,
            agent_definition_revision,agent_definition_hash,catalog_revision,
            source_effective_toolset_hash,effective_toolset_hash,model_snapshot,
            toolset_snapshot,scope_snapshot,channel,budget_snapshot,provider_key,
            capability_key,provider_revision,capability_revision,request_hash
        ) VALUES(
            adoption_id,task.id,task.org_id,task.user_id,facts->>'agent_definition_id',
            facts->>'agent_definition_revision',facts->>'agent_definition_hash',facts->>'catalog_revision',
            facts->>'source_effective_toolset_hash',facts->>'effective_toolset_hash',facts->'model_snapshot',
            facts->'toolset_snapshot',facts->'scope_snapshot',facts->>'channel',facts->'budget_snapshot',
            facts->>'provider_key',facts->>'capability_key',facts->>'provider_revision',
            facts->>'capability_revision',facts->>'request_hash'
        );
        INSERT INTO agent_runtime_scheduled_execution_profiles(
            scheduled_task_id,org_id,user_id,source_action_id,source_attempt_id,source_run_id,
            agent_definition_id,agent_definition_revision,agent_definition_hash,catalog_revision,
            source_effective_toolset_hash,effective_toolset_hash,model_snapshot,toolset_snapshot,
            scope_snapshot,channel,budget_snapshot,provider_key,capability_key,provider_revision,
            capability_revision,request_hash,state_version
        ) VALUES(
            task.id,task.org_id,task.user_id,NULL,NULL,NULL,
            facts->>'agent_definition_id',facts->>'agent_definition_revision',facts->>'agent_definition_hash',
            facts->>'catalog_revision',facts->>'source_effective_toolset_hash',facts->>'effective_toolset_hash',
            facts->'model_snapshot',facts->'toolset_snapshot',facts->'scope_snapshot',facts->>'channel',
            facts->'budget_snapshot',facts->>'provider_key',facts->>'capability_key',
            facts->>'provider_revision',facts->>'capability_revision',facts->>'request_hash',1
        );
        applied := applied + 1;
    END LOOP;
    RETURN jsonb_build_object('outcome','adopted','applied_count',applied,
        'already_adopted_count',already_adopted,'ordinary_execution_history_created',FALSE);
END;
$$;

CREATE FUNCTION read_agent_runtime_scheduled_adoption_v1(p_task_id UUID DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER STABLE SET search_path = pg_catalog, public AS $$
DECLARE result JSONB;
BEGIN
    IF current_user <> 'everydayai_owner' THEN
        RAISE EXCEPTION 'SCHEDULED_ADOPTION_OWNER_REQUIRED' USING ERRCODE = '42501';
    END IF;
    SELECT jsonb_build_object('outcome','readback','ordinary_execution_history_created',FALSE,
        'profiles',COALESCE(jsonb_agg(jsonb_build_object(
            'scheduled_task_id',profile.scheduled_task_id,
            'adoption_id',profile.adoption_id,
            'org_id',profile.org_id,'user_id',profile.user_id,
            'channel',profile.channel,'agent_definition_id',profile.agent_definition_id,
            'agent_definition_revision',profile.agent_definition_revision,
            'catalog_revision',profile.catalog_revision,
            'effective_toolset_hash',profile.effective_toolset_hash,
            'model_snapshot',profile.model_snapshot,'toolset_snapshot',profile.toolset_snapshot,
            'scope_snapshot',profile.scope_snapshot,'budget_snapshot',profile.budget_snapshot,
            'provider_key',profile.provider_key,'capability_key',profile.capability_key,
            'provider_revision',profile.provider_revision,'capability_revision',profile.capability_revision,
            'request_hash',profile.request_hash,'state_version',profile.state_version
        ) ORDER BY profile.scheduled_task_id), '[]'::JSONB)) INTO result
    FROM agent_runtime_scheduled_adoption_profiles profile
    WHERE p_task_id IS NULL OR profile.scheduled_task_id = p_task_id;
    RETURN result;
END;
$$;

CREATE FUNCTION rollback_agent_runtime_scheduled_adoption_v1(p_task_id UUID)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE adoption UUID;
BEGIN
    IF current_user <> 'everydayai_owner' THEN
        RAISE EXCEPTION 'SCHEDULED_ADOPTION_OWNER_REQUIRED' USING ERRCODE = '42501';
    END IF;
    SELECT profile.adoption_id INTO adoption
    FROM agent_runtime_scheduled_adoption_profiles profile
    WHERE profile.scheduled_task_id = p_task_id FOR UPDATE;
    IF adoption IS NULL THEN
        RAISE EXCEPTION 'SCHEDULED_ADOPTION_NOT_FOUND' USING ERRCODE = '22023';
    END IF;
    IF EXISTS(SELECT 1 FROM agent_runtime_scheduled_run_bindings binding
              WHERE binding.scheduled_task_id = p_task_id AND binding.owner_kind = 'runtime')
       OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_submission_intents intent
                 WHERE intent.scheduled_task_id = p_task_id)
       OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_delivery_intents delivery
                 WHERE delivery.scheduled_task_id = p_task_id)
       OR EXISTS(SELECT 1 FROM agent_runtime_scheduled_web_projection_receipts projection
                 WHERE projection.scheduled_task_id = p_task_id) THEN
        RAISE EXCEPTION 'SCHEDULED_ADOPTION_ROLLBACK_SIDE_EFFECTS_EXIST' USING ERRCODE = '55000';
    END IF;
    PERFORM set_config('app.agent_runtime_scheduled_adoption_rollback','1',TRUE);
    DELETE FROM agent_runtime_scheduled_execution_profiles
    WHERE scheduled_task_id = p_task_id
      AND source_action_id IS NULL
      AND source_attempt_id IS NULL
      AND source_run_id IS NULL;
    DELETE FROM agent_runtime_scheduled_adoption_profiles WHERE adoption_id = adoption;
    DELETE FROM agent_runtime_scheduled_adoption_provenance WHERE adoption_id = adoption;
    RETURN jsonb_build_object('outcome','rolled_back','scheduled_task_id',p_task_id,
        'ordinary_execution_history_created',FALSE);
END;
$$;

REVOKE ALL ON TABLE agent_runtime_scheduled_adoption_provenance,
    agent_runtime_scheduled_adoption_profiles
    FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
         everydayai_sync,everydayai,everydayai_agent_runtime_worker;
REVOKE ALL ON FUNCTION _agent_runtime_scheduled_adoption_immutable(),
    _agent_runtime_scheduled_profile_immutable(),
    adopt_agent_runtime_scheduled_tasks_v1(JSONB,UUID),
    read_agent_runtime_scheduled_adoption_v1(UUID),
    rollback_agent_runtime_scheduled_adoption_v1(UUID)
    FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
         everydayai_sync,everydayai,everydayai_agent_runtime_worker;

RESET ROLE;
