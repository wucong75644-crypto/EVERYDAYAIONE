# 定时任务：代码核验与增量升级边界

日期：2026-09-12。升级基准：`75fced912ce1ee30668b1a588043b26120ee1e8d`；被测版本为该 HEAD 加当前任务未提交差异，见 [源码指纹](scheduled-task-upgrade-evidence/source-checks.json)。

状态：**用户明确“开始开发”后，A–D 已在原系统增量实现；本地验证完成，未提交部署。** 体验目标见 [交互简化方案](PROPOSAL_定时任务交互简化.md)，逐项证据见 [验收记录](SCHEDULED_TASK_UPGRADE_ACCEPTANCE.md)。第 3 节保留升级前的诊断事实，第 8 节描述最终接口和部署约束。

## 1. 结论与实施边界

现有系统已具备自然语言解析、任务定义归一化、Planner、工具范围快照、ChangeSet、原子提交、幂等回执、任务认领、Agent 执行、积分结算、运行历史及可靠投递。可以在这些模块中升级，不需要另一套任务系统。

升级集中在：任务管理入口如何表达执行意图、哪些已检查变更可以提交、调度意图怎样与当前运行共存、提交回执如何到达聊天和任务中心。

不重写 ERP 引擎、文件/沙盒内核、媒体结算、ScheduledTaskAgent 的工具循环、积分计算或投递 Worker；不另建任务主表、第二套 ChangeSet、第二条权限执行路径或新调度平台。不删除旧导入和 API，不自动重建用户已有任务。

本轮是用户另行授权的定时任务行为升级，不将行为改变伪装为工具统一 07 的定义迁移，也不启动工具统一 08。

## 2. 从现有代码确认的复用点

| 现有模块 | 已有职责 | 增量调整位置 |
| --- | --- | --- |
| [chat_task_manager.py](../../backend/services/scheduler/chat_task_manager.py) | 查任务、自然语言解析、预填表单、提出管理变更 | 完整明确请求进入同一个任务变更服务；信息不足继续使用现有表单/补问；不直接写任务表 |
| [scheduled_tasks.py](../../backend/api/routes/scheduled_tasks.py) | 组织上下文、ScopedDB、API、时间解析、手动运行入口 | 聊天与页面调用相同服务；兼容旧请求默认行为和返回 DTO，统一恢复时间计算 |
| [scheduled_task_change_adapter.py](../../backend/services/scheduler/scheduled_task_change_adapter.py) | resolve/normalize/authorize/validate/diff/preflight/commit，生成 PlanRelease | 按实际操作和差异决定所需检查及确认，继续由现有 adapter.commit 提交 |
| [scheduled_task_workflow.py](../../backend/services/scheduler/scheduled_task_workflow.py) | create_plan、ScheduledExecutionPolicy、旧草稿兼容 | 复用计划生成与策略格式；内容未变时按契约复用原计划；旧草稿收尾接口保留 |
| [ChangeSet 服务](../../backend/services/changeset/service.py) | 状态流转、幂等确认、恢复 committing、统一提交结果 | 扩展受约束的直接提交条件，复用现有 `_commit` 与 `resume_committing`；人工 confirm 继续可用 |
| [任务提交 RPC](../../backend/migrations/249_scheduled_task_changeset_adapter.sql) | 锁任务、核验 revision、固定字段提交、幂等回执 | 新迁移扩展必要状态语义与回执，不重写或复制所有业务提交流程 |
| [scanner.py](../../backend/services/scheduler/scanner.py) | 到期扫描、原子领取、实例并发控制、超时恢复 | 补调度意图检查、启动边界和恢复时的条件更新 |
| [task_executor.py](../../backend/services/scheduler/task_executor.py) | 建运行记录、积分锁、执行 Agent、完成/失败、通知 | 只调整完成/失败对调度状态的处理及回执投影，保留业务执行和结算逻辑 |
| [scheduled_task_agent.py](../../backend/services/agent/scheduled_task_agent.py) | 授权快照校验、预算、ModelGateway、ToolExecutor、完成检查 | 原执行机制复用；减少创建前试跑不等于取消正式运行约束 |
| [ToolSpec](../../backend/services/tools/definitions/task.py)、[动作规则](../../backend/services/tools/action_rules.py)、[ToolPolicy](../../backend/services/tools/policy.py) | 统一声明并判断任务工具的实际动作、模式、缓存和执行资格 | 直接提交必须事先准确声明为有状态修改，不能继续冒充 proposal；不另建任务权限表 |
| [ToolResult](../../backend/services/tools/result.py)、[结果消费](../../backend/services/handlers/chat_tool_result_mixin.py) | 包装旧结果、投递、审计、保留元数据 | 把已有 ChangeSet 引用变成可持久化的展示内容，并区分提案与应用完成 |
| [任务中心](../../frontend/src/components/scheduled-tasks/ScheduledTaskPanel.tsx)、[TaskCard](../../frontend/src/components/scheduled-tasks/TaskCard.tsx)、[TaskForm](../../frontend/src/components/scheduled-tasks/TaskForm.tsx) | 任务列表、表单、按钮和结果页 | 调整当前组件的主路径与显示状态，不重做整个页面体系 |
| [ChangeSetCard](../../frontend/src/components/chat/message/ChangeSetCard.tsx)、[消息块渲染](../../frontend/src/components/chat/message/MessageContentBlocks.tsx) | 已有确认、结果、按 ID 读取与聊天引用渲染 | 简化普通任务呈现，保留详细视图与旧消息兼容；补生产者而非创建第二套卡片状态 |

