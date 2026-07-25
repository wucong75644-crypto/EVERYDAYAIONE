# Web 运行时角色切换闭环修复

> 状态：方案已确认，待实施验证
> 日期：2026-07-26

## 1. 已验证根因

迁移 `153_runtime_message_rls_and_auth.sql` 为 `everydayai_runtime` 建立了 Web
核心表的最小 ACL 和 RLS policy。随后
`deploy/transfer-runtime-message-ownership.sh` 在接管 owner 后对同一组表执行
`REVOKE ALL`，但没有重建迁移 153 的 Web ACL；最终切换检查也没有校验这组 ACL。
生产 Backend 已使用 `everydayai_runtime`，因此用户、企业、会话、消息、任务和订阅
接口统一以 `permission denied` 失败。

数据仍然存在，故障是访问能力断层，不是数据删除或代码回退。

## 2. 必须恢复的不变量

1. Web 请求只使用 `everydayai_runtime + access_kind=runtime`。
2. 每个已认证请求在查询前注入 JWT 主体和可选企业 Scope。
3. 普通用户只能访问本人及当前有效企业允许的事实。
4. 平台管理员跨企业读取必须由数据库重新验证 active `super_admin`。
5. WeCom、Worker、Sync 和 PUBLIC 不获得 Web 核心表权限。
6. refresh token、WeCom 身份映射及 Secret 表继续只允许受控能力访问。
7. owner 转移脚本再次执行后，Web ACL 仍与迁移合同一致。

## 3. 实施设计

### 3.1 请求级数据库 Scope

新增 FastAPI 请求数据库依赖：从已验证 JWT 提取 `actor_user_id`，从
`X-Org-Id` 提取可选企业 UUID，为现有 `Database` 调用统一包装
`ScopedDatabaseClient`。公开请求使用 actor/org 均为空的 runtime Scope。

`get_current_user` 显式使用 actor Scope；`get_org_context` 使用 actor + 请求企业
Scope 验证企业和成员。错误 UUID 在进入数据库前返回 400。

### 3.2 Web ACL 与平台管理员读取

新增不可变迁移 189：

- 重建迁移 153 已定义的 `everydayai_runtime` 精确 ACL；
- 新增 owner 持有的 `tenant_platform_admin()` 身份判断；
- 只为平台管理员增加必要的 SELECT policy，不开放通用跨租户写入；
- 撤销 PUBLIC、WeCom 和 Worker 对该判断函数的执行权。

企业及平台治理写入继续使用迁移 156/157 的能力门面；积分调整和资产查询继续使用
既有管理员 RPC。

### 3.3 部署合同

所有权转移脚本在统一撤权后必须重建 Web ACL。preflight/finalize 同时校验：

- Runtime 必需权限存在；
- Runtime 禁止权限不存在；
- WeCom/Worker 未获得 Web 表权限；
- RLS 与 owner 状态正确。

## 4. 兼容与失败语义

- HTTP 路径、请求体和响应结构不变。
- 缺失或非法 Scope 失败关闭，不回退旧数据库角色。
- 停用用户、停用企业和失效成员不能因恢复 ACL 获得数据。
- 迁移和 ACL 变更在事务内执行，失败不留下半授权状态。
- 不新增表、不改业务字段、不更新或删除业务行。

## 5. 验证

1. 散客、企业 A/B、企业管理员、平台管理员身份矩阵。
2. 历史会话、创建会话、发消息、任务、订阅、管理员列表。
3. 无 Scope、错误企业、停用成员和跨企业访问拒绝。
4. Runtime/WeCom/Worker/PUBLIC PostgreSQL 真实角色矩阵。
5. transfer 后 ACL、迁移 apply/rollback/reapply、部署 preflight/finalize。
6. WebSocket 消息终态及企微真实消息回归。

## 6. 回滚

先回滚应用，再执行 189 rollback，撤销新增平台管理员 policy、判断能力和 Runtime
ACL。回滚不修改业务数据。若旧 Backend 不能使用隔离角色，必须按既有受保护 Runbook
先恢复服务连接，不能通过扩大新角色权限替代回滚。
