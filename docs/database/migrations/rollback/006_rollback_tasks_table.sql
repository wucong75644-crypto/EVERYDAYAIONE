-- ========================================
-- Rollback: 006_add_tasks_table
-- Description: 删除 tasks 表及相关对象
-- 警告：此操作将删除所有任务数据
-- ========================================

-- 1. 删除策略
DROP POLICY IF EXISTS "Users can view own tasks" ON tasks;
DROP POLICY IF EXISTS "Service role can manage all tasks" ON tasks;

-- 2. 删除索引
DROP INDEX IF EXISTS idx_tasks_user_status;
DROP INDEX IF EXISTS idx_tasks_conversation;
DROP INDEX IF EXISTS idx_tasks_created;

-- 3. 删除表
DROP TABLE IF EXISTS tasks;
