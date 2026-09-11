# 工具统一板块 05：实时结果与前端展示验收记录

> 2026-09-11 最新生产复验：1df0e01c 的工作区插入与重新上传均出现模型未识别当前附件。已准备当前 user 消息绑定附件的候选，718 项回归及 6 次真实模型合成对照通过，尚未部署，原生产场景待复验；05 不得标记技术通过或进入 06。见 [附件绑定调查与增量验收](TOOL_UNIFICATION_05_ATTACHMENT_BINDING.md)。以下保留此前实施记录。

> 最新恢复阶段（2026-09-11）：用户要求恢复上下文架构相关逻辑后，已实现独立历史交付事实、缓存投影隔离、逐调用状态与动态摘要替换，并补齐预算/循环摘要保留完成事实的链路。原代码 44 项新回归全部失败；当前 44 项通过，相关总计 **986 passed / 3 既有 skipped / 0 failed**。第 1、2 项改动继续保留；未提交部署。当前 A/G 矩阵、问题表、回退和用户验证单以 [上下文结果恢复记录](TECH_05_上下文结果恢复.md) 为准。真实模型目标行为和用户观感尚未验证，**05 整块技术验收仍未通过，不能进入 06**。下面的“上下文未实施”及先前测试数字是前一阶段快照。

> 最新状态（2026-09-11）：用户授权第 1、2 项后，删除外键生命周期和统一文件调用参数已实现，当前 608 项相关自动化通过。上下文/状态只做 Grok Build 与 Git 历史对照，未实施；此前附件位置/目标提示补丁已撤回。**05 整块技术验收仍未通过，真实模型与用户生产复验未完成。** 最新逐项 A/G 矩阵、问题表、文件边界及用户验证单见 [本轮修复与 Actor 对照](TOOL_UNIFICATION_05_ITEMS12_AND_ACTOR_COMPARISON.md)。下文是首次实现的历史证据快照；其中“通过”不覆盖本轮未关闭问题。本轮未提交部署，不能启动 06。

## 1. 范围与版本

- 本任务仅实施 05；基准 `6c0737ab78f2d0cb3b2a8376498e7825b0829431`，分支 `codex/task/20260910221341-tool-unification-05`，工作树 `/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-05`。
- 创建时从最新 origin/main 受控 start。该 main 包含板块 04 的最终修复 `1f288018`，两者 tree 均为 `5196ad3351ebaefa8e78a5c0215211018a2174db`；用户已明确 1–4 验收进入 main。原交接中的“04 待关闭”属于历史快照。
- 被测版本为以上 HEAD **加本任务未提交差异**；没有新候选 SHA、推送、部署或合并。代码/测试/原协议夹具 SHA256 见 [源检查](tool-unification-evidence/05-source-checks.json)。基准 SHA 不代表已测试的新代码提交。
- 实际变更：实时结果取值、两循环消费、取消/uncertain 分类、同轮完成结果的产物/审计收齐、旧持久化边界防误传；没有改工具 schema、ERP/媒体/沙盒业务引擎、Actor lease、数据库结构或前端组件。新版 replay/cache payload 留给 06。

## 2. 逐项技术证据

