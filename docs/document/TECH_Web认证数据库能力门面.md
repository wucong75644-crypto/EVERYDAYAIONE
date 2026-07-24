# Web 认证数据库能力门面技术设计

> 状态：153/154 数据库部分已完成代码准备，Python 接入待开发
> 所属任务：数据库租户纵深防御 5.3b.2c-2
> 日期：2026-07-23

## 1. 项目上下文

### 架构现状

- FastAPI 公开认证路由通过 `AuthService` 直接访问 `users`、`organizations`、
  `org_members`、`credits_history` 和 `refresh_tokens`。
- 登录前尚未建立用户身份，但数据库连接仍应使用
  `everydayai_runtime + access_kind=runtime`，并保持 actor/org 为空。
- 登录后的普通业务通过 `DatabaseScope` 注入 actor/org；WeCom 消息面使用独立
  `everydayai_wecom_runtime`。
- 迁移 150/152 已提供角色与 Scope 校验基础；第二批 17 张表尚未启用 RLS。

### 可复用模块

- `DatabaseScope`、`ScopedDatabaseClient`：认证前 runtime 事务 Scope。
- `tenant_actor_user_id()`、`tenant_org_id()`、
  `tenant_database_role_matches_scope()`：数据库身份校验。
- `create_access_token()`、`create_refresh_token()`：JWT 与随机 refresh token 生成。
- AuthService 现有验证码、bcrypt 校验、错误映射与响应格式。

### 设计约束

- bcrypt、短信验证码和 JWT 留在 Python，不引入 PostgreSQL 密码扩展。
- 数据库只接收 refresh token 哈希，明文 token 永不落库。
- Web runtime 不获得 `refresh_tokens`、手机号用户检索、WeCom mapping/target 的直表权限。
- PUBLIC、WeCom runtime、Worker 不获得 Web 认证 RPC。
- 迁移 150–152 checksum 不变，新能力使用迁移 153。
- 本阶段不切换 Backend runtime，不开放管理员、企业治理、OAuth、Scheduler 能力。

### 潜在冲突

- `create_token_pair()` 当前直接插入 `refresh_tokens`，必须拆分“生成材料”和“持久化”职责，
  同时保持现有 WeCom OAuth 调用兼容。
- 公开认证依赖当前拿到未 scoped 的 Database，必须改为认证前 runtime Scope。
- `user_subscriptions` 无仓库建表迁移；生产只读审计已确认其真实字段与约束。

## 2. 生产只读 Schema 证据

- 17 张第二批表 owner 均为 `everydayai`。
- 17 张表 `relrowsecurity=false`、`relforcerowsecurity=false`。
- `user_subscriptions`：
  - `id UUID NOT NULL DEFAULT uuid_generate_v4()`
  - `user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE`
  - `model_id VARCHAR(100) NOT NULL`
  - `subscribed_at TIMESTAMPTZ DEFAULT now()`
  - `UNIQUE(user_id, model_id)`
- 审计只访问 pg_catalog/information_schema，不读取业务行。

## 3. 认证数据流

### 手机号/企业密码登录

1. 公开路由创建 `DatabaseScope(None, None, runtime)`。
2. `lookup_web_auth_candidate` 按精确手机号和可选企业名返回最小候选。
3. Python 验证短信或 bcrypt。
4. Python 生成 access token、refresh token 明文、哈希和到期时间。
5. `commit_web_login` 锁定用户，重新检查用户/企业/成员状态，更新登录时间并写入 refresh 哈希。
6. RPC 成功后才向客户端返回明文 token。

### 注册

1. Python 验证短信并生成密码哈希与 token 材料。
2. `register_web_identity` 在单事务创建用户、注册积分记录和 refresh token。
3. 手机号唯一冲突映射为现有 `ConflictError`。
4. RPC 成功后返回 user，Python返回 token。

### Refresh 轮换

1. Python 哈希旧 token，预生成新 token 材料。
2. `rotate_web_refresh_token` 对旧记录 `FOR UPDATE`。
3. 校验用户状态与有效期。
4. 重放时吊销该用户全部有效 token。
5. 正常时吊销旧 token、清理该用户过期/已吊销记录、写入新 hash。
6. 成功后 Python签发新的 access token并返回预生成的 refresh 明文。

### 重置密码与登出

- `reset_web_password`：短信验证后更新 password hash，并吊销该用户全部 refresh token。
- `revoke_web_refresh_token`：按精确 hash 吊销当前 token，不返回用户资料。

## 4. 数据库函数接口

### `_assert_web_auth_scope() -> void`

- `SECURITY INVOKER`
- 固定 `search_path=pg_catalog,public`
- 要求 `session_user=everydayai_runtime`
- 要求 `app.access_kind=runtime`
- 要求 actor/org 均为空
- 不授予任何登录角色

### `lookup_web_auth_candidate(phone text, org_name text default null) -> jsonb`

