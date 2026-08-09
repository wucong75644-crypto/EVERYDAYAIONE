SET LOCAL ROLE everydayai_owner;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_runtime_scheduler_operation_intents)
       OR EXISTS (SELECT 1 FROM agent_runtime_scheduler_cancel_gates) THEN
        RAISE EXCEPTION 'AR_18_B7_ROLLBACK_BLOCKED_SCHEDULER_INTENTS'
            USING ERRCODE = '55000';
    END IF;
END;
$$;

REVOKE EXECUTE ON FUNCTION
    mutate_agent_runtime_scheduled_task_control_v1(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,BIGINT,BIGINT,TEXT,UUID,TEXT,UUID,JSONB,TEXT,TIMESTAMPTZ),
    get_agent_runtime_scheduled_task_resume_context_v1(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,BIGINT,BIGINT,TEXT,UUID,UUID),
    read_agent_runtime_scheduled_task_control_v1(UUID,TEXT,UUID,BIGINT,TEXT),
    cancel_agent_runtime_scheduled_task_control_v1(UUID,TEXT,UUID,BIGINT,TEXT,TEXT),
    reconcile_agent_runtime_scheduled_task_control_v1(UUID,TEXT,UUID,BIGINT,TEXT)
    FROM everydayai_agent_runtime_worker;
GRANT EXECUTE ON FUNCTION runtime_mutate_scheduled_task(UUID,UUID,UUID,BIGINT,TEXT,TEXT,JSONB,UUID)
    TO everydayai_agent_runtime_worker;

DROP FUNCTION IF EXISTS reconcile_agent_runtime_scheduled_task_control_v1(UUID,TEXT,UUID,BIGINT,TEXT);
DROP FUNCTION IF EXISTS cancel_agent_runtime_scheduled_task_control_v1(UUID,TEXT,UUID,BIGINT,TEXT,TEXT);
DROP FUNCTION IF EXISTS read_agent_runtime_scheduled_task_control_v1(UUID,TEXT,UUID,BIGINT,TEXT);
DROP FUNCTION IF EXISTS mutate_agent_runtime_scheduled_task_control_v1(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,TEXT,BIGINT,BIGINT,TEXT,UUID,TEXT,UUID,JSONB,TEXT,TIMESTAMPTZ);
DROP FUNCTION IF EXISTS get_agent_runtime_scheduled_task_resume_context_v1(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,UUID,BIGINT,BIGINT,TEXT,UUID,UUID);
DROP FUNCTION IF EXISTS _runtime_scheduler_schedule_hash(scheduled_tasks);
DROP FUNCTION IF EXISTS _runtime_scheduler_response(TEXT,agent_runtime_scheduler_operation_receipts);
DROP FUNCTION IF EXISTS _runtime_scheduler_control_context(UUID,UUID,UUID,UUID,UUID,TEXT,TEXT,TEXT,UUID,BIGINT,UUID);
DROP FUNCTION IF EXISTS _runtime_scheduler_payload_valid(TEXT,JSONB);
DROP FUNCTION IF EXISTS _runtime_scheduler_payload_safe(JSONB);
DROP FUNCTION IF EXISTS _runtime_scheduler_push_target_allowed(UUID,UUID,JSONB,INTEGER);
DROP FUNCTION IF EXISTS _runtime_scheduler_operation_allowed(UUID,UUID,TEXT,UUID);
DROP FUNCTION IF EXISTS _runtime_scheduler_actor_allowed(UUID,UUID,BOOLEAN);
DROP TRIGGER IF EXISTS scheduler_operation_receipt_immutable ON agent_runtime_scheduler_operation_receipts;
DROP TRIGGER IF EXISTS scheduler_operation_intent_immutable ON agent_runtime_scheduler_operation_intents;
DROP TRIGGER IF EXISTS scheduler_cancel_gate_immutable ON agent_runtime_scheduler_cancel_gates;
DROP FUNCTION IF EXISTS _runtime_scheduler_immutable_fact();
DROP TABLE IF EXISTS agent_runtime_scheduler_operation_receipts;
DROP TABLE IF EXISTS agent_runtime_scheduler_operation_intents;
DROP TABLE IF EXISTS agent_runtime_scheduler_cancel_gates;

RESET ROLE;
