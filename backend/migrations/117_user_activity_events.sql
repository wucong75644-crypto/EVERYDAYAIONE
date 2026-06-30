-- 117: 用户活跃事件表 + last_active_at 读模型
-- 目标：统一“上次活跃”口径，同时保持管理员列表排序高效。

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS user_activity_events (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  org_id UUID REFERENCES organizations(id) ON DELETE SET NULL,
  event_type TEXT NOT NULL CHECK (
    event_type IN (
      'login_success',
      'conversation_created',
      'message_sent',
      'task_created',
      'wecom_message_received',
      'file_uploaded'
    )
  ),
  source TEXT NOT NULL DEFAULT 'web' CHECK (source IN ('web', 'wecom', 'system')),
  resource_type TEXT,
  resource_id TEXT,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_activity_user_time
  ON user_activity_events(user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_activity_org_time
  ON user_activity_events(org_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_activity_event_time
  ON user_activity_events(event_type, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_users_last_active
  ON users(last_active_at DESC);

CREATE OR REPLACE FUNCTION record_user_activity(
  p_user_id UUID,
  p_event_type TEXT,
  p_org_id UUID DEFAULT NULL,
  p_source TEXT DEFAULT 'web',
  p_resource_type TEXT DEFAULT NULL,
  p_resource_id TEXT DEFAULT NULL,
  p_occurred_at TIMESTAMPTZ DEFAULT NOW(),
  p_metadata JSONB DEFAULT '{}'::jsonb
)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  INSERT INTO user_activity_events (
    user_id, org_id, event_type, source, resource_type,
    resource_id, occurred_at, metadata
  ) VALUES (
    p_user_id, p_org_id, p_event_type, p_source, p_resource_type,
    p_resource_id, COALESCE(p_occurred_at, NOW()), COALESCE(p_metadata, '{}'::jsonb)
  );

  UPDATE users
  SET last_active_at = (
    SELECT MAX(v)
    FROM (VALUES (users.last_active_at), (COALESCE(p_occurred_at, NOW()))) AS t(v)
    WHERE v IS NOT NULL
  )
  WHERE id = p_user_id;
END;
$$;

-- 历史回填：只回填读模型，不伪造历史事件明细。
UPDATE users u
SET last_active_at = latest.last_active_at
FROM (
  SELECT
    u2.id,
    (
      SELECT MAX(v)
      FROM (
        VALUES
          (u2.last_login_at),
          ((SELECT MAX(c.updated_at) FROM conversations c WHERE c.user_id = u2.id)),
          ((SELECT MAX(t.created_at) FROM tasks t WHERE t.user_id = u2.id)),
          ((SELECT MAX(t.completed_at) FROM tasks t WHERE t.user_id = u2.id))
      ) AS candidates(v)
      WHERE v IS NOT NULL
    ) AS last_active_at
  FROM users u2
) latest
WHERE u.id = latest.id
  AND latest.last_active_at IS NOT NULL
  AND (u.last_active_at IS NULL OR u.last_active_at < latest.last_active_at);
