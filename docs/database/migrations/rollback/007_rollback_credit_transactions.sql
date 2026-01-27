-- ========================================
-- Rollback: 007_add_credit_transactions
-- Description: 删除 credit_transactions 表及相关函数
-- 警告：此操作将删除所有积分事务数据
-- ========================================

-- 1. 删除函数
DROP FUNCTION IF EXISTS cleanup_expired_credit_locks();
DROP FUNCTION IF EXISTS refund_credits(UUID, INTEGER);
DROP FUNCTION IF EXISTS deduct_credits_atomic(UUID, INTEGER, TEXT, TEXT);

-- 2. 删除策略
DROP POLICY IF EXISTS "Users can view own transactions" ON credit_transactions;
DROP POLICY IF EXISTS "Service role can manage all transactions" ON credit_transactions;

-- 3. 删除索引
DROP INDEX IF EXISTS idx_credit_tx_task_unique;
DROP INDEX IF EXISTS idx_credit_tx_user;
DROP INDEX IF EXISTS idx_credit_tx_status;

-- 4. 删除表
DROP TABLE IF EXISTS credit_transactions;
