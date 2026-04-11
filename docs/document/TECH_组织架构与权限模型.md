# 技术方案：组织架构与权限模型

> 版本：V1.1 | 日期：2026-04-11
> 状态：方案待实施
> 设计参考：Salesforce Profile+Permission Sets / 飞书职位角色 / Notion 权限透明 / Azure PIM 临时权限 / AWS IAM 三级优先级
>
> **前置依赖**：PostgreSQL ltree 扩展（migration 050 自动安装）

---

## 一、核心理念

借鉴大厂混合模型，三个核心概念：

```
最终权限 = 职位默认角色 ∪ 额外授权 - 撤销 ∩ 数据范围
           ↑              ↑          ↑       ↑
          飞书           Salesforce  AWS    飞书
       自动分配        Permission Sets Deny  数据权限
```

### 设计原则

| 原则 | 实现 |
|------|------|
| **零配置** | 16 人公司新员工加入自动按部门拿角色 |
| **可扩展** | 数据库一次到位，100+ 人不重写 |
| **熟悉感** | 中国 SaaS 用户用过钉钉/飞书的概念 |
| **可审计** | 所有权限变更不可变记录 |
| **可临时** | 支持到期自动撤销 |

### 三个正交维度

| 维度 | 含义 | 例子 |
|------|------|------|
| **功能权限** | 能做什么 | 查看订单、修改库存、导出财务报表 |
| **数据范围** | 能操作谁的数据 | 全公司、本部门、自己 |
| **资源对象** | 操作哪个具体对象 | 某个任务、某个订单 |

---

## 二、组织结构

### 2.1 层级模型

```
公司 (organization)
├── 老板 (boss)                    ← 全部权限
│
├── 副总 (vp)                      ← 两种模式
│   ├── 全公司副总 → 看全公司
│   └── 分管副总 → 看分管的多个部门
│
└── 部门 (department)              ← 平铺，不嵌套
    ├── 运营一部 (淘宝/天猫)
    │   ├── 主管 (manager)        ← 看本部门所有人
    │   ├── 副主管 (deputy)        ← 同员工权限
    │   └── 员工 (member) × N
    │
    ├── 运营二部 (拼多多/抖音)
    ├── 财务部
    ├── 仓库部
    ├── 客服部
    ├── 设计部
    └── 人事部
```

### 2.2 5 个职位

| code | 名称 | 默认数据范围 | 说明 |
|------|------|------------|------|
| `boss` | 老板 | all | 公司创建者，全部权限 |
| `vp` | 副总 | all 或 dept_subtree[] | 全公司副总或分管副总 |
| `manager` | 主管 | dept_subtree | 看本部门所有人 |
| `deputy` | 副主管 | self | 头衔，权限同员工 |
| `member` | 员工 | self | 只看自己 |

### 2.3 6 个部门类型

| type | 中文 | 业务角色 |
|------|------|---------|
| `ops` | 运营部（可多个子部门）| 运营角色 |
| `finance` | 财务部 | 财务角色 |
| `warehouse` | 仓库部 | 仓库角色 |
| `service` | 客服部 | 客服角色 |
| `design` | 设计部 | 设计角色 |
| `hr` | 人事部 | 人事角色 |

---

## 三、数据库设计

### 3.1 部门表

```sql
CREATE TABLE org_departments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    parent_id       UUID REFERENCES org_departments(id),
    name            VARCHAR(50) NOT NULL,
    type            VARCHAR(20) NOT NULL CHECK (type IN ('ops','finance','warehouse','service','design','hr','other')),
    
    -- 物化路径（加速子树查询）
    path            LTREE NOT NULL,
    
    sort_order      INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_dept_org ON org_departments(org_id);
CREATE INDEX idx_dept_parent ON org_departments(parent_id);
CREATE INDEX idx_dept_path ON org_departments USING GIST(path);
```

> 需要 PostgreSQL `ltree` 扩展：`CREATE EXTENSION IF NOT EXISTS ltree;`

### 3.2 职位表

```sql
CREATE TABLE org_positions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    
    code            VARCHAR(20) NOT NULL CHECK (code IN ('boss','vp','manager','deputy','member')),
    name            VARCHAR(50) NOT NULL,
    level           INTEGER NOT NULL,           -- 1=boss(最高), 5=member(最低)
    is_system       BOOLEAN DEFAULT TRUE,
    
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (org_id, code)
);
```

### 3.3 角色表

```sql
CREATE TABLE org_roles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    
    code            VARCHAR(50) NOT NULL,
    name            VARCHAR(50) NOT NULL,
    description     TEXT,
    is_system       BOOLEAN DEFAULT TRUE,
    
    created_by      UUID,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (org_id, code)
);
```

### 3.4 权限点表（全局）

