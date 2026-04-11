-- 064: 成员任职表（权限模型 Phase 0 - 5/9）
-- 技术文档: docs/document/TECH_组织架构与权限模型.md §3.6

CREATE TABLE IF NOT EXISTS org_member_assignments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    department_id   UUID REFERENCES org_departments(id),  -- 老板/副总可空
    position_id     UUID NOT NULL REFERENCES org_positions(id),

    job_title       VARCHAR(50),                          -- 自定义"高级运营专员"
    is_primary      BOOLEAN NOT NULL DEFAULT TRUE,        -- 一人可在多部门

    -- 数据范围
    data_scope      VARCHAR(20) NOT NULL
        CHECK (data_scope IN ('all','dept_subtree','self')),
    data_scope_dept_ids UUID[],                           -- 副总分管多部门时填

    -- 性能优化：权限版本号，变更时 +1，缓存失效
    perm_version    BIGINT NOT NULL DEFAULT 1,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_assignment_user ON org_member_assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_assignment_org_dept ON org_member_assignments(org_id, department_id);

-- 一个用户在一个组织内只能有一个主任职
CREATE UNIQUE INDEX IF NOT EXISTS idx_assignment_primary
    ON org_member_assignments(org_id, user_id)
    WHERE is_primary = TRUE;

COMMENT ON TABLE org_member_assignments IS '成员任职表（部门 + 职位 + 数据范围）';
COMMENT ON COLUMN org_member_assignments.data_scope IS 'all=全公司, dept_subtree=本部门及下级, self=仅自己';
COMMENT ON COLUMN org_member_assignments.data_scope_dept_ids IS '副总分管多个部门时填，dept_subtree 模式下的扩展列表';
COMMENT ON COLUMN org_member_assignments.perm_version IS '权限版本号，权限变更时 +1，用于缓存失效';
