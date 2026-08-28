SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS search_governed_user_by_phone(TEXT);
DROP FUNCTION IF EXISTS list_all_governed_organizations();
DROP FUNCTION IF EXISTS list_actor_pending_invitations();
DROP FUNCTION IF EXISTS list_governed_members(UUID);
DROP FUNCTION IF EXISTS get_governed_organization(UUID);
DROP FUNCTION IF EXISTS list_actor_organizations();
DROP FUNCTION IF EXISTS _assert_governance_self_scope();
DROP FUNCTION IF EXISTS _record_governance_audit(
    UUID, TEXT, TEXT, TEXT, TEXT, JSONB
);
DROP FUNCTION IF EXISTS _assert_governance_authority(UUID, TEXT[], BOOLEAN);
DROP TABLE IF EXISTS governance_audit_log;

RESET ROLE;
