-- 电商图模式：会话级全局风格约束
-- 用途：同一会话中生成的所有图片（主图、详情页）风格统一
-- enhance API 首次生成时写入，后续延续或调整
-- 设计文档：docs/document/TECH_电商图片Agent.md §6
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS image_style_directive TEXT;
COMMENT ON COLUMN conversations.image_style_directive IS '电商图模式全局风格约束（会话级，enhance API 创建/更新）';
