# Agent Runtime S4-B：历史定时任务 Adoption

## 当前边界

历史 `scheduled_tasks` 不是可由字段回填直接切换的旧数据。Runtime profile 必须绑定来源
Action、Attempt、Run、模型/工具集/权限快照、request hash 与租户/Provider/Capability epoch。
没有这些事实的任务继续由 legacy scheduler 执行；删除 `ScheduledTaskAgent` 或放宽 profile
校验都会制造重复副作用或破坏任务语义。

## 第一批：只读 adoption preflight

`227_59_agent_runtime_scheduled_adoption_preflight.sql` 新增 owner-only、read-only RPC：

`read_agent_runtime_scheduled_adoption_plan_v1(org_id, include_inactive)`

返回稳定排序的分类、数量、task semantics hash、delivery target hash 和有限 reason code，
不返回 prompt、push target、Secret、Provider payload，也不写 `scheduled_tasks`、Runtime
Session/Command/Run/Action/Attempt。分类为：

- `runtime_owned`：已有 profile，继续走 Runtime Owner；
- `candidate_runtime_source_required`：active 且任务结构有效，但仍缺 Runtime 来源事实；
- `preserve_paused` / `preserve_error`：保持现状，不自动唤醒或重派；
- `blocked_running`：存在进行中执行，必须先 drain/readback；
- `blocked_partial_runtime_facts`：已有不完整 Runtime 身份，禁止猜测补齐；
- `blocked_invalid_task` / `blocked_unknown_status`：保持 legacy 或 unavailable，等待人工处理。

离线导出使用 `backend/scripts/dry_run_scheduled_runtime_adoption.py`，只读取 JSON 导出，
输出与 SQL preflight 相同的脱敏分类和哈希报告；默认不连接 PostgreSQL。

## 后续真实 adoption 门禁

只有在非生产快照中逐项证明租户、owner、schedule、payload、delivery target、model/toolset
facts 与权限语义不变后，才设计下一条 additive migration，使用正常 Runtime Action/Attempt/Run
创建来源事实，再调用现有 profile RPC。任何 `accepted/unknown`、运行中任务、部分 Runtime 事实或
无法重建 Provider/Capability 版本的任务都保持 blocked/unavailable，不能普通重派。

Rollback 仅删除 227_59 的两个函数；它不删除任务、profile、Run 或历史执行记录。

