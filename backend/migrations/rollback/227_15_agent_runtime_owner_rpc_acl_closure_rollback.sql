-- Restore the exact application-role ACL established by 227.13 and 227.14.
-- This rollback changes no Runtime facts or task Owner state.
SET LOCAL ROLE everydayai_owner;

GRANT EXECUTE ON FUNCTION
    runtime_submit_ingress_v5(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB),
    restore_prepared_task_to_legacy_actor(UUID,UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT),
    mark_prepared_task_runtime_owned(UUID,UUID,UUID,UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,UUID,UUID),
    runtime_submit_ingress_v5_owner_transition(UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,UUID,TEXT,TEXT,TEXT,JSONB,JSONB,TEXT,JSONB,UUID,TEXT,UUID,UUID,UUID,TEXT)
TO everydayai_runtime, everydayai_wecom_runtime;

GRANT EXECUTE ON FUNCTION
    enqueue_wecom_runtime_turn_v6(JSONB,UUID,UUID,UUID,JSONB,JSONB,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT)
TO everydayai_wecom_runtime;

RESET ROLE;
