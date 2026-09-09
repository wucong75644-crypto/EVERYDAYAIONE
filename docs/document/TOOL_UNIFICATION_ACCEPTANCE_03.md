# 工具统一 03：技术验收记录

## 1. 范围与版本

状态：**技术验收通过，待部署/用户验收**。只完成执行分发、Legacy Handler、ToolResult 与隔离策略入口，不切生产。

| 项目 | 记录 |
|---|---|
| 基准提交 | `8e74f57de1073cbef8b3b8c8256e4409d60c506b`，受控 task-worktree start 从最新 origin/main 创建 |
| 分支 | `codex/task/20260909195904-tool-unification-03` |
| 当前 HEAD | 同基准，尚不含本块未提交实现；不作为已测试候选 SHA |
| 工作树 / 测试执行目录 | `/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-03` |
| 前置 | 用户已确认板块 01–02 验收进入 main；基准合入 02 `4084db4e`，tree 均为 `81f34e6dc3bee23db8499bcceb9b67f91257e9ad`；01 `b4c854ac` 也是祖先，01 合并/候选 tree 相同。已读最新 Spec/Registry/Policy、原内部 Handler、Agent/File/Form 和实际 Chat/Loop 消费函数 |
| 被测版本 | 基准加本任务未提交的 tools/__init__.py、新 dispatcher.py/execution.py/legacy_handler.py/result.py、新 test_tool_execution.py/test_tool_result.py；全部源文件 SHA256 见 [03-source-checks.txt](tool-unification-evidence/03-source-checks.txt) |
| 文档增量 | 更新 HANDOFF；新增本记录、EXECUTION_03 接口说明、run-03.sh、检查脚本与本块测试日志/证据 |
| 发布候选 | 尚未提交/推送/部署；用户明确“提交部署”后记录确定 SHA，并核对其代码与此处指纹。未合并、未清理 |

## 2. 逐项证据表

