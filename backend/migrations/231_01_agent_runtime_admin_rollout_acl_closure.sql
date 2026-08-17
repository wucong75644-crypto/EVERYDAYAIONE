-- 231.01: restore Runtime Admin access to the audited rollout control RPCs.
-- 228.08q tightened worker ACLs but also revoked these admin-only RPCs
-- without re-granting them, making tenant rollout impossible through the
-- documented control plane.

SET LOCAL ROLE everydayai_owner;

GRANT EXECUTE ON FUNCTION
    set_agent_runtime_org_rollout(UUID,UUID,BOOLEAN,TEXT),
    set_agent_runtime_rollout_subject(TEXT,TEXT,TEXT,BOOLEAN,JSONB)
TO everydayai_runtime_admin;

RESET ROLE;
