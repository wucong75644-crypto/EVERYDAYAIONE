-- 169: 兼容 OrgScopedDB 自动注入 p_org_id 的企微生成上下文能力。

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION get_wecom_generation_context(
    p_user_id UUID,
    p_conversation_id UUID,
    p_org_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF p_org_id IS NULL
       OR public.tenant_org_id() IS DISTINCT FROM p_org_id THEN
        RAISE EXCEPTION 'WECOM_GENERATION_CONTEXT_ORG_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;
    RETURN public.get_wecom_generation_context(
        p_user_id, p_conversation_id
    );
END;
$$;

REVOKE ALL ON FUNCTION get_wecom_generation_context(UUID, UUID, UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime, everydayai_worker;
GRANT EXECUTE ON FUNCTION get_wecom_generation_context(UUID, UUID, UUID)
TO everydayai_wecom_runtime;

DO $legacy_compatibility$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'everydayai') THEN
        GRANT EXECUTE ON FUNCTION
            get_wecom_generation_context(UUID, UUID, UUID)
        TO everydayai;
    END IF;
END
$legacy_compatibility$;

RESET ROLE;
