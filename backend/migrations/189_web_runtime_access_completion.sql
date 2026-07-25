-- 189: Restore the Web runtime ACL after owner transfer and close platform reads.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION tenant_platform_admin()
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT session_user = 'everydayai_runtime'
       AND current_setting('app.access_kind', TRUE) = 'runtime'
       AND public.tenant_actor_user_id() IS NOT NULL
       AND EXISTS (
           SELECT 1
             FROM public.users app_user
            WHERE app_user.id = public.tenant_actor_user_id()
              AND app_user.role::TEXT = 'super_admin'
              AND app_user.status::TEXT = 'active'
       )
$$;

REVOKE ALL ON FUNCTION tenant_platform_admin()
FROM PUBLIC, everydayai_wecom_runtime, everydayai_worker, everydayai_sync;
GRANT EXECUTE ON FUNCTION tenant_platform_admin()
TO everydayai_runtime;

CREATE OR REPLACE FUNCTION get_public_organization_name(p_org_id UUID)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT CASE
        WHEN session_user <> 'everydayai_runtime'
          OR current_setting('app.access_kind', TRUE) <> 'runtime'
          OR p_org_id IS NULL
        THEN NULL
        ELSE (
            SELECT jsonb_build_object(
                'name', organization.name,
                'status', organization.status
            )
              FROM public.organizations organization
             WHERE organization.id = p_org_id
        )
    END
$$;

REVOKE ALL ON FUNCTION get_public_organization_name(UUID)
FROM PUBLIC, everydayai_wecom_runtime, everydayai_worker, everydayai_sync;
GRANT EXECUTE ON FUNCTION get_public_organization_name(UUID)
TO everydayai_runtime;

CREATE POLICY platform_admin_users_select ON users
FOR SELECT TO everydayai_runtime USING (tenant_platform_admin());
CREATE POLICY platform_admin_organizations_select ON organizations
FOR SELECT TO everydayai_runtime USING (tenant_platform_admin());
CREATE POLICY platform_admin_org_members_select ON org_members
FOR SELECT TO everydayai_runtime USING (tenant_platform_admin());
CREATE POLICY platform_admin_org_configs_select ON org_configs
FOR SELECT TO everydayai_runtime USING (tenant_platform_admin());
CREATE POLICY platform_admin_conversations_select ON conversations
FOR SELECT TO everydayai_runtime USING (tenant_platform_admin());
CREATE POLICY platform_admin_messages_select ON messages
FOR SELECT TO everydayai_runtime USING (tenant_platform_admin());
CREATE POLICY platform_admin_tasks_select ON tasks
FOR SELECT TO everydayai_runtime USING (tenant_platform_admin());
CREATE POLICY platform_admin_credits_history_select ON credits_history
FOR SELECT TO everydayai_runtime USING (tenant_platform_admin());
CREATE POLICY platform_admin_credit_transactions_select ON credit_transactions
FOR SELECT TO everydayai_runtime USING (tenant_platform_admin());
CREATE POLICY platform_admin_image_generations_select ON image_generations
FOR SELECT TO everydayai_runtime USING (tenant_platform_admin());
CREATE POLICY platform_admin_detail_projects_select ON detail_projects
FOR SELECT TO everydayai_runtime USING (tenant_platform_admin());
CREATE POLICY platform_admin_detail_project_images_select ON detail_project_images
FOR SELECT TO everydayai_runtime USING (tenant_platform_admin());
CREATE POLICY platform_admin_user_subscriptions_select ON user_subscriptions
FOR SELECT TO everydayai_runtime USING (tenant_platform_admin());
CREATE POLICY platform_admin_user_memory_settings_select ON user_memory_settings
FOR SELECT TO everydayai_runtime USING (tenant_platform_admin());
ALTER TABLE error_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY platform_admin_error_logs_select ON error_logs
FOR SELECT TO everydayai_runtime USING (tenant_platform_admin());
CREATE POLICY platform_admin_error_logs_update ON error_logs
FOR UPDATE TO everydayai_runtime
USING (tenant_platform_admin()) WITH CHECK (tenant_platform_admin());
CREATE POLICY platform_admin_error_logs_delete ON error_logs
FOR DELETE TO everydayai_runtime USING (tenant_platform_admin());

GRANT SELECT, UPDATE ON users TO everydayai_runtime;
GRANT SELECT ON organizations, org_members, org_configs, credits_history
TO everydayai_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON
    conversations, messages, tasks, detail_projects, detail_project_images,
    user_subscriptions, user_memory_settings
TO everydayai_runtime;
GRANT SELECT, INSERT, UPDATE ON credit_transactions, image_generations
TO everydayai_runtime;
GRANT SELECT, UPDATE, DELETE ON error_logs TO everydayai_runtime;

RESET ROLE;