```sql
CREATE TABLE permissions (
    code            VARCHAR(80) PRIMARY KEY,
    module          VARCHAR(20) NOT NULL,
    action          VARCHAR(20) NOT NULL,
    name            VARCHAR(50) NOT NULL,
    description     TEXT
);

CREATE INDEX idx_perm_module ON permissions(module);
```

### 3.5 角色-权限关联

```sql
CREATE TABLE role_permissions (
    role_id         UUID NOT NULL REFERENCES org_roles(id) ON DELETE CASCADE,
    permission_code VARCHAR(80) NOT NULL REFERENCES permissions(code),
    PRIMARY KEY (role_id, permission_code)
);
```

### 3.6 成员任职表

```sql
CREATE TABLE org_member_assignments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    department_id   UUID REFERENCES org_departments(id),  -- 老板/副总可空
    position_id     UUID NOT NULL REFERENCES org_positions(id),
    
    job_title       VARCHAR(50),                          -- 自定义"高级运营专员"
    is_primary      BOOLEAN DEFAULT TRUE,                 -- 一人可在多部门
    
    -- 数据范围
    data_scope      VARCHAR(20) NOT NULL CHECK (data_scope IN ('all','dept_subtree','self')),
    data_scope_dept_ids UUID[],                           -- 副总分管多部门时填
    
    -- 性能优化：权限版本号，变更时 +1，缓存失效
    perm_version    BIGINT DEFAULT 1,
    
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_assignment_user ON org_member_assignments(user_id);
CREATE INDEX idx_assignment_org_dept ON org_member_assignments(org_id, department_id);
CREATE UNIQUE INDEX idx_assignment_primary ON org_member_assignments(user_id) WHERE is_primary = TRUE;
```

### 3.7 职位默认角色

```sql
CREATE TABLE position_default_roles (
    org_id          UUID NOT NULL,
    position_code   VARCHAR(20) NOT NULL,                 -- 不用 id，用 code 方便迁移
    department_type VARCHAR(20),                          -- NULL = 不限部门
    role_id         UUID NOT NULL REFERENCES org_roles(id) ON DELETE CASCADE,
    
    created_at      TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (org_id, position_code, department_type, role_id)
);
```

### 3.8 用户额外授权（V2 启用）

```sql
CREATE TABLE user_extra_grants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    -- 授权内容（角色或单个权限二选一）
    grant_type      VARCHAR(10) NOT NULL CHECK (grant_type IN ('role','permission')),
    role_id         UUID REFERENCES org_roles(id),
    permission_code VARCHAR(80),
    
    -- 数据范围
    data_scope      VARCHAR(20) NOT NULL CHECK (data_scope IN ('all','dept_subtree','specific_users','specific_resources')),
    target_dept_ids UUID[],
    target_user_ids UUID[],
    target_resource_ids UUID[],                           -- 某个具体任务/订单
    
    -- 元数据
    granted_by      UUID NOT NULL REFERENCES users(id),
    reason          TEXT,
    expires_at      TIMESTAMPTZ,                          -- 临时授权
    
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_extra_grants_user ON user_extra_grants(org_id, user_id);
CREATE INDEX idx_extra_grants_expires ON user_extra_grants(expires_at) WHERE expires_at IS NOT NULL;
```

### 3.9 用户撤销表（黑名单，V2 启用）

```sql
CREATE TABLE user_revocations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    permission_code VARCHAR(80) NOT NULL,
    target_user_ids UUID[],
    target_resource_ids UUID[],
    
    revoked_by      UUID NOT NULL REFERENCES users(id),
    reason          TEXT,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_revocations_user ON user_revocations(org_id, user_id);
```

### 3.10 审计日志（按月分区）

```sql
CREATE TABLE permission_audit_log (
    id              UUID DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL,
    
    actor_id        UUID NOT NULL,                        -- 操作人
    action          VARCHAR(30) NOT NULL,                 -- grant/revoke/check_denied/auto_expired/assignment_changed
    
    target_user_id  UUID,                                 -- 被操作人
    target_permission VARCHAR(80),
    target_resource_id UUID,
    
    before_state    JSONB,
    after_state     JSONB,
    reason          TEXT,
    
    ip_address      INET,
    user_agent      TEXT,
    request_id      VARCHAR(50),
    
    created_at      TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- 按月创建分区，例：
CREATE TABLE permission_audit_log_2026_04 PARTITION OF permission_audit_log
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');

CREATE INDEX idx_audit_org_actor ON permission_audit_log(org_id, actor_id, created_at DESC);
CREATE INDEX idx_audit_target ON permission_audit_log(org_id, target_user_id, created_at DESC);
```

---

## 四、系统预设角色与权限

### 4.1 业务角色（按部门自动分配）

