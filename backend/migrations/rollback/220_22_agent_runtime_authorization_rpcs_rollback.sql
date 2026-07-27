SET LOCAL ROLE everydayai_owner;

DROP FUNCTION get_agent_dispatch_policy_receipt(UUID, TEXT, TEXT, INTEGER, TEXT);
DROP FUNCTION record_agent_policy_receipt(
    UUID, TEXT, TEXT, INTEGER, TEXT, TEXT, UUID, JSONB,
    TEXT[], TEXT[], TEXT, INTEGER);
DROP FUNCTION revoke_agent_authorization_grant(UUID);
DROP FUNCTION resolve_agent_authorization_interaction(
    UUID, BIGINT, TEXT, TEXT, JSONB, TEXT, TEXT, INTEGER);
DROP FUNCTION open_agent_authorization_interaction(
    UUID, BIGINT, JSONB, TEXT, INTEGER);

RESET ROLE;
