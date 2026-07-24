-- 回滚 152：先将 WeCom 服务切回旧数据库角色，再撤销独立能力门面。

SET LOCAL ROLE everydayai_owner;

DROP FUNCTION IF EXISTS upsert_wecom_ingress_chat_target(
    TEXT, TEXT, TEXT, UUID
);
DROP FUNCTION IF EXISTS update_wecom_ingress_chat_address(
    TEXT, TEXT, TEXT, TEXT, UUID
);
DROP FUNCTION IF EXISTS resolve_wecom_ingress_user(
    TEXT, TEXT, UUID, TEXT, TEXT
);
DROP FUNCTION IF EXISTS _assert_wecom_ingress_scope(UUID, TEXT);

CREATE OR REPLACE FUNCTION tenant_database_role_matches_scope()
RETURNS BOOLEAN
LANGUAGE sql
STABLE
PARALLEL SAFE
SET search_path = pg_catalog
AS $$
    SELECT CASE session_user
        WHEN 'everydayai_runtime' THEN
            current_setting('app.access_kind', TRUE) = 'runtime'
        WHEN 'everydayai_worker' THEN
            current_setting('app.access_kind', TRUE) = 'worker'
        ELSE FALSE
    END
$$;

-- 不恢复旧函数的 PUBLIC EXECUTE；旧服务角色作为函数 owner 不受影响。
REVOKE ALL ON FUNCTION wecom_get_or_create_user(
    TEXT, TEXT, UUID, TEXT, TEXT
) FROM PUBLIC;
DO $legacy_compatibility$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'everydayai'
    ) THEN
        GRANT EXECUTE ON FUNCTION wecom_get_or_create_user(
            TEXT, TEXT, UUID, TEXT, TEXT
        ) TO everydayai;
    END IF;
END
$legacy_compatibility$;

RESET ROLE;
