# 工具统一 04：技术验收记录

## 1. 范围与版本

状态：**技术验收通过，待部署/用户验收**。聊天/Actor、定时/预检 ToolLoop 和旧 `ToolExecutor.execute` 已完整接入 Registry/Policy/Dispatcher。这里只报告真实生产代码配 mock 业务 Handler 的本地验证；没有执行真实删除、ERP 写入或付费生成，没有发布生产。

| 项目 | 记录 |
|---|---|
| 基准提交 | `0f65d72dd00a0fce6885d4df0b7977454f666812`，受控 task-worktree start 从最新 origin/main 创建 |
| 当前分支 | `codex/task/20260909225830-tool-unification-04` |
| 当前 HEAD | 同基准；尚不包含本块未提交实现，不能作为已测试候选 SHA |
| 工作树 / 执行目录 | `/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-04` |
| 前置证据 | 用户确认 01–03 已验收进入 main；核验 01 `b4c854ac`、02 `4084db4e`、03 `e243ba2c` 及附件/文件边界修复 `2ed4d783` 均为基准祖先。基准与 03 最终候选 `2ed4d783` 的 tree 均为 `c38a4f18c3ec0ae8193af3c7b9750877df6ae933`。03 文档中的未发布状态是其当时快照，不能否定当前 Git 及用户验收事实 |
| 被测版本 | 基准加本任务未提交的 backend 差异；逐文件 SHA256、实际构造点、业务/协议对照见 [04-source-checks.txt](tool-unification-evidence/04-source-checks.txt) |
| 文档与证据 | 本记录、HANDOFF 的 04 增量、run-04.sh、check-04.py、三份最终测试日志及 fixture 初轮复现日志 |
| 发布候选 | 未提交、未推送、未部署、未合并、未清理。待用户“提交部署”后记录确定候选 SHA，并核对其源码与上述指纹 |

实际改动对应范围：

- `tools/runtime.py`、`runtime_context.py` 装配可信身份、模式/域、资源、授权、确认、预算/取消及统一执行；`tools/execution.py` 增加只在 Policy allow 后运行的兼容缓存/ledger hooks；`policy.py` 保留旧授权名单上界并拒绝失效版本。
- Chat 的 stream_setup/tool_loop/execution_engine、ChatToolMixin 和 ExecutionScope 接入；`chat/tool_lifecycle.py` 适配已有 Actor invocation，`tool_invocation_store.py` 只增加旧表的只读 lookup；stream_loop 清理请求运行引用。
- ToolExecutor 旧门面、ToolLoopExecutor/helpers、ScheduledTaskAgent、scheduled_task_workflow 统一执行和核心/动态展示；FileExecutor 增加不创建目录的检查构造选项，默认行为不变。
- WS 确认响应只接受字面布尔值 `True`，不再把字符串 `"false"` 转成批准。WS 字段、builder、前端界面和结果 content blocks 不变。
- 新增集成测试及可信测试 DB/Handler 支撑，更新受影响的旧 fixture/过期断言。没有数据库迁移、新结果 reader/writer、业务 Handler 重写、ERP 内部编排或板块 05–07 改造。

## 2. 逐项证据表

