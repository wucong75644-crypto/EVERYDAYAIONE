-- 060: 组织部门表（权限模型 Phase 0 - 1/9）
-- 技术文档: docs/document/TECH_组织架构与权限模型.md §3.1

-- ════════════════════════════════════════════════════════
-- 前置：安装 ltree 扩展（用于部门子树查询）
-- ════════════════════════════════════════════════════════
CREATE EXTENSION IF NOT EXISTS ltree;

-- ════════════════════════════════════════════════════════
-- 部门表
-- ════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS org_departments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    parent_id       UUID REFERENCES org_departments(id),
    name            VARCHAR(50) NOT NULL,
    type            VARCHAR(20) NOT NULL
        CHECK (type IN ('ops','finance','warehouse','service','design','hr','other')),

    -- 物化路径（加速子树查询，根节点 = 'root'，子节点 = 'root.ops_1'）
    path            LTREE NOT NULL,

    sort_order      INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dept_org ON org_departments(org_id);
CREATE INDEX IF NOT EXISTS idx_dept_parent ON org_departments(parent_id);
CREATE INDEX IF NOT EXISTS idx_dept_path ON org_departments USING GIST(path);

COMMENT ON TABLE org_departments IS '组织部门表（权限模型 V1）';
COMMENT ON COLUMN org_departments.type IS 'ops/finance/warehouse/service/design/hr/other';
COMMENT ON COLUMN org_departments.path IS 'ltree 物化路径，加速本部门及下级查询';