下表 `I` 指 [新增消费集成测试](../../backend/tests/test_tool_result_consumption.py)，`P` 指 [既有生产入口集成](../../backend/tests/test_tool_production_integration.py)。精确执行命令由 [run-05.sh](tool-unification-evidence/run-05.sh) 给出；用例结果见 [集成日志](tool-unification-evidence/05-integration.txt)、[回归日志](tool-unification-evidence/05-regression.txt)、[独立 ERP 日志](tool-unification-evidence/05-erp.txt)。

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| A-05-01 | Chat 原 to_message_content；ToolLoop 原 to_tool_content；文本/表格/FileRef/错误分别对照 | 原 AgentResult 方法源码未改；两消费者逐值相等；Chat 结构化 blocks 与 Loop DATA_REF 文本未混用；FileRead 图片单独注入；表单使用 llm_hint | 通过 | I `test_live_states_projection_metadata_and_audit`（12 场景）、`test_tool_loop_result_projection_and_artifact_collection`（8）、`test_real_media_handler_with_existing_offline_provider_samples`（8）；test_tool_result.py 的原字段/投影对照 |
| A-05-02 | 文件、图片、沙盒、ERP、媒体、表单原 WS block、必需字段、顺序、delivery 元数据一致 | 2 sink × 9 场景共 18 份原 main goldens 完整深比较；仅校验后归一化时钟值，ID/seq/attempt/全部业务字段保留；旧 video Handler 仍是 summary URL | 通过 | I `test_live_chat_protocol_matches_main`；[夹具与归一化规则](../../backend/tests/fixtures/tool_result_05/README.md)、[原 main 生成日志](tool-unification-evidence/05-golden-main.txt)、[原 main 复现脚本](tool-unification-evidence/capture-05-main.sh) |
| A-05-03 | 可解析文件、图片注入及 retry_context；ERP 交互不重复 TABLE，定时收集；表单终止；长文本不丢产物 | 临时 CSV 完整读取、Parquet 实际读取、PNG 解码；Chat ERP 0 个额外 TABLE，ScheduledTaskAgent 恰好 1 个 TABLE；真实 Chat 模型循环表单后不再请求模型；长字符串 staging 全量尾标记保留，AgentResult 的完整 FileRef/emit 不修改；steer 也收齐已完成结果 | 通过 | I `test_live_chat_protocol_matches_main[file/image/erp/form/media_failure]`、`test_scheduled_agent_consumes_unified_table_and_audits_once`、`test_full_chat_loop_stops_after_terminal_result[form]`、`test_long_model_projection_keeps_full_file_and_payload`、`test_steer_keeps_completed_artifacts_and_audits` |
| A-05-04 | success/error/timeout/empty/partial/plan 与异常取消；tokens/thinking/metadata 保留；uncertain 不成功、不普通重试 | 6 原状态在两消费者逐项断言；业务失败 invocation 仍 succeeded，业务状态仍 error；取消抛出；实际 wait_for 超时保留 dispatch 副作用状态；unknown-effect 异常及旧 uncertain invocation 停止后续工具重试；缓存错误不覆盖 success | 通过 | I `test_live_states_projection_metadata_and_audit`、`test_exception_timeout_cancel_and_effect_certainty`（6）、`test_cache_failure_state_and_single_audit_per_consumption`、`test_full_chat_loop_stops_after_terminal_result[uncertain]`、`test_full_tool_loop_uncertain_wraps_up_without_retry`、`test_current_actor_uncertain_never_reexecutes_or_counts_success`；P 的取消/拒绝/完成回归 |
| A-05-05 | ledger/checkpoint 继续旧兼容载荷；恢复保持旧版内容；无 ToolResult 对象字符串；审计/投递各一次 | lifecycle 显式 legacy_persistence_value 后才调用原 serializer；9 类旧载荷逐值对照及恢复；未改旧 reader，writer 仅加误传拒绝。checkpoint 接收投影消息/blocks，与 main golden 相等；直接传信封会明确抛错。业务只执行一次，展示失败仍完成旧 ledger 且审计一次；cached 每次消费一条审计 | 通过 | I `test_explicit_legacy_persistence_projection_and_old_reader`（9）、`test_actor_ledger_and_audit_written_once_without_business_redo`（2）、`test_cache_failure_state_and_single_audit_per_consumption`；[AST 兼容检查](tool-unification-evidence/check-05.py)；P invocation 回归 |
| G-01 | 变更与 05 对应，无后续板块或业务重写 | 13 个生产文件仅适配结果消费、停止信号及边界；ToolRuntime/Policy/Registry、schema、原 emit/sink、业务 Handler 源码不变 | 通过 | 第 1/4 节范围；05-source-checks.json 的未变边界与文件指纹；Git diff |
| G-02 | A-05 全部执行，含失败/边界 | A-05-01～05 均有独立场景断言；取消、未知副作用、错误缓存、投递失败、staging 和 steer 均覆盖 | 通过 | 上方 A 矩阵及逐用例日志 |
| G-03 | 新增与相关旧回归通过；失败必须复现/修复/复验 | 结果与环境处理见第 3、5 节。旧内部返回对象断言已按 05 的信封消费目标改为精确投影/原 raw 身份验证；未删测试或跳过正确断言 | 通过 | 原 main 333 passed；旧环境失败对照；最终 run-05 三组日志 |
| G-04 | 旧 API、名称/schema、模型、WS、ledger 兼容 | ToolExecutor.execute 仍返回旧对象/原异常；旧 invoke_tool_with_cache helper 仍保留。生产 Loop 改用新 helper；原 schema/emit/sink/AgentResult 不变，模型及 WS 与 main 对照通过 | 通过 | test_tool_executor、test_tool_loop_helpers、test_chat_tools 等回归；源检查；18 goldens |
| G-05 | 接口、证据、限制、回退和下一块前置可交接 | 四个边界关系、旧持久化损失边界及回退明确，交接已链接本记录；没有声称已发布/用户验收 | 通过 | [交接](TOOL_UNIFICATION_HANDOFF.md) 与第 4、6、7 节 |