下表 `I::` 指 [test_tool_production_integration.py](../../backend/tests/test_tool_production_integration.py)，`E::` 指 [test_tool_execution.py](../../backend/tests/test_tool_execution.py)，`T::` 指 [test_tool_result.py](../../backend/tests/test_tool_result.py)。I 中所有模型/外部业务 IO 为 mock；真实 Registry、Policy、Dispatcher、兼容出口、Chat/ToolLoop 调度、WS 确认等待和旧 ledger 序列化参与执行。准确命令及文件集合见 [run-04.sh](tool-unification-evidence/run-04.sh)。

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| A-04-01 | 所有模型工具生产入口的新旧工具都经同一策略，无公共 execute 旁路 | legacy/chat/loop × explicit file_search/legacy web_search 的 Handler 与 Dispatcher 均为 1；拒绝矩阵覆盖三个入口。共享 Chat `_run_loop` 在普通/Actor × auto/plan 四种组合实际装配 ToolExecutor；ScheduledTaskAgent 与真实 ToolLoop 串联。生产构造点仅 ChatToolMixin、ScheduledTaskAgent，ToolExecutionService 仅由 ToolRuntime 构造 | 通过 | I::test_all_entrypoints_new_and_legacy_reach_dispatcher_once；I::test_shared_chat_engine_passes_mode_and_retains_safe_points；I::test_scheduled_agent_real_assembly_and_loop；[调用点检查](tool-unification-evidence/04-source-checks.txt)；下方入口清单 |
| A-04-02 | 无权限、plan 写/生成、跨用户/组织/域、越出定时范围、ERP query 写 action 在 Handler/cache/invocation 前拒绝 | 三入口十种拒绝场景均 0 Handler；另以 trap 验证 cache.get、replay、begin 均未访问。实时组织状态、PermissionChecker、授权版本/名单、功能开关均参与。ERP 读/批准写为 1，query 写和未知 category 为 0；旧 execute 的交互授权快照仍是上界。恢复文件的外部目标和旧缓存撤权也拒绝 | 通过 | I::test_denial_precedes_handler_cache_and_ledger；I::test_business_permission_checker_denies_before_handler；I::test_declared_permission_is_loaded_without_trusting_a_prior_boolean；I::test_scheduled_scope_intersection；I::test_real_erp_action_route；I::test_legacy_snapshot_is_still_an_upper_bound_in_interactive_mode；I::test_cache_cannot_survive_revoked_membership；I::test_restore_record_with_foreign_destination_is_rejected_before_handler |
| A-04-03 | 未确认/拒绝/超时/异常/断连为 0，批准为 1；不重复确认，不能借用旧参数/范围的批准 | 三入口均用真实 WebSocketManager.wait_for_confirm/resolve_confirm：批准 1，其余 0，等待器全部清理。取消确认时 0。批准后重新读取 mode/owner/org/membership/allowed_tools/manifest；任一改变均阻止业务。参数变化生成不同绑定，同 call ID 不重复 dispatch。Actor 恢复匹配的持久批准时无新弹窗、只执行 1；资源通知及任务提案无附加弹窗 | 通过 | I::test_real_confirmation_channel；I::test_cancellation_propagates[confirm]；I::test_approval_cannot_survive_scope_or_authorization_change；I::test_changed_arguments_get_new_confirmation_and_duplicate_is_single_use；I::test_actor_durable_approval_bound_to_arguments_and_owner；I::test_manifest_revocation_after_confirmation_precedes_invocation；I::test_proposals_and_resource_notices_do_not_add_confirmation；旧 test_tool_confirm/test_ws_tool_confirmation |
| A-04-04 | 真实读 A/B 重叠；写 C 等两者结束；D 等 C；写写不重叠 | Chat/ToolLoop 两入口：A/B 都到达 Event 屏障且未释放时只有两条 start；释放后 C 的 start 位于 A/B 两条 end 之后，D 在 C end 之后，E 在 D end 之后；C/E 开始时 running 集合均只有自身。模型调用顺序保留，不再按随机 call ID 排序。取消同批一个读时另一读被取消并 drain，后续写不开始 | 通过 | I::test_real_read_overlap_and_write_barriers；I::test_real_tool_loop_run_uses_model_order；I::test_batch_cancellation_stops_sibling_reads_and_skips_write；旧 test_tool_loop_parallel。断言基于事件/轨迹；2 秒 wait_for 仅防测试死锁，不作为性能阈值 |
| A-04-05 | actor/owner 隔离、预算/取消、Actor 确认恢复；锁、幂等和安全点保留 | 群 owner 与真实 actor 分离，个人工具不可见/不可执行，当前 channel scope 改变后 0 Handler；预算耗尽在 DB/Handler 前中止，确认/业务/批次取消继续传播。恢复使用当前 manifest 解析 fid，原缓存为空也能安全回放；过期成员或外部产物拒绝且不重执行业务。invocation execute/in_progress/uncertain/ownership_lost 的 Handler 次数为 1/0/0/0；原 fenced RPC 与 safe_point 调用 AST 一致 | 通过 | I::test_channel_actor_owner_and_personal_tool_isolation；I::test_budget_exhausted_precedes_everything；I::test_cancellation_propagates；I::test_invocation_gate_and_legacy_completion；I::test_restored_file_id_uses_manifest_and_preserves_legacy_replay；I::test_replay_current_permissions_without_business_or_confirmation；I::test_replay_foreign_artifact_denied_without_business；旧 test_execution_scope/test_chat_execution_engine/test_model_gateway_concurrency_integration；[源码对照](tool-unification-evidence/04-source-checks.txt) |
| A-04-06 | 仍经兼容投影进入旧消费者/ledger；相关旧执行器/循环/确认/权限测试通过，无半接入入口 | 原 AgentResult/ToolOutput、FileReadResult、Form/str 投影及 WS 消费测试通过。Actor ledger 仍写旧 kind=agent_result 等；返回业务 error 仍记调用 succeeded，Handler 异常记 uncertain；拒绝/回放不登记新执行。投递/完成写入/缓存写入故障不重做业务，不把已完成业务误报为未开始。三组最终 2482 passed，无失败/跳过/xfail | 通过 | I::test_invocation_gate_and_legacy_completion；I::test_handler_uncertain_is_separate_from_delivery_failure；I::test_cache_write_failure_preserves_completed_business_and_single_use；I::test_chat_reuses_request_service_across_model_rounds；T 全部；[核心](tool-unification-evidence/04-core.txt)、[回归](tool-unification-evidence/04-regression.txt)、[ERP](tool-unification-evidence/04-erp.txt) |
| G-01 | 实际变化对应 04；无未说明公共契约变化或业务重写 | 本节范围与下方入口/旁路表对应；原 ToolExecutor 业务方法 AST、FileExecutor 除构造器外方法 AST、ERP 内部目录、媒体/沙盒业务、配置 schema、前端/WS builder 均无差异。新许可外壳一次完整接入，未实施结果展示或新 replay 协议 | 通过 | 第 1 节；[源码结构与指纹](tool-unification-evidence/04-source-checks.txt) |
| G-02 | A-04-01～06 每项含成功/拒绝/失败和必要边界 | 上述六项均有对应生产入口集成及最终日志；不是仅测新类或仅看分批数组。未知授权、撤权、确认故障、群隔离、生命周期均有 0/1 调用证据 | 通过 | 上述 A 表；[04-core.txt](tool-unification-evidence/04-core.txt) 中 I 的 118 个用例 |
| G-03 | 当前新增/相关旧测试通过；正确旧断言不删除/跳过/弱化 | 858 核心/集成 + 1613 回归 + 11 ERP = 2482 passed，0 failed/error/skipped/xfail。改为真实 runtime 的旧 fixture 和必需目标冲突断言说明见问题清单。共享网关 fixture 初轮 2 failed 已定位并最终复验；没有待处理的本块技术失败 | 通过 | [执行脚本](tool-unification-evidence/run-04.sh)、三份最终日志、[fixture 初轮](tool-unification-evidence/04-fixture-initial.txt)；第 4 节 |
| G-04 | 工具名/schema/合法参数别名、旧 API/返回、WS 对照通过 | Registry 深比较原 schema/别名，业务方法未改。旧 execute 原位置与参数保留，新增运行事实参数及 keyword-only call_id；安全拒绝仍通过兼容 PermissionError/旧消费者呈现。file_ids/files/path 先转稳定目标再传原 Handler；旧文件/图片/表单/ERP TABLE 对照全通过。确认 id 仍占用原不透明 tool_call_id 字段，值绑定参数/范围，批准必须为 bool | 通过 | test_tool_registry/test_tool_execution/test_tool_result/test_file_id_protocol/test_file_handles_e2e/test_chat_generate_mixin/test_tool_loop_tooloutput/test_ws_tool_confirmation；源码对照与下方兼容说明 |
| G-05 | 接口/调用点/证据/限制/回退及下一块前置可交接 | HANDOFF 更新当前 main 前置与 04 实际接口。本块没有新 payload/schema，原 serializer/deserializer 与 reader 不变；回退只撤销相对 0f65d72d 的 04 增量，保留 03 附件修复。未部署、用户验证未完成，05 只具备代码前置，不可启动 | 通过 | [交接](TOOL_UNIFICATION_HANDOFF.md#板块-04-实际接入)、第 5/6 节 |

### 生产入口与已消除旁路

| 入口 | 当前真实调用链 / 可信事实来源 | 消除的旁路 |
|---|---|---|
| Web 聊天 | run_legacy_chat_stream → execute_chat/_run_loop → ChatToolMixin._execute_tool_calls/_execute_single_tool → ToolRuntime.execute → ToolExecutionService → Dispatcher → 原 Legacy Handler；身份来自服务端请求/会话，mode/domain 从本轮 prepared context 显式传递 | 原只按 SafetyLevel 分组和工具名单检查后直接 execute；执行路径与确认路径分离导致的遗漏 |
| Actor 聊天（含群） | ChatGenerationExecutor.execute → 同一个 execute_chat/_run_loop；已解析 ExecutionScope 的 actor、owner、channel_scope_id + ContextAnchor/ResourceManifest + Actor budget/cancel/token；同一 runtime 加 ActorToolLifecycle | 先登记 invocation 再补检查；回放先返回旧数据再检查当前资源；确认恢复借用未绑定参数/范围的 ID；投递异常误记 uncertain |
| 定时运行 / 预检 | ScheduledTaskAgent.execute → _build_tool_loop → ToolLoopExecutor.run/_execute_tools → invoke_tool_with_cache → 同一 runtime/service/dispatcher；DB task 的 actor/owner、execution_policy/原 tool_policy_snapshot、scheduled/preflight、auto/general、预算/取消显式装配 | cache 命中先于授权；无 UI 或确认服务异常时自动放行；工具名授权扩大成危险参数授权；按随机 call ID 重排；复制模板前不复核当前身份 |
| 旧 execute / re-export | services.tool_executor 仍导出 services.agent.tool_executor.ToolExecutor；execute(name,args,call_id=...) → runtime → to_legacy | 公共 execute 直接 `_handlers.get` 调用；仅凭 allowed_tools 或旧 preflight 手写名单检查即可执行；interactive 快照绕过 |
| 核心 / 动态发现 / 定时规划展示 | stream_setup._prepare_permission_and_tools、chat.tool_loop.prepare_tool_turn、ToolRuntime.advertised、ToolLoop 的动态扩展、scheduled_task_workflow 的 preflight/plan schemas 均取 Registry.resolve/Spec | 核心/动态列表另走 get_tools_by_names/filter 或 CapabilityRegistry；发现名称直接扩权。旧 catalog helper 只用于元数据兼容，不发执行许可 |

源码检查枚举 services/api 的全部 ToolExecutor、ToolLoopExecutor、ToolExecutionService 构造点和分发调用。Dispatcher 的唯一工具生产调用者是 ToolExecutionService；LegacyToolHandler 只绑定原私有 `_handlers`。剩余 scheduler.chat_task_manager/ERP 内部 action 分发属于原业务实现，不接收模型工具调用，也不构成公共模型执行入口。旧 partition_tool_calls、validate_runtime_tool、ToolLoop._request_user_confirm 保留兼容定义，但生产链已无调用；不存在拒绝后的旧执行兜底。

### 执行顺序与兼容边界

1. 静态 Registry/模式/域/action 拒绝 → 模型身份覆盖检查 → 当前组织/成员/会话与声明业务权限读取 → 刷新资源清单 → 规范化路径/ID 和资源检查 → Policy。
2. Actor 只读 lookup 在当前权限/资源允许后发生；已完成记录校验名称、参数哈希和显式 workspace 产物范围，使用原 reader 回放，0 Handler、0 新 begin。运行中/uncertain 或参数不符拒绝，不盲目重试。
3. 需要真实确认时走既有 WS/Actor 命令；确认后重新构造当前 mode/domain/actor/owner/org/授权/feature/manifest 和目标，再由同一 Policy 核对绑定。资源消耗 CONFIRM 仍是通知，任务管理仍交原提案/表单机制。
4. 最终 allow 后，请求服务预占 call ID → 允许的旧缓存读取 → 原 Actor begin/fencing → 原 Handler → 旧对象兼容投影和旧 ledger completion → 原消费者/审计。取消穿透；同批取消清理其他读任务。

确认等待期间文件名/缓存别名不能改换目标：危险文件参数先解析成稳定绝对路径。不同参数、资源清单或作用域不能复用批准。服务跨同一聊天请求的模型轮次复用，结束时清理；Actor 重启依靠原 invocation 和已绑定的持久确认，不新增永久内存幂等系统。

未更改 code_execute 的原缓存资格、restore_file 的原 safe 风险及 invocation 资格、业务锁/重试/退款、ERP 查询 execute_raw 保护或结果展示。旧 ledger payload 结构及序列化完全相同；只增加授权后的 read-only lookup。跨版本旧批准 ID 无新绑定，不能当作新批准；旧 invocation 如规范化参数哈希不一致会明确拒绝，不迁移/改写记录也不重跑业务。此为安全恢复行为，不能据此宣称已经完成板块 06 的 replay 协议改造。

## 3. 测试环境与摘要

在第 1 节工作树执行：

```bash
bash docs/document/tool-unification-evidence/run-04.sh
/Users/wucong/EVERYDAYAIONE/.venv/bin/python docs/document/tool-unification-evidence/check-04.py > docs/document/tool-unification-evidence/04-source-checks.txt
```

Python 3.14.2、pytest 9.0.3；脚本拒绝存在 `.env` 或 `backend/.env` 的测试工作树。显式 APP_ENV=testing、占位 PostgreSQL `127.0.0.1:1`、Redis 端口 1、测试 JWT；不读取生产配置。所有 pytest 使用 `-o addopts='' -v --tb=short`；没有增加 skip/xfail 或覆盖率门槛。

| 组 | 实际结果 | 日志 |
|---|---|---|
| 新生产接入 I + 03 执行/结果 + 01/02 Registry/Policy | 858 passed，2 warnings | [04-core.txt](tool-unification-evidence/04-core.txt) |
| 相关执行器/Chat/Actor/循环/确认/权限/资源/文件/任务/网关/ERP 旧回归 | 1613 passed，4 warnings | [04-regression.txt](tool-unification-evidence/04-regression.txt) |
| 既有 ERP Mixin，独立进程 | 11 passed，2 warnings | [04-erp.txt](tool-unification-evidence/04-erp.txt) |
| 总计 | **2482 passed，0 failed/error/skipped/xfail** | 三组最终命令均 exit 0 |

警告保留：pytest-env 未安装导致 env 配置项未知（脚本显式设置，不依赖插件）；KIE Pydantic class Config 弃用；FastAPI/Starlette asyncio.iscoroutinefunction 弃用。ERP Mixin 的收集期 sys.modules 污染沿用 01 的已复现依据，继续独立进程执行；该文件未改，未跳过测试。

结构检查包括全部变更 Python AST、git diff --check、脚本语法、生产调用点、原业务方法及持久化/WS/安全点源码对照和测试代码指纹。未运行全仓测试；已按实际受影响调用链运行上述相关回归。没有真实 ERP、数据库权限撤销、浏览器生产确认、删除/付费操作的实测结论。

## 4. 问题清单

本块未处理的技术问题：**无**。真实生产验证仍为“未验证”，见下一节，不计入本地 mock 测试成功。

| 问题编号 | 复现 | 根因/影响 | 所属板块 | 处理结果 | 复验证据 |
|---|---|---|---|---|---|
| P-04-01 | 旧无 task/确认异常单测断言自动返回 True | 旧 ToolLoop 确认失败放行，与 A-04-03 直接冲突 | 04 | 生产确认收口；保留 helper 兼容但 fail closed，旧断言改为 False；三入口真实确认故障 0 Handler 替代验证 | test_tool_confirm；I::test_real_confirmation_channel；最终核心/回归日志 |
| P-04-02 | 01–03 的源码测试要求生产文件绝不 import services.tools；旧 helper 测试使用已不存在的 data_query | 前者仅适用于基础隔离阶段，与 A-04-01 的生产接入冲突；后者非当前工具符号 | 04 测试 | 源码断言改为生产接入存在、目录无循环依赖；data_query 换为当前 search_knowledge，保留动态发现去重/域/退出行为断言 | test_tool_policy/test_tool_registry/test_tool_loop_helpers；source-checks；最终日志 |
| P-04-03 | 旧 executor/循环 fixture 只替换 execute 或提供 MagicMock 身份，无法覆盖新可信入口 | fixture 绕过要验的 Policy/Dispatcher，或缺真实上下文事实；不是扩大运行权限的理由 | 04 测试 | 使用真实 ToolExecutor/runtime + mock 当前身份 DB/业务 Handler；补 mode/domain/budget/cancel/snapshot 和认证批准 user_id；保留返回、计费、图片、表单、终止及审计断言 | 受影响 test_tool_executor/test_chat_tool_mixin/test_tool_loop_parallel/test_scheduled_task_agent 等；最终回归日志 |
| P-04-04 | [初轮两项共享网关失败](tool-unification-evidence/04-fixture-initial.txt)，日志显示 unexpected keyword execution_context；并发测试等待 stream start 无法到达 | 旧 `_prepare_permission_and_tools` fixture lambda 未接受新增内部 context 参数 | 04 测试 | 更新 test_chat_gateway_retry_integration/test_model_gateway fixture 参数接口，保持原模型回退、完成、计费、容量和 lease 断言。未弱化正确预期 | 最终回归含 test_chat_gateway_retry_integration、test_model_gateway、test_model_gateway_concurrency_integration 全部 passed |
| P-04-05 | 末轮边界测试注入撤权、缺旧 permission bool、缓存写异常及 Actor 重入 | 模板准备需前置当前授权；新业务权限应实时读取；缓存失败不能覆盖已完成状态；跨轮重建服务会丢失请求内预占 | 04 | 模板前刷新；只对当前声明/任务要求核验权限；缓存写失败留原结果；同请求服务复用。均已补集成复验 | I::test_scheduled_revocation_precedes_template_copy；I::test_declared_permission_is_loaded_without_trusting_a_prior_boolean；I::test_cache_write_failure_preserves_completed_business_and_single_use；I::test_chat_reuses_request_service_across_model_rounds |

开发过程中发现的符号/fixture 错误均已修正并由最终相关全组覆盖；没有以“历史问题”名义遗留本块失败。唯一继承的分进程测试方式依据是 [01 验收记录](TOOL_UNIFICATION_ACCEPTANCE_01.md#4-问题清单) 的 ERP 收集污染基准复现。

## 5. 用户验证单

确定部署版本：**待用户指令“提交部署”后记录候选 SHA**。以下均未在生产执行；真实写入/删除/付费生成需要用户明确授权及指定的可恢复测试资源。没有该授权时仅做只读与拒绝步骤，批准后的真实写入项保留“未验证”。

| 步骤 | 前提/操作 | 预期 | 用户实际记录 |
|---|---|---|---|
| U-04-01 版本 | 提交部署后记录候选 SHA、测试时间、测试组织/工作区 | 实际生产版本与本次被测源码指纹一致 | 未验证；待候选 SHA |
| U-04-02 普通只读 | 在获准测试工作区检索知识/列文件，观察普通聊天与 Actor 消费 | 正常调用与原结果/图片/文件展示；无多余确认 | 未验证 |
| U-04-03 plan 拒写 | 进入 plan 请求生成/文件删除或业务写入；只观察拒绝 | 明确未执行，原资源无变化，无绕行执行 | 未验证 |
| U-04-04 危险拒绝 | 在指定测试资源发起危险操作，拒绝确认；另观察关闭/等待超时 | 0 业务执行，无重复弹窗，无资源变化 | 未验证 |
| U-04-05 危险批准 | 仅在另行获真实写入授权后，对可恢复测试资源批准一次；核对结果与调用记录 | 1 次业务执行；参数/范围改变不能借用原批准 | 未验证；缺真实写入授权，不执行 |
| U-04-06 定时范围 | 使用获准的只读定时任务；尝试超出确认名单、失效授权或危险动作 | 授权只读正常；其余明确未执行，不扩大定时能力 | 未验证 |
| U-04-07 群/取消/恢复 | 获准群工作区只读访问，取消一次只读任务；有条件时验证 Actor 恢复 | 个人资源隔离、取消停止后续调用、已完成业务不因恢复重做 | 未验证 |
| U-04-08 关闭 | 用户对确定候选完成必要验证后明确“清理工作树” | 受控 accept-and-close 验证生产已测候选与 main tree 一致；不重复部署，之后才可启动 05 | 待用户验收和指令 |

## 6. 结论与交接

**A-04-01～06、G-01～05 技术验收通过，待部署/用户验收。** 本块真实调用入口的本地集成、相关回归及逐项证据齐备；生产用户验证全部待完成，不把 mock Handler 成功表述成真实服务成功。

回退到本块基准 `0f65d72dd00a0fce6885d4df0b7977454f666812`：撤销本任务 04 的 backend 增量并删除 runtime/runtime_context/tool_lifecycle 新模块，保留 01–03 和 `2ed4d783` 文件身份/边界修复。测试/文档随对应代码版本保留记录。没有数据库迁移、新 WS 字段或新的结果持久化载荷；旧 reader 可直接读取本块写出的原格式，无 payload 数据回迁。已有 running/uncertain invocation 仍交原保护机制，不因回退自动重试；跨版本参数哈希/旧批准不匹配明确拒绝。

板块 05 的代码前置已具备；流程仍缺确定候选的提交部署、用户验证、受控验收关闭及 main 包含成果核验。**用户验收关闭前不得进入板块 05。**
