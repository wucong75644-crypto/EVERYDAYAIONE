-- 回滚 151：撤销首组角色授权，并恢复资产 policy 的非 owner 角色集合。

SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON TABLE
    conversation_artifacts,
    conversation_attachment_refs,
    conversation_channel_bindings,
    conversation_compactions,
    conversation_context_items,
    conversation_context_receipts,
    conversation_data_evidence,
    message_generation_requests,
    task_attachment_refs,
    memory_atoms,
    user_assets,
    user_asset_refs,
    user_activity_events
FROM everydayai_runtime, everydayai_worker;

REVOKE EXECUTE ON FUNCTION tenant_actor_user_id()
FROM everydayai_runtime, everydayai_worker;
REVOKE EXECUTE ON FUNCTION tenant_org_id()
FROM everydayai_runtime, everydayai_worker;
REVOKE EXECUTE ON FUNCTION tenant_database_role_matches_scope()
FROM everydayai_runtime, everydayai_worker;
REVOKE EXECUTE ON FUNCTION tenant_actor_is_active_member(UUID)
FROM everydayai_runtime, everydayai_worker;
REVOKE EXECUTE ON FUNCTION tenant_user_fact_visible(UUID, UUID)
FROM everydayai_runtime, everydayai_worker;
REVOKE EXECUTE ON FUNCTION tenant_conversation_visible(UUID, UUID)
FROM everydayai_runtime, everydayai_worker;
REVOKE EXECUTE ON FUNCTION tenant_task_visible(UUID, UUID)
FROM everydayai_runtime, everydayai_worker;
REVOKE EXECUTE ON FUNCTION tenant_asset_visible(UUID, TEXT, TEXT)
FROM everydayai_runtime, everydayai_worker;
REVOKE EXECUTE ON FUNCTION tenant_asset_ref_visible(UUID)
FROM everydayai_runtime, everydayai_worker;
REVOKE EXECUTE ON FUNCTION register_user_asset(
    UUID, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT,
    TEXT, BIGINT, TEXT, JSONB, TEXT, UUID, TEXT, TEXT, TEXT, UUID,
    UUID, UUID, UUID, UUID, INTEGER, TEXT, TEXT, JSONB, TIMESTAMPTZ
) FROM everydayai_runtime, everydayai_worker;

ALTER POLICY tenant_user_assets
    ON user_assets
    TO everydayai_runtime, everydayai_worker;
ALTER POLICY tenant_user_asset_refs
    ON user_asset_refs
    TO everydayai_runtime, everydayai_worker;

RESET ROLE;