## 3. 升级前的关键代码事实（75fced91 诊断快照）

### 3.1 “无需额外审批”已经存在，但流程仍固定等待

`DefaultRiskPolicy` 已返回 `requires_approval`。普通持久化变化可能是 medium/false；然而 `ScheduledTaskChangeSetService.complete` 检查通过后一律转为 `awaiting_approval`，同步 `propose` 路径也一样。

因此应打通现有决策结果与提交流程。不过不能简单看到 false 就自动执行：提案意图、缺少真实授权、历史等待确认记录都必须保持原含义。

目前适配器把所有 create 当作 `tool_scope_expanded`，某些管理操作还根据任务原来的收件人判定 external_effect。新的确认规则应根据本次实际变化构造事实；不能通过全局降低风险等级达到免确认。

### 3.2 直接提交需要同时扩展 Python 与数据库状态迁移

[Python 状态机](../../backend/services/changeset/state_machine.py) 和 [数据库 transition_change_set](../../backend/migrations/248_change_sets.sql) 都只允许 `preflighting → awaiting_approval → committing`。`ChangeSetService.confirm` 会记录 `user_confirmation`。

推荐新增受限制的检查完成后提交分支，复用同一个 `_commit`；既有人工确认分支保留。新分支要记录“明确操作请求 + 服务端政策允许”的真实来源，不能后台代点 confirm 并伪造用户已点击审批。

数据库的对应迁移也要检查此分支的适用范围及必要检查事实。不能只在 Python 放行，也不能对所有资源开放任意从 preflighting 到 committing 的通道。

### 3.3 任务工具目前声明的是提案，直接写入会改变 Policy 的输入事实

`manage_scheduled_task` 的 Spec 当前是 `effects=('proposal_or_form',)`、可缓存、允许 plan 模式；动作规则把 create/update/pause/resume/delete 都识别为 proposal，且工具声明包含 scheduled 执行模式。

所以不能仅让 Handler 在原声明下开始实际修改：必须让统一 Policy 在执行之前知道本次走提案还是实际提交，并据此检查模式、副作用、并发和缓存。服务端可信上下文确定可用路径，模型参数不能自行选择免确认或扩大授权。

特别要验证：plan 模式仍不会实际修改任务；preflight 不修改任务；已有 scheduled 授权快照不会因为交互改造而获得无人值守创建、恢复、删除其他任务的新能力。直接管理的启用范围不能从 interactive 自动扩展到其他模式。

