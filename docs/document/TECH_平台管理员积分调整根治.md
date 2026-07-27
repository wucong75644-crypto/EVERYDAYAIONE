# 平台管理员积分调整根治

## 背景与约束

生产 `admin_adjust_credits` 仍归旧角色 `everydayai`，而 `users` 与
`credits_history` 已归 `everydayai_owner` 并启用 RLS。SECURITY DEFINER RPC
因此看不到真实用户并返回 `user_not_found`。充值、扣减和备注共用该事务入口。

修复恢复以下约束：只有数据库确认的当前平台超级管理员可以调整积分；余额更新、
非负校验、备注和操作员审计必须在同一事务内完成。

## 设计

1. 管理员脚本只接管
   `public.admin_adjust_credits(UUID, INTEGER, TEXT, UUID, UUID)` 的 owner，
   并拒绝未知 owner。转移事务立即撤销全部执行权，避免 migration 应用前出现提权窗口。
2. migration 220 在 `everydayai_owner` 下重建同签名 RPC，保持 API 合同不变。
3. RPC 校验 Runtime 会话、`runtime` access kind、`tenant_platform_admin()`，
   并要求参数操作员等于事务 Actor。
4. RPC 固定 `search_path`，撤销 PUBLIC、旧角色及非 Web Runtime 的执行权限。
5. 加减余额与 `credits_history` 写入保持单事务；扣减后余额不得为负，备注继续写
   `description`。

## 失败与回滚

权限或 Actor 不匹配以 PostgreSQL `42501` 失败关闭；用户不存在和余额不足继续返回
既有结构化原因。`run-migrations.sh` 仅在发现 migration 220 待执行时，通过服务器
本地 PostgreSQL 管理入口完成精确 owner 转移；随后执行 migration、应用启动与冒烟。

回滚时先执行 migration rollback；仅当应用数据库身份也恢复为旧角色后，才可设置
`CONFIRM_ADMIN_CREDIT_OWNER_ROLLBACK=1` 执行 owner 回滚脚本。回滚会恢复 migration
115 的函数行为，但在 owner 回切前仍保持 Runtime-only ACL，避免出现 owner 已提权
而 PUBLIC 可执行的危险窗口；整套回滚只用于整版恢复。

## 验证

- migration 契约、发现顺序和 rollback 映射；
- 超级管理员增加、扣减、备注落库与流水回读；
- 余额不足、用户不存在、伪造操作员和非管理员失败；
- 并发扣减不产生负余额或丢失更新；
- 生产 apply、rollback、reapply 演练及小额加减回环。
