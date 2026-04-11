-- 063: 全局权限点目录（权限模型 Phase 0 - 4/9）
-- 技术文档: docs/document/TECH_组织架构与权限模型.md §3.4

CREATE TABLE IF NOT EXISTS permissions (
    code            VARCHAR(80) PRIMARY KEY,
    module          VARCHAR(20) NOT NULL,
    action          VARCHAR(20) NOT NULL,
    name            VARCHAR(50) NOT NULL,
    description     TEXT
);

CREATE INDEX IF NOT EXISTS idx_perm_module ON permissions(module);

COMMENT ON TABLE permissions IS '全局权限点目录（不分租户，是权限码的字典）';

-- ════════════════════════════════════════════════════════
-- 角色-权限关联表
-- ════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS role_permissions (
    role_id         UUID NOT NULL REFERENCES org_roles(id) ON DELETE CASCADE,
    permission_code VARCHAR(80) NOT NULL REFERENCES permissions(code),
    PRIMARY KEY (role_id, permission_code)
);

CREATE INDEX IF NOT EXISTS idx_role_perms_perm ON role_permissions(permission_code);

COMMENT ON TABLE role_permissions IS '角色权限关联（一个角色对应多个权限点）';

-- ════════════════════════════════════════════════════════
-- 预填权限点目录（V1 范围：定时任务 + ERP 业务 + 系统配置）
-- ════════════════════════════════════════════════════════
INSERT INTO permissions (code, module, action, name, description) VALUES
    -- 定时任务
    ('task.view',    'task',  'view',    '查看定时任务',     '查看定时任务列表和详情'),
    ('task.create',  'task',  'create',  '创建定时任务',     '创建新的定时任务'),
    ('task.edit',    'task',  'edit',    '编辑定时任务',     '修改任务配置/暂停/恢复'),
    ('task.delete',  'task',  'delete',  '删除定时任务',     '删除任务'),
    ('task.execute', 'task',  'execute', '立即执行定时任务',  '手动触发任务立即执行'),
    -- 订单
    ('order.view',    'order', 'view',    '查看订单',  '查看订单数据'),
    ('order.edit',    'order', 'edit',    '编辑订单',  '修改订单'),
    ('order.export',  'order', 'export',  '导出订单',  '导出订单为 Excel/CSV'),
    -- 商品
    ('product.view',  'product', 'view',  '查看商品',  '查看商品库'),
    ('product.edit',  'product', 'edit',  '编辑商品',  '修改商品信息'),
    -- 财务
    ('finance.view',      'finance', 'view',      '查看财务',  '查看财务报表'),
    ('finance.export',    'finance', 'export',    '导出财务',  '导出财务数据'),
    ('finance.reconcile', 'finance', 'reconcile', '财务对账',  '执行对账操作'),
    -- 库存
    ('stock.view',     'stock', 'view',     '查看库存',  '查看库存数据'),
    ('stock.edit',     'stock', 'edit',     '编辑库存',  '修改库存'),
    ('stock.inbound',  'stock', 'inbound',  '入库',     '执行入库操作'),
    ('stock.outbound', 'stock', 'outbound', '出库',     '执行出库操作'),
    -- 系统配置（仅老板）
    ('sys.member.add',      'sys', 'member_add',  '添加员工',         '邀请新员工加入企业'),
    ('sys.member.edit',     'sys', 'member_edit', '编辑员工部门职位',  '修改员工的部门、职位、数据范围'),
    ('sys.erp.config',      'sys', 'erp_config',  '配置 ERP 凭证',     '修改快麦等 ERP 平台的凭证'),
    ('sys.wecom.config',    'sys', 'wecom_config','配置企微',          '修改企微 corp_id/secret/agent_id'),
    ('sys.permission.grant','sys', 'perm_grant',  '授予额外权限',      'V2: 给员工开通跨岗位的额外权限')
ON CONFLICT (code) DO NOTHING;
