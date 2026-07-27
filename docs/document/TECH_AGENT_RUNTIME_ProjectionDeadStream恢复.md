# Agent Runtime Projection Dead Stream恢复

## 合同

兼容投影按`(session_id, projection_kind, event.sequence)`顺序推进。首个未delivered
Outbox即是后续claim门禁；dead首项不能被正常claim，但仍作为earlier gap阻塞后续项。
恢复不得跳过checkpoint、伪造Result或直接修改Message、Task、Delivery。

220_26提供只读list/get与人工requeue。调用者必须是`everydayai_runtime`、runtime
actor、active super_admin，且只能访问当前`tenant_org_id`；个人scope仅允许actor本人。
inspect不返回event payload、消息正文或业务表内容。

## 状态与审计

requeue只接受精确dead Outbox、expected recovery version、expected attempt count、
不可复用的recovery request ID、原因和not-before。成功在同一事务写入
`agent_projection_dead_recoveries`并把Outbox恢复为pending；attempt count、
last error和checkpoint永久保留。相同request仅在outbox、actor、reason、not-before
及全部expected字段一致时返回already_requeued，否则冲突。

恢复后的claim会把attempt从至少8增加到至少9；再次fail立即dead。再次恢复必须由新的
人工request产生下一条审计事实，不存在自动无限重试。

## 锁序与唯一入口

220_26将compat apply包装为：

```text
无锁定位 → Session → Outbox → Event → CompatCheckpoint → CompatResult
```

requeue采用同序并在加锁后完整复核关联、tenant、status、version、attempt、checkpoint
和Result。claim/fail只锁Outbox，不持有其他较早锁，因此不形成反向等待。

215通用claim收紧为仅audit；web_runtime/wecom只能由220_12有序compat claim领取。
rollback通过恢复重命名的215/220_12原函数，保持历史函数体和ACL。

## 权限与回滚

恢复审计表ENABLE/FORCE RLS且仅owner policy，所有登录角色无直表权限。inspect/requeue
只授权Runtime并在函数内调用`tenant_platform_admin()`；Worker、WeCom、Sync和PUBLIC
无执行权。所有新SECURITY DEFINER固定`search_path=pg_catalog, public`。

220_26 rollback在存在任意恢复审计事实时失败关闭；无事实时删除新RPC、审计表和字段，
并精确恢复旧claim/apply函数。当前阶段不接API、UI、startup或ingress。
