SET LOCAL ROLE everydayai_owner;
DO $$ BEGIN IF EXISTS(SELECT 1 FROM agent_action_attempts WHERE provider IS NOT NULL OR provider_task_ref IS NOT NULL) THEN RAISE EXCEPTION 'AGENT_RUNTIME_226_ROLLBACK_GUARD_FACTS_EXIST'; END IF; END $$;
REVOKE ALL ON FUNCTION record_agent_action_provider_submission(UUID,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TIMESTAMPTZ,JSONB),record_agent_action_unknown(UUID,UUID,TEXT,JSONB),resolve_agent_action_provider_reconciliation(UUID,UUID,TEXT,TEXT,JSONB,JSONB) FROM everydayai_agent_runtime_worker;
DROP FUNCTION resolve_agent_action_provider_reconciliation(UUID,UUID,TEXT,TEXT,JSONB,JSONB); DROP FUNCTION record_agent_action_unknown(UUID,UUID,TEXT,JSONB); DROP FUNCTION record_agent_action_provider_submission(UUID,UUID,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TEXT,TIMESTAMPTZ,JSONB); DROP FUNCTION _agent_runtime_226_append_action_event(UUID,TEXT,JSONB);
DROP INDEX idx_agent_attempt_callback_correlation,idx_agent_attempt_provider_ref,idx_agent_attempt_reconcile_due,uq_agent_attempt_provider_idempotency;
ALTER TABLE agent_action_attempts DROP CONSTRAINT agent_attempt_provider_hash, DROP CONSTRAINT agent_attempt_provider_pair,
 DROP COLUMN late_receipt_hash, DROP COLUMN cancel_confirmed_at, DROP COLUMN cancel_requested_at, DROP COLUMN last_provider_status,
 DROP COLUMN next_reconcile_at, DROP COLUMN provider_request_hash, DROP COLUMN provider_idempotency_key, DROP COLUMN callback_correlation,
 DROP COLUMN provider_status_locator, DROP COLUMN provider_task_ref, DROP COLUMN provider;
RESET ROLE;
