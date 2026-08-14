SET LOCAL ROLE everydayai_owner;

DO $guard$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_interactions)
       OR EXISTS (SELECT 1 FROM agent_authorization_grants)
       OR EXISTS (SELECT 1 FROM agent_authorization_grant_uses)
       OR EXISTS (SELECT 1 FROM agent_policy_receipts)
       OR EXISTS (SELECT 1 FROM agent_action_dispatch_intents) THEN
        RAISE EXCEPTION 'AGENT_AUTHORIZATION_RECOVERY_ROLLBACK_HAS_FACTS'
            USING ERRCODE = '55000';
    END IF;
END
$guard$;

DROP FUNCTION cancel_agent_run(UUID, BIGINT, TEXT);
ALTER FUNCTION _cancel_agent_run_220_23(UUID, BIGINT, TEXT)
    RENAME TO cancel_agent_run;

DROP FUNCTION claim_next_agent_action_reconciliation(TEXT, INTEGER, INTEGER);
DROP FUNCTION revoke_agent_authorization_grant(UUID);
DROP FUNCTION expire_agent_authorization_interaction(UUID, BIGINT);
DROP FUNCTION activate_agent_authorized_action(
    UUID, BIGINT, UUID, UUID, BIGINT, UUID);
DROP FUNCTION renew_agent_authorization_recovery(
    UUID, UUID, BIGINT, INTEGER);
DROP FUNCTION claim_next_agent_authorization_recovery(TEXT, INTEGER);
DROP FUNCTION resolve_agent_authorization_interaction(
    UUID, BIGINT, TEXT, TEXT, JSONB, TEXT, TEXT, INTEGER);
DROP FUNCTION open_agent_authorization_interaction(
    UUID, BIGINT, JSONB, TEXT, INTEGER);
DROP FUNCTION _agent_action_dispatch_snapshot(agent_action_attempts);

ALTER FUNCTION _open_agent_authorization_interaction_220_22(
    UUID, BIGINT, JSONB, TEXT, INTEGER)
    RENAME TO open_agent_authorization_interaction;
ALTER FUNCTION _resolve_agent_authorization_interaction_220_22(
    UUID, BIGINT, TEXT, TEXT, JSONB, TEXT, TEXT, INTEGER)
    RENAME TO resolve_agent_authorization_interaction;
ALTER FUNCTION _revoke_agent_authorization_grant_220_22(UUID)
    RENAME TO revoke_agent_authorization_grant;
ALTER FUNCTION _agent_action_dispatch_snapshot_220_04(agent_action_attempts)
    RENAME TO _agent_action_dispatch_snapshot;
ALTER FUNCTION _claim_next_agent_action_reconciliation_220_04(
    TEXT, INTEGER, INTEGER)
    RENAME TO claim_next_agent_action_reconciliation;

ALTER TABLE agent_interactions
    DROP COLUMN recovery_lease_expires_at,
    DROP COLUMN recovery_token,
    DROP COLUMN recovery_worker_id;

GRANT EXECUTE ON FUNCTION
    open_agent_authorization_interaction(
        UUID, BIGINT, JSONB, TEXT, INTEGER),
    claim_next_agent_action_reconciliation(TEXT, INTEGER, INTEGER)
TO everydayai_worker;
GRANT EXECUTE ON FUNCTION
    resolve_agent_authorization_interaction(
        UUID, BIGINT, TEXT, TEXT, JSONB, TEXT, TEXT, INTEGER),
    revoke_agent_authorization_grant(UUID)
TO everydayai_runtime, everydayai_wecom_runtime;
GRANT EXECUTE ON FUNCTION cancel_agent_run(UUID, BIGINT, TEXT)
TO everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

RESET ROLE;
