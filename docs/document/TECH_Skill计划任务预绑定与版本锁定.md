# Skill 第二期第 4 步：计划任务预绑定与版本锁定

日期：2026-09-25。工作树 `codex/task/20260925022130-scheduled-skill-revision-lock`，代码基座 `f1654be19117610a09f3153b9b29452a9db5cb84`。已完成本地实施与验证，尚未提交、部署、合并或清理。没有接入 MCP，没有修改旧 Runtime 平台路径。

## 当前行为与边界

1. 创建或编辑任务时，用户显式提交 `skills: [{skill_id, revision}]`，最多 4 项。省略表示保留当前绑定（新任务为空），`[]` 明确移除。正文、组织、权限、工具名单、哈希和内部 revision ID 均不接受客户端设置；ChangeSet 中的 `skill_revision_snapshot` 只能由服务端生成或保留原值。新入口和仍开放的 legacy draft/PATCH 入口采用相同校验。
2. 服务端按任务执行所有者的活跃组织身份、业务权限、工具策略、`user/general/scheduled` 模式校验；管理员编辑他人任务不能借用管理员自身权限。Skill 元数据必须允许 scheduled，组织必须有 enabled assignment，选择必须是明确存在的 published revision。查询指定版本，不把请求替换成当前 assignment 指向的新版本。
3. `published` 不等于经过审核。264 新增不可变 `skill_revisions.reviewed`：新发布由数据库依据已审核草稿及匹配的整文件哈希生成；历史版本只凭受保护的草稿发布审计补齐证明。没有审核证明的旧导入版本不得用于计划任务，需按既有草稿审核流程发布新版本。
4. 任务定义增加 `skill_revision_snapshot`，记录 version、固定 candidate、revision UUID、整文件 SHA-256、正文 SHA-256、创建时的工具上限。多个 Skill 的上限与任务计划工具集合持续取交集；platform 工具策略也只保存绑定当时可用的工具，之后增加平台工具不会扩权。危险工具继续由 ToolPolicy 拒绝。
5. 每个配置存入 `scheduled_task_config_revisions` 不可变记录；任务的 `config_revision` 指向当前定义。业务配置变化生成新 UUID，领取、完成等记账不会制造配置 revision。领取时固定 `run_config_revision`；开始执行 RPC 返回该配置正文，执行器使用这一返回值，不能从当前任务重新解析 Skill。
6. 快速重试通过 `retry_config_revision` 保留失败运行的原配置；成功或不再快速重试时清除，随后正常周期使用当前配置。进程失效后的领取恢复也保留原配置。历史运行记录持久化配置 ID、完整定义与 Skill 快照，并拒绝原地修改这些字段。保留现有 running 状态下不能编辑/删除任务的规则；等待重试期间的配置编辑不会改写原重试快照。
7. 现有 ScheduledTaskAgent 继续使用共享工具循环。执行前按锁定版本读取正文和资源、核验哈希、注入方法并收窄广告和实际执行器；不读取会话绑定或最新目录。压缩后恢复相同正文。Skill 的必要权限传到工具调用上下文，执行期间每次工具调用仍会检查权限。
8. scheduled/preflight 模式禁止模型调用 `activate_skill`，不展示激活 schema；模型伪造调用也在控制分支和 ToolPolicy 拒绝。Actor scheduled checkpoint 保存同一 Skill 快照及精确渲染结果，恢复使用 checkpoint，不能替换为当前任务配置或重新发现目录。交互式会话行为保持不变。

既有已绑定 revision 废弃（deprecated）后仍可执行和恢复，但不能作为新选择添加；禁用、退役、assignment 撤销、身份或必要权限失效、文件/哈希漂移会阻止执行，不自动换版。无 Skill 的旧任务迁移为空快照，继续原有执行路径。沿用 `SKILL_CATALOG_ENABLED`、`SKILL_RUNTIME_ENABLED`、`VITE_SKILL_UI_ENABLED`；运行开关关闭时带 Skill 的任务停止，不能忽略约束执行。

## 接口与界面

