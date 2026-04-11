-- 061: 组织职位表（权限模型 Phase 0 - 2/9）
-- 技术文档: docs/document/TECH_组织架构与权限模型.md §3.2

CREATE TABLE IF NOT EXISTS org_positions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

    code            VARCHAR(20) NOT NULL
        CHECK (code IN ('boss','vp','manager','deputy','member')),
    name            VARCHAR(50) NOT NULL,
    level           INTEGER NOT NULL,           -- 1=boss(最高), 5=member(最低)
    is_system       BOOLEAN NOT NULL DEFAULT TRUE,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, code)
);

CREATE INDEX IF NOT EXISTS idx_position_org ON org_positions(org_id);

COMMENT ON TABLE org_positions IS '组织职位表（boss/vp/manager/deputy/member）';
COMMENT ON COLUMN org_positions.level IS '数字职级，1=boss 最高，5=member 最低';
