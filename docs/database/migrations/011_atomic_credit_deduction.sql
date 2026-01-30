-- 011_atomic_credit_deduction.sql
-- 原子性积分扣除函数，防止竞态条件
-- 创建日期: 2026-01-30

-- 创建原子性积分扣除函数
CREATE OR REPLACE FUNCTION deduct_credits(
  p_user_id UUID,
  p_amount INT,
  p_description TEXT,
  p_change_type TEXT
) RETURNS INT AS $$
DECLARE
  v_current_credits INT;
  v_new_balance INT;
BEGIN
  -- 使用 FOR UPDATE 锁定用户行，防止并发问题
  SELECT credits INTO v_current_credits
  FROM users
  WHERE id = p_user_id
  FOR UPDATE;

  -- 检查用户是否存在
  IF v_current_credits IS NULL THEN
    RAISE EXCEPTION 'User not found: %', p_user_id;
  END IF;

  -- 检查积分是否足够
  IF v_current_credits < p_amount THEN
    RAISE EXCEPTION 'Insufficient credits: current=%, required=%', v_current_credits, p_amount;
  END IF;

  -- 计算新余额
  v_new_balance := v_current_credits - p_amount;

  -- 原子性更新积分
  UPDATE users
  SET credits = v_new_balance
  WHERE id = p_user_id;

  -- 记录积分变动历史（需要将 TEXT 转换为枚举类型）
  INSERT INTO credits_history (
    user_id,
    change_amount,
    balance_after,
    change_type,
    description
  ) VALUES (
    p_user_id,
    -p_amount,
    v_new_balance,
    p_change_type::credits_change_type,
    p_description
  );

  -- 返回新余额
  RETURN v_new_balance;
END;
$$ LANGUAGE plpgsql;

-- 添加函数注释
COMMENT ON FUNCTION deduct_credits IS '原子性扣除用户积分，使用行锁防止竞态条件';
