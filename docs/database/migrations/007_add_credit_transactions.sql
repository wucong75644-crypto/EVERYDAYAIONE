-- 007_add_credit_transactions.sql
-- 创建积分事务表（用于锁定-确认-退回流程）
-- 创建日期：2026-01-26

-- 创建积分事务表
CREATE TABLE IF NOT EXISTS credit_transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id UUID NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL CHECK (amount > 0),
    type VARCHAR(20) NOT NULL CHECK (type IN ('lock', 'deduct', 'refund')),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'confirmed', 'refunded', 'expired')),
    reason VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confirmed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '10 minutes')
);

-- 创建唯一索引（每个任务只能有一个事务）
CREATE UNIQUE INDEX IF NOT EXISTS idx_credit_tx_task_unique ON credit_transactions(task_id);

-- 创建普通索引
CREATE INDEX IF NOT EXISTS idx_credit_tx_user ON credit_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_credit_tx_status ON credit_transactions(status, expires_at);

-- 启用行级安全
ALTER TABLE credit_transactions ENABLE ROW LEVEL SECURITY;

-- 用户只能查看自己的事务
DROP POLICY IF EXISTS "Users can view own transactions" ON credit_transactions;
CREATE POLICY "Users can view own transactions" ON credit_transactions FOR SELECT
    USING (auth.uid() = user_id);

-- 服务角色可以管理所有事务
DROP POLICY IF EXISTS "Service role can manage all transactions" ON credit_transactions;
CREATE POLICY "Service role can manage all transactions" ON credit_transactions FOR ALL
    USING (auth.role() = 'service_role');

-- 添加注释
COMMENT ON TABLE credit_transactions IS '积分事务表，用于锁定-确认-退回流程';
COMMENT ON COLUMN credit_transactions.type IS '事务类型：lock(锁定)/deduct(扣除)/refund(退回)';
COMMENT ON COLUMN credit_transactions.status IS '事务状态：pending/confirmed/refunded/expired';
COMMENT ON COLUMN credit_transactions.expires_at IS '锁定过期时间，超时自动退回';

-- ============================================
-- 创建积分操作的 RPC 函数
-- ============================================

-- 原子扣除函数
CREATE OR REPLACE FUNCTION deduct_credits_atomic(
    p_user_id UUID,
    p_amount INTEGER,
    p_reason TEXT,
    p_change_type TEXT
) RETURNS JSONB AS $$
DECLARE
    v_new_balance INTEGER;
BEGIN
    -- 原子扣除
    UPDATE users
    SET credits = credits - p_amount,
        updated_at = NOW()
    WHERE id = p_user_id
      AND credits >= p_amount
    RETURNING credits INTO v_new_balance;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'message', 'Insufficient credits');
    END IF;

    -- 记录历史
    INSERT INTO credits_history (user_id, change_type, change_amount, balance_after, description)
    VALUES (p_user_id, p_change_type::credits_change_type, -p_amount, v_new_balance, p_reason);

    RETURN jsonb_build_object('success', true, 'new_balance', v_new_balance);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 退回函数
CREATE OR REPLACE FUNCTION refund_credits(
    p_user_id UUID,
    p_amount INTEGER
) RETURNS VOID AS $$
BEGIN
    UPDATE users
    SET credits = credits + p_amount,
        updated_at = NOW()
    WHERE id = p_user_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 清理过期锁定（定时任务调用）
CREATE OR REPLACE FUNCTION cleanup_expired_credit_locks() RETURNS INTEGER AS $$
DECLARE
    v_count INTEGER := 0;
    v_tx RECORD;
BEGIN
    FOR v_tx IN
        SELECT id, user_id, amount
        FROM credit_transactions
        WHERE status = 'pending' AND expires_at < NOW()
    LOOP
        -- 退回积分
        PERFORM refund_credits(v_tx.user_id, v_tx.amount);

        -- 更新状态
        UPDATE credit_transactions
        SET status = 'expired', confirmed_at = NOW()
        WHERE id = v_tx.id;

        v_count := v_count + 1;
    END LOOP;

    RETURN v_count;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 添加函数注释
COMMENT ON FUNCTION deduct_credits_atomic IS '原子扣除积分，余额不足时返回失败';
COMMENT ON FUNCTION refund_credits IS '退回积分';
COMMENT ON FUNCTION cleanup_expired_credit_locks IS '清理过期的积分锁定';
