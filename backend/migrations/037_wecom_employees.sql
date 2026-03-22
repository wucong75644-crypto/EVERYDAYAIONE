-- 037_wecom_employees.sql
-- 企微通讯录同步：部门表 + 员工表

-- 部门表
CREATE TABLE IF NOT EXISTS wecom_departments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    department_id INT NOT NULL,
    corp_id VARCHAR(64) NOT NULL,
    name VARCHAR(256) NOT NULL,
    parent_id INT DEFAULT 0,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(department_id, corp_id)
);

CREATE INDEX IF NOT EXISTS idx_wecom_dept_parent
ON wecom_departments(corp_id, parent_id);

-- 员工表
CREATE TABLE IF NOT EXISTS wecom_employees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wecom_userid VARCHAR(64) NOT NULL,
    corp_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    department_ids INT[] DEFAULT '{}',
    status INT NOT NULL DEFAULT 1,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(wecom_userid, corp_id)
);

CREATE INDEX IF NOT EXISTS idx_wecom_emp_status
ON wecom_employees(corp_id, status);

COMMENT ON TABLE wecom_departments IS '企微部门（通讯录同步）';
COMMENT ON TABLE wecom_employees IS '企微员工（通讯录同步）';
COMMENT ON COLUMN wecom_employees.status IS '1=在职 0=已离职（API中不存在时标记）';