- `SECURITY DEFINER`
- 精确手机号；可选企业名时同时返回 active 成员候选。
- 仅返回：用户 id、昵称、头像、遮罩响应所需字段、password_hash、账号状态、
  企业 id/name/role/status。
- 不返回企业密钥、refresh token 或其他用户记录。

### `register_web_identity(...) -> jsonb`

输入：Python 预生成 user_id、phone、nickname、可空 password_hash、refresh hash、refresh expiry。
输出：现有用户响应所需字段。
事务：创建 user、credits_history、refresh_tokens。

### `commit_web_login(...) -> jsonb`

输入：user_id、可空 org_id、refresh hash、refresh expiry。
事务：锁定 user；重新检查 user/org/member active；更新 current_org/last_login；
写 refresh token。
输出：用户响应字段和可选企业上下文。

### `rotate_web_refresh_token(old_hash, new_hash, new_expiry) -> jsonb`

事务：锁旧 token；校验状态/有效期/用户；重放时吊销全部；正常时吊销旧 token、
清理旧记录并写新 token。
输出：`outcome`、`user_id`。错误不泄露 token 是否存在。

### `reset_web_password(phone, password_hash) -> boolean`

锁定精确手机号用户，更新 password hash，吊销该用户全部 refresh token。

### `revoke_web_refresh_token(token_hash) -> boolean`

精确吊销匹配且未吊销的 token；不存在时仍返回安全的幂等结果。

## 5. 第二批 RLS 分类

| 分类 | 表 | 策略 |
|---|---|---|
| 用户本人事实 | users、credits_history、credit_transactions、image_generations、detail_projects、user_memory_settings、user_subscriptions | `user_id=tenant_actor_user_id()`；users 使用 `id` |
| 会话父事实 | conversations | 复用 `tenant_conversation_visible` |
| 会话子事实 | messages | 经 conversation 父事实，并校验 org 一致 |
| 任务事实 | tasks | 复用 `tenant_task_visible` |
| 项目子事实 | detail_project_images | 经 detail_projects 父事实，并校验 user/org |
| 企业可见事实 | organizations | owner 或 active member，仅当前 org |
| 企业成员 | org_members | 当前 active 企业成员可读；治理写入本阶段不授权 |
| 企业配置 | org_configs | active member 可读；治理写入本阶段不授权 |
| WeCom 身份/目标 | wecom_user_mappings、wecom_chat_targets | 仅 owner 安全门面；登录角色无直表权限 |
| 认证事实 | refresh_tokens | 仅 owner 认证门面；登录角色无直表权限 |

所有表先 `ENABLE ROW LEVEL SECURITY`，本阶段不 `FORCE`。

## 6. 角色权限矩阵

### Web runtime

- 获得普通用户业务实际需要的 SELECT/INSERT/UPDATE/有限 DELETE。
- 获得六个认证 RPC 和既定普通 Web RPC EXECUTE。
- 不获得认证表、WeCom 表、管理员/ERP/Worker RPC。

### WeCom runtime

- 获得 152 三个安全门面。
- 获得已确认的 WeCom 消息 RPC。
- 不获得 Web认证、Worker/admin RPC。
- mapping/target/member 无直表权限。

### Worker

- 本迁移不扩张全局扫描权限；后续受控 Worker RPC 独立处理。

## 7. 边界场景

| 场景 | 处理 |
|---|---|
| 认证前 actor/org 非空 | 认证辅助函数拒绝 |
| 手机号不存在 | 返回统一未找到，不泄露其他账号 |
| 同手机号并发注册 | 唯一约束 + 冲突映射，只创建一次 |
| 候选查询后账号/企业/员工停用 | commit RPC 重新检查并拒绝 |
| refresh token 重放 | 吊销该用户全部有效 token |
| refresh 过期 | 拒绝，不写入新 token |
| 新 token DB 写入失败 | 整体回滚，明文 token 丢弃 |
| 密码重置成功 | 同事务吊销全部 refresh token |
| 登出重复调用 | 幂等成功 |
| corp/chatid 跨企业重放 | 继续由 152 门面拒绝 |
| 中途回滚 | 先恢复旧 AuthService/数据库角色，再撤销 153 |

## 8. 连锁修改清单

| 改动点 | 影响文件 | 同步内容 |
|---|---|---|
| 认证前 runtime Scope | `backend/api/routes/auth.py` | `get_auth_service` 包装 scoped DB |
| token生成与持久化分离 | `backend/core/security.py` | 新增 token material，保留旧兼容入口 |
| 六个认证 RPC | `backend/services/auth_service.py` | 替换公开认证直表操作 |
| logout直表更新 | `backend/api/routes/auth.py` | 调 AuthService 门面 |
| migration 153 | migrations + rollback | RLS、policy、grant、revoke |
| 测试 | auth/RLS/grant tests | 正常、冲突、重放、角色拒绝、rollback |
| 文档 | Overview/Index/Issues/TECH | 同步阶段状态 |

