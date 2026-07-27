-- Restore migration 115 behavior. Only transfer ownership back when the
-- application has also returned to the legacy database role.

SET LOCAL ROLE everydayai_owner;

CREATE OR REPLACE FUNCTION admin_adjust_credits(
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

    UPDATE users
       SET credits = credits + p_delta,
           updated_at = NOW()
     WHERE id = p_user_id
       AND credits + p_delta >= 0
    RETURNING credits INTO v_new_balance;

    IF NOT FOUND THEN
        IF EXISTS (SELECT 1 FROM users WHERE id = p_user_id) THEN
            RETURN jsonb_build_object(
                'success', false, 'reason', 'insufficient_balance'
            );
        END IF;
        RETURN jsonb_build_object(
            'success', false, 'reason', 'user_not_found'
        );
    END IF;

    INSERT INTO credits_history (
        user_id, change_amount, balance_after, change_type,
        description, operator_id, org_id
    ) VALUES (
        p_user_id, p_delta, v_new_balance,
        'admin_adjust'::credits_change_type,
        p_reason, p_operator_id, p_org_id
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
GRANT EXECUTE ON FUNCTION admin_adjust_credits(
    UUID, INTEGER, TEXT, UUID, UUID
) TO everydayai_runtime;

COMMENT ON FUNCTION admin_adjust_credits(
    UUID, INTEGER, TEXT, UUID, UUID
) IS '管理员积分手动调整（正=充值/负=扣减），写 operator_id 审计';

RESET ROLE;
