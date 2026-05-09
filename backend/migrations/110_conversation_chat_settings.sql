-- 对话级设置持久化：所有对话模式和参数设置跟对话走
-- 用 JSONB 存储，后续加新设置无需跑迁移

ALTER TABLE conversations
  ADD COLUMN IF NOT EXISTS chat_settings JSONB DEFAULT '{}';

COMMENT ON COLUMN conversations.chat_settings IS '对话级设置（深度思考/参数/图片/视频），per-conversation 持久化';