- `GET /api/scheduled-tasks/skill-options[?task_id=...]` 返回当前有权用于该任务的已审核版本摘要；编辑时按任务所有者解析。没有正文、存储路径或权限声明。
- `POST /api/scheduled-tasks/changesets` 的 `definition.skills` 支持显式版本选择。现有风险、规划、确认和提交机制不变；规划和提交前再次校验。
- `POST /api/scheduled-tasks/drafts`、`PATCH /api/scheduled-tasks/{id}` 同样接收 `skills`。绑定失效返回 422；legacy 草稿确认前再次校验锁定快照。
- 原任务表单新增“固定 Skill”区域，展示已选版本，允许移除或显式更换。目录刷新不替换已选版本，迟到响应不能覆盖其他任务。未改绑定的编辑不重发选择，从而保留废弃但仍有效的旧绑定。

## 实施与验证地图

| 验证目标 | 证据 |
| --- | --- |
| 版本明确、更新产生新配置 | API 字段白名单；真实 PostgreSQL create/update/duplicate RPC；旧配置保持原快照 |
| 审核、组织、模式与工具权限 | 未审核 legacy 发布被拒、reviewed 不可伪改、组织不匹配、权限撤销、interactive-only Skill 与危险工具配置被拒 |
| 延迟和重试不追最新 | 领取后配置变化仍取原 definition；快速重试取原 config revision；成功后下一次取新配置 |
| Skill 状态与正文漂移 | 新版发布不影响旧 pins；废弃可继续；禁用/退役/撤销 assignment/正文漂移拒绝执行 |
| Actor 恢复 | checkpoint 固定 pins、原始渲染、工具交集；当前任务换版本仍恢复旧 checkpoint；跨模式和伪造 checkpoint 被拒 |
| 真实计划任务 Agent | 固定正文注入、实际广告与执行器工具交集、动态 activate 拒绝、执行期间权限撤销拒绝 |
| 迁移与历史 | 配置和运行快照不可原地改写；空绑定历史可逆迁移再升级；非空历史原子拒绝逆迁移 |
| UI | 显式选择/更换/移除、失败重试、迟到响应、4 项上限、只提交身份和 revision |

2026-09-25 本地结果：后端主批次 **1789 passed，6 skipped**，补充真实 headless Agent 测试 **1 passed**；合计 **1790 passed**。6 个跳过项为默认关闭的真实模型评估，没有调用外部模型。PostgreSQL 集成使用临时 Unix socket 集群，不读取项目数据库配置。前端 **23 passed**，TypeScript app/node 检查、改动文件 ESLint（0 warning）、`bash -n deploy/deploy.sh`、`git diff --check` 通过。表单错误路径测试有预期的错误日志，断言通过。

独立只读审查发现 legacy 创建会忽略选择，已修复并增加 HTTP 回归；同时统一绑定失败为 422。审查复核未留下高置信缺陷。未执行生产业务验收或真实模型业务正确性评估。

复验（backend，PATH 包含 initdb/pg_ctl，使用已有 Python 环境）：

```bash
DATABASE_URL=postgresql://unused JWT_SECRET_KEY=skill-tests-only python -m pytest \
  tests/test_skill*.py tests/test_scheduled_skill*.py tests/test_scheduled_task*.py \
  tests/test_scheduler_scanner.py tests/test_chat_execution_engine.py \
  tests/test_chat_generation_executor.py tests/test_chat_gateway_retry_integration.py \
  tests/test_chat_tool_mixin.py tests/test_chat_tool_loop.py tests/test_tool_policy.py \
  tests/test_tool_execution.py tests/test_tool_production_integration.py \
  tests/test_conversation_commands.py tests/test_replay_checkpoint_store.py -q
```

## 生产验证步骤（尚未执行）

