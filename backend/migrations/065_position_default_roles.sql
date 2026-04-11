-- 065: 职位默认角色映射（权限模型 Phase 0 - 6/9）
-- 技术文档: docs/document/TECH_组织架构与权限模型.md §3.7

CREATE TABLE IF NOT EXISTS position_default_roles (
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    position_code   VARCHAR(20) NOT NULL,                 -- boss/vp/manager/deputy/member
    department_type VARCHAR(20),                          -- ops/finance/.../NULL
    role_id         UUID NOT NULL REFERENCES org_roles(id) ON DELETE CASCADE,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- NULL 的 department_type 用 'all' 占位（PostgreSQL 复合主键不允许 NULL）
    PRIMARY KEY (org_id, position_code, department_type, role_id)
);

CREATE INDEX IF NOT EXISTS idx_pdr_org ON position_default_roles(org_id);

COMMENT ON TABLE position_default_roles IS '职位+部门类型 → 默认角色映射';
COMMENT ON COLUMN position_default_roles.department_type IS 'ops/finance/.../"all"=不限部门（老板/副总）';
