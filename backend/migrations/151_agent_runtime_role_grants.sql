-- 151: Agent Runtime 首组 13 表最小角色授权。
-- 前置：管理员已完成表与资产 SECURITY DEFINER 函数的 owner 转移。
-- 本迁移不代表服务角色可切换；完整 RPC 权限清单由任务 5.3b 补齐。

SET LOCAL ROLE everydayai_owner;

ALTER POLICY tenant_user_assets
    ON user_assets
    TO everydayai_owner, everydayai_runtime, everydayai_worker;
ALTER POLICY tenant_user_asset_refs
    ON user_asset_refs
    TO everydayai_owner, everydayai_runtime, everydayai_worker;

GRANT SELECT ON TABLE
    conversation_artifacts,
    conversation_attachment_refs,
    conversation_channel_bindings,
    conversation_compactions,
    conversation_context_items,
    conversation_data_evidence,
    task_attachment_refs,
    memory_atoms
TO everydayai_runtime;

GRANT SELECT, INSERT, UPDATE ON TABLE
    conversation_attachment_refs,
    conversation_channel_bindings,
    message_generation_requests,
    memory_atoms
TO everydayai_runtime;

GRANT INSERT ON TABLE
    user_activity_events
TO everydayai_runtime;

GRANT SELECT ON TABLE
    conversation_artifacts,
    conversation_attachment_refs,
    conversation_channel_bindings,
    conversation_compactions,
    conversation_context_items,
    conversation_context_receipts,
    conversation_data_evidence,
    task_attachment_refs,
    memory_atoms
TO everydayai_worker;

GRANT INSERT ON TABLE
    conversation_artifacts,
    conversation_compactions,
    conversation_context_items,
    conversation_context_receipts,
    conversation_data_evidence,
    task_attachment_refs,
    memory_atoms
TO everydayai_worker;

GRANT UPDATE ON TABLE
    conversation_attachment_refs,
    conversation_context_receipts,
    memory_atoms
TO everydayai_worker;

GRANT EXECUTE ON FUNCTION tenant_actor_user_id()
TO everydayai_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION tenant_org_id()
TO everydayai_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION tenant_database_role_matches_scope()
TO everydayai_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION tenant_actor_is_active_member(UUID)
TO everydayai_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION tenant_user_fact_visible(UUID, UUID)
TO everydayai_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION tenant_conversation_visible(UUID, UUID)
TO everydayai_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION tenant_task_visible(UUID, UUID)
TO everydayai_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION tenant_asset_visible(UUID, TEXT, TEXT)
TO everydayai_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION tenant_asset_ref_visible(UUID)
TO everydayai_runtime, everydayai_worker;

GRANT EXECUTE ON FUNCTION register_user_asset(
    UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, BIGINT, TEXT, JSONB, TEXT, UUID, TEXT, TEXT, TEXT, UUID,
    UUID, UUID, UUID, UUID, INTEGER, TEXT, TEXT, JSONB, TIMESTAMPTZ
) TO everydayai_runtime, everydayai_worker;

RESET ROLE;