`E::` 指 [test_tool_execution.py](../../backend/tests/test_tool_execution.py)，`T::` 指 [test_tool_result.py](../../backend/tests/test_tool_result.py)，`R::` 指已有 [test_tool_registry.py](../../backend/tests/test_tool_registry.py)。准确命令、完整文件集合及隔离配置在 [run-03.sh](tool-unification-evidence/run-03.sh)。所有新用例参数名与结果见 [03-isolated.txt](tool-unification-evidence/03-isolated.txt)。

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| A-03-01 | 三代表经 Spec/Registry→Policy→Dispatcher→Legacy Handler→ToolResult；成功、确认、拒绝，未获准 0/获准 1 | 11 行明确矩阵：知识/文件搜索 allow=1、范围拒绝=0；删除 ask/auto 待确认=0、批准=1、拒绝/plan/越界=0。每行同时断言内部 Handler 与 IO mock 次数，并走真实 `_search_knowledge` 或原文件闭包/`_file_dispatch`；无公共 execute 调用。额外验证批准并发重入只执行 1 次 | 通过 | E::test_representatives_complete_chain_real_internal_handlers；E::test_pending_approval_concurrent_resubmission_runs_once_and_freezes_arguments；下方调用计数表 |
| A-03-02 | 正确 handler_key/executor_type 路由；未知/缺失兼容错误；无递归、确认 UI 或业务实现 | 自定义新工具名映射 internal_key，explicit/legacy 两定义和 safe/dangerous 两风险均经真实 Policy；待批/拒绝零执行，批准一次。未知工具、缺绑定、错误 executor_type 不兜底；与旧 execute 的 ValueError 类型及文本逐字比较。Dispatcher 拒绝非许可对象、deny/待批决定、外部实例许可与重复许可 | 通过 | E::test_new_and_legacy_specs_use_same_policy_and_handler_key；E::test_unknown_tool_or_missing_handler_preserves_value_error；E::test_unknown_executor_type_mapping_cannot_fall_back_to_same_key；E::test_dispatcher_only_consumes_own_allow_permit_once；[结构检查](tool-unification-evidence/03-source-checks.txt) |
| A-03-03 | Agent/File/Form/str 原投影、模型/展示、错误/重试、产物、审计与执行元数据逐字段对照 | Agent 3 format×7 status 共 21 行，遍历所有 dataclass 字段 identity；Chat/Loop 两投影对比旧方法，FileRef 全字段、Decimal/date/UUID、emit 双路径、失败卡片、Agent 元数据/audit 均保留。3 类 File 场景经过旧 Chat 实际图片注入；表单走旧消费函数验证 hint、payload、终止和一次审计；4 字符串场景含 40K 原文无截断。4 返回类型同时经过新入口及 execute_legacy | 通过 | T::test_agent_all_fields_and_distinct_model_projections_preserved；T::test_file_read_text_image_and_actual_chat_injection_unchanged；T::test_form_exact_payload_hint_and_terminal_behavior；T::test_strings_preserve_exact_content_without_staging_or_error_guessing；T::test_multimodal_emit_and_erp_table_compatibility_uses_existing_consumers；T::test_wrapping_runtime_metadata_does_not_serialize_or_validate_files；E::test_entrypoint_and_legacy_exit_return_all_supported_types；[字段表](TOOL_UNIFICATION_EXECUTION_03.md#3-字段级结果映射) |
| A-03-04 | 原异常传播、返回错误保留；取消不吞、不成功、不重试；unknown 副作用不标安全 | ValueError/PermissionError/RuntimeError/TimeoutError 原对象及类型/消息保留，execute_legacy 抛同一异常，次数为 1；真实文件内部捕获 PermissionError 仍返回 AgentResult error，真实知识 Handler 仍传播。执行前取消 0、Handler/任务取消 1；Policy 内取消再检查阻止执行。unknown 异常 uncertain；返回业务 error 调用完成为 succeeded。12 行 retry hint×effects 对照，unknown/file_index 不标 safe_to_retry | 通过 | E::test_raised_errors_preserve_identity_and_never_retry；E::test_real_internal_handler_returned_error_vs_propagated_exception；E::test_cancellation_propagates_without_retry；E::test_policy_exception_and_cancellation_during_decision_fail_closed；T::test_exception_envelopes_preserve_cancel_and_never_offer_retry；T::test_retry_hint_retained_but_effects_not_inferred_safe |
| A-03-05 | 不接生产、不双执行、不改持久化/WS/AgentResult；旧执行器/返回类型测试通过；后续契约落盘 | 720 个旧主组 + 11 ERP 全通过；ToolExecutor、Mixin、Chat/Loop、WS、AgentResult/ToolOutput、invocation、缓存等原文件无差异。新模块无生产反向导入；新层无 serializer、外部业务/审计/缓存写入。1471 仅为隔离与既有回归结果，不声称生产新链已上线 | 通过 | [03-regression.txt](tool-unification-evidence/03-regression.txt)、[ERP](tool-unification-evidence/03-regression-erp.txt)、[结构检查](tool-unification-evidence/03-source-checks.txt)；[04 接入契约](TOOL_UNIFICATION_EXECUTION_03.md#4-板块-04-接入前置与责任) |
| G-01 | 范围与目标对应，无未说明公共契约变化/业务重写/后续混入 | 4 新基础模块、1 导出增量、2 新测试及文档；接口与新内存状态说明完整。已有 Registry/Policy/Spec、schema、业务实现、生产编排、数据库均未修改 | 通过 | 第 1 节；[接口](TOOL_UNIFICATION_EXECUTION_03.md)；[实际文件与范围检查](tool-unification-evidence/03-source-checks.txt) |
| G-02 | 本块 A 项逐条实际执行，含成功/拒绝/失败及边界 | A-03-01～05 有独立用例与通过日志；另验证确认后参数/ID/会话/plan/权限/设施变更均拒绝、scheduled/preflight 名字授权不能执行危险 Handler | 通过 | 上述 A 表；E::test_approval_rechecks_current_call_context_before_any_dispatch；E::test_noninteractive_dangerous_names_are_not_execution_grants |
| G-03 | 新测试及现有相关测试全部通过；问题修复补齐复验，不弱化旧断言 | 41 执行 + 49 结果 + 552 Policy + 98 Registry + 720 旧主组 + 11 ERP = 1471 passed，0 failed/error/skipped。初轮 1 项新增测试 ImportError，修正准确符号后单项及最终受影响全组通过。既有测试文件无改动；ERP 收集污染仍按前置记录独立进程执行 | 通过 | [初轮](tool-unification-evidence/03-isolated-initial.txt)、[失败项复验](tool-unification-evidence/03-fix-retest.txt)、[最终新组](tool-unification-evidence/03-isolated.txt)、[最终旧组](tool-unification-evidence/03-regression.txt)、[ERP](tool-unification-evidence/03-regression-erp.txt)、第 4 节 |
| G-04 | 工具名/schema/合法参数别名、旧 API/投影与 WS content blocks 兼容 | Registry 的 98 例继续深比较全目录 schema/旧参数；新链删除同时传 file_ids/files 且原封传至原 file dispatch；旧 types 为 identity，旧 Chat/Loop 投影、WS/表单/产物消费测试通过。生产 config/Agent/handlers/invocation 完全无源码变化 | 通过 | R::test_three_representative_parameter_contracts_field_by_field；R::test_parameter_reading_and_legacy_aliases_unchanged；E 三代表；T 全字段/图片/表单/产物用例；旧组 test_tool_executor/test_chat_tools/test_tool_args_validator/test_chat_tool_mixin/test_chat_generate_mixin/test_tool_loop_tooloutput/test_ws_tool_confirmation 等日志 |
| G-05 | 实际接口/调用点/证据/限制/回退/下一块前置可交接 | 完整接口/状态/字段表、可信 Context/Executor 配对、确认/参数责任、单请求一次性边界、旧出口和 04 接入契约落盘。无持久化新载荷，回退到 8e74f57d 的增量可直接撤回，保留 01–02。04 仅代码前置具备，需本块用户验收关闭后才能启动 | 通过 | [交接](TOOL_UNIFICATION_HANDOFF.md#板块-03-实际增量)、[接口](TOOL_UNIFICATION_EXECUTION_03.md)、第 5/6 节 |

用户重点核对的实际调用计数（每次均新隔离服务与 IO mock）：

| 工具 | 场景 | 内部 Handler / IO mock | execution.status |
|---|---|---:|---|
| search_knowledge | ask allow / 范围拒绝 | 1/1；0/0 | succeeded；not_started |
| file_search | ask allow / 范围拒绝 | 1/1；0/0 | succeeded；not_started |
| file_delete | ask 待确认 / 批准 / 拒绝 | 0/0；1/1；0/0 | not_started；succeeded；not_started |
| file_delete | auto 待确认 / 批准 | 0/0；1/1 | not_started；succeeded |
| file_delete | plan / 范围拒绝 | 0/0；0/0 | not_started |

知识/文件搜索的原风险为 safe，不人为要求确认；同策略对 dangerous 自定义新/legacy Spec 的待确认、批准、拒绝另有测试。没有影子调用真实删除或媒体生成。

## 3. 测试环境与摘要

在第 1 节工作树执行：

```bash
bash docs/document/tool-unification-evidence/run-03.sh
/Users/wucong/EVERYDAYAIONE/.venv/bin/python docs/document/tool-unification-evidence/check-03.py
```

`run-03.sh` 保存三个完整 pytest 命令，均 `-o addopts='' -v --tb=short`，不隐藏警告。Python 3.14，测试工作树没有 `.env`/`backend/.env`，脚本会拒绝这两种情况；APP_ENV=testing，数据库占位 `127.0.0.1:1`，Redis 端口 1，JWT 为测试占位。没有加载生产配置、连接真实 ERP/数据库、执行真实删除或付费生成。

| 组 | 结果 | 日志 |
|---|---|---|
| 新执行/结果 + Registry/Policy | 740 passed，0 failed/error/skipped，2 warnings | [03-isolated.txt](tool-unification-evidence/03-isolated.txt) |
| 相关旧执行/结果/WS/上下文/权限/任务/缓存/审计/产物 | 720 passed，0 failed/error/skipped，4 warnings | [03-regression.txt](tool-unification-evidence/03-regression.txt) |
| 旧 ERP Mixin，独立进程 | 11 passed，0 failed/error/skipped，2 warnings | [03-regression-erp.txt](tool-unification-evidence/03-regression-erp.txt) |

初轮新组 732 passed / 1 failed；失败修复后单项 1 passed，随后补充 7 个入口边界用例并完整复验为 740 passed。未删除或削弱正确断言。全套最终 **1471 passed**，非固定数量或覆盖率门槛。

警告：pytest-env 未安装导致 `env` 配置项不识别（本轮显式环境变量，不依赖插件）；KIE Pydantic class Config 弃用；旧 WS 测试引入的 FastAPI/Starlette asyncio.iscoroutinefunction 弃用。均在日志中保留。

结构/自审：源代码 AST 解析、空白、diff --check、脚本语法；生产反向导入、生产源码与旧测试零差异、前置 ancestry/tree、全新增源/测试指纹均核验。没有发现未处理的高置信实现问题。未运行全仓测试或真实生产联调；对于板块 03 的隔离基础，这两者不是本块技术通过的替代条件。

## 4. 问题清单

本块实现遗留问题：**无**。所有必需技术验收项均有通过证据。

| 问题编号 | 复现 | 根因/影响 | 所属板块 | 处理结果 | 复验证据 |
|---|---|---|---|---|---|
| P-03-01 | 初轮 test_multimodal_emit_and_erp_table_compatibility_uses_existing_consumers ImportError | 新测试将当前 `_collect_interactive_agent_payloads` 误写为 `_collect_interactive_tool_payloads`，阻止该用例执行；不是旧代码失败 | 03 测试 | 核验最新源码后修正准确符号，不改业务代码/预期 | [初轮失败](tool-unification-evidence/03-isolated-initial.txt)、[单项复验 1 passed](tool-unification-evidence/03-fix-retest.txt)、[最终全组 740 passed](tool-unification-evidence/03-isolated.txt) |
| P-01（继承） | 板块 01 已在基准联合收集 ERP 测试与 executor 测试，复现 fixture errors | 旧 test_erp_tool_mixin_unit 收集时污染 sys.modules；当前文件内容未变，不是本块引入 | 既有测试 | 保留测试与断言，沿用有依据的独立进程完整执行；未声称修复旧测试框架 | [01 原问题与基准日志](TOOL_UNIFICATION_ACCEPTANCE_01.md#4-问题清单)、[03 ERP 11 passed](tool-unification-evidence/03-regression-erp.txt)、源检查旧测试零差异 |

板块 04 的真实确认超时/断连、运行批次和身份装配，以及 05/06 的实时/持久化接入均明确未实施，不把隔离 mock 成功称为生产效果。

## 5. 用户验证单

确定部署版本：**待用户“提交部署”后记录候选 SHA**。当前只有内部基础能力，生产行为应与基准相同。

| 步骤 | 前提/操作 | 预期 | 用户实际记录 |
|---|---|---|---|
| 1 调用计数 | 审阅上方三代表计数表及 E 对应测试/日志 | 未获准均 0，批准为 1，auto 不免确认；真实旧内部执行器被复用，公共 execute 零回调 | 待用户核对 |
| 2 结果对照 | 审阅 [字段表](TOOL_UNIFICATION_EXECUTION_03.md#3-字段级结果映射) 与 T 对应测试 | 原 Agent/File/Form/str identity/投影保留；图片/文件双路径、表单终止、失败卡与元数据完整 | 待用户核对 |
| 3 可选本地复验 | 在本工作树执行 run-03.sh | 本记录全部技术测试通过；不执行真实外部副作用 | 待用户核对 |
| 4 提交部署后旧行为检查 | 仅在用户指令部署之后，使用既有测试工作区普通只读搜索/列文件 | 原界面和返回正常；不称为新链生产验证 | 待候选 SHA 与用户结果 |
| 5 关闭 | 用户确认本候选通过并明确“清理工作树” | 受控 accept-and-close 核验生产候选/main tree 一致后关闭，之后才允许板块 04 | 待用户指令 |

本块用户验证不需要真实删除、ERP 写入或付费生成；这些动作没有执行。若后续板块需要真实外部副作用，使用对应授权与指定测试资源。

## 6. 结论与交接

**A-03-01～05、G-01～05 技术验收通过，待部署/用户验收。** 代码、验证、自审、逐项证据和后续接口已齐备；生产入口没有切换，任务尚未提交/推送/部署/合并/清理。

回退：撤回本块四个新模块、两项测试及文档增量，将 services/tools/__init__.py 恢复到 `8e74f57d`；无数据库/WS/持久化新载荷，不需要数据迁移或额外生产开关。

板块 04 的代码前置已具备，流程仍缺本块确定候选部署、用户验证和受控关闭；用户验收关闭前不能进入下一块。
