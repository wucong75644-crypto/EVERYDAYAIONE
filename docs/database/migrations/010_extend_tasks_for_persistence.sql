-- 010_extend_tasks_for_persistence.sql
-- 扩展任务表以支持任务持久化和恢复
-- 创建日期: 2026-01-30

-- 扩展字段
ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS external_task_id VARCHAR(100),
  ADD COLUMN IF NOT EXISTS request_params JSONB,
  ADD COLUMN IF NOT EXISTS result JSONB,
  ADD COLUMN IF NOT EXISTS fail_code VARCHAR(50),
  ADD COLUMN IF NOT EXISTS placeholder_message_id UUID,
  ADD COLUMN IF NOT EXISTS last_polled_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS client_context JSONB,
  ADD COLUMN IF NOT EXISTS kie_url_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1,
  ADD COLUMN IF NOT EXISTS oss_retry_count INTEGER DEFAULT 0;

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_tasks_external_id ON tasks(external_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status_type ON tasks(status, type);
CREATE INDEX IF NOT EXISTS idx_tasks_user_pending ON tasks(user_id, status)
  WHERE status IN ('pending', 'running');
CREATE INDEX IF NOT EXISTS idx_tasks_conversation ON tasks(conversation_id)
  WHERE conversation_id IS NOT NULL;

-- 添加注释
COMMENT ON COLUMN tasks.external_task_id IS 'KIE API返回的task_id';
COMMENT ON COLUMN tasks.request_params IS '生成请求参数 (prompt, model, size等)';
COMMENT ON COLUMN tasks.result IS '任务结果 (image_urls, video_url等)';
COMMENT ON COLUMN tasks.placeholder_message_id IS '前端占位符消息ID,用于更新UI';
COMMENT ON COLUMN tasks.last_polled_at IS '最后一次轮询时间';
COMMENT ON COLUMN tasks.fail_code IS 'KIE返回的失败错误码';
COMMENT ON COLUMN tasks.client_context IS '客户端设备信息 (device, browser, tab_id等)';
COMMENT ON COLUMN tasks.kie_url_expires_at IS 'KIE原始URL过期时间';
COMMENT ON COLUMN tasks.version IS '版本号，用于乐观锁防止并发更新冲突';
COMMENT ON COLUMN tasks.oss_retry_count IS 'OSS上传重试次数';
