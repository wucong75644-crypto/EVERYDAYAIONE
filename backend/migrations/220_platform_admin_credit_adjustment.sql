-- 220: Restore and harden the platform-admin credit adjustment capability.
-- Prerequisite: deploy/transfer-admin-credit-adjustment-ownership.sh.

SET LOCAL ROLE everydayai_owner;

DO $verify$
BEGIN
    IF to_regrole('everydayai_runtime') IS NULL
       OR to_regprocedure(
           'public.admin_adjust_credits(uuid,integer,text,uuid,uuid)'
       ) IS NULL THEN
        RAISE EXCEPTION 'ADMIN_CREDIT_ADJUSTMENT_PREREQUISITE_MISSING';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc procedure
         WHERE procedure.oid =
               'public.admin_adjust_credits(uuid,integer,text,uuid,uuid)'::REGPROCEDURE
           AND pg_get_userbyid(procedure.proowner) = 'everydayai_owner'
    ) THEN
        RAISE EXCEPTION 'ADMIN_CREDIT_ADJUSTMENT_OWNER_INVALID';
    END IF;
END
$verify$;

CREATE OR REPLACE FUNCTION admin_adjust_credits(
    p_user_id UUID,
    p_delta INTEGER,
    p_reason TEXT,
    p_operator_id UUID,
    p_org_id UUID DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_actor_user_id UUID;
    v_new_balance INTEGER;
BEGIN
    IF session_user <> 'everydayai_runtime'
       OR current_setting('app.access_kind', TRUE) <> 'runtime'
       OR NOT public.tenant_platform_admin() THEN
        RAISE EXCEPTION 'PLATFORM_ADMIN_REQUIRED'
            USING ERRCODE = '42501';
    END IF;

    v_actor_user_id := public.tenant_actor_user_id();
    IF v_actor_user_id IS NULL
       OR p_operator_id IS DISTINCT FROM v_actor_user_id THEN
        RAISE EXCEPTION 'ADMIN_CREDIT_OPERATOR_MISMATCH'
            USING ERRCODE = '42501';
    END IF;

    IF p_delta = 0 THEN
        RETURN jsonb_build_object('success', false, 'reason', 'zero_delta');
    END IF;

    UPDATE public.users
       SET credits = credits + p_delta,
           updated_at = NOW()
     WHERE id = p_user_id
       AND credits + p_delta >= 0
    RETURNING credits INTO v_new_balance;

    IF NOT FOUND THEN
        IF EXISTS (SELECT 1 FROM public.users WHERE id = p_user_id) THEN
            RETURN jsonb_build_object(
                'success', false, 'reason', 'insufficient_balance'
            );
        END IF;
        RETURN jsonb_build_object(
            'success', false, 'reason', 'user_not_found'
        );
    END IF;

    INSERT INTO public.credits_history (
        user_id, change_amount, balance_after, change_type,
        description, operator_id, org_id
    ) VALUES (
        p_user_id, p_delta, v_new_balance,
        'admin_adjust'::public.credits_change_type,
        p_reason, v_actor_user_id, p_org_id
    );

    RETURN jsonb_build_object(
        'success', true,
        'new_balance', v_new_balance,
        'delta', p_delta
    );
END;
$$;

REVOKE ALL ON FUNCTION admin_adjust_credits(
    UUID, INTEGER, TEXT, UUID, UUID
) FROM PUBLIC, everydayai_runtime, everydayai_wecom_runtime,
    everydayai_worker, everydayai_sync, everydayai;
DO $$
BEGIN
    IF to_regrole('service_role') IS NOT NULL THEN
        REVOKE ALL ON FUNCTION admin_adjust_credits(
            UUID, INTEGER, TEXT, UUID, UUID
        ) FROM service_role;
    END IF;
END;
$$;
GRANT EXECUTE ON FUNCTION admin_adjust_credits(
    UUID, INTEGER, TEXT, UUID, UUID
) TO everydayai_runtime;

COMMENT ON FUNCTION admin_adjust_credits(
    UUID, INTEGER, TEXT, UUID, UUID
) IS '平台超级管理员原子调整积分；数据库校验会话 Actor 并写审计流水';

RESET ROLE;
