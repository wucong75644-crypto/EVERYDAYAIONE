SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_scheduled_execution_profiles)
       OR EXISTS (SELECT 1 FROM agent_runtime_scheduled_run_bindings)
       OR EXISTS (SELECT 1 FROM agent_runs WHERE run_kind='scheduled')
       OR EXISTS (SELECT 1 FROM agent_session_commands
          WHERE payload->'run_envelope'->'request_identity'->>'source'='scheduler') THEN
        RAISE EXCEPTION 'AR_18_B7_S2_A1_ROLLBACK_OWNER_FACTS_EXIST'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

REVOKE EXECUTE ON FUNCTION
    create_agent_runtime_scheduled_execution_profile_v1(UUID,UUID,UUID,BIGINT),
    read_agent_runtime_scheduled_execution_profile_v1(UUID,UUID,UUID),
    select_agent_runtime_scheduled_run_owner_v1(UUID,UUID,UUID,UUID,TEXT,TEXT,TIMESTAMPTZ,TEXT,BIGINT,TEXT,TEXT,BIGINT),
    read_agent_runtime_scheduled_run_owner_v1(UUID,UUID,UUID,UUID),
    bind_agent_runtime_scheduled_run_runtime_v1(UUID,UUID,UUID,BIGINT),
    assert_agent_runtime_scheduled_run_owner_v1(UUID,UUID,TEXT)
FROM everydayai_agent_runtime_worker;

DROP FUNCTION IF EXISTS assert_agent_runtime_scheduled_run_owner_v1(UUID,UUID,TEXT);
DROP FUNCTION IF EXISTS _agent_runtime_scheduled_owner_gate(UUID,UUID,TEXT);
DROP FUNCTION IF EXISTS bind_agent_runtime_scheduled_run_runtime_v1(UUID,UUID,UUID,BIGINT);
DROP FUNCTION IF EXISTS read_agent_runtime_scheduled_run_owner_v1(UUID,UUID,UUID,UUID);
DROP FUNCTION IF EXISTS select_agent_runtime_scheduled_run_owner_v1(UUID,UUID,UUID,UUID,TEXT,TEXT,TIMESTAMPTZ,TEXT,BIGINT,TEXT,TEXT,BIGINT);
DROP FUNCTION IF EXISTS read_agent_runtime_scheduled_execution_profile_v1(UUID,UUID,UUID);
DROP FUNCTION IF EXISTS create_agent_runtime_scheduled_execution_profile_v1(UUID,UUID,UUID,BIGINT);
DROP TRIGGER IF EXISTS runtime_scheduled_binding_identity_immutable ON agent_runtime_scheduled_run_bindings;
DROP TRIGGER IF EXISTS runtime_scheduled_profile_immutable ON agent_runtime_scheduled_execution_profiles;
DROP FUNCTION IF EXISTS _agent_runtime_scheduled_identity_immutable();
DROP FUNCTION IF EXISTS _agent_runtime_scheduled_snapshot_safe(JSONB);
DROP FUNCTION IF EXISTS _agent_runtime_scheduled_owner_actor();
DROP TABLE IF EXISTS agent_runtime_scheduled_run_bindings;
DROP TABLE IF EXISTS agent_runtime_scheduled_execution_profiles;

ALTER TABLE agent_runs DROP CONSTRAINT agent_runs_run_kind_check;
ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_run_kind_check
 CHECK(run_kind IN('user','continuation'));

RESET ROLE;
