-- 011_rollback_atomic_credit_deduction.sql
-- 回滚原子性积分扣除函数
-- 创建日期: 2026-01-30

-- 删除原子性积分扣除函数
DROP FUNCTION IF EXISTS deduct_credits(UUID, INT, TEXT, TEXT);

-- 删除函数注释（自动随函数删除）