1. 用户明确“提交部署”后，通过 `deploy/release.sh` 发布当前任务并执行 264。新迁移复用受控发布的 scheduled-task-drain：在锁内等待任务完成并停止旧进程，然后事务应用迁移。迁移发现仍有 running 任务会拒绝，不自行终止业务运行。核对三个 Skill 开关及服务恢复。
2. 在测试组织审核发布允许 scheduled 的只读 Skill A v1，任务表单明确选择 v1。查看任务定义和运行历史，确认 `config_revision`、revision UUID、正文哈希和工具上限已保存，真实结果采用 v1 方法。
3. 发布 A v2，重复执行旧任务应仍为 v1；显式更换版本并确认新配置，config revision 应改变，新执行使用 v2，旧运行历史仍显示 v1。执行中编辑应沿用现有冲突响应。
4. 使用测试任务制造一次可重试失败，在等待重试期间编辑到 v2；快速重试仍使用 v1 配置，成功后正常周期才使用 v2。检查多个运行历史中的配置 ID 与正文哈希。延迟领取和进程恢复的底层边界已由本地真实数据库测试覆盖。
5. 废弃 A v1，已有绑定仍能执行；新建任务不能选它。禁用测试 Skill 或撤销任务所有者必要权限后，执行/恢复必须停止，不调用工具。恢复权限后仍按原版本运行。管理员编辑他人任务、跨组织选择和危险工具均应被拒绝。
6. 让 scheduled 模型尝试 `activate_skill`，确认返回拒绝、没有新 Skill 激活或会话绑定。检查无 Skill 的旧任务仍可运行，企微/网页交付和既有确认流程正常。Actor checkpoint 的精确恢复在本地验证；当前生产计划任务仍走独立 headless 执行器。

## 回滚点与兼容限制

代码基座为 `f1654be19117610a09f3153b9b29452a9db5cb84`，回退也需用户授权并通过受控发布入口。工作树及任务分支保持保留。

**不能只回退旧代码而保留 264 schema，即使绑定历史为空也不行。** 旧 `SkillRevision` 禁止额外字段，仓储 `SELECT r.*` 将读到新增 reviewed 列而报错；旧代码也无法解析含 scheduled_snapshot 的 Actor checkpoint。

- 尚无任何非空 Skill 配置历史，且没有 running 任务时，可以在停止入口和排空执行后，受控执行 `rollback/264_scheduled_skill_snapshots_rollback.sql`，再回退代码。须先确认没有需旧代码恢复的 scheduled Skill checkpoint。反向迁移不删除任务或运行数据，但撤去本期新增的空绑定配置表及列。
- 已有绑定历史时，反向迁移原子拒绝，不能删除配置历史或 checkpoint 来绕过。保留 264 schema 与兼容 reviewed/快照的新代码，使用前向修复，或单独准备保留历史的兼容回退候选。暂停新增调度并保留原 revision/NAS/历史数据；关运行开关会阻止带 Skill 任务，不会把它降级成无约束执行。
- 生产业务验证尚未执行；本说明不授权发布、逆迁移、合并或清理。

## 改动文件

- `backend/api/routes/scheduled_tasks.py`
- `backend/migrations/264_scheduled_skill_snapshots.sql`
- `backend/migrations/rollback/264_scheduled_skill_snapshots_rollback.sql`
- `backend/services/agent/scheduled_task_agent.py`
- `backend/services/handlers/chat/execution_engine.py`
- `backend/services/scheduler/scheduled_task_change_adapter.py`
- `backend/services/scheduler/scheduled_task_workflow.py`
- `backend/services/scheduler/task_executor.py`
- `backend/services/scheduler/task_submission.py`
- `backend/services/skills/context.py`
- `backend/services/skills/contracts.py`
- `backend/services/skills/repository.py`
- `backend/services/skills/runtime.py`
- `backend/services/skills/scheduled.py`
- `backend/services/tools/policy.py`
- `backend/tests/test_scheduled_skill_api.py`
- `backend/tests/test_scheduled_skill_postgres.py`
- `backend/tests/test_scheduled_skill_snapshots.py`
- `backend/tests/test_scheduled_task_deploy_drain.py`
- `deploy/deploy.sh`
- `docs/document/TECH_ActorSkillRuntime.md`
- `docs/document/TECH_SkillCatalog与可见目录.md`
- `docs/document/TECH_Skill统一运行机制.md`
- `docs/document/TECH_Skill计划任务预绑定与版本锁定.md`
- `frontend/src/components/scheduled-tasks/ScheduledSkillPicker.tsx`
- `frontend/src/components/scheduled-tasks/TaskForm.tsx`
- `frontend/src/components/scheduled-tasks/__tests__/ScheduledSkillPicker.test.tsx`
- `frontend/src/components/scheduled-tasks/__tests__/TaskForm.test.tsx`
- `frontend/src/services/scheduledTask.ts`
- `frontend/src/types/scheduledTask.ts`