## 3. 测试环境、命令与摘要

执行目录为本任务工作树；Python 3.14.2 / pytest 9.0.3。运行器拒绝 `.env` 和 `backend/.env`，只用测试占位 JWT、APP_ENV=testing、数据库和 Redis 的 `127.0.0.1:1`。未使用生产配置或真实业务数据。

```bash
# 共享 venv 缺少项目已声明的这四项依赖；只安装到临时目录。
/Users/wucong/EVERYDAYAIONE/.venv/bin/python -m pip install \
  --target /private/tmp/tool05-testdeps --no-cache-dir \
  matplotlib==3.10.8 ipython==8.32.0 lxml==5.3.0 time-machine==2.14.1
PYTHONPATH=/private/tmp/tool05-testdeps:backend bash docs/document/tool-unification-evidence/run-05.sh
/Users/wucong/EVERYDAYAIONE/.venv/bin/python docs/document/tool-unification-evidence/check-05.py \
  > docs/document/tool-unification-evidence/05-source-checks.json
git diff --check
```

最终 **280 集成/执行 + 893 相关回归 + 11 独立 ERP = 1184 passed，0 failed/error/xfail，2 skipped**。其中本块新增消费集成 72 项，原 main 协议夹具对照 18 项包含在这 72 项内；基线与中间验证不重复计入最终总数。

2 个 skipped 为原 `test_emit_three_engines.py` 中 Linux 文泉驿真实字体注册/重复注册检查（`test_chinese_aliases_registered_to_real_font`、`test_alias_registration_dedupes_on_second_call`），测试本来即在本地无 `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc` 时跳过。本块没有改字体/自动 hook 实现或跳过条件，它们属于扩大检查中的平台环境项，不属于 A-05 必需消费协议；明确保留“未验证”，不计入通过数。[同依赖下原 main 复验](tool-unification-evidence/05-main-environment-fixed.txt) 为 81 passed / 同样 2 skipped，证明是相同平台边界。A-05 必需项、Chat/ToolLoop/原媒体 Handler/表单/ledger 集成没有跳过。

macOS 缺少中文字体产生既有 glyph warning，IPython source 参数和 KIE Config 有弃用警告；不以这些测试声称生产图表中文字体已验证。新增集成日志为 2 个配置/弃用警告，扩大回归为 14 个警告。临时依赖清单见 [环境记录](tool-unification-evidence/05-environment.json)，未改 requirements。

原主工作树未修改；独立 main 导出上的 [333 passed 基线](tool-unification-evidence/05-main-regression.txt) 与 [201 passed 窄基线](tool-unification-evidence/05-baseline.txt) 保留。新集成中只替换身份 DB/模型响应/外部业务 IO，实际两循环、Policy/Dispatcher、emit、sink、模型投影和 ledger 编排均执行。

