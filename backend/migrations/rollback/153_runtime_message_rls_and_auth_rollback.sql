-- 回滚 153：服务须先切回旧数据库角色；不删除任何业务事实。

SET LOCAL ROLE everydayai_owner;

REVOKE ALL ON TABLE
    users, organizations, org_members, org_configs, wecom_user_mappings,
    wecom_chat_targets, conversations, messages, tasks, credits_history,
    credit_transactions, image_generations, detail_projects,
    detail_project_images, refresh_tokens, user_subscriptions,
    user_memory_settings
FROM everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;

REVOKE EXECUTE ON FUNCTION lookup_web_auth_candidate(TEXT, TEXT),
    register_web_identity(UUID, TEXT, TEXT, TEXT, TEXT, TIMESTAMPTZ),
    commit_web_login(UUID, UUID, TEXT, TIMESTAMPTZ),
    rotate_web_refresh_token(TEXT, TEXT, TIMESTAMPTZ),
    reset_web_password(TEXT, TEXT),
    revoke_web_refresh_token(TEXT)
FROM everydayai_runtime;
REVOKE EXECUTE ON FUNCTION resolve_wecom_ingress_user(TEXT, TEXT, UUID, TEXT, TEXT),
    update_wecom_ingress_chat_address(TEXT, TEXT, TEXT, TEXT, UUID),
    upsert_wecom_ingress_chat_target(TEXT, TEXT, TEXT, UUID),
    tenant_actor_user_id(), tenant_org_id(),
    tenant_database_role_matches_scope(), tenant_actor_is_active_member(UUID),
    tenant_user_fact_visible(UUID, UUID), tenant_conversation_visible(UUID, UUID),
    tenant_task_visible(UUID, UUID)
FROM everydayai_wecom_runtime;

DROP POLICY IF EXISTS tenant_users ON users;
DROP POLICY IF EXISTS tenant_organizations ON organizations;
DROP POLICY IF EXISTS tenant_org_members ON org_members;
DROP POLICY IF EXISTS tenant_org_configs ON org_configs;
DROP POLICY IF EXISTS tenant_conversations_runtime ON conversations;
DROP POLICY IF EXISTS tenant_messages_runtime ON messages;
DROP POLICY IF EXISTS tenant_tasks_runtime ON tasks;
DROP POLICY IF EXISTS tenant_credits_history ON credits_history;
DROP POLICY IF EXISTS tenant_credit_transactions ON credit_transactions;
DROP POLICY IF EXISTS tenant_image_generations ON image_generations;
DROP POLICY IF EXISTS tenant_detail_projects ON detail_projects;
DROP POLICY IF EXISTS tenant_detail_project_images ON detail_project_images;
DROP POLICY IF EXISTS tenant_user_subscriptions ON user_subscriptions;
DROP POLICY IF EXISTS tenant_user_memory_settings ON user_memory_settings;
DROP POLICY IF EXISTS tenant_wecom_user_mappings ON wecom_user_mappings;
DROP POLICY IF EXISTS tenant_wecom_chat_targets ON wecom_chat_targets;
DROP POLICY IF EXISTS tenant_refresh_tokens ON refresh_tokens;

ALTER TABLE users DISABLE ROW LEVEL SECURITY;
ALTER TABLE organizations DISABLE ROW LEVEL SECURITY;
ALTER TABLE org_members DISABLE ROW LEVEL SECURITY;
ALTER TABLE org_configs DISABLE ROW LEVEL SECURITY;
ALTER TABLE wecom_user_mappings DISABLE ROW LEVEL SECURITY;
ALTER TABLE wecom_chat_targets DISABLE ROW LEVEL SECURITY;
ALTER TABLE conversations DISABLE ROW LEVEL SECURITY;
ALTER TABLE messages DISABLE ROW LEVEL SECURITY;
ALTER TABLE tasks DISABLE ROW LEVEL SECURITY;
ALTER TABLE credits_history DISABLE ROW LEVEL SECURITY;
ALTER TABLE credit_transactions DISABLE ROW LEVEL SECURITY;
ALTER TABLE image_generations DISABLE ROW LEVEL SECURITY;
ALTER TABLE detail_projects DISABLE ROW LEVEL SECURITY;
ALTER TABLE detail_project_images DISABLE ROW LEVEL SECURITY;
ALTER TABLE refresh_tokens DISABLE ROW LEVEL SECURITY;
ALTER TABLE user_subscriptions DISABLE ROW LEVEL SECURITY;
ALTER TABLE user_memory_settings DISABLE ROW LEVEL SECURITY;

DROP FUNCTION IF EXISTS revoke_web_refresh_token(TEXT);
DROP FUNCTION IF EXISTS reset_web_password(TEXT, TEXT);
DROP FUNCTION IF EXISTS rotate_web_refresh_token(TEXT, TEXT, TIMESTAMPTZ);
DROP FUNCTION IF EXISTS commit_web_login(UUID, UUID, TEXT, TIMESTAMPTZ);
DROP FUNCTION IF EXISTS register_web_identity(
    UUID, TEXT, TEXT, TEXT, TEXT, TIMESTAMPTZ
);
DROP FUNCTION IF EXISTS lookup_web_auth_candidate(TEXT, TEXT);
DROP FUNCTION IF EXISTS _assert_web_auth_scope();

RESET ROLE;