### 3.4 暂停状态下“立即运行”后端已有支持

`POST /scheduled-tasks/{id}/run` 使用 `claim_scheduled_task_now` 原子领取，携带 `_manual_run` 与 `_previous_status`；成功和失败路径已处理原任务为 paused/error 时保持不自动启用。

TaskCard 当前仅在 active 时显示立即执行按钮。因此这部分优先接通已有后端能力，无需再写一套运行器。仍要回归两个用户同时操作、与 Scanner 同时领取及运行中重复点击。

### 3.5 “运行中暂停”不能只删除 task_running 判断

适配器校验与任务提交 RPC 都拒绝 running 状态的管理修改。定时认领和手动认领依靠任务主表 running 状态防重；运行记录是在领取后由执行器创建，二者之间存在正常的交接窗口。

若简单把运行中主表状态改成 paused，再恢复为 active，可能破坏当前防重复领取依据。只依赖是否已有一条 running 运行记录也不足以覆盖领取后尚未建记录的窗口。

建议在现有任务表补充可持久化的调度意图，与正在执行的状态协同处理；继续使用已有运行表记录本次执行。具体字段、兼容投影和回填需在该批迁移设计中确定，不新建运行系统。

成功 RPC 已有“只有仍为 running 才改下一状态”的保护，应保留和扩展。失败路径、超时恢复目前会按任务 ID 写回 active，必须一并加入最新意图与条件更新；不能只把成功路径测通。

### 3.6 已有聊天引用和持久化协议可以继续使用

前端已经支持 `type='changeset'` 与 `change_set_id`，ToolResult 的 metadata 及 v1 结果载荷也能保存合法 JSON 元数据。缺口是聊天结果生产/消费没有把暂停返回的引用送入消息内容。

优先用现有 ChangeSet 引用、消息内容块及统一结果投递补齐，而不是保存另一份独立的审批状态。旧表单引用继续通过 `attach_chat_form_changeset` 收尾；该 RPC 只处理表单，不能错误地用它替代无表单消息的内容写入。

普通管理结果应终止模型对操作状态的自由续写或绑定权威回执，不把工具调用 success 当成业务 applied。实时、历史和调用重放都应重建同一个引用；重复回放不得重新执行管理动作。

### 3.7 减少完整试跑仍必须生成有效执行授权

ScheduledTaskAgent 会拒绝缺失、格式错误、版本不符或空工具列表的 execution_policy。创建与更新目前通过 `_build_release` 调用既有 `create_plan`，再生成 PlanRelease。

因此“一句话创建”不意味着直接把 prompt 和 cron 写入任务表。仍要复用 Planner 的范围校验和快照生成。只改名称/时间时可以在确认工具、数据、内容未改变后复用有效快照；新内容不能无条件复用旧计划。

完整试跑是否可省，由拟议的普通任务条件及必要检查决定；不能通过设置空策略、移除完成门槛或返回假预检成功实现。

### 3.8 恢复一次性任务存在入口差异，需明确契约

HTTP 恢复会把已过去的 run_at 提升为当前时间；聊天 `_propose_chat_change` 则保留原 run_at。上一版提出的“过去的一次性任务先选择新时间”是明确的行为调整，不是已经实现的逻辑。

增量升级时应将计算规则收敛到现有共享调度归一化位置，旧入口按兼容约定处理；不在页面、聊天和 RPC 各复制一份新规则。

## 4. 推荐的同一条服务链

```text
聊天 / 任务面板 / 兼容 API
  → 现有任务变更适配器：解析、归一化、鉴权、必要检查与范围快照
  → 现有 ChangeSet 保存检查、差异与提交依据
       ├─ 明确请求且规则允许：受约束地进入 committing
       └─ 需要额外确认或仅请求提案：保留 awaiting_approval → confirm
  → 同一个 adapter.commit / 任务提交 RPC / 幂等回执
  → applied 或明确失败/冲突
  → 同一结果消费、消息引用与任务列表刷新
```