| 角色 code | 名称 | 包含权限 |
|-----------|------|---------|
| `role_ops` | 运营角色 | 订单查看/编辑/导出、商品管理、店铺管理、定时任务 |
| `role_finance` | 财务角色 | 财务报表、对账、导出 |
| `role_warehouse` | 仓库角色 | 库存查看、入库、出库、调拨 |
| `role_service` | 客服角色 | 订单查询、售后处理、客户信息 |
| `role_design` | 设计角色 | 素材管理、上传 |
| `role_hr` | 人事角色 | 员工管理、考勤、薪资 |

### 4.2 系统级角色（按职位分配）

| 角色 code | 名称 | 适用职位 | 权限特点 |
|-----------|------|---------|---------|
| `role_boss_full` | 老板全权 | boss | 所有权限 + all 范围 |
| `role_vp_full` | 副总全权 | vp | 业务权限 + all 或 dept_subtree |
| `role_manager_addon` | 主管加成包 | manager | 不增功能，数据范围升级到 dept_subtree |

**核心思路**：
- 业务角色（运营/财务/...）= **功能** 默认 `self` 范围
- 系统级角色（老板/副总/主管）= **数据范围** 升级器
- 副主管 / 员工 = 只有业务角色，范围是 `self`

### 4.3 权限点目录（V1）

```python
# backend/services/permissions/permission_points.py

PERMISSIONS = {
    # ─── 定时任务 ───
    'task.view':    ('task', 'view',    '查看定时任务'),
    'task.create':  ('task', 'create',  '创建定时任务'),
    'task.edit':    ('task', 'edit',    '编辑定时任务'),
    'task.delete':  ('task', 'delete',  '删除定时任务'),
    'task.execute': ('task', 'execute', '立即执行定时任务'),
    
    # ─── 订单 ───
    'order.view':    ('order', 'view',    '查看订单'),
    'order.edit':    ('order', 'edit',    '编辑订单'),
    'order.export':  ('order', 'export',  '导出订单'),
    
    # ─── 商品 ───
    'product.view':  ('product', 'view',  '查看商品'),
    'product.edit':  ('product', 'edit',  '编辑商品'),
    
    # ─── 财务 ───
    'finance.view':       ('finance', 'view',   '查看财务'),
    'finance.export':     ('finance', 'export', '导出财务'),
    'finance.reconcile':  ('finance', 'reconcile', '财务对账'),
    
    # ─── 库存 ───
    'stock.view':    ('stock', 'view',   '查看库存'),
    'stock.edit':    ('stock', 'edit',   '编辑库存'),
    'stock.inbound': ('stock', 'inbound','入库'),
    'stock.outbound':('stock', 'outbound','出库'),
    
    # ─── 系统配置（仅老板）───
    'sys.member.add':    ('sys', 'member_add',    '添加员工'),
    'sys.member.edit':   ('sys', 'member_edit',   '编辑员工部门职位'),
    'sys.erp.config':    ('sys', 'erp_config',    '配置 ERP 凭证'),
    'sys.wecom.config':  ('sys', 'wecom_config',  '配置企微'),
    'sys.permission.grant': ('sys', 'perm_grant', '授予额外权限'),
}
```

### 4.4 创建组织时自动初始化

```python
async def initialize_organization(org_id: str, owner_user_id: str):
    """新建组织时自动创建职位、角色、默认部门"""
    
    # 1. 创建 5 个职位
    positions = [
        ('boss',    '老板',   1),
        ('vp',      '副总',   2),
        ('manager', '主管',   3),
        ('deputy',  '副主管', 4),
        ('member',  '员工',   5),
    ]
    for code, name, level in positions:
        await create_position(org_id, code, name, level)
    
    # 2. 创建 9 个系统角色
    roles = [
        ('role_ops',           '运营角色',     [op for op in PERMISSIONS if op.startswith(('task.','order.','product.'))]),
        ('role_finance',       '财务角色',     [op for op in PERMISSIONS if op.startswith('finance.')]),
        ('role_warehouse',     '仓库角色',     [op for op in PERMISSIONS if op.startswith('stock.')]),
        ('role_service',       '客服角色',     [op for op in PERMISSIONS if op.startswith('order.')]),
        ('role_design',        '设计角色',     []),
        ('role_hr',            '人事角色',     [op for op in PERMISSIONS if op.startswith('sys.member.')]),
        ('role_boss_full',     '老板全权',     list(PERMISSIONS.keys())),
        ('role_vp_full',       '副总全权',     [op for op in PERMISSIONS if not op.startswith(('sys.member.','sys.erp.','sys.wecom.','sys.permission.'))]),
        ('role_manager_addon', '主管加成包',   []),
    ]
    for code, name, perms in roles:
        role_id = await create_role(org_id, code, name)
        for p in perms:
            await add_role_permission(role_id, p)
    
    # 3. 创建 6 个默认部门
    departments = [
        ('运营一部', 'ops'),
        ('财务部',   'finance'),
        ('仓库部',   'warehouse'),
        ('客服部',   'service'),
        ('设计部',   'design'),
        ('人事部',   'hr'),
    ]
    for name, type_ in departments:
        await create_department(org_id, name, type_)
    
    # 4. 配置职位默认角色映射
    # 业务角色按部门类型自动分配
    for dept_type, role_code in [
        ('ops', 'role_ops'),
        ('finance', 'role_finance'),
        ('warehouse', 'role_warehouse'),
        ('service', 'role_service'),
        ('design', 'role_design'),
        ('hr', 'role_hr'),
    ]:
        # 该部门类型下，员工/副主管/主管 都自动获得对应业务角色
        for pos in ('member', 'deputy', 'manager'):
            await add_position_default_role(org_id, pos, dept_type, role_code)
        # 主管额外获得加成包（数据范围升级）
        await add_position_default_role(org_id, 'manager', dept_type, 'role_manager_addon')
    
    # 老板和副总不属于具体部门，独立配置
    await add_position_default_role(org_id, 'boss', None, 'role_boss_full')
    await add_position_default_role(org_id, 'vp',   None, 'role_vp_full')
    
    # 5. 把创建者设为老板
    await assign_member(
        org_id=org_id,
        user_id=owner_user_id,
        department_id=None,
        position_code='boss',
        data_scope='all'
    )
```