## 4. 实际边界适配关系

| 边界 | 当前入口与适配 | 保留行为 |
|---|---|---|
| 模型 | `ChatToolMixin._execute_single_tool` 返回 ToolResult → `ChatToolResultMixin._process_unified_result` → `apply_tool_results/unpack_tool_result` → `model_content('chat')`；`invoke_tool_result_with_cache` → ToolLoop → `model_content('tool_loop')` | 原 AgentResult 的两种不同投影；FileRead.text/独立图片注入；表单 llm_hint；原字符串 staging 预算与模型消息结构 |
| 前端 | `ToolResult.display/artifacts/collect_payloads` → Chat 待投递列表或 ToolLoop 收集列表 → 原 `emit_payloads` 转换、`ExecutionSink/ActorWebSink` | 原 blocks、WS builders、delivery 元数据与表单终止；ERP Chat TABLE 过滤、定时 TABLE 补齐且去重；失败图片 retry_context；video 原 summary URL |
| 审计 | Chat 从 `audit_fields` 取状态/长度/分流/cache，经原 `_emit_tool_audit`；ToolAuditHook 从同一信封取值，仍用 `ToolAuditEntry/record_tool_audit` | 每次消费一个审计调用；Loop 模型轮次 prompt/completion tokens 原样传入；子 Agent tokens/thinking/metadata 留在统一结果，Chat 原 tokens 累加保留；不虚构审计表的新列 |
| 持久化 | `ActorToolLifecycle.complete` → `legacy_persistence_value` → 原 `serialize_tool_result`；模型/blocks 已投影后进入 `_build_replay_context` | 不写新 envelope 版本；原 reader 不变；直接误传 ToolResult 会明确失败而非字符串化；cache 仍写旧 raw；不改 invocation hash/资格/RPC |

`ToolResult.with_model_content` 只保存按消费者分开的内存投影及分流标志，不替换 raw、完整 FileRef、data、emit。`validation_issues` 复用原 AgentResult.validate。

异常取消仍传播。Service 在被 wait_for 取消的异常上携带内存执行状态，由 timeout 消费者区分尚未开始/明确只读失败/外部效果未知；不新增重试器或 ledger 状态。旧 running/in_progress/uncertain 通过 UncertainToolInvocationError 保留未知副作用含义。Chat 终止进一步工具轮次；Loop 交现有 stop_policy 收尾。业务返回 error/timeout 与 invocation succeeded 仍是两个维度。

`ToolAuditHook` 不负责可靠投递。Chat 的审计调用在展示前发生，展示失败不会漏掉本次审计调用或重做业务；数据库写失败仍是原 best-effort warning。steer 保留原模型“跳过”反馈，但对已经完成的工具继续收齐产物/审计，不把跳过模型反馈当成业务未执行。

## 5. 问题与复验

| 问题编号 | 复现 | 根因/影响 | 所属板块 | 处理结果 | 复验证据 |
|---|---|---|---|---|---|
| P-05-01 | 首轮接入 21 个旧断言失败 | 内部对象已按任务从 raw 改为 ToolResult；部分 mock 仍指旧 helper | 05 测试适配 | 保留 raw 身份和精确 model_content 断言；仅把生产 mock 定位至新 helper。FileRead/Form 在 Loop 的旧“传原对象”断言改为字符串投影，避免对象进入模型消息 | 最终回归及 18 份旧协议对照；旧断言变更见 Git diff |
| P-05-02 | 扩大回归 21 failed / 874 passed；未改 main 同类失败 | 本地共享 venv 缺 time-machine、Matplotlib、IPython、lxml；HTML table 解析缺 lxml 返回 None | 测试环境 | 临时安装 requirements 中指定版本；没有改共享 venv、项目依赖或删/skip 测试 | [初次环境日志](tool-unification-evidence/05-initial-environment.txt)、[main 复现](tool-unification-evidence/05-main-environment.txt)、最终回归日志 |
| P-05-03 | 模型消息中途 steer，后续结果已执行却跳过产物和审计 | 原后处理提前 break | 05 消费边界 | 继续收齐已完成结果；保留原模型反馈顺序，不重做 Handler | I test_steer_keeps_completed_artifacts_and_audits；旧 test_steer_skips_remaining_parallel_results |
| P-05-04 | 原 helper to_legacy 异常会丢失 execution.uncertain | 信封在停止分类前被拆掉；原文本 fallback 可能判成可重试 | 05 消费边界 | 实时保留 ToolResult；超时携带实际启动状态，uncertain 使用既有停止机制；取消继续抛出 | I 异常/超时/取消、两个完整循环 uncertain 用例、旧 invocation 用例 |

