-- 191: Expose the current actor's organization authority without table ACL.

SET LOCAL ROLE everydayai_owner;

CREATE FUNCTION get_governed_actor_authority(p_org_id UUID)
RETURNS TEXT
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_authority TEXT;
BEGIN
    v_authority := public._assert_governance_authority(
        p_org_id, ARRAY['owner', 'admin', 'member'], FALSE
    );
    RETURN v_authority;
END;
$$;

REVOKE ALL ON FUNCTION get_governed_actor_authority(UUID)
FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai;
GRANT EXECUTE ON FUNCTION get_governed_actor_authority(UUID)
TO everydayai_runtime;

RESET ROLE;
