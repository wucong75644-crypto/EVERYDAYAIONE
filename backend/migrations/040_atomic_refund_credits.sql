-- 040: 原子退款函数（修复 refund RPC + 状态更新非原子导致的双倍退款风险）
--
-- 根因：旧 refund_credits(user_id, amount) 只加余额不更新事务状态，
--       应用层分两步执行（RPC加余额 → UPDATE状态），中间崩溃会重复退款。
-- 方案：新建 atomic_refund_credits(transaction_id)，在单个 SQL 事务内
--       用 CAS（WHERE status='pending'）同时完成状态检查、余额退回、状态更新。

-- 0. 补充枚举值（已有库 ALTER，新库在 init-database.sql 中内联）
ALTER TYPE credits_change_type ADD VALUE IF NOT EXISTS 'refund';

-- 1. 新建原子退款函数
CREATE OR REPLACE FUNCTION atomic_refund_credits(
    p_transaction_id UUID,
    p_final_status TEXT DEFAULT 'refunded'
) RETURNS JSONB AS $$
DECLARE
    v_user_id UUID;
    v_amount INTEGER;
    v_org_id UUID;
    v_status TEXT;
BEGIN
    -- CAS: 只有 pending 状态才能退款，同时锁行防并发
    UPDATE credit_transactions
    SET status = p_final_status, confirmed_at = NOW()
    WHERE id = p_transaction_id AND status = 'pending'
    RETURNING user_id, amount, org_id INTO v_user_id, v_amount, v_org_id;

    -- 未匹配到 pending 行：已退款或不存在
    IF v_user_id IS NULL THEN
        SELECT status INTO v_status
        FROM credit_transactions WHERE id = p_transaction_id;

        IF v_status IS NULL THEN
            RETURN jsonb_build_object('refunded', false, 'reason', 'not_found');
        ELSE
            RETURN jsonb_build_object('refunded', false, 'reason', 'status_' || v_status);
        END IF;
    END IF;

    -- 退回积分
    UPDATE users SET credits = credits + v_amount, updated_at = NOW()
    WHERE id = v_user_id;

    -- 记录积分变动历史（含 org_id 保持企业数据隔离一致性）
    INSERT INTO credits_history (user_id, change_amount, balance_after, change_type, description, org_id)
    SELECT v_user_id, v_amount,
           (SELECT credits FROM users WHERE id = v_user_id),
           'refund'::credits_change_type,
           'Refund for transaction ' || p_transaction_id,
           v_org_id;

    RETURN jsonb_build_object(
        'refunded', true,
        'user_id', v_user_id,
        'amount', v_amount
    );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION atomic_refund_credits(UUID, TEXT) IS '原子退款：CAS检查pending + 退回余额 + 更新状态，单事务内完成，防双倍退款';

-- 2. 重建 cleanup 函数使用新的原子退款
CREATE OR REPLACE FUNCTION cleanup_expired_credit_locks() RETURNS INTEGER AS $$
DECLARE
    v_count INTEGER := 0;
    v_tx RECORD;
    v_result JSONB;
BEGIN
    FOR v_tx IN
        SELECT id FROM credit_transactions
        WHERE status = 'pending' AND expires_at < NOW()
    LOOP
        v_result := atomic_refund_credits(v_tx.id, 'expired');
        IF (v_result->>'refunded')::boolean THEN
            v_count := v_count + 1;
        END IF;
    END LOOP;
    RETURN v_count;
END;
$$ LANGUAGE plpgsql;

-- 3. 删除旧的/未使用的函数
DROP FUNCTION IF EXISTS refund_credits(UUID, INTEGER);
DROP FUNCTION IF EXISTS deduct_credits(UUID, INT, TEXT, TEXT);
