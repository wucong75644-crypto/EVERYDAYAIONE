-- 047: 对话来源标识
-- 区分 Web 端 vs 企微端创建的对话，前端据此显示来源图标

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'web';

COMMENT ON COLUMN conversations.source IS '对话来源: web / wecom';

-- 回填已有企微对话
UPDATE conversations SET source = 'wecom' WHERE title LIKE '企微%' AND source = 'web';
