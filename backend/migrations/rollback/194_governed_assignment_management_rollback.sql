SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS update_governed_member_assignment(UUID, UUID, JSONB);
DROP FUNCTION IF EXISTS _validate_governed_assignment_change(UUID, UUID, JSONB);
DROP FUNCTION IF EXISTS list_governed_wecom_assignments(UUID);
DROP FUNCTION IF EXISTS list_governed_member_assignments(UUID);
ALTER TABLE org_members DROP COLUMN display_name;

RESET ROLE;
