-- 回滚 150：先停止新角色服务，再移除首组 policy。
-- 仅恢复迁移 150 之前未启用 RLS 的 6 张表；既有 7 张保持 ENABLE RLS。

SET LOCAL ROLE everydayai_owner;

DROP POLICY IF EXISTS tenant_conversation_artifacts
    ON conversation_artifacts;
DROP POLICY IF EXISTS tenant_conversation_attachment_refs
    ON conversation_attachment_refs;
DROP POLICY IF EXISTS tenant_conversation_channel_bindings
    ON conversation_channel_bindings;
DROP POLICY IF EXISTS tenant_conversation_compactions
    ON conversation_compactions;
DROP POLICY IF EXISTS tenant_conversation_context_items
    ON conversation_context_items;
DROP POLICY IF EXISTS tenant_conversation_context_receipts
    ON conversation_context_receipts;
DROP POLICY IF EXISTS tenant_conversation_data_evidence
    ON conversation_data_evidence;
DROP POLICY IF EXISTS tenant_message_generation_requests
    ON message_generation_requests;
DROP POLICY IF EXISTS tenant_task_attachment_refs
    ON task_attachment_refs;
DROP POLICY IF EXISTS tenant_memory_atoms ON memory_atoms;
DROP POLICY IF EXISTS tenant_user_assets ON user_assets;
DROP POLICY IF EXISTS tenant_user_asset_refs ON user_asset_refs;
DROP POLICY IF EXISTS tenant_user_activity_events ON user_activity_events;

ALTER TABLE conversation_attachment_refs DISABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_channel_bindings DISABLE ROW LEVEL SECURITY;
ALTER TABLE message_generation_requests DISABLE ROW LEVEL SECURITY;
ALTER TABLE task_attachment_refs DISABLE ROW LEVEL SECURITY;
ALTER TABLE memory_atoms DISABLE ROW LEVEL SECURITY;
ALTER TABLE user_activity_events DISABLE ROW LEVEL SECURITY;

DROP FUNCTION IF EXISTS tenant_asset_ref_visible(UUID);
DROP FUNCTION IF EXISTS tenant_asset_visible(UUID, TEXT, TEXT);
DROP FUNCTION IF EXISTS tenant_task_visible(UUID, UUID);
DROP FUNCTION IF EXISTS tenant_conversation_visible(UUID, UUID);
DROP FUNCTION IF EXISTS tenant_user_fact_visible(UUID, UUID);
DROP FUNCTION IF EXISTS tenant_actor_is_active_member(UUID);
DROP FUNCTION IF EXISTS tenant_database_role_matches_scope();
DROP FUNCTION IF EXISTS tenant_org_id();
DROP FUNCTION IF EXISTS tenant_actor_user_id();

RESET ROLE;
