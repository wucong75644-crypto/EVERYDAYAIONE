-- 016_add_credit_transaction_id.sql
-- 添加积分事务ID字段到任务表，用于关联预扣积分
-- 创建日期: 2026-02-08

-- 添加字段到 tasks 表
ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS credit_transaction_id UUID REFERENCES credit_transactions(id);

-- 创建索引（用于查询有积分事务的任务）
CREATE INDEX IF NOT EXISTS idx_tasks_credit_tx ON tasks(credit_transaction_id)
  WHERE credit_transaction_id IS NOT NULL;

-- 添加注释
COMMENT ON COLUMN tasks.credit_transaction_id IS '关联的积分事务ID，用于完成后确认或失败后退回';