直接提交前重新检查任务权限、最新资源状态与版本，消息/会话绑定也必须先验证。现有 `/changesets` 在提出方案后才处理部分表单引用；引入实际提交时，不能在验证调用方是否有权绑定该会话之前就执行依赖该请求的业务修改。

保留现有 HTTP 路径与 ChangeSet DTO。新交互若需要表达“检查后提交”的执行意图，可以增量扩展可选字段或可信服务端调用参数，缺省仍兼容旧提案调用；该值只能表达意图，不是审批凭证。既有工具名和合法参数继续可用。

旧的待确认 ChangeSet 和打开的旧表单仍按原承诺等待确认，不随部署自动应用。当前直接创建接口的 409 护栏不能简单移除后直写数据库；有需要也必须进入同一个受控服务链。

## 5. 调度状态升级的必要联动

需要共同覆盖五个位置：提交暂停/恢复、到期认领、手动认领、成功/失败结束、超时恢复。

- 暂停先于启动边界生效，尚未启动的工作不得继续启动；已经启动则显示本次继续。
- 运行中恢复只改变之后的安排，不重置当前执行占用，不让第二个 Worker 再次启动同一任务。
- 旧运行结束不得覆盖用户更新后的调度意图。失败重试、手动运行及超时恢复也遵守同一规则。
- 主表现有 revision 会随所有更新递增。定义版本与运行状态的分离可以在这批迁移统一处理；至少必须保持真正的并发修改冲突检测，不能为了方便删除 revision 校验。
- 回填历史 running 行时不能一律认定它原来启用了定时；手动执行暂停任务也可能处于 running。应依据可靠持久化来源处理，无法判断的运行需在切换前受控完成并核实，不能凭猜测开启调度。

“停止本次”可复用 Agent 已有 cancellation_event 参数，但目前手动运行和 Scanner 启动路径没有展示一条完整的跨进程取消控制链。该能力需要补请求传达与运行归属核验，不能宣传为已有按钮接上线即可，也不应通过杀整个 Worker 实现。它不应阻塞先修复普通暂停/恢复。

## 6. 迁移、兼容和回退

按兼容扩展执行，不修改已应用的历史迁移文件。新迁移编号实施时以最新目录确定。

ChangeSet 新增提交分支时同时更新 Python 和 SQL 的迁移规则及条件检查，保留原状态枚举、DTO 和人工确认路径。新字段使用有版本的已有快照或兼容扩展，缺失时按旧行为处理，不能默认授予自动提交。

调度意图变更需先提供兼容读取和历史数据处理，再启用运行中暂停；避免老 Worker 忽略新暂停意图写回 active。切换必须覆盖所有相关进程，必要时短暂停止领取并让在途任务完成。不是删除任务或重新创建任务。

关闭新交互可以恢复原提案流程，但不能回到不识别已写入调度意图的老 Worker。回退需保留兼容读取/写回保护，或先受控结束在途运行并将状态转换成旧代码可正确理解的形式。不得以回退为由自动恢复用户暂停的任务。

旧草稿、表单、ChangeSet 记录、结果 v1 载荷、运行历史和已有投递记录继续可读；不为简化界面清空历史，不改变 ERP、文件/沙盒、媒体业务契约。

## 7. 推荐批次与验证依据

| 批次 | 修改范围 | 必须证明 |
| --- | --- | --- |
| A：结果真实 | 现有 ToolResult 消费、ChangeSet 引用、聊天卡与历史持久化 | 提案不报已生效；实时、刷新、旧 helper、Actor 回放一致；回放不重做业务 |
| B：普通直接管理 | ChangeSet 受约束提交分支、任务适配器、任务动作 Policy、现有按钮/API | 普通暂停/恢复一次完成；高影响/提案仍确认；无伪造审批；组织与用户隔离；幂等、冲突、plan/preflight/scheduled 模式不扩权 |
| C：运行中暂停 | 原任务状态、认领、完成/失败、超时恢复及必要兼容迁移 | 暂停与启动竞态；恢复不重复领取；失败、超时和进程恢复不重开已暂停任务 |
| D：一句话创建与简化表单 | 原解析与表单、计划生成、按需检查、简洁卡片 | 普通完整请求无需二次确认；缺项补齐；合法快照不缺失；新旧创建/API 与真实执行一致 |