---

## 五、权限检查算法

### 5.1 V1 简化版（硬编码逻辑）

```python
# backend/services/permissions/checker.py

class PermissionChecker:
    """V1 权限检查器：硬编码岗位逻辑，不读 extra_grants"""
    
    async def check(
        self,
        user: User,
        permission_code: str,
        resource: dict | None = None
    ) -> bool:
        assignment = await self._get_assignment(user.id)
        
        # 1. 老板：全部允许
        if assignment.position_code == 'boss':
            return True
        
        # 2. 副总：业务权限全部允许，系统配置拒绝
        if assignment.position_code == 'vp':
            if permission_code.startswith('sys.'):
                return False
            # 检查数据范围
            return await self._check_vp_scope(assignment, resource)
        
        # 3. 主管：本部门内的业务权限
        if assignment.position_code == 'manager':
            if permission_code.startswith('sys.'):
                return False
            if not self._has_role_permission(assignment, permission_code):
                return False
            return await self._check_dept_scope(assignment, resource)
        
        # 4. 副主管/员工：只能操作自己的资源
        if assignment.position_code in ('deputy', 'member'):
            if permission_code.startswith('sys.'):
                return False
            if not self._has_role_permission(assignment, permission_code):
                return False
            if resource and resource.get('user_id') != user.id:
                return False
            return True
        
        return False
    
    async def _check_vp_scope(self, assignment, resource):
        """副总数据范围检查"""
        if assignment.data_scope == 'all':
            return True
        if not resource:
            return True  # 列表查询，由 SQL 注入处理
        # 分管副总：检查资源创建者是否在分管部门
        return await self._is_resource_in_depts(resource, assignment.data_scope_dept_ids)
    
    async def _check_dept_scope(self, assignment, resource):
        """主管数据范围检查：本部门"""
        if not resource:
            return True
        return await self._is_resource_in_depts(resource, [assignment.department_id])
    
    async def _is_resource_in_depts(self, resource, dept_ids: list) -> bool:
        """判断资源创建者是否在指定部门列表中"""
        creator_id = resource.get('user_id')
        if not creator_id:
            return False
        creator_assignment = await self._get_assignment(creator_id)
        return creator_assignment.department_id in dept_ids
```

### 5.2 V2 完整版（含 extra_grants 和缓存）

```python
class PermissionCheckerV2:
    """V2 完整版权限检查器"""
    
    async def check(
        self,
        user: User,
        permission_code: str,
        resource: dict | None = None
    ) -> bool:
        # 1. 缓存查询
        perms = await self._get_effective_perms_cached(user)
        
        # 2. 黑名单优先
        if perms.is_revoked(permission_code, resource):
            await self._audit('check_denied', user, permission_code, resource, reason='revoked')
            return False
        
        # 3. 检查功能权限
        if permission_code not in perms.functions:
            return False
        
        # 4. 检查数据范围
        if resource is not None:
            scope = perms.scope_for(permission_code)
            if not scope.includes(resource):
                return False
        
        return True
    
    async def _get_effective_perms_cached(self, user: User):
        cache_key = f"perms:{user.id}:v{user.perm_version}"
        cached = await redis.get(cache_key)
        if cached:
            return EffectivePerms.from_json(cached)
        
        perms = await self._build_effective_perms(user)
        await redis.setex(cache_key, 300, perms.to_json())
        return perms
    
    async def _build_effective_perms(self, user: User) -> EffectivePerms:
        perms = EffectivePerms()
        
        # 1. 职位默认角色
        assignment = await get_assignment(user.id)
        default_roles = await get_position_default_roles(
            user.org_id, assignment.position_code, assignment.department.type
        )
        for role in default_roles:
            perms.merge_role(role, assignment.data_scope, assignment.data_scope_dept_ids)
        
        # 2. 额外授权
        extra_grants = await get_active_grants(user.id)  # expires_at > now() OR NULL
        for grant in extra_grants:
            perms.apply_grant(grant)
        
        # 3. 撤销
        revocations = await get_revocations(user.id)
        for rev in revocations:
            perms.apply_revocation(rev)
        
        return perms
```

