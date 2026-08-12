-- 227_62: close the scheduled-task legacy Owner only after a verified cutover.
-- The default is pending: historical tasks continue to use the existing
-- legacy path until an owner-only completion RPC proves Runtime coverage.

SET LOCAL ROLE everydayai_owner;

CREATE TABLE agent_runtime_scheduled_adoption_control(
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK(singleton),
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK(state IN ('pending','complete')),
    state_version BIGINT NOT NULL DEFAULT 0 CHECK(state_version >= 0),
    completed_at TIMESTAMPTZ,
    completed_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK((state = 'pending' AND completed_at IS NULL AND completed_by IS NULL)
       OR (state = 'complete' AND completed_at IS NOT NULL
           AND NULLIF(btrim(completed_by), '') IS NOT NULL))
);
INSERT INTO agent_runtime_scheduled_adoption_control(singleton)
VALUES(TRUE)
ON CONFLICT (singleton) DO NOTHING;

ALTER TABLE agent_runtime_scheduled_adoption_control ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_runtime_scheduled_adoption_control FORCE ROW LEVEL SECURITY;
CREATE POLICY scheduled_adoption_control_owner_all
    ON agent_runtime_scheduled_adoption_control
    FOR ALL TO everydayai_owner USING(TRUE) WITH CHECK(TRUE);
REVOKE ALL ON TABLE agent_runtime_scheduled_adoption_control
    FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
         everydayai_sync,everydayai,everydayai_agent_runtime_worker;

CREATE FUNCTION _agent_runtime_scheduled_adoption_complete()
RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT COALESCE((
        SELECT state = 'complete'
        FROM agent_runtime_scheduled_adoption_control
        WHERE singleton
    ), FALSE)
$$;

CREATE FUNCTION read_agent_runtime_scheduled_adoption_control_v1()
RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE control agent_runtime_scheduled_adoption_control%ROWTYPE;
BEGIN
    IF current_user NOT IN ('everydayai_owner', 'everydayai_worker') THEN
        RAISE EXCEPTION 'SCHEDULED_ADOPTION_CONTROL_SCOPE_REQUIRED' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO control
    FROM agent_runtime_scheduled_adoption_control
    WHERE singleton;
    RETURN jsonb_build_object(
        'state', control.state,
        'state_version', control.state_version,
        'completed_at', control.completed_at,
        'completed_by', control.completed_by,
        'updated_at', control.updated_at
    );
END;
$$;

