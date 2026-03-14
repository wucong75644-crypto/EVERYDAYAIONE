-- 026: 企业微信用户映射表
-- 将企微 userid 映射到系统 user_id，支持智能机器人和自建应用两个渠道

CREATE TABLE IF NOT EXISTS wecom_user_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wecom_userid VARCHAR(64) NOT NULL,
    corp_id VARCHAR(64) NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel VARCHAR(20) NOT NULL DEFAULT 'smart_robot',
    wecom_nickname VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 同一企业内企微用户唯一
CREATE UNIQUE INDEX IF NOT EXISTS idx_wecom_userid_corp
    ON wecom_user_mappings (wecom_userid, corp_id);

-- 反查系统用户的企微账号
CREATE INDEX IF NOT EXISTS idx_wecom_user_id
    ON wecom_user_mappings (user_id);
