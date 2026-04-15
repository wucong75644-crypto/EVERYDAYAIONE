-- 079: 按量计费差额退回函数
-- 定时任务锁定 max_credits 后，按实际消耗确认，差额退回用户余额

-- 1. 补充枚举值
ALTER TYPE credits_change_type ADD VALUE IF NOT EXISTS 'partial_refund';

-- 2. 原子退回函数（含 org_id，与 deduct_credits_atomic / atomic_refund_credits 对齐）
CREATE OR REPLACE FUNCTION partial_refund_credits(
    p_user_id UUID,
    p_refund_amount INTEGER,
    p_description TEXT DEFAULT '',
    p_org_id UUID DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    v_new_balance INTEGER;
BEGIN
    IF p_refund_amount <= 0 THEN
        RETURN jsonb_build_object('refunded', false, 'reason', 'zero_or_negative');
    END IF;

    UPDATE users
    SET credits = credits + p_refund_amount,
        updated_at = NOW()
    WHERE id = p_user_id
    RETURNING credits INTO v_new_balance;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('refunded', false, 'reason', 'user_not_found');
    END IF;

    INSERT INTO credits_history (user_id, change_type, change_amount, balance_after, description, org_id)
    VALUES (p_user_id, 'partial_refund'::credits_change_type, p_refund_amount, v_new_balance, p_description, p_org_id);

    RETURN jsonb_build_object('refunded', true, 'new_balance', v_new_balance, 'amount', p_refund_amount);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

COMMENT ON FUNCTION partial_refund_credits IS '按量计费差额退回（定时任务等场景，含多租户 org_id）';
