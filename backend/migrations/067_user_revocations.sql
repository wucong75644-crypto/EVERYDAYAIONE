-- 067: 用户撤销表（权限模型 Phase 0 - 8/9, V2 启用）
-- 技术文档: docs/document/TECH_组织架构与权限模型.md §3.9

CREATE TABLE IF NOT EXISTS user_revocations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    permission_code VARCHAR(80) NOT NULL,
    target_user_ids UUID[],
    target_resource_ids UUID[],

    revoked_by      UUID NOT NULL REFERENCES users(id),
    reason          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_revocations_user ON user_revocations(org_id, user_id);

COMMENT ON TABLE user_revocations IS '用户权限黑名单（V2 启用，V1 不读）';
