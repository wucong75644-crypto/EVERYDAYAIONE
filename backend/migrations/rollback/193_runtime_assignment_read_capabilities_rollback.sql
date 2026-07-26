SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS list_runtime_department_user_ids(UUID, UUID[]);
DROP FUNCTION IF EXISTS list_runtime_org_positions(UUID);
DROP FUNCTION IF EXISTS list_runtime_org_departments(UUID);
DROP FUNCTION IF EXISTS list_runtime_member_assignments(UUID, UUID[]);
DROP FUNCTION IF EXISTS get_runtime_member_assignment(UUID, UUID);

RESET ROLE;
