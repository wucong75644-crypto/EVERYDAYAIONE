SET LOCAL ROLE everydayai_owner;

DROP POLICY IF EXISTS platform_admin_users_select ON users;
DROP POLICY IF EXISTS platform_admin_organizations_select ON organizations;
DROP POLICY IF EXISTS platform_admin_org_members_select ON org_members;
DROP POLICY IF EXISTS platform_admin_org_configs_select ON org_configs;
DROP POLICY IF EXISTS platform_admin_conversations_select ON conversations;
DROP POLICY IF EXISTS platform_admin_messages_select ON messages;
DROP POLICY IF EXISTS platform_admin_tasks_select ON tasks;
DROP POLICY IF EXISTS platform_admin_credits_history_select ON credits_history;
DROP POLICY IF EXISTS platform_admin_credit_transactions_select
ON credit_transactions;
DROP POLICY IF EXISTS platform_admin_image_generations_select
ON image_generations;
DROP POLICY IF EXISTS platform_admin_detail_projects_select ON detail_projects;
DROP POLICY IF EXISTS platform_admin_detail_project_images_select
ON detail_project_images;
DROP POLICY IF EXISTS platform_admin_user_subscriptions_select
ON user_subscriptions;
DROP POLICY IF EXISTS platform_admin_user_memory_settings_select
ON user_memory_settings;
DROP POLICY IF EXISTS platform_admin_error_logs_select ON error_logs;
DROP POLICY IF EXISTS platform_admin_error_logs_update ON error_logs;
DROP POLICY IF EXISTS platform_admin_error_logs_delete ON error_logs;
REVOKE SELECT, UPDATE, DELETE ON error_logs FROM everydayai_runtime;
ALTER TABLE error_logs DISABLE ROW LEVEL SECURITY;

REVOKE ALL ON FUNCTION tenant_platform_admin()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
DROP FUNCTION IF EXISTS tenant_platform_admin();
REVOKE ALL ON FUNCTION get_public_organization_name(UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync;
DROP FUNCTION IF EXISTS get_public_organization_name(UUID);

REVOKE ALL ON TABLE
    users, organizations, org_members, org_configs, conversations, messages,
    tasks, credits_history, credit_transactions, image_generations,
    detail_projects, detail_project_images, user_subscriptions,
    user_memory_settings
FROM everydayai_runtime;

RESET ROLE;