既有警告：pytest-env 未安装导致 env 配置项未知（运行器显式设环境）；KIE Pydantic Config 弃用；组合测试中 AsyncMock 未 await 警告已在未修改 main 的 333 项运行复现。ERP 测试在 collection 修改 sys.modules，按既有约定独立进程执行。环境依赖问题修复后仍须以最终日志为准，不能把原失败算通过。

## 6. 用户验证单（人工观感单独记录，当前均未验证）

部署候选 SHA：待用户指令“提交部署”后记录。以下均需在该确定版本上验收；本地 mock/协议测试不等于生产成功。

| 场景 | 最少步骤与预期 | 人工状态/实际 |
|---|---|---|
| 指定测试文件/图片 | 在获准工作区上传测试 CSV/XLSX 和 PNG；先搜索，再读取/分析；点击返回文件和图片应能打开，下一轮能引用该图；记录文件名与路径 | 未验证 |
| 沙盒/长输出 | 用测试数据生成表格文件和图；各产物只出现一次且可打开；大输出保留完整文件，不只有预览 | 未验证 |
| ERP | 只读查询获准测试数据；交互式结果没有第二份重复 TABLE；执行既有定时只读任务，结果中表格应正常保留 | 未验证 |
| 任务表单 | 提出测试定时任务；只出现原表单，字段可编辑、提交/取消遵循原流程；表单后没有第二段模型确认文案 | 未验证 |
| 错误与取消 | 在测试资源触发可控失败/取消；错误卡片不显示成功；已有失败图片样本的重试信息完整；未知副作用提示先核验而不是直接重做 | 未验证 |
| 付费媒体 | 优先查看已有获准生成样本：图片可打开、失败卡片保留 retry_context、视频原 URL 可播放；需要新生成时须另有该付费测试授权 | 未验证，未调用付费服务 |

自动验证已读取本地 CSV/Parquet、解码本地 PNG，且实际媒体 Handler 复用了旧测试 Provider 样本；没有声称 example.test/cdn.example.com 离线示例 URL 在生产可访问。

## 7. 结论、限制与回退

**技术验收通过，待部署及用户验收。** A-05-01～05 和 G-01～05 的必需证据已完成；上述 2 个未改动 Linux 字体环境检查和第 6 节人工观感/真实外部服务检查未验证，均不冒充通过。用户验收未完成。未经用户指令不推送、部署、合并或清理。板块 06 只能在本块候选用户验收及受控关闭、核验 main 后启动。

回退基准为 `6c0737ab`。没有新数据库结构或持久化格式；撤回本块代码仍由原 reader 读取同样的 agent_result/scalar/json 载荷和 checkpoint。旧 writer/reader 的 AST 与新边界实际落盘样本已经对照；不是假定代码回退就自然兼容。

明确保留旧载荷的历史限制：AgentResult ledger 只保存 to_tool_content 摘要、status、error_message、emit_payloads，不能恢复 raw 的完整 tokens/thinking/任意 metadata/完整表格；FileReadResult 和 FormBlockResult 的旧 ledger serializer 本来会保存旧对象的字符串表示。05 证明不新增损失、不会把 ToolResult 退化成字符串；不会把这些旧限制冒充新版无损回放。实时路径保留全部原字段，checkpoint 按原模型消息/blocks 保存。无损新版本及旧载荷读取策略由 06 单独设计与验证。