暂停时手动运行的页面接通可纳入相应批次，复用现有 run API。停止当前运行作为单独可验证的小项，完成必要控制连接后再开放。

现有测试基础包括 `test_changeset_core.py`、`test_changeset_api.py`、`test_scheduled_task_changeset_adapter.py`、`test_scheduled_tasks_routes.py`、`test_scheduled_task_executor_delivery.py`、`test_scheduled_task_agent.py`、迁移测试，以及 TaskCard/TaskPanel/ChangeSetCard/FormBlock 的前端测试。适配器测试已经覆盖幂等重复、键冲突、版本冲突和不写旧草稿；执行器测试已有暂停任务手动执行失败后保持暂停的场景。

实施时在这些测试旁补充新行为及跨层回归，并做真实 PostgreSQL 锁、重复提交、迁移兼容测试；仅靠 SQL 字符串断言不能证明竞态安全。生产验证按确定版本覆盖普通 ERP 报表创建、手动运行、暂停、恢复、到点运行与投递。

上述批次已顺序完成；精确命令、失败复验和证据见验收记录。以下为最终实现。

## 8. 已实现的接口与运行机制

### 提交契约

`POST /scheduled-tasks/changesets` 增量接受 `submission_mode: proposal | apply_if_allowed`，缺省 `proposal`；原 `begin/propose`、ChatTaskManager 三个位置参数、旧草稿/表单/API 继续有效。新任务面板和带 `_submission_mode` 的新聊天表单表达 `apply_if_allowed`，原表单保留提案。`parse-nl` 增加可选 `explicit_fields_only` 和 `operation`；旧解析仍保留预填行为。

模型工具 schema、名称和参数别名不变。可信 interactive/model 上下文且非 plan 模式才启用直接管理，模型 JSON 不能绕过此条件。动作规则先将修改声明为 `business_write`；直接管理禁用结果缓存并串行执行。Spec 的 effects 增加 `task_definition`，其余 schema/域/风险/并行定义不变。plan、preflight、scheduled 和旧 helper 不获得无人值守管理权限。

适配器仍完成 normalize → authorize → validate → Planner/PlanRelease → preflight → 原 commit。明确、已授权、个人通知、普通预算和只读范围的请求进入受约束的 `preflighting → committing`；Python 与迁移 254 同时检查版本、发起人、操作、风险和检查记录。记录 `request_accepted`，不伪造 `user_confirmation`。历史 awaiting_approval 记录不会自动执行。

`scheduled_task.submit.v1` 存在原 policy_snapshot.submission 中，含模式、actor_id、request_hash；请求哈希不含暂停/恢复时变化的运行字段。相同 key 重试先回放原回执，再做时间归一化。新恢复规则由 `resume_time` 在 adapter.normalize 统一调用：循环任务取下一次，过去或无效的一次性任务要求新时间；旧入口保留旧约定。

只改名称/时间时复用并重新校验原 PlanRelease；普通任务仅省略完整 Agent 试跑，仍调用现有 ToolRuntime 检查实际任务所有者的组织成员资格、可用工具与授权范围。需要确认的直接请求同样先检查所有者范围，再运行原只读试跑。正式运行仍执行原 ScheduledTaskAgent 和权限快照。旧 data_scope 仍是 task_prompt 时，任意指令改写不能证明等价；确定的展示格式追加保留整个原指令并直接应用，其他改写展示差异确认。

### 调度状态与认领

迁移 255 在原 scheduled_tasks 增加 `schedule_enabled`、`run_token`、`claimed_at`、`run_manual`、`run_previous_status`、`run_claim_revision`。`status=running` 仍保持本次互斥占用；`schedule_enabled` 表示后续调度意图。

