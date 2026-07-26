# Worker 周期巡检与模型评分闭环

## 1. 背景与已验证根因

生产 Backend 使用两个 Uvicorn worker。每个进程都会启动
`BackgroundTaskWorker`，而企微重复账号巡检和模型评分只使用进程内时间戳节流，
因此同一周期会并发执行两次。

企微巡检在 Worker 角色撤销业务表直读权限后仍读取 `users` 和
`wecom_user_mappings`，并把权限异常转换为零，最终错误记录为巡检通过。

模型评分使用的公共 `compute_content_hash()` 生成 32 位哈希，而迁移 198 的提交
能力要求 64 位哈希。生产现有 606 个 Knowledge 节点均为 32 位，因此所有
`auto_applied` 评分无法提交。评分快照还把 `user_feedback`、`unknown`、`auto`
和工具执行指标当作模型生成质量，违反既有反馈信号语义。

## 2. 设计约束

- Worker 不恢复任何业务表直读权限。
- 企业、散客和系统 Knowledge 所有权规则保持不变。
- 媒体轮询、定时任务和其他已有租约链路不调整。
- 历史审核记录不删除；修复仅阻止新增重复记录。
- 周期任务失败不得伪装成功，并允许租约到期后重试。

## 3. 数据库能力

迁移 208 新增 `worker_periodic_job_runs`，主键为任务名和周期起点。仅允许
`model_scoring` 与 `wecom_dup_monitor` 两个任务名。

`worker_claim_periodic_job()` 根据数据库时间生成小时或自然日周期，以行锁和租约
原子领取。已完成周期返回 `completed`，有效租约返回 `busy`，失败或过期租约可重新
领取。执行者每分钟通过 `worker_renew_periodic_job()` 续期五分钟租约；
`worker_finish_periodic_job()` 只接受当前 token，成功标记完成，失败设置五分钟后
可重试。

`worker_wecom_identity_health_snapshot()` 仅允许无 Actor、无企业 Scope 的 Worker
调用，在函数内部统计：

- `created_by='wecom'` 但不存在 mapping 的孤儿用户；
- 相同 `(wecom_userid, corp_id, org_id)` 的重复身份组。

函数只返回计数，不返回昵称或企微标识。

模型评分 Snapshot 只保留 `chat/image/video` 的真实执行指标，并排除
`unknown/auto`。Commit 接受项目现有 32 位小写十六进制哈希，在同一租户、
模型、任务和指标窗口上使用事务锁与已存在记录检查实现幂等；Knowledge 节点写入由
owner 私有 helper 承担，Worker 不能直接调用。

## 4. 应用调用链

`BackgroundTaskWorker` 在本地时间门禁后先调用周期任务领取能力：

1. `claimed`：执行任务并提交成功或失败；
2. `completed`：同步本进程时间戳并跳过；
3. `busy`：跳过，等待持有者完成；
4. RPC 或任务失败：记录错误，不更新成功时间戳。

`WecomDuplicateMonitor` 使用显式全局 Worker Scope 调用快照 RPC。无效响应或数据库
异常向上抛出，禁止输出“通过”。

## 5. 失败、并发与恢复

- Worker 崩溃：租约到期后另一进程重新领取。
- 两进程并发：数据库主键和行锁只允许一个 token。
- 重复评分提交：Commit 返回 `already_recorded`，不重复写审核日志。
- 空数据：巡检返回零并通过；评分返回空并跳过。
- 部分模型失败：继续处理其他模型并记录失败数量，下一周期重新计算。
- 数据库不可用：周期不标记完成，错误进入现有告警链路。

## 6. 部署与回滚

先应用迁移，再部署应用。旧应用不会调用新增周期能力；新应用要求迁移存在，因此部署
门禁必须先验证三个新增 RPC 的签名和 Worker EXECUTE 权限。

回滚应用后可执行 208 rollback，删除新增周期表和企微快照能力。评分函数保留 208
的向后兼容实现，因为恢复迁移 198 会重新引入已证实的 64 位哈希故障和重复审核写入。
回滚不删除 Knowledge、指标或评分审核业务事实。

## 7. 验证

- SQL 合同：角色、Scope、ACL、指标过滤、32 位哈希和幂等检查。
- 单元测试：巡检成功、异常、无效响应；周期领取的 claimed/busy/completed/failure。
- 并发测试：同周期只能领取一次，过期租约可恢复，重复评分只写一次。
- 回归测试：模型评分计算、后台任务节流、Worker 数据库身份。
- 生产只读验证：巡检真实计数、评分 auto-applied 节点、重复审核增长和服务日志。
