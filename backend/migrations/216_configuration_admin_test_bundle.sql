-- 216: Runtime 企业管理员测试企微机器人配置的独立最小权限门面。

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION get_wecom_bot_admin_test_bundle()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor UUID := public._assert_configuration_runtime_org_admin();
BEGIN
    RETURN public._resolve_configuration_bundle(
        'v1', 'wecom.bot', v_actor, public.tenant_org_id()
    );
END;
$$;

REVOKE ALL ON FUNCTION get_wecom_bot_admin_test_bundle()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

GRANT EXECUTE ON FUNCTION get_wecom_bot_admin_test_bundle()
TO everydayai_runtime;

RESET ROLE;
