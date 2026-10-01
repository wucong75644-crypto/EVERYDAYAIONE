-- Restore the current trusted backend's authentication access after the
-- tenant-role cutover. The web backend authenticates users and checks org
-- membership itself; it connects as everydayai without SET ROLE/tenant GUCs.
-- Do not disable RLS, grant BYPASSRLS, or change existing tenant-role policies.
-- SESSION_USER prevents other login roles from gaining access via SET ROLE.
-- LocalDB uses RETURNING * for writes, requiring matching SELECT policies.

DROP POLICY IF EXISTS users_legacy_auth_select ON public.users;
CREATE POLICY users_legacy_auth_select ON public.users
    FOR SELECT TO everydayai USING (SESSION_USER = 'everydayai');
DROP POLICY IF EXISTS users_legacy_auth_insert ON public.users;
CREATE POLICY users_legacy_auth_insert ON public.users
    FOR INSERT TO everydayai WITH CHECK (SESSION_USER = 'everydayai');
DROP POLICY IF EXISTS users_legacy_auth_update ON public.users;
CREATE POLICY users_legacy_auth_update ON public.users
    FOR UPDATE TO everydayai USING (SESSION_USER = 'everydayai')
    WITH CHECK (SESSION_USER = 'everydayai');

DROP POLICY IF EXISTS organizations_legacy_auth_select ON public.organizations;
CREATE POLICY organizations_legacy_auth_select ON public.organizations
    FOR SELECT TO everydayai USING (SESSION_USER = 'everydayai');
DROP POLICY IF EXISTS org_members_legacy_auth_select ON public.org_members;
CREATE POLICY org_members_legacy_auth_select ON public.org_members
    FOR SELECT TO everydayai USING (SESSION_USER = 'everydayai');
DROP POLICY IF EXISTS org_members_legacy_auth_insert ON public.org_members;
CREATE POLICY org_members_legacy_auth_insert ON public.org_members
    FOR INSERT TO everydayai WITH CHECK (SESSION_USER = 'everydayai');
DROP POLICY IF EXISTS org_configs_legacy_auth_select ON public.org_configs;
CREATE POLICY org_configs_legacy_auth_select ON public.org_configs
    FOR SELECT TO everydayai USING (SESSION_USER = 'everydayai');

DROP POLICY IF EXISTS refresh_tokens_legacy_auth ON public.refresh_tokens;
CREATE POLICY refresh_tokens_legacy_auth ON public.refresh_tokens
    FOR ALL TO everydayai USING (SESSION_USER = 'everydayai')
    WITH CHECK (SESSION_USER = 'everydayai');

DROP POLICY IF EXISTS wecom_user_mappings_legacy_auth_select ON public.wecom_user_mappings;
CREATE POLICY wecom_user_mappings_legacy_auth_select ON public.wecom_user_mappings
    FOR SELECT TO everydayai USING (SESSION_USER = 'everydayai');
DROP POLICY IF EXISTS wecom_user_mappings_legacy_auth_insert ON public.wecom_user_mappings;
CREATE POLICY wecom_user_mappings_legacy_auth_insert ON public.wecom_user_mappings
    FOR INSERT TO everydayai WITH CHECK (SESSION_USER = 'everydayai');
DROP POLICY IF EXISTS wecom_user_mappings_legacy_auth_delete ON public.wecom_user_mappings;
CREATE POLICY wecom_user_mappings_legacy_auth_delete ON public.wecom_user_mappings
    FOR DELETE TO everydayai USING (SESSION_USER = 'everydayai');

DROP POLICY IF EXISTS credits_history_legacy_auth_select ON public.credits_history;
CREATE POLICY credits_history_legacy_auth_select ON public.credits_history
    FOR SELECT TO everydayai USING (SESSION_USER = 'everydayai');
DROP POLICY IF EXISTS credits_history_legacy_auth_insert ON public.credits_history;
CREATE POLICY credits_history_legacy_auth_insert ON public.credits_history
    FOR INSERT TO everydayai WITH CHECK (SESSION_USER = 'everydayai');

-- record_user_activity and wecom_get_or_create_user already have the required
-- legacy-role EXECUTE grants and SECURITY DEFINER implementation in production.
-- No function, role membership, table grant, owner, data or RLS flag changes.
