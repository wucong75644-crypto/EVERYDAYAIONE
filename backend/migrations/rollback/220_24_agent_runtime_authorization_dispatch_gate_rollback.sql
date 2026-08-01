SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_action_dispatch_intents)
       OR EXISTS (SELECT 1 FROM agent_authorization_grant_uses) THEN
        RAISE EXCEPTION 'AGENT_AUTHORIZATION_GATE_ROLLBACK_HAS_FACTS'
            USING ERRCODE = '55000';
    END IF;
END
$guard$;

DROP FUNCTION get_agent_action_dispatch_intent(UUID, TEXT);
DROP FUNCTION gate_agent_action_dispatch(
    UUID, UUID, BIGINT, TEXT, UUID, TEXT, INTEGER, TEXT, TEXT);
DROP FUNCTION _reject_agent_action_before_dispatch_gate(
    UUID, UUID, TEXT, TEXT);
DROP FUNCTION _close_agent_authorization_action(UUID, TEXT, TEXT);
DROP FUNCTION _recompute_agent_run_wait_state(UUID);
DROP TABLE agent_action_dispatch_intents;

ALTER TABLE agent_action_attempts
    ALTER COLUMN execution_token SET NOT NULL,
    ALTER COLUMN lease_expires_at SET NOT NULL;

REVOKE EXECUTE ON FUNCTION
    mark_agent_action_dispatching(UUID, UUID, BIGINT, TEXT)
FROM everydayai_worker;

DROP FUNCTION record_agent_policy_receipt(
    UUID, TEXT, TEXT, INTEGER, TEXT, TEXT, UUID, JSONB,
    TEXT[], TEXT[], TEXT, INTEGER);
ALTER FUNCTION _record_agent_policy_receipt_220_22(
    UUID, TEXT, TEXT, INTEGER, TEXT, TEXT, UUID, JSONB,
    TEXT[], TEXT[], TEXT, INTEGER
) RENAME TO record_agent_policy_receipt;

RESET ROLE;
