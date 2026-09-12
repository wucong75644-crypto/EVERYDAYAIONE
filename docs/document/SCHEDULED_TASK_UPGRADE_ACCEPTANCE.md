# 定时任务增量升级：实施与验收记录

2026-09-12。**本地技术验证通过，未提交部署，用户生产验收待完成。** 这是用户在工具统一 07 后续诊断、调研后明确授权“开始开发”的行为升级；不是 07 机械迁移，也不是板块 08。

**提交部署准备补充：**用户随后已明确授权发布。已补受控部署的 255 切换步骤：等待运行完成、锁住任务新认领并停止旧服务后再同步后端和迁移；它不改业务数据。生产只读核验显示旧发布锁和空验收候选，旧本地 PID 38236 已退出、远端无部署/迁移/同步进程，四个服务 active、2 项任务 active、0 项 running、尚未应用 schedule_enabled。该现状不代表本版上线，最终候选和结果以本次受控发布回执为准。

发布准备复验：`test_scheduled_task_deploy_drain` 与原部署服务契约 11 passed，发布协调测试 10 passed，脚本语法检查通过；[切换证据](scheduled-task-upgrade-evidence/deploy-cutover-checks.txt)、[协调证据](scheduled-task-upgrade-evidence/release-coordination.txt)。已使用原协调器、原所有者完成旧锁状态恢复，结果 `RELEASE_RECOVERY_RESULT status=recovered prior_executor=stopped owner_verified=true candidate=invalidated`；没有盲目删锁或将旧部署当作成功候选。

## 1. 范围与版本

