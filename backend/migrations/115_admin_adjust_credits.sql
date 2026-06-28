-- 115: 管理员积分调整 RPC
-- 用于管理员后台手动充值/扣减用户积分（admin_adjust 枚举已在 init-database.sql 定义）
-- 原子保证：UPDATE 行锁 + WHERE credits + delta >= 0 防扣到负数

CREATE OR REPLACE FUNCTION admin_adjust_credits(
    p_user_id UUID,
    p_delta INTEGER,           -- 正=充值，负=扣减
    p_reason TEXT,
    p_operator_id UUID,
    p_org_id UUID DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    v_new_balance INTEGER;
BEGIN
    IF p_delta = 0 THEN
        RETURN jsonb_build_object('success', false, 'reason', 'zero_delta');
    END IF;

    -- 行锁串行化：扣减时 WHERE 检查防止溢出到负数
    UPDATE users
    SET credits = credits + p_delta,
        updated_at = NOW()
    WHERE id = p_user_id
      AND credits + p_delta >= 0
    RETURNING credits INTO v_new_balance;

    IF NOT FOUND THEN
        -- 余额不足 或 用户不存在；用 SELECT 区分
        IF EXISTS (SELECT 1 FROM users WHERE id = p_user_id) THEN
            RETURN jsonb_build_object('success', false, 'reason', 'insufficient_balance');
        END IF;
        RETURN jsonb_build_object('success', false, 'reason', 'user_not_found');
    END IF;

    INSERT INTO credits_history (
        user_id, change_amount, balance_after, change_type,
        description, operator_id, org_id
    ) VALUES (
        p_user_id, p_delta, v_new_balance, 'admin_adjust'::credits_change_type,
        p_reason, p_operator_id, p_org_id
    );

    RETURN jsonb_build_object(
        'success', true,
        'new_balance', v_new_balance,
        'delta', p_delta
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

COMMENT ON FUNCTION admin_adjust_credits IS '管理员积分手动调整（正=充值/负=扣减），写 operator_id 审计';
