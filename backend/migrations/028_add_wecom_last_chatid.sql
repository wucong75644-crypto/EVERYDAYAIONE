-- 028: 企微用户映射表新增 last_chatid / last_chat_type 字段
-- 用于主动推送消息时查找目标会话

ALTER TABLE wecom_user_mappings
    ADD COLUMN IF NOT EXISTS last_chatid VARCHAR(128),
    ADD COLUMN IF NOT EXISTS last_chat_type VARCHAR(20) DEFAULT 'single';

COMMENT ON COLUMN wecom_user_mappings.last_chatid IS '用户最近活跃的 chatid（主动推送寻址用）';
COMMENT ON COLUMN wecom_user_mappings.last_chat_type IS '最近会话类型：single / group';
