-- 流式 content_blocks 增量持久化
-- 用途：刷新/重连时恢复 thinking + tool_step 等结构化内容块
-- 与 accumulated_content(TEXT) 互补：text 走已有机制，blocks 走此字段
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS accumulated_blocks JSONB DEFAULT '[]';