- 任务工作树：`/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-07`；分支 `codex/task/20260911215117-tool-unification-07`。
- 基准及当前 HEAD：`75fced912ce1ee30668b1a588043b26120ee1e8d`。**被测版本是此 HEAD 加当前未提交差异**，不是该 SHA 本身。准确文件指纹与完整代码 diff：[source-checks.json](scheduled-task-upgrade-evidence/source-checks.json)、[implementation.patch](scheduled-task-upgrade-evidence/implementation.patch)。当前升级尚无发布候选 SHA。
- 按 A 结果真实 → B 受约束直接管理 → C 调度意图和运行认领 → D 一句话创建及界面简化完成并逐批验证。复用原任务表、ChangeSet、Planner、ToolRuntime、ScheduledTaskAgent、运行记录和投递链。
- 明确新增行为：普通已授权请求检查后直接提交；旧提案入口默认保留；运行中可暂停后续定时；暂停任务可手动运行一次；新入口过去的一次性任务恢复需要新时间；直接管理不缓存。删除、收件人/范围扩大、增加成本仍需确认，权限拒绝不能由确认绕过。
- ERP 引擎、文件/沙盒内核、媒体结算、Agent 工具循环、积分计算和投递 Worker 不重写；源码保护路径逐项核验。停止当前运行未开放，不为它重建任务平台。产品及实际边界：[方案](PROPOSAL_定时任务交互简化.md)、[最终技术设计第 8 节](TECH_定时任务增量升级边界.md#8-已实现的接口与运行机制)。

## 2. 逐项证据

用例名以下相对 `backend/tests/`；前端明确标注。各文件的完整命令、实际结果和本地界面人工记录见 [COMMANDS.md](scheduled-task-upgrade-evidence/COMMANDS.md)，不把单个总数当作全部场景证据。

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
| --- | --- | --- | --- | --- |
| ST-A | 暂停提案不能显示已应用；实时、历史、旧结果和 Actor 回放具有相同 ChangeSet 引用 | 有效 ID 被送入 WS 和 checkpoint；终止模型自由续写状态；重复引用去重，非法元数据不阻止正常模型流程 | 通过 | `test_scheduled_task_chat_results` 的三组用例；`test_tool_result_consumption`、`test_tool_result_persistence_06`；[A 日志](scheduled-task-upgrade-evidence/batch-a.txt) |
| ST-B1 | 普通明确请求一次提交；无虚构审批；默认/旧请求继续提案 | 同一 adapter.commit、原提交 RPC 和 receipt；request_accepted 事件；历史 awaiting_approval 不自动应用；同键重放不重复提交，异请求同键冲突 | 通过 | `test_scheduled_task_direct_submission::test_apply_requested_has_real_receipt_and_no_forged_confirmation`、`test_direct_submission_fails_closed`、`test_direct_pause_uses_same_commit_and_replays_after_task_revision_changes`；[B 日志](scheduled-task-upgrade-evidence/batch-b.txt) |
| ST-B2 | 权限、计划模式、内部调用、组织/用户隔离不扩权；成本和范围变更可审查 | 可信 interactive/model 才可直接管理；plan/preflight/scheduled/legacy 保留原边界；静态执行范围和完整预检按任务所有者复核；复杂 cron、增加预算/重试/时长需要确认 | 通过 | `test_trusted_mode_changes_policy_before_handler`、`test_assessment_keeps_cost_scope_and_external_changes_for_confirmation`、`test_usage_checks_follow_actual_cron_and_creation_budget`、`test_manager_edit_checks_runtime_as_task_owner_and_fails_closed_if_missing`；数据库 actor/org 拒绝矩阵 |
| ST-B3 | Python 和数据库共同约束直接提交，不通过另一路径绕过权限 | 缺 submission、requires_approval、高风险、错 actor/org、缺检查均拒绝；原确认路径及回退保持 | 通过 | `test_scheduled_task_upgrade_postgres::test_database_enforces_direct_submission_evidence`（8 情形）、`test_migration_reapply_and_rollback_preserve_old_confirmation` |
| ST-C1 | 暂停与启动竞争、运行中暂停/恢复不破坏互斥 | 领取后尚未启动则暂停阻止执行；已启动则继续且不能被重复 starter 撤销；恢复保留原 token；两连接同时领取只有一个成功 | 通过 | `test_pause_between_claim_and_start_blocks_work`、`test_running_pause_survives_success_failure_and_recovery`、`test_concurrent_claims_start_once_and_pause_wins_when_locked_first`；[事务复验](scheduled-task-upgrade-evidence/final-postgres.txt) |
| ST-C2 | 成功/失败/超时恢复不重新启用已暂停任务，旧 Worker 不能覆盖新运行 | 成功、失败、stale 三路径均保持暂停；暂停任务手动执行成功/失败均保持暂停；旧 token 完成/失败均被拒绝 | 通过 | `test_manual_run_does_not_resume_paused_task`、`test_resume_running_keeps_claim_and_old_worker_cannot_finish_new_run`、`test_scheduler_scanner`、`test_scheduled_task_executor_delivery`；[C 日志](scheduled-task-upgrade-evidence/batch-c.txt) |
| ST-C3 | 数据迁移、回填、回退保留用户调度意图 | 255 首次迁移与回退均拒绝仍在运行的历史任务；重复应用不重置意图；回退保持暂停；没有任务重建 | 通过 | `test_lifecycle_migration_reapply_and_rollback_guards`（真实迁移 069/071/242/244/245/249/254/255）；[迁移](../../backend/migrations/255_scheduled_task_schedule_intent.sql)、[rollback](../../backend/migrations/rollback/255_scheduled_task_schedule_intent_rollback.sql) |
| ST-D1 | 完整普通创建免重复确认；缺项只补齐；时间/原指令不被默认替换 | 保留原提示词中的业务范围；缺时间不默认 09:00，未识别收件人不默认改为自己；一次性任务必须具体日期，不重复要求 time_str；完整任务生成原格式 PlanRelease，普通只读不执行 Agent 试跑 | 通过 | `test_scheduled_task_simplified_creation` 的 strict parser、complete/incomplete request、ordinary create、one-shot/date 用例；[D 日志](scheduled-task-upgrade-evidence/batch-d.txt)及[最终边界](scheduled-task-upgrade-evidence/final-contracts.txt) |
| ST-D2 | 修改只作用于明确字段；恢复不补跑；重试回执真实 | 修改时间保留 prompt/原计划且不调用模型重规划；展示格式追加保留全部原指令；任意改写/追加店铺需要确认；过去的一次性任务新请求拒绝，同键已成功请求先回放 | 通过 | `test_edit_time_keeps_definition_and_revalidates_saved_plan_without_model`、`test_presentation_additions_keep_data_scope_but_arbitrary_rewrites_do_not`、`test_resume_past_one_shot_requires_a_new_time`、`test_resume_http_retry_replays_receipt_before_time_validation` |
| ST-D3 | 界面操作直接、状态和结果可读，旧卡片/表单兼容 | 原列表暂停/恢复；暂停显示手动执行；running 区分本次和后续；普通结果简化，详情可展开；成功/失败事件使用服务器实际调度状态，并刷新权威列表 | 通过 | 前端 TaskCard、TaskPanel、ChangeSetCard、FormBlock、WS handlers、WebSocketContext 共 122 passed；[前端日志](scheduled-task-upgrade-evidence/final-frontend.txt)、[本地浏览器记录](scheduled-task-upgrade-evidence/COMMANDS.md#本地界面记录) |
| A-07-01 复核 | 公开、别名、handler-only 全量映射，重复/遗漏 0 | 33 public + 2 internal = 35，定义仍为 explicit；原 ERP→文件/沙盒→媒体→任务分组迁移记录继续可查 | 通过 | [当前完整目录快照](scheduled-task-upgrade-evidence/catalog.json)、[source check](scheduled-task-upgrade-evidence/source-checks.json)；`test_complete_helpers_handlers_and_schema_order`、`test_each_schema_resource_has_exactly_one_spec`；[原 07 分组验收](TOOL_UNIFICATION_ACCEPTANCE_07.md) |
| A-07-02/03 复核 | Spec 唯一持有元数据、Planner/helper 派生，schema/别名/合法参数/旧 API/权限快照兼容 | 冻结基准未改；唯一声明差异为任务 Spec effects 增加 task_definition，实际行为差异显式受可信上下文控制；其余工具目录/授权矩阵和内部可见性不变 | 通过 | `test_tool_definitions_07` 全字段/JSON/导入/矩阵/Planner 对照及 `test_tool_definitions_07_entries`；[最终 469 项对照](scheduled-task-upgrade-evidence/final-contracts.txt) |
| A-07-04 复核 | 额外语义变化独立记录，业务内核保持复用 | 原 07 机械迁移证据保留为历史；本次明确修改任务生命周期，不冒充任务 Handler 无 diff；ERP/沙盒/媒体/任务 Agent、workflow、cache 内核保护路径无 diff | 通过 | [完整增量 diff](scheduled-task-upgrade-evidence/implementation.patch)、source-checks.json 的 protected_sources_unchanged，第 1 节行为差异 |
| A-07-05 复核 | Registry/Policy/Dispatcher/旧 helper/ToolExecutor/结果/回放一致，无循环和请求泄漏 | 1963 passed；独立进程导入、原生产入口及新静态权限检查点核验；并发/取消的 ContextVar call_id 均复位 | 通过 | `test_fresh_process_import_orders_have_no_initialization_cycle`、`test_dispatch_identity_is_scoped_across_concurrent_calls_and_cancellation`、source-checks 的构造点映射；[最终后端日志](scheduled-task-upgrade-evidence/final-backend.txt) |
| A-07-06/G-05 | 当前代码、接口、legacy、迁移/回退与后续前置可交接 | 01–07 历史记录可查；实际升级设计和确定源码指纹齐全；08 未启动，等待本版发布验证/用户关闭 | 通过 | [共同交接](TOOL_UNIFICATION_HANDOFF.md)、[最终架构](TOOL_UNIFICATION_ARCHITECTURE_07.md)、本记录及技术设计第 8 节 |
| G-01 | 授权范围、契约变化和复用边界明确 | 本轮为明确授权行为升级，没有后续板块或未说明业务重写；主工作树未改、未提交部署/合并/清理 | 通过 | 第 1 节、完整 diff、source check |
| G-02 | 成功、拒绝、失败、竞态、恢复逐项执行 | 上述 ST 项及原工具全量对照均有对应测试；生产代表业务另列不计通过 | 通过 | 本表、backend-test-files.txt、迁移事务矩阵 |
| G-03 | 失败修复后复验，保留正确断言、不偷换历史基准 | 首轮 3 失败已修正；最后 1963 passed/1 opt-in skip；同一个 opt-in 测试体在临时库执行通过；前端 122 passed，TS/构建通过 | 通过 | 第 3、4 节；initial-regression.txt 和 final-backend.txt；未更改冻结 baseline JSON |
| G-04 | 工具、schema、别名、旧返回/入口和 WS blocks 兼容 | 不新增工具参数；旧提案模式保留；新请求可选字段、WS 事件可选属性兼容；回放仍 v1 原协议 | 通过 | 全量 07 契约测试、结果持久化 06、Chat engine、WS 表单、前端旧表单/事件测试 |
| U-PROD | 指定真实店铺、模型、到点执行、结果与通知的生产代表流程 | 本轮未部署或调用真实 ERP/模型/通知；尚无本版候选 SHA | 未验证 | 第 5 节用户验证单，后续受控发布回执与用户结果 |

## 3. 测试环境与摘要

全部在任务工作树运行；测试专用占位配置、Python/Node、命令和精确文件集合见 COMMANDS.md。真实 PostgreSQL 测试运行于短生命周期的本机 socket 集群，临时数据已清理；不依赖生产库或真实业务资源。

| 阶段 | 结果 |
| --- | --- |
| A → B → C → D 分批 | 180 / 132 / 72 / 104 passed，各批完成后再继续 |
| 最后契约/边界复验 | 469 passed |
| 最后生命周期/迁移复验 | 42 passed，含 19 个真实 PostgreSQL 用例 |
| 最后受影响全链后端 | **1963 passed，1 skipped，0 failed** |
| 前端组件、WS 生产消费链 | **122 passed，0 failed** |
| TypeScript / Vite 生产构建 | exit 0 / exit 0 |
| 注册、源码保护、导入与 diff 格式 | 35 注册、0 遗漏/重复、保护路径无业务重写；git diff --check 通过 |

各轮有重叠，不能累加为互不重复测试总数。唯一 skip 为原 DSN opt-in 用例入口；其原测试函数通过临时 PostgreSQL 执行成功，不存在该回放机制的未验证缺口。前端 motion mock 的 whileHover/whileTap 提示和 >500KB chunk 构建提示原样保留，不属于此次失败。未跑全仓无关测试，未进行生产 LLM/ERP 或通知的业务验证。

## 4. 问题清单与复验

| 问题编号 | 复现 | 根因/影响 | 所属范围 | 处理结果 | 复验证据 |
| --- | --- | --- | --- | --- | --- |
| ST-01 | 生产只读排查发现 pause 为 awaiting_approval、任务 active、聊天却说暂停；两个基准离线复现均缺卡 | ChangeSet 元数据没有进入聊天块，模型继续自由续写；旧接口本身只提出方案 | 07 后续任务管理/结果消费 | 修复结果引用及终止条件；普通明确暂停在同一提交链实际应用 | pause-before-base/current.json（合成数据）；ST-A/B1 |
| ST-02 | 首轮最终回归有两个 ScheduledTaskAgent 断言失败 | 旧设置替身无新 flag 字段，提前抛 AttributeError，遮盖原执行失败信息 | 兼容上下文 | 缺字段使用兼容 false；原正确断言不变 | initial-regression.txt → final-backend.txt |
| ST-03 | 首轮任务 Spec 全字段对照失败 effects | 当前已授权直接任务管理确实增加持久化副作用，不能继续伪装 proposal-only | Spec/运行 Policy | 显式增加 task_definition；测试仅允许此一差异，冻结 07 基准不改，其余所有字段原样对照 | `test_full_spec_contract_unchanged`、trusted mode 矩阵 |
| ST-04 | 检查成功/失败事件消费者发现会把 success 映射 active | 运行结果不等于调度意图，暂停状态可能显示反弹 | 任务事件/前端 | 数据库回传实际状态并带 schedule_enabled；前端刷新最新任务 | PostgreSQL success/failure + 前端 scheduled completion projection |
| ST-05 | 审查重复 starter 时序：已开始→用户暂停后续→重复 start | 先判断暂停会错误释放已开始运行的 token | 调度启动边界 | start RPC 先检测同 token 已启动；仅未开始任务受暂停阻止 | `test_running_pause_survives_success_failure_and_recovery` 三种结束路径均重复 start 后验证 token 保留 |
| ST-06 | 管理员改他人任务、恢复一次性任务同键重试、复杂 cron 伪装 daily | 检查身份需对应实际执行者；重试需先回执；频率必须看实际 cron | 提交适配器 | 所有者 scope 检查、先回放再归一化、复杂 cron/预算保守确认 | 最终边界用例、469/1963 项复验 |

开发中个别测试命令路径/fixture 错误已修正再执行，不计入通过数量。**本地范围内无已知未修复阻塞项**；生产代表流程仍为未验证，发布状态未关闭。没有将这类限制写成“已通过”。

## 5. 用户生产验证单（待确定发布候选）

部署版本：待填写候选 SHA 和受控发布回执；不要用 75fced91 当本版代码。发布前必须完成技术设计中的在途任务排空及全部 Worker 切换，应用迁移 254/255；不能直接混跑。

使用自己有权限的一家测试店铺及低费用任务，先站内发给自己：

1. “每天上午 9 点，把昨天 **指定店铺** 的销售汇总发给我。”检查服务端回执有准确店铺要求、时间、收件人、积分上限和下次时间，无额外“确认创建”；创建不等于立即执行。
2. “把这个任务改到上午 10 点”，核对只改时间；再暂停，列表立即显示暂停且不再显示下次执行；刷新聊天/任务面板仍保持同一结果。若说“第一个”，先展示列表且核对具体任务名，不能选错任务。
3. 暂停状态点击“立即执行”，确认仅产生一次运行和结果，结束仍暂停；执行过程中暂停后续调度应显示“本次执行中 · 后续定时已暂停”，结束不会自己启用。
4. 恢复循环任务，核对下一次时间，无补跑暂停期间历史；在一个近期测试触发点实际观察运行记录、结果及通知。过去的一次性任务恢复应提示改时间或手动运行。
5. 改成更高频、更多积分或新增测试收件人，检查具体差异确认；没有相应权限时应拒绝。删除仅对用户指定的测试任务确认执行。未测试的外部通知渠道不声称通过。

用户无需遍历 35 个工具：工具/schema/域/别名和旧链已自动化全量对照，手工只验证本次受影响的任务/ERP 代表业务和真实体验。记录任务 ID、候选 SHA、实际结果及失败时间；不在公共文档粘贴业务明细。

## 6. 结论与交接

本地开发及技术验证通过；生产 ERP、真实模型交互和投递体验未验收。本次没有提交、推送、执行生产迁移、部署、合并 main 或清理任务工作树。先前 75fced91 发布过程存在未完成回执的历史，不能据此声称本升级已上线；后续发布应从受控入口核验实际生产状态。

legacy 保留的是旧接口/草稿/消息投影以及原领域业务适配，继续受原 ToolRuntime、ChangeSet 和执行授权约束；没有新增另一套权限执行路径。文件/沙盒 code_execute 缓存、restore_file 风险及无组织 ERP 可见性保持原语义。实际可调用点、迁移接口与回退见技术设计。

工具统一 01–07 记录保持可追溯；本次源码指纹是待提交的确定实现依据，**不启动 08、不宣布整体用户验收完成**。用户对确定候选验收关闭后，才按原工作树生命周期规则处理 main 与后续板块。
