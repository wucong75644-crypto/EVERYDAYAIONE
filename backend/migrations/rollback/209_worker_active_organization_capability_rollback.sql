SET LOCAL ROLE everydayai_owner;

REVOKE EXECUTE ON FUNCTION worker_list_active_organization_ids()
FROM everydayai_worker;

DROP FUNCTION worker_list_active_organization_ids();

RESET ROLE;
