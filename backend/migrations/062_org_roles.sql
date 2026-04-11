-- 062: 组织角色表（权限模型 Phase 0 - 3/9）
-- 技术文档: docs/document/TECH_组织架构与权限模型.md §3.3

CREATE TABLE IF NOT EXISTS org_roles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

    code            VARCHAR(50) NOT NULL,
    name            VARCHAR(50) NOT NULL,
    description     TEXT,
    is_system       BOOLEAN NOT NULL DEFAULT TRUE,

    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, code)
);

CREATE INDEX IF NOT EXISTS idx_role_org ON org_roles(org_id);

COMMENT ON TABLE org_roles IS '组织角色表，包含系统预设和用户自定义角色';
COMMENT ON COLUMN org_roles.code IS 'role_ops/role_finance/role_boss_full/...';
