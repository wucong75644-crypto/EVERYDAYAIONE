-- Restore the pre-266 access state; this makes legacy authentication unavailable
-- again. Keep all existing tenant policies, owners, grants, data and RLS flags.
DROP POLICY IF EXISTS users_legacy_auth_select ON public.users;
DROP POLICY IF EXISTS users_legacy_auth_insert ON public.users;
DROP POLICY IF EXISTS users_legacy_auth_update ON public.users;
DROP POLICY IF EXISTS organizations_legacy_auth_select ON public.organizations;
DROP POLICY IF EXISTS org_members_legacy_auth_select ON public.org_members;
DROP POLICY IF EXISTS org_members_legacy_auth_insert ON public.org_members;
DROP POLICY IF EXISTS org_configs_legacy_auth_select ON public.org_configs;
DROP POLICY IF EXISTS refresh_tokens_legacy_auth ON public.refresh_tokens;
DROP POLICY IF EXISTS wecom_user_mappings_legacy_auth_select ON public.wecom_user_mappings;
DROP POLICY IF EXISTS wecom_user_mappings_legacy_auth_insert ON public.wecom_user_mappings;
DROP POLICY IF EXISTS wecom_user_mappings_legacy_auth_delete ON public.wecom_user_mappings;
DROP POLICY IF EXISTS credits_history_legacy_auth_select ON public.credits_history;
DROP POLICY IF EXISTS credits_history_legacy_auth_insert ON public.credits_history;
