SET LOCAL ROLE everydayai_owner;

REVOKE EXECUTE ON FUNCTION get_governed_actor_authority(UUID)
FROM everydayai_runtime;
DROP FUNCTION get_governed_actor_authority(UUID);

RESET ROLE;