---

## 六、SQL 查询时数据范围注入

**关键性能优化**：把数据范围作为 WHERE 条件，而不是先查后过滤。

```python
# backend/services/permissions/scope_filter.py

async def apply_data_scope(
    query,
    user: User,
    permission_code: str,
    user_id_field: str = 'user_id'
):
    """根据用户权限给查询自动加 WHERE 条件"""
    
    assignment = await get_assignment(user.id)
    
    # 老板：不加过滤
    if assignment.position_code == 'boss':
        return query
    
    # 副总
    if assignment.position_code == 'vp':
        if assignment.data_scope == 'all':
            return query
        # 分管副总：限制到分管部门的成员
        dept_user_ids = await get_users_in_depts(assignment.data_scope_dept_ids)
        return query.in_(user_id_field, list(dept_user_ids))
    
    # 主管：本部门所有成员
    if assignment.position_code == 'manager':
        dept_user_ids = await get_users_in_depts([assignment.department_id])
        return query.in_(user_id_field, list(dept_user_ids))
    
    # 副主管/员工：只看自己
    return query.eq(user_id_field, user.id)


async def get_users_in_depts(dept_ids: list[UUID]) -> set[UUID]:
    """查询部门下所有成员（包含子部门）"""
    if not dept_ids:
        return set()
    
    # 用 ltree 查子树
    result = await db.execute("""
        SELECT DISTINCT a.user_id
        FROM org_member_assignments a
        JOIN org_departments d ON a.department_id = d.id
        WHERE d.id = ANY(%s) OR d.path <@ ANY(
            SELECT path FROM org_departments WHERE id = ANY(%s)
        )
    """, (dept_ids, dept_ids))
    
    return {row['user_id'] for row in result}
```

---

## 七、典型场景示例

### 场景 1：员工查看任务列表

```python
# 张三 = 运营一部 员工
# 应该只看到自己创建的任务

@router.get("/scheduled-tasks")
async def list_tasks(current_user: User):
    query = db.scheduled_tasks.select().eq('org_id', current_user.org_id)
    query = await apply_data_scope(query, current_user, 'task.view')
    # 自动变成: WHERE org_id = X AND user_id = 张三.id
    return await query.execute()
```

### 场景 2：主管查看任务列表

```python
# 王主管 = 运营一部 主管
# 应该看到运营一部所有人的任务

# 同样的代码，apply_data_scope 自动判断
# 自动变成: WHERE org_id = X AND user_id IN (运营一部所有成员)
```

### 场景 3：副总暂停任务

```python
# 李副总 = 分管运营一部、运营二部
# 暂停某个运营一部员工的任务

@router.post("/scheduled-tasks/{task_id}/pause")
async def pause_task(task_id: str, current_user: User):
    task = await get_task(task_id)
    
    if not await check_permission(current_user, 'task.edit', task):
        raise HTTPException(403)
    # check_permission 自动判断:
    # - 不是 boss
    # - 是 vp，data_scope=dept_subtree, data_scope_dept_ids=[运营一部, 运营二部]
    # - 检查 task 创建者属于哪个部门
    # - 创建者属于运营一部 → 在 data_scope_dept_ids 内 → 允许
    
    await pause(task)
```

### 场景 4：员工尝试编辑别人的任务

```python
# 张三 = 运营一部 员工
# 尝试编辑李四（同部门同事）的任务 → 拒绝

# check_permission 判断:
# - 不是 boss / vp / manager
# - 是 member
# - resource.user_id != current_user.id → 拒绝
```

---

## 八、/api/auth/me 端点扩展

参考 Clerk / Frontegg 的 B2B 多租户标准模式：**单个 `/me` 一次性返回所有 membership 信息**，避免前端"权限闪烁"。

### 当前实现

