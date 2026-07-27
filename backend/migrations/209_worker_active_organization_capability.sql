-- 209: Worker 活跃企业枚举窄能力，避免直接读取 organizations。

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION worker_list_active_organization_ids()
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_organization_ids JSONB;
BEGIN
    IF session_user <> 'everydayai_worker'
       OR current_setting(
            'app.access_kind', TRUE
          ) IS DISTINCT FROM 'worker'
       OR NULLIF(
            current_setting('app.actor_user_id', TRUE), ''
          ) IS NOT NULL
       OR NULLIF(
            current_setting('app.org_id', TRUE), ''
          ) IS NOT NULL THEN
        RAISE EXCEPTION 'WORKER_ACTIVE_ORGANIZATION_SCOPE_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    SELECT COALESCE(
        jsonb_agg(organization.id ORDER BY organization.id),
        '[]'::JSONB
    )
      INTO v_organization_ids
      FROM public.organizations organization
     WHERE organization.status = 'active';

    SELECT jsonb_build_object(
        'outcome', 'listed',
        'organization_ids', v_organization_ids
    ) INTO v_organization_ids;

    RETURN v_organization_ids;
END;
$$;

REVOKE ALL ON FUNCTION worker_list_active_organization_ids()
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
     everydayai_worker, everydayai_sync, everydayai;

GRANT EXECUTE ON FUNCTION worker_list_active_organization_ids()
TO everydayai_worker;

RESET ROLE;
