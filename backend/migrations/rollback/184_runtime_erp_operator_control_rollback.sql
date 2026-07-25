SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS runtime_unbind_erp_operator(UUID, UUID);
DROP FUNCTION IF EXISTS runtime_bind_erp_operator(
    UUID, UUID, TEXT, UUID
);

RESET ROLE;
