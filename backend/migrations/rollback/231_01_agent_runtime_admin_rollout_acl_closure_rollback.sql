-- Restore the pre-231.01 ACL state created by 228.08q.

SET LOCAL ROLE everydayai_owner;

REVOKE EXECUTE ON FUNCTION
    set_agent_runtime_org_rollout(UUID,UUID,BOOLEAN,TEXT),
    set_agent_runtime_rollout_subject(TEXT,TEXT,TEXT,BOOLEAN,JSONB)
FROM everydayai_runtime_admin;

RESET ROLE;
