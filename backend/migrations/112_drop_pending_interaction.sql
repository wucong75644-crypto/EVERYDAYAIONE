-- 删除 ask_user 工具的 pending_interaction 表
-- ask_user 工具已移除，AI 直接在回复文本中向用户提问，不再需要冻结/恢复机制
DROP TABLE IF EXISTS pending_interaction;
