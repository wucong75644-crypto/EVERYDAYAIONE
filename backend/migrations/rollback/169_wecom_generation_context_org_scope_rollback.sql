SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION get_wecom_generation_context(UUID, UUID, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
DROP FUNCTION get_wecom_generation_context(UUID, UUID, UUID);

RESET ROLE;
