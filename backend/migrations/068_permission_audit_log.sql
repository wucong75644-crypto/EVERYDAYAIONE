-- 068: 权限审计日志（权限模型 Phase 0 - 9/9, V2 启用）
-- 技术文档: docs/document/TECH_组织架构与权限模型.md §3.10
--
-- V1 阶段：表建好但不写日志
-- V2 阶段：grant/revoke/check_denied 全部记录
-- V3 阶段：按月分区（公司规模 50+ 时启用）

CREATE TABLE IF NOT EXISTS permission_audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

    actor_id        UUID NOT NULL REFERENCES users(id),    -- 操作人
    action          VARCHAR(30) NOT NULL,                  -- grant/revoke/check_denied/auto_expired/assignment_changed

    target_user_id  UUID,                                  -- 被操作人
    target_permission VARCHAR(80),
    target_resource_id UUID,

    before_state    JSONB,
    after_state     JSONB,
    reason          TEXT,

    ip_address      INET,
    user_agent      TEXT,
    request_id      VARCHAR(50),

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_org_actor
    ON permission_audit_log(org_id, actor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_target
    ON permission_audit_log(org_id, target_user_id, created_at DESC);

COMMENT ON TABLE permission_audit_log IS '权限审计日志（V2 启用）';
COMMENT ON COLUMN permission_audit_log.action IS 'grant/revoke/check_denied/auto_expired/assignment_changed';
