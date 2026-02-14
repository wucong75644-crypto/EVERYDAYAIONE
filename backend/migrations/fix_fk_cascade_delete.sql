-- 修复外键约束：将 ON DELETE SET NULL 改为 ON DELETE CASCADE
-- 这样删除对话时会自动删除关联的任务和消息

-- 1. 先查询现有的外键约束名称
-- 注意：需要根据实际的约束名称修改

-- 2. 删除旧的外键约束（假设约束名为 tasks_conversation_id_fkey）
ALTER TABLE tasks
DROP CONSTRAINT IF EXISTS tasks_conversation_id_fkey;

-- 3. 添加新的外键约束（ON DELETE CASCADE）
ALTER TABLE tasks
ADD CONSTRAINT tasks_conversation_id_fkey
FOREIGN KEY (conversation_id)
REFERENCES conversations(id)
ON DELETE CASCADE;

-- 4. 同样处理 messages 表（如果需要）
ALTER TABLE messages
DROP CONSTRAINT IF EXISTS messages_conversation_id_fkey;

ALTER TABLE messages
ADD CONSTRAINT messages_conversation_id_fkey
FOREIGN KEY (conversation_id)
REFERENCES conversations(id)
ON DELETE CASCADE;

-- 5. 添加注释
COMMENT ON CONSTRAINT tasks_conversation_id_fkey ON tasks IS '级联删除：删除对话时自动删除关联任务';
COMMENT ON CONSTRAINT messages_conversation_id_fkey ON messages IS '级联删除：删除对话时自动删除关联消息';
