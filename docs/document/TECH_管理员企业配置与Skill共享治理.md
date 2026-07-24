# 管理员、企业配置与 Skill 共享治理

> 状态：156–157 治理基础保留；158 以后由统一配置与 Secret 控制平面设计取代
> 日期：2026-07-24
> 前置：迁移 150–155、数据库租户纵深防御、Web 认证与企微 OAuth 能力门面

> 说明：本文保留主体、治理授权和 Skill 共享需求；原 158–163 的存储与接线顺序不再
> 作为实施依据。后续以 `TECH_统一配置与Secret控制平面.md` 为准。

## 1. 目标与主体

系统保持三类业务主体和四层能力来源：

1. 平台全局管理员：管理平台默认配置、平台推荐 Skill 和跨企业治理。
2. 企业 owner/admin：管理本企业成员、企业配置和企业共享 Skill。
3. 企业员工：保留个人配置、个人 Skill 和个人 Workspace，同时使用企业发布内容。
4. 散客：只有个人配置、个人 Skill 和个人 Workspace，与企业数据完全隔离。

企业、个人与平台内容不能通过共享数据库角色直接访问。服务器上的用户 Workspace
等价于用户本地环境，但权限边界仍由 DatabaseScope、RLS、能力门面和文件系统共同执行。

## 2. 生产事实

2026-07-24 对生产数据库执行显式 `BEGIN READ ONLY` 审计并 `ROLLBACK`：

- 1 个 active 企业、50 个 active 成员、1 个 super_admin。
- `org_configs` 有 13 个配置键；未读取任何配置值。
- `users`、`organizations`、`org_members`、`org_configs`、`org_invitations`
  均由旧 `everydayai` 角色持有，未启用 RLS/FORCE RLS。
- 生产只有专项 `admin_adjust_credits`、`list_admin_user_assets` 管理函数，
  没有通用企业治理或全局配置能力门面。

## 3. 授权不变量

- 只有 `everydayai_runtime + access_kind=runtime + actor_user_id` 可调用 Web 治理门面。
- 全局管理员身份必须由数据库中的 active `users.role=super_admin` 证明。
- 企业治理必须同时满足精确 `org_id` Scope、active 企业、active 成员和允许角色。
- owner/admin 权限不能由请求参数、JWT 自报角色或前端可见性单独证明。
- WeCom runtime、Worker、PUBLIC 和无 actor 的认证前 Scope 均不得获得治理能力。
- 配置秘密值不进入审计、响应、URL、日志或 Skill manifest。
- 所有治理写操作在业务事实和审计记录的同一数据库事务内提交。

## 4. 实施波次

### 156a：治理授权根与审计账本

- 新建 `governance_audit_log`，启用 FORCE RLS，仅 owner 可直接访问。
- 新建 `_assert_governance_authority`，统一解析 `super_admin/owner/admin/member`。
- 新建 `_record_governance_audit`，只接受非秘密元数据。
- 不授予 runtime 内部函数 EXECUTE，不提供任何业务 CRUD。

### 156b-1：企业与成员只读治理门面

- 我的企业、超管企业列表、企业详情与更新。
- 本人企业、本人待处理邀请、企业安全详情和成员列表。
- 超管企业列表和精确手机号搜索。
- 企业响应不返回加密密钥或历史 Secret，手机号保持掩码。

### 157：企业、成员与邀请写治理门面

- 企业创建/更新、成员添加/移除/角色变更。
- 邀请创建和本人原子接受邀请。
- 写操作复用 156a 授权根并同事务写审计。
- 成员上限由企业行锁串行化；邀请按企业和手机号 advisory lock 串行化。

### 158：企业配置治理门面

- 固定 12 个受支持键，提供无密文状态和密文 upsert/delete。
- `wecom_corp_id` 继续由 `organizations` 唯一管理，不接受历史重复配置路径。
- Python 继续负责 AES 加密/解密；数据库只接受受支持键。
- runtime 不获得 `org_configs` 直接写权限。
- 删除不存在的键保持幂等且不写虚假审计；真实变更与审计同事务提交。
- ERP Token 自动刷新没有用户 actor，使用 Worker 专用能力，禁止伪装管理员写入。

### 159：Python/API 切换

- runtime 管理员与 actorless Worker 分别使用独立的 ERP Token 原子轮换能力。
- 拆分超限的 `org.py`、`OrgService` 和 `OrgManagePanel`。
- 现有 HTTP 路径与响应保持兼容，内部改用 scoped capability client。
- 删除治理链路对保护表的直接 CRUD。

### 160–163：配置继承与 Skill

- 160：`platform → organization → user` 有效配置、锁定和来源。
- 161：配置 API/UI 与审计投影。
- 162：平台、企业、个人 Skill Registry 与推荐安装事实。
- 163：按租户、用户、通道生成固定版本 Skill Catalog 并接入 Agent Runtime。

MCP、Plugin、Goal 后续复用 Registry、版本、授权、SecretRef 和审计，不另建权限体系。

## 5. 边界与回滚

| 场景 | 处理 |
|---|---|
| actor 缺失、账号停用 | 失败关闭 |
| 企业不存在或 suspended | 失败关闭 |
| 员工被禁用或移除 | 下一次调用立即失败 |
| 超管跨企业操作 | 数据库验证 super_admin，并写操作审计 |
| 两名管理员并发修改 | 写门面锁定目标事实；约束冲突返回稳定业务错误 |
| 审计写入失败 | 整个治理写事务回滚 |
| 配置值为空或未知 key | 参数错误，不写库 |
| 回滚 156 | 先恢复旧应用路径，再删除门面和空审计表；不删除企业业务事实 |

## 6. 验收

- 真实 PostgreSQL 角色矩阵覆盖 runtime、WeCom、Worker、PUBLIC。
- 企业 A 管理员不能读取或修改企业 B。
- member、disabled member、散客不能执行管理写操作。
- super_admin 可执行显式跨企业能力，但每次写操作都有审计。
- 配置密文和明文均不出现在日志、审计及 API 状态响应。
- 迁移 apply/rollback/reapply 和既有 API 回归全部通过。