原两个 claim RPC 保留名字、参数和返回投影，认领时补 token。`start_scheduled_task_run(task_id, org_id, run_token)` 锁任务后检查 token：已启动重复请求只返回 already_started；未启动且被暂停则释放认领且不运行。暂停任务原本发起的手动运行允许启动，领取之后新的暂停请求则阻止启动。

暂停/恢复不释放 running 占用。原 success RPC 和新增窄接口 `finish_scheduled_task_failure` 统一按 task→run 顺序加锁，用 token 防止旧 Worker 覆盖新运行，用最新 schedule_enabled 决定完成状态。暂停后手动执行成功、失败和超时恢复都不自动启用定时。业务执行、积分计算和投递负载不重写。运行记账不增加定义 revision，真实定义/调度意图变更仍参与冲突检查。

结果事件增量携带 `task_status/schedule_enabled/next_run_at`，按组织发给任务所有者；前端刷新权威列表。聊天沿用 changeset 内容块和 ID 回读，停止模型自由续写操作状态。无新增独立消息审批状态。

### 上线与回退要求

1. 本次未执行生产迁移或发布。后续“提交部署”仍走受控 release 入口，并核实确定候选；不能沿用旧发布未完成的结论。
2. 停止新的任务领取和手动启动，让旧在途运行完成，随后停止全部旧 Scheduler/HTTP Worker；执行 254、255，再启动本版全部相关进程。不能混跑不认识 schedule_enabled 的旧 Worker。
   提交部署准备时已将此要求接入 `deploy.sh::prepare_scheduled_task_cutover`：本地后端检查成功后、同步后端前，使用本次发布锁所有者执行 `scheduled-task-drain.py`。脚本在运行数量非零时释放任务表锁并等待；数量为零时持有 `SHARE ROW EXCLUSIVE` 写锁停止四个原服务，杜绝空闲检查与停机之间的新认领。最长等待 15 分钟；超时不强制改任务状态、不执行迁移。停止失败则中止发布并保留现场。
3. 255 首次迁移遇到 running 行会拒绝执行，避免猜测历史暂停手动运行的调度意图。迁移可重复应用；不删除或重建任务，不重置已有暂停意图。
4. 关闭 `scheduled_task_direct_enabled` 可恢复提案交互，但保留新调度 Worker。需回退旧代码时先完成在途运行并停止进程，执行 255 rollback、254 rollback 后切回基准；回退也拒绝 running，已暂停状态保持。真实 PostgreSQL 已覆盖上/下迁移和拒绝条件。

停止当前运行尚未开放，需单独补全跨进程取消控制。店铺和业务时间范围继续由原任务指令及 ERP 执行边界解释；本轮不新增店铺授权模型，也不声称仅靠工具名单能证明任意指令改写等价。真实 ERP、模型表达和通知效果列入确定候选的用户生产验证。

### 2026-09-12 创建表单生产复验补充

上文升级已发布为 8773b8b7，254/255 已应用。随后发现 formFieldSchema 与后端既有载荷不一致：datetime-local 导致整块被丢弃，visible_when.not 被剥离。此次在前端统一协议入口补齐字段，WS 与 normalizeMessage 历史读取共用，不另设展示路径。

直接 create 的 description 从当轮上下文最后一条 user 提取；UserLayer 多模态的首个 text 为完整原话，后续生成的附件引用不混入。只创建局部参数副本，不改变调用 JSON、旧 API/计划模式或引入全局请求状态。严格解析使用独立 changes/evidence/recipient 契约，失败保持可编辑原文；明确的店铺模板标记同时由补全表单和原 ChangeSet normalize 边界校验，不能扩大为全店权限。所有实际创建仍经过原授权、规划、检查和 commit 链。

此次没有新迁移、停止/启动语义或通知改动；旧历史表单不改写数据，只修正读取兼容。当前修复尚未重新部署。[逐项复验证据与回退](SCHEDULED_TASK_FORM_FIX_ACCEPTANCE.md)。