[backend/api/routes/auth.py:185-205](../../backend/api/routes/auth.py#L185-L205)：

```python
@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: CurrentUser) -> dict:
    return {
        "id": current_user["id"],
        "nickname": current_user["nickname"],
        "avatar_url": current_user.get("avatar_url"),
        "phone": masked_phone,
        "role": current_user["role"],
        "credits": current_user["credits"],
        "created_at": current_user["created_at"],
    }
```

### 扩展后

```python
@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: CurrentUser) -> dict:
    # 现有字段保持不变（向后兼容）
    base = {
        "id": current_user["id"],
        "nickname": current_user["nickname"],
        # ... 现有
    }
    
    # 新增：当前组织的 membership 信息
    org_id = current_user.get("current_org_id")
    if org_id:
        # join org_member_assignments + org_positions + org_departments
        member = await fetch_member_context(current_user["id"], org_id)
        # 计算权限码列表（用于前端路由守卫）
        permissions = await compute_user_permissions(current_user["id"], org_id)
        
        base["current_org"] = {
            "id": org_id,
            "name": (await fetch_org(org_id))["name"],
            "role": (await fetch_org_member_role(org_id, current_user["id"])),  # 现有 owner/admin/member
            "member": {
                "position_code": member.position_code,        # boss/vp/manager/deputy/member
                "department_id": member.department_id,
                "department_name": member.department_name,
                "department_type": member.department_type,    # ops/finance/...
                "job_title": member.job_title,
                "data_scope": member.data_scope,              # all/dept_subtree/self
                "managed_departments": member.managed_departments,  # 副总分管部门
            },
            "permissions": permissions,  # ['task.view', 'task.create', 'order.view', ...]
        }
    
    base["orgs"] = await fetch_user_orgs(current_user["id"])  # 切换组织用
    return base
```

### Schema 扩展

```python
# backend/schemas/auth.py
from typing import List, Optional, Literal

class CurrentMember(BaseModel):
    """当前组织内的任职信息（V1.0+ 新增）"""
    position_code: Literal['boss','vp','manager','deputy','member']
    department_id: Optional[str] = None
    department_name: Optional[str] = None
    department_type: Optional[Literal['ops','finance','warehouse','service','design','hr']] = None
    job_title: Optional[str] = None
    data_scope: Literal['all','dept_subtree','self']
    managed_departments: Optional[List[dict]] = None  # 副总分管部门 [{id, name}]


class CurrentOrg(BaseModel):
    id: str
    name: str
    role: Literal['owner','admin','member']  # 现有的 org_members.role
    member: CurrentMember                     # 新增的任职信息
    permissions: List[str]                    # 扁平化权限码


class UserResponse(BaseModel):
    """用户信息响应（V1.0+ 扩展）"""
    # 现有字段保持不变
    id: str
    nickname: str
    avatar_url: Optional[str]
    phone: Optional[str]
    role: str
    credits: int
    created_at: str
    
    # 新增字段
    current_org: Optional[CurrentOrg] = None
    orgs: List[dict] = []  # [{id, name, role}]
```

### 前端缓存

```typescript
// frontend/src/stores/useAuthStore.ts 扩展

interface CurrentMember {
  position_code: 'boss' | 'vp' | 'manager' | 'deputy' | 'member';
  department_id?: string;
  department_name?: string;
  department_type?: string;
  job_title?: string;
  data_scope: 'all' | 'dept_subtree' | 'self';
  managed_departments?: Array<{ id: string; name: string }>;
}

interface CurrentOrg {
  id: string;
  name: string;
  role: 'owner' | 'admin' | 'member';
  member: CurrentMember;
  permissions: string[];
}

interface User {
  // 现有字段
  id: string;
  nickname: string;
  // ...
  
  // 新增
  current_org?: CurrentOrg;
  orgs?: Array<{ id: string; name: string; role: string }>;
}
```

**缓存策略**：
- 登录后立即拉一次 `/api/auth/me`，存入 Zustand
- React Query / SWR 缓存 5 分钟
- 切换组织时调用 `setCurrentOrg()` → 重新拉 `/me` → 重置所有业务数据缓存
- 老板修改成员部门时通过 WebSocket 推 `member.changed` 事件，前端 invalidate 后重拉

---

## 九、UI 权限管理面板

### 8.1 成员列表

```
┌────────────────────────────────────────────────┐
│  组织管理 / 成员                  [+ 邀请成员]  │
├────────────────────────────────────────────────┤
│                                                  │
│  全部成员 (16)  按部门 ▾                         │
│                                                  │
│  ┌──────────────────────────────────────┐      │
│  │ 👤 王老板                             │      │
│  │ 老板 · 全部权限                       │      │
│  │                          [查看] [编辑]│      │
│  └──────────────────────────────────────┘      │
│                                                  │
│  ┌──────────────────────────────────────┐      │
│  │ 👤 李副总                             │      │
│  │ 副总 · 分管 [运营一部] [运营二部]      │      │
│  │                          [查看] [编辑]│      │
│  └──────────────────────────────────────┘      │
│                                                  │
│  ┌──────────────────────────────────────┐      │
│  │ 👤 王主管                             │      │
│  │ 运营一部 · 主管                       │      │
│  │                          [查看] [编辑]│      │
│  └──────────────────────────────────────┘      │
│                                                  │
│  ┌──────────────────────────────────────┐      │
│  │ 👤 张三                               │      │
│  │ 运营一部 · 员工                       │      │
│  │                          [查看] [编辑]│      │
│  └──────────────────────────────────────┘      │
└────────────────────────────────────────────────┘
```

### 8.2 成员详情面板

```
┌──────────────────────────────────────────────────────────┐
│  👤 张三                                          [×]    │
│  运营一部 · 员工                                          │
├──────────────────────────────────────────────────────────┤
│                                                            │
│  🏢 部门和职位                                  [✏ 编辑]   │
│  ├─ 主部门: 运营一部                                       │
│  ├─ 职位: 员工                                            │
│  └─ 自定义头衔: 高级运营                                   │
│                                                            │
│  ─────────────────────────────────────────────────       │
│                                                            │
│  ✅ 默认权限                                              │
│  根据职位和部门自动分配                                    │
│                                                            │
│  📦 运营角色                       [来源: 运营一部·员工]   │
│     ├─ 查看订单                                           │
│     ├─ 编辑订单                                           │
│     ├─ 创建定时任务                                       │
│     └─ 编辑/删除自己的任务                                 │
│  数据范围: 自己的数据                                      │
│                                                            │
│  ─────────────────────────────────────────────────       │
│                                                            │
│  ➕ 额外权限              [+ 添加]   (V2 启用)             │
│                                                            │
│  ─────────────────────────────────────────────────       │
│                                                            │
│  📋 审计日志                              [查看完整]      │
│  04-11 15:30  老板 设置职位为「员工」                      │
│  04-11 15:30  系统 自动分配「运营角色」                    │
│                                                            │
└──────────────────────────────────────────────────────────┘
```

---

## 十、迁移路径

### 9.0 前置：安装 ltree 扩展

migration 050 第一行：

```sql
-- migrations/050_org_departments.sql
CREATE EXTENSION IF NOT EXISTS ltree;

CREATE TABLE org_departments (
    -- ...
    path LTREE NOT NULL,
    -- ...
);
```

### 9.1 现有数据情况

| 表 | 字段 | 当前状态 |
|---|------|---------|
| `org_members` | `(org_id, user_id, role, status, permissions, invited_by, joined_at)` | role 只有 `owner` / `admin` / `member` |
| `permissions` 字段 | JSONB | 已存在但未使用，是预留字段 |
| `org_members` 表 | — | **保留不动**，新方案在新表 `org_member_assignments` 实现，不破坏现有逻辑 |

### 9.2 迁移脚本

```sql
-- ────────────────────────────────────────────────────
-- Step 1: 创建新表（按顺序执行）
-- ────────────────────────────────────────────────────
\i migrations/050_org_departments.sql        -- 含 CREATE EXTENSION ltree
\i migrations/051_org_positions.sql
\i migrations/052_org_roles.sql
\i migrations/053_permissions.sql
\i migrations/054_org_member_assignments.sql
\i migrations/055_position_default_roles.sql
\i migrations/056_user_extra_grants.sql      -- V2 启用，V1 只建表
\i migrations/057_user_revocations.sql       -- V2 启用，V1 只建表
\i migrations/058_permission_audit_log.sql   -- V2 启用，V1 只建表

-- ────────────────────────────────────────────────────
-- Step 2: 为每个现有组织初始化职位、角色、默认部门
-- ────────────────────────────────────────────────────
-- 由 Python 函数 initialize_organization() 执行（见 4.4 节）
-- 通过运维脚本一次性运行：
--   python -m scripts.init_existing_orgs

-- ────────────────────────────────────────────────────
-- Step 3: 把现有 org_members 映射到新的 assignments 表
-- ────────────────────────────────────────────────────
-- 老板（owner）→ position=boss, scope=all
INSERT INTO org_member_assignments (
    org_id, user_id, position_id, department_id, data_scope, is_primary
)
SELECT 
    om.org_id, om.user_id,
    (SELECT id FROM org_positions WHERE org_id = om.org_id AND code = 'boss'),
    NULL,         -- 老板不属于具体部门
    'all',
    TRUE
FROM org_members om
WHERE om.role = 'owner' AND om.status = 'active';

-- admin 和 member → position=member, scope=self（默认只能看自己）
-- 部门为空，由老板后续手动分配
INSERT INTO org_member_assignments (
    org_id, user_id, position_id, department_id, data_scope, is_primary
)
SELECT 
    om.org_id, om.user_id,
    (SELECT id FROM org_positions WHERE org_id = om.org_id AND code = 'member'),
    NULL,         -- 部门待分配
    'self',
    TRUE
FROM org_members om
WHERE om.role IN ('admin', 'member') AND om.status = 'active';
```

### 9.3 现有员工的部门归属

迁移后所有非 owner 的成员**没有部门归属**，权限 = 「只能看自己」（最安全的默认值）。

**过渡策略**：
- 老板登录后，前端在管理面板顶部显示横幅："X 个成员未分配部门，[立即分配]"
- 老板进入成员管理面板，逐个或批量分配部门和职位
- 未分配部门的成员仍然能正常用系统，只是数据范围限制为本人

### 9.4 与现有 `org_members` 表的关系

**两张表并存**：
- `org_members`：保留现有的 `role`（owner/admin/member），用于**全局控制权限**（添加成员、改 ERP 凭证等）
- `org_member_assignments`：新建，用于**业务数据权限**（看任务、看订单等）

**为什么不直接改 org_members**：
- 不破坏现有 36 张表的 OrgScopedDB 多租户隔离逻辑
- 迁移风险最小
- 未来可以再合并（V3 可选）

---

## 十一、实施分阶段

### Phase 1（V1.0 — 这次做）

**数据库**：
- ✅ 所有 10 张表全部建好
- ✅ 系统预设角色/职位/权限点初始化
- ✅ 现有数据迁移脚本

**业务逻辑**（硬编码极简版）：
- ✅ `PermissionChecker` V1（只判断 5 个职位）
- ✅ `apply_data_scope` SQL 注入
- ✅ 创建组织时自动初始化
- ❌ extra_grants 表数据可以存，但不参与运行时检查
- ❌ revocations 同上
- ❌ 缓存（直接 DB 查询）

**前端**：
- ✅ 成员管理面板（基础版：查看/编辑职位部门）
- ❌ 额外权限授权面板（V2）

**预估**：1.5-2 天

### Phase 2（V1.5 — 1 个月后）

- 启用 extra_grants 和 revocations 运行时检查
- 实现授权面板 UI
- 加视图切换器到定时任务面板
- Redis 缓存
- 审计日志查看

**预估**：3 天

### Phase 3（V2.0 — 50+ 人时）

- 临时权限到期 cron
- 审计日志按月分区
- 权限申请审批工作流
- 季度权限复核

**预估**：1 周

---

## 十二、关键文件清单

### 后端

```
backend/services/permissions/
├── __init__.py
├── permission_points.py         # 权限点常量
├── checker.py                   # PermissionChecker 类
├── scope_filter.py              # SQL 数据范围注入
├── effective_perms.py           # EffectivePerms 数据结构（V2）
├── audit.py                     # 审计日志写入
└── initialization.py            # 创建组织时初始化职位/角色

backend/api/routes/
├── org_members.py               # 成员 CRUD（含部门职位编辑）
└── org_permissions.py           # 权限管理 API（V2）

backend/migrations/
├── 050_org_departments.sql
├── 051_org_positions.sql
├── 052_org_roles.sql
├── 053_permissions.sql
├── 054_org_member_assignments.sql
├── 055_position_default_roles.sql
├── 056_user_extra_grants.sql
├── 057_user_revocations.sql
└── 058_permission_audit_log.sql
```

### 前端

```
frontend/src/components/admin/permissions/
├── MemberListPanel.tsx          # 成员列表
├── MemberDetailPanel.tsx        # 成员详情（部门/职位/权限）
├── EditAssignmentForm.tsx       # 编辑部门职位
├── PermissionTreeView.tsx       # 权限树状显示
├── ExtraGrantPanel.tsx          # 额外权限管理（V2）
└── AuditLogView.tsx             # 审计日志查看（V2）
```

---

## 十三、关键设计权衡

| 决策 | 选择 | 不选的原因 |
|------|------|-----------|
| 框架选型 | 自研 | Casbin/OpenFGA 对 16 人公司过度设计，自研 ~500 行可控 |
| 权限模型 | RBAC + 数据范围 | 纯 ABAC 调试困难，纯 RBAC 角色爆炸 |
| 副主管 | 权限同员工 | 简化矩阵，副主管只是 HR 头衔 |
| 部门嵌套 | 支持但 V1 平铺 | ltree 支持嵌套，但 V1 不暴露给用户 |
| 多部门归属 | 数据库支持，V1 单部门 | `is_primary` 字段预留，未来可扩展 |
| 缓存 | V1 直查，V2 加 Redis | 16 人不需要缓存，过度设计 |
| 审计日志分区 | V2 启用 | 16 人公司日志量小 |

---

## 十四、参考来源

- **Salesforce Profile + Permission Sets**: https://help.salesforce.com/s/articleView?id=sf.perm_sets_overview.htm
- **飞书职位与角色**: https://open.feishu.cn/document/server-docs/contact-v3/overview
- **AWS IAM 评估顺序**: https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html
- **Azure PIM 临时权限**: https://learn.microsoft.com/en-us/azure/active-directory/privileged-identity-management/
- **GCP IAM Conditions**: https://cloud.google.com/iam/docs/conditions-overview
- **Notion 权限透明 UI**: https://www.notion.so/help/sharing-and-permissions
- **PostgreSQL ltree 物化路径**: https://www.postgresql.org/docs/current/ltree.html
