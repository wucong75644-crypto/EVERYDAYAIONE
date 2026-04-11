-- 066: 用户额外授权（权限模型 Phase 0 - 7/9, V2 启用）
-- 技术文档: docs/document/TECH_组织架构与权限模型.md §3.8
-- V1 阶段只建表，运行时不查询；V2 启用 extra_grants 检查

CREATE TABLE IF NOT EXISTS user_extra_grants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- 授权内容（角色或单个权限二选一）
    grant_type      VARCHAR(10) NOT NULL CHECK (grant_type IN ('role','permission')),
    role_id         UUID REFERENCES org_roles(id),
    permission_code VARCHAR(80),

    -- 数据范围
    data_scope      VARCHAR(20) NOT NULL
        CHECK (data_scope IN ('all','dept_subtree','specific_users','specific_resources')),
    target_dept_ids UUID[],
    target_user_ids UUID[],
    target_resource_ids UUID[],                           -- 某个具体任务/订单

    -- 元数据
    granted_by      UUID NOT NULL REFERENCES users(id),
    reason          TEXT,
    expires_at      TIMESTAMPTZ,                          -- 临时授权

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 至少有一个授权目标
    CHECK (
        (grant_type = 'role' AND role_id IS NOT NULL) OR
        (grant_type = 'permission' AND permission_code IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_extra_grants_user ON user_extra_grants(org_id, user_id);
CREATE INDEX IF NOT EXISTS idx_extra_grants_expires
    ON user_extra_grants(expires_at)
    WHERE expires_at IS NOT NULL;

COMMENT ON TABLE user_extra_grants IS '用户额外授权（V2 启用，V1 不读）';
COMMENT ON COLUMN user_extra_grants.expires_at IS '临时授权到期时间，NULL=永久';
