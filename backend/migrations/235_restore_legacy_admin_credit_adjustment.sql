-- 235: 恢复旧 Web 管理后台的管理员积分调整 RPC。
--
-- 232 的 Runtime 回退会移除 tenant_* 辅助函数，并回收 Runtime 角色权限。
-- 但数据库中可能已经执行过 220，导致同名 RPC 仍依赖
-- tenant_platform_admin()/tenant_actor_user_id()，旧 Web 后端因此无法充值。
-- 当前后端使用 everydayai；接口层仍负责 super_admin 校验，数据库函数负责
-- 原子余额更新、非负校验和流水审计。

CREATE OR REPLACE FUNCTION public.admin_adjust_credits(
    p_user_id UUID,
    p_delta INTEGER,
    p_reason TEXT,
    p_operator_id UUID,
    p_org_id UUID DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_new_balance INTEGER;
BEGIN
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
            RETURN jsonb_build_object('success', false, 'reason', 'insufficient_balance');
        END IF;
        RETURN jsonb_build_object('success', false, 'reason', 'user_not_found');
    END IF;

    INSERT INTO public.credits_history (
        user_id, change_amount, balance_after, change_type,
        description, operator_id, org_id
    ) VALUES (
        p_user_id, p_delta, v_new_balance, 'admin_adjust'::public.credits_change_type,
        p_reason, p_operator_id, p_org_id
    );

    RETURN jsonb_build_object(
        'success', true,
        'new_balance', v_new_balance,
        'delta', p_delta
    );
END;
$$;

ALTER FUNCTION public.admin_adjust_credits(
    UUID, INTEGER, TEXT, UUID, UUID
) OWNER TO everydayai;

REVOKE ALL ON FUNCTION public.admin_adjust_credits(
    UUID, INTEGER, TEXT, UUID, UUID
) FROM PUBLIC;

DO $$
DECLARE
    v_role TEXT;
BEGIN
    FOREACH v_role IN ARRAY ARRAY[
        'everydayai_runtime',
        'everydayai_wecom_runtime',
        'everydayai_worker',
        'everydayai_sync',
        'service_role'
    ] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN
            EXECUTE format(
                'REVOKE ALL ON FUNCTION public.admin_adjust_credits(uuid,integer,text,uuid,uuid) FROM %I',
                v_role
            );
        END IF;
    END LOOP;
END;
$$;

GRANT EXECUTE ON FUNCTION public.admin_adjust_credits(
    UUID, INTEGER, TEXT, UUID, UUID
) TO everydayai;

COMMENT ON FUNCTION public.admin_adjust_credits(
    UUID, INTEGER, TEXT, UUID, UUID
) IS '旧 Web 管理后台原子调整积分（正=充值/负=扣减），写 operator_id 审计流水';
