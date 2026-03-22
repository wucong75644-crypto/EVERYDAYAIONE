-- 036_wecom_chat_targets.sql
-- 群聊目标收集表：记录智能机器人见过的所有群聊和私聊目标
-- 用于定时任务推送时选择推送目标

CREATE TABLE IF NOT EXISTS wecom_chat_targets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chatid VARCHAR(128) NOT NULL,
    chat_type VARCHAR(20) NOT NULL DEFAULT 'group',
    chat_name VARCHAR(256),
    corp_id VARCHAR(64) NOT NULL,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    message_count INT NOT NULL DEFAULT 1,
    UNIQUE(chatid, corp_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_targets_corp_type
ON wecom_chat_targets(corp_id, chat_type);

COMMENT ON TABLE wecom_chat_targets IS '企微聊天目标收集（被动记录机器人见过的群/私聊）';
COMMENT ON COLUMN wecom_chat_targets.chat_name IS '群名或用户昵称（由用户标注，初始为空）';
COMMENT ON COLUMN wecom_chat_targets.is_active IS '是否活跃（推送失败时标记 false）';
