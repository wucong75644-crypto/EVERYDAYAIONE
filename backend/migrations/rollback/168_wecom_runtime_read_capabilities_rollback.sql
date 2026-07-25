SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON FUNCTION
    get_wecom_generation_context(UUID, UUID),
    update_wecom_ingress_display_name(UUID, TEXT, TEXT, UUID, TEXT),
    reset_wecom_conversation(UUID, UUID, UUID),
    get_wecom_manual_memories(UUID, UUID),
    clear_wecom_manual_memories(UUID, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;

DROP FUNCTION clear_wecom_manual_memories(UUID, UUID);
DROP FUNCTION get_wecom_manual_memories(UUID, UUID);
DROP FUNCTION reset_wecom_conversation(UUID, UUID, UUID);
DROP FUNCTION update_wecom_ingress_display_name(UUID, TEXT, TEXT, UUID, TEXT);
DROP FUNCTION get_wecom_generation_context(UUID, UUID);

RESET ROLE;
