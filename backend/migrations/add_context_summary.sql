-- 对话历史摘要压缩：conversations 表新增摘要字段
-- 用于存储千问压缩的早期对话摘要，实现低成本"长记忆"

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS context_summary TEXT;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS summary_message_count INTEGER DEFAULT 0;

COMMENT ON COLUMN conversations.context_summary IS '千问压缩的对话历史摘要（≤500字）';
COMMENT ON COLUMN conversations.summary_message_count IS '生成摘要时的消息总数（用于判断是否需要更新）';