CREATE FUNCTION complete_agent_runtime_scheduled_adoption_v1(
    p_request_id UUID
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
    control agent_runtime_scheduled_adoption_control%ROWTYPE;
    total_tasks BIGINT;
    runtime_owned_tasks BIGINT;
    profileless_tasks BIGINT;
    in_flight_profileless BIGINT;
BEGIN
    IF current_user <> 'everydayai_owner' OR p_request_id IS NULL THEN
        RAISE EXCEPTION 'SCHEDULED_ADOPTION_COMPLETION_SCOPE_INVALID' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO control
    FROM agent_runtime_scheduled_adoption_control
    WHERE singleton
    FOR UPDATE;

    SELECT count(*) INTO total_tasks FROM scheduled_tasks;
    SELECT count(*) INTO runtime_owned_tasks
    FROM scheduled_tasks task
    JOIN agent_runtime_scheduled_execution_profiles profile
      ON profile.scheduled_task_id = task.id;
    SELECT count(*) INTO profileless_tasks
    FROM scheduled_tasks task
    LEFT JOIN agent_runtime_scheduled_execution_profiles profile
      ON profile.scheduled_task_id = task.id
    WHERE profile.scheduled_task_id IS NULL;
    SELECT count(*) INTO in_flight_profileless
    FROM scheduled_tasks task
    LEFT JOIN agent_runtime_scheduled_execution_profiles profile
      ON profile.scheduled_task_id = task.id
    WHERE profile.scheduled_task_id IS NULL
      AND (task.status = 'running' OR EXISTS(
          SELECT 1 FROM scheduled_task_runs run
          WHERE run.task_id = task.id AND run.status = 'running'
      ));

    IF profileless_tasks <> 0 OR in_flight_profileless <> 0 THEN
        RAISE EXCEPTION 'SCHEDULED_ADOPTION_RUNTIME_COVERAGE_INCOMPLETE'
            USING ERRCODE = '55000',
                  DETAIL = format(
                      'total=%s runtime_owned=%s profileless=%s in_flight_profileless=%s',
                      total_tasks, runtime_owned_tasks, profileless_tasks,
                      in_flight_profileless
                  );
    END IF;

    IF control.state = 'complete' THEN
        RETURN jsonb_build_object(
            'outcome', 'already_complete',
            'state', control.state,
            'state_version', control.state_version,
            'total_tasks', total_tasks,
            'runtime_owned_tasks', runtime_owned_tasks
        );
    END IF;

    UPDATE agent_runtime_scheduled_adoption_control
    SET state = 'complete', state_version = state_version + 1,
        completed_at = clock_timestamp(), completed_by = p_request_id::TEXT,
        updated_at = clock_timestamp()
    WHERE singleton
    RETURNING * INTO control;
    RETURN jsonb_build_object(
        'outcome', 'completed',
        'state', control.state,
        'state_version', control.state_version,
        'total_tasks', total_tasks,
        'runtime_owned_tasks', runtime_owned_tasks,
        'profileless_tasks', profileless_tasks,
        'request_id', p_request_id
    );
END;
$$;

CREATE OR REPLACE FUNCTION worker_claim_due_scheduled_executions_v1(
    p_now TIMESTAMPTZ, p_limit INTEGER
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
    t scheduled_tasks%ROWTYPE;
    items JSONB := '[]'::JSONB;
    item JSONB;
    claimed INTEGER := 0;
BEGIN
    PERFORM _agent_runtime_scheduled_submission_worker();
    IF p_now IS NULL OR p_limit NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'SCHEDULED_WORKER_CLAIM_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    FOR t IN
        SELECT candidate.*
        FROM scheduled_tasks candidate
        WHERE candidate.status = 'active'
          AND candidate.next_run_at IS NOT NULL
          AND candidate.next_run_at <= p_now
          AND (_agent_runtime_scheduled_submission_enabled() OR NOT EXISTS(
              SELECT 1 FROM agent_runtime_scheduled_execution_profiles profile
              WHERE profile.scheduled_task_id = candidate.id
          ))
          AND (candidate.org_id IS NULL OR EXISTS(
              SELECT 1 FROM organizations org
              WHERE org.id = candidate.org_id AND org.status = 'active'
          ))
        ORDER BY candidate.next_run_at, candidate.id
        LIMIT p_limit * 4
        FOR UPDATE OF candidate SKIP LOCKED
    LOOP
        EXIT WHEN claimed >= p_limit;
        IF _agent_runtime_scheduled_adoption_complete()
           AND NOT EXISTS(
               SELECT 1 FROM agent_runtime_scheduled_execution_profiles profile
               WHERE profile.scheduled_task_id = t.id
           ) THEN
            RAISE EXCEPTION 'SCHEDULED_ADOPTION_RUNTIME_OWNER_INCOMPLETE'
                USING ERRCODE = '55000';
        END IF;
        IF EXISTS(
            SELECT 1 FROM agent_runtime_scheduled_execution_profiles profile
            WHERE profile.scheduled_task_id = t.id
        ) THEN
            item := _submit_agent_runtime_scheduled_execution_v1(
                t.id, 'scheduled', 'scheduled:' || t.next_run_at::TEXT,
                t.next_run_at, NULL, t.user_id, p_now
            );
            IF item->>'outcome' = 'runtime_disabled' THEN
                CONTINUE;
            END IF;
        ELSE
            UPDATE scheduled_tasks
            SET status = 'running', next_run_at = NULL, updated_at = p_now
            WHERE id = t.id
            RETURNING * INTO t;
            item := jsonb_build_object(
                'outcome', 'claimed', 'owner_kind', 'legacy', 'task', to_jsonb(t)
            );
        END IF;
        items := items || jsonb_build_array(item);
        claimed := claimed + 1;
    END LOOP;
    RETURN items;
END;
$$;

CREATE OR REPLACE FUNCTION worker_assert_scheduled_task_legacy_owner_v1(
    p_task_id UUID
) RETURNS JSONB
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    PERFORM _agent_runtime_scheduled_submission_worker();
    IF p_task_id IS NULL THEN
        RAISE EXCEPTION 'SCHEDULED_LEGACY_OWNER_ARGUMENT_INVALID' USING ERRCODE = '22023';
    END IF;
    IF EXISTS(
        SELECT 1 FROM agent_runtime_scheduled_execution_profiles
        WHERE scheduled_task_id = p_task_id
    ) THEN
        RAISE EXCEPTION 'SCHEDULED_RUN_RUNTIME_OWNED' USING ERRCODE = '42501';
    END IF;
    IF _agent_runtime_scheduled_adoption_complete() THEN
        RAISE EXCEPTION 'SCHEDULED_LEGACY_OWNER_DISABLED'
            USING ERRCODE = '42501';
    END IF;
    RETURN jsonb_build_object('outcome', 'allowed', 'owner_kind', 'legacy');
END;
$$;

REVOKE ALL ON TABLE agent_runtime_scheduled_adoption_control
    FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
         everydayai_sync,everydayai,everydayai_agent_runtime_worker;
REVOKE ALL ON FUNCTION _agent_runtime_scheduled_adoption_complete(),
    read_agent_runtime_scheduled_adoption_control_v1(),
    complete_agent_runtime_scheduled_adoption_v1(UUID),
    worker_claim_due_scheduled_executions_v1(TIMESTAMPTZ, INTEGER),
    worker_assert_scheduled_task_legacy_owner_v1(UUID)
    FROM PUBLIC,everydayai_runtime,everydayai_wecom_runtime,everydayai_worker,
         everydayai_sync,everydayai,everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION worker_claim_due_scheduled_executions_v1(TIMESTAMPTZ, INTEGER),
    worker_assert_scheduled_task_legacy_owner_v1(UUID)
    TO everydayai_worker;
GRANT EXECUTE ON FUNCTION read_agent_runtime_scheduled_adoption_control_v1()
    TO everydayai_owner;
GRANT EXECUTE ON FUNCTION complete_agent_runtime_scheduled_adoption_v1(UUID)
    TO everydayai_owner;

RESET ROLE;
