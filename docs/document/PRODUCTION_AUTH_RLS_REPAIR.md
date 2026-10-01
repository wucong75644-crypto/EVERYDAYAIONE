# 2026-10-01 生产登录 RLS 权限修复

## 已验证根因

生产连接使用 `everydayai`，账号有表级读写权限，但没有 superuser、BYPASSRLS，
也不继承 `everydayai_runtime/worker/owner`。认证表属于 `everydayai_owner` 并启用 RLS；
旧策略只允许 tenant 角色。当前后台通过 LocalDB 直接查询，无 SET ROLE/tenant scope。
PostgreSQL 因没有适用策略把现存账号、企业和配置过滤掉。

用同一个生产数据库的管理连接只读验证：用户存在、active、有密码哈希；
企业存在、active、有 corp_id。业务账号相同查询返回 0 行。
验证码已通过，随后登录 404；密码登录因用户不可见返回 401；企业二维码因企业不可见返回 404。
现有 `/health/db` 只证明连接与查询成功，无法判断 RLS 是否隐藏了全部数据。

## 可追溯的数据库调整

- 源码 `d8f828c9de15269182154c0b61039ff22b932a87`，2026-07-24 22:48:30 +08，
  `feat: establish tenant-isolated runtime and config control plane` 引入 150～161。
- 153 `runtime_message_rls_and_auth` 的 SHA-256 为
  `88bd8e231c9a7f53dba4cfe94a4a21dcd0a992edf9d096e40957274e6ce6a5a8`，
  与生产 `schema_migration_ledger` 一致。该迁移建立认证表的 tenant-role RLS。
- 生产账本记录 150～161 于 2026-07-25 01:25:17.547787 +08 执行，
  执行标记 `tenant-cutover-150-161`。
- 2026-08-22 的 235/236/238 部分恢复传统后台访问，未覆盖本次完整认证链路。

以上能确认策略来源和迁移批次，不能据此确认故障首次发生时间或具体操作人员。
生产未启用 SQL 语句审计，当前发布历史不包含完整 tenant 分支祖先；
没有证据证明近期 Skill 开发关闭了账号或权限。

## 修复边界

266 增加仅适用于 `SESSION_USER='everydayai'` 的独立策略，保留原 tenant 策略：

| 表 | 允许的命令 | 当前调用 |
|---|---|---|
| users | SELECT / INSERT / UPDATE | 登录、注册、重置密码、登录时间 |
| organizations | SELECT | 企业状态、corp_id、配置解密密钥 |
| org_members | SELECT / INSERT | 归属判断、企业微信自动加入 |
| org_configs | SELECT | 自建应用配置 |
| refresh_tokens | SELECT / INSERT / UPDATE / DELETE | 签发、刷新、登出、清理 |
| wecom_user_mappings | SELECT / INSERT / DELETE | 查询、绑定、解绑 |
| credits_history | SELECT / INSERT | 注册积分赠送及 INSERT RETURNING |

这是当前可信后台、应用层身份/组织权限检查下的兼容恢复。业务数据库账号能读这些表的各行；
并非把数据库端 tenant 隔离方案重新接回当前架构。没有向浏览器开放数据库身份。
其他登录角色即使 SET ROLE everydayai，SESSION_USER 条件仍阻止获得新增策略。
不删除数据，不重置密码，不更改 owner、角色成员、表级 grant、RLS flags 或 BYPASSRLS。
`record_user_activity`、`wecom_get_or_create_user` 已有 EXECUTE 和 SECURITY DEFINER，保持原样。

迁移必须由管理账号执行。发布新增显式 `MIGRATION_EXECUTOR=local-postgres` 模式：
通过随机 session advisory lock 验证本机 postgres 与应用连接指向同库后，
仍在 release 受控锁、文件锁、事务、checksum 账本内执行。
默认 application 模式保留；不为迁移提升业务账号的数据库权限。

## 验证与发布

临时 socket-only PostgreSQL 使用真实 LOGIN 角色，而非管理员 SET ROLE 模拟应用：
先复现正确密码 401、已通过验证码 404 和二维码企业不可见，再应用迁移验证登录、
token 哈希存储、刷新/吊销、注册及积分赠送、企业配置解密和二维码 URL。
同时验证错误密码、错误验证码、停用账号仍被拒绝；不允许的写命令、其他登录角色、
SET ROLE、迁移重复执行、回滚与重执行、管理连接目标不匹配及失败后锁释放。
现有认证/二维码/刷新、发布服务和定时任务停机检查纳入定向回归。

生产自动复验仅输出数量/布尔值和 HTTP 状态，不输出手机号、密码、token 或配置值。
数据库写权限可在显式事务内探测并 ROLLBACK；不发送短信、通知或创建持久业务账号。
真实密码/短信/企业微信授权登录由用户在生产界面最终复验。
正式提交、发布门禁及生产结果以本次 release 的结构化结果和对话工具记录为准。

## 回滚与独立问题

266 rollback 只删除新增策略，原策略、数据和 RLS flags 不变；回滚后传统认证会再次不可用。
应用版本回退不会自动撤销数据库策略。若需撤销数据库修复，应在同样受控发布锁和事务内
显式执行 rollback，再核验账本；不能直接运行 deploy.sh 或全局关闭 RLS。

另见生产日志中的 `scheduled_tasks.run_token` 缺列、message_idempotency 清理权限、
error_logs RLS 写入失败。这些不是本次认证不可见的原因，本次未修复，不表示全站无其他问题。