WeCom OAuth 仍使用旧角色路径；Backend runtime 切换前必须完成独立 OAuth 能力迁移。

## 9. 架构影响

| 维度 | 风险 | 应对 |
|---|---|---|
| 模块边界 | 中 | Python负责凭证验证，数据库负责事实原子性 |
| 数据流 | 中 | 候选查询与提交分离，但提交重新锁定检查 |
| 扩展性 | 低 | 精确手机号/token hash索引查询，无列表扫描 |
| 耦合度 | 中 | AuthService依赖固定 RPC；禁止通用认证查询 |
| 一致性 | 中 | 注册、登录提交、refresh轮换均单事务 |
| 可观测性 | 中 | 日志仅记录 user_id/outcome，不记录手机号、hash、token |
| 可回滚性 | 低 | 保留旧角色路径，先切连接再撤迁移 |

## 10. 方案对比结论

| 方案 | 优点 | 风险 |
|---|---|---|
| 单个万能认证 RPC | 表面原子 | 无法执行现有 bcrypt/SMS/JWT，职责混乱 |
| 全拆分查询/写入 RPC | 实现简单 | 可拼接权限、TOCTOU、多步半完成 |
| 候选查询 + 原子提交 | 保留 Python验证，数据库提交原子 | 需要明确两阶段协议 |

采用第三种方案。

## 11. 文件计划

新增：

- `backend/migrations/153_runtime_message_rls_and_auth.sql`
- `backend/migrations/rollback/153_runtime_message_rls_and_auth_rollback.sql`
- `backend/tests/test_runtime_message_rls_and_auth_migration.py`

修改：

- `backend/api/routes/auth.py`
- `backend/services/auth_service.py`
- `backend/core/security.py`
- 对应认证测试和数据库纵深防御文档。

不新增依赖，不修改公开 HTTP API。

## 12. 开发任务

1. 153a：认证 SQL 门面、第二批 RLS/policy、角色权限及 rollback。✅
2. 154：WeCom 消息 RPC owner-only core、安全门面与旧角色切换兼容。✅
3. 153b：Python token material 与认证前 scoped DB。✅
4. 153c：AuthService 六条认证链切换。✅
5. 153d：定向测试、全量回归、代码审查。进行中：生产 Schema-only 克隆库已由
   migrator 成功应用 150–154，真实角色矩阵 5 项通过；剩余联合回归和最终审查。
6. 155a：WeCom OAuth 数据库能力已完成代码准备。新增未登录企业 Scope 与已登录
   actor Scope 门禁、企业配置精确读取、原子登录、绑定/解绑/状态门面；跨用户企微
   身份不自动合并，返回 `MERGE_REVIEW_REQUIRED`，等待企业治理域提供管理员审核。
7. 155b：WeCom OAuth Python 已接入 scoped identity client；二维码配置、exchange
   secret、登录、绑定、解绑和状态不再直访保护表。旧账号合并服务已删除，绑定 RPC
   同事务提交 refresh hash 与登录活动。✅
8. 155c：OAuth 回调改为 Redis 60 秒一次性交接码；回调 URL 不再携带 token、user
   或 org，前端通过 POST + GETDEL 原子交换并在组件卸载时 Abort。重复、过期和非法
   payload 均失败关闭。✅
9. OAuth 联合回归与最终审查已完成：真实 PostgreSQL 验证历史 `org_id = NULL`
   的同 Corp 映射可在事务锁内安全归属当前企业，非空跨企业归属仍失败关闭；后端全量、
   前端全量、类型检查、生产构建和核心覆盖率均通过。✅
10. 后续：闭合管理员、企业治理、Scheduler、Worker 能力域。

## 13. 部署与回滚

部署：

1. 备份数据库。
2. 创建角色并安装独立 URL。
3. 执行第二批 owner 转移。
4. 连续应用 150–153。
5. 测试库执行认证、个人/企业 A/B、WeCom角色矩阵。
6. 后续能力闭合前不切换 Backend runtime。

回滚：

1. 服务恢复旧数据库 URL。
2. 回滚 153，撤销新角色权限和第二批 policy。
3. 回滚 152/151/150。
4. 确认无 FORCE RLS 后恢复 owner。
5. 保留全部业务事实与角色，不 DROP 表。

迁移 155 回滚只撤销 OAuth RPC 与 EXECUTE，不删除用户、映射、成员、token 或活动事实。
应用回滚必须先恢复旧 OAuth 数据库路径；跨账号冲突从未自动迁移，因此无需反向拆分用户。

## 14. 设计自检

- [x] 已加载架构、项目总览、问题清单和函数索引。
- [x] 已完成生产只读 Schema 审计。
- [x] 已覆盖认证、refresh、并发、停用和回滚边界。
- [x] 已纳入调用链连锁修改。
- [x] 不新增依赖或公开 HTTP API。
- [x] 新文件预计均低于 500 行，函数低于 120 行。
- [x] 不在权限矩阵闭合前切换 Backend runtime。
