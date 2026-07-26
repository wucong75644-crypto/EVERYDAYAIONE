SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS is_runtime_wecom_self_target(UUID, TEXT);
DROP FUNCTION IF EXISTS resolve_governed_wecom_push_target(UUID, UUID, TEXT);
DROP FUNCTION IF EXISTS update_governed_wecom_chat_target_name(UUID, UUID, TEXT);
DROP FUNCTION IF EXISTS list_governed_wecom_chat_targets(UUID);
DROP FUNCTION IF EXISTS list_runtime_wecom_chat_targets(
    UUID, BOOLEAN, BOOLEAN
);

RESET ROLE;
