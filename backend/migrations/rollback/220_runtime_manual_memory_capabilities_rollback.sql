SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION
    runtime_create_manual_memory(UUID, UUID, TEXT, TEXT, TEXT, INTEGER),
    runtime_update_manual_memory(UUID, UUID, UUID, TEXT, TEXT, TEXT),
    runtime_delete_memory_atom(UUID, UUID, UUID),
    runtime_clear_memory_atoms(UUID, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

DROP FUNCTION IF EXISTS runtime_create_manual_memory(
    UUID, UUID, TEXT, TEXT, TEXT, INTEGER
);
DROP FUNCTION IF EXISTS runtime_update_manual_memory(
    UUID, UUID, UUID, TEXT, TEXT, TEXT
);
DROP FUNCTION IF EXISTS runtime_delete_memory_atom(UUID, UUID, UUID);
DROP FUNCTION IF EXISTS runtime_clear_memory_atoms(UUID, UUID);
DROP FUNCTION IF EXISTS _assert_runtime_manual_memory_scope(UUID, UUID);

RESET ROLE;
