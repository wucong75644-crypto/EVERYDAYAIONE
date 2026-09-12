# ST-26：结构化定时任务输入升级与验收

## 1. 范围与版本

2026-09-12；工作树 `worktrees/tool-unification-07`；分支 `codex/task/20260911215117-tool-unification-07`；基准 HEAD/此前生产版本 `d16adf195d4e93df6007239eba4bdbec34f4ca9d`。本记录对应该基准上的未提交差异，包含先前 ST-25 口语兼容修复。没有提交、推送、部署、合并 main 或关闭任务。

用户已同意在现有实现上采用 Grok Build 的职责分工：主模型一次提取任务字段，工具负责字段校验与确定提交，更新传补丁，生命周期操作按任务 ID，回复使用真实回执。参考公开源码固定版本 [xai-org/grok-build / 37949780](https://github.com/xai-org/grok-build/tree/37949780c144e37df692e3d669051a21fec24f20)，对应 `/loop` 指令与 scheduler 工具。这里借鉴输入分工，不声称知道 Grok.com 云端实现，也没有移植其 interval、七天 TTL、立即首跑或内存调度器；其公开四种操作未包含 pause/resume，本项目继续保留原有暂停/恢复能力。

此次是用户另行授权的任务管理升级，超出板块 07 最初的机械定义迁移。公开契约的明确变化只有 `manage_scheduled_task` 新增可选 `definition`、`recipient`，以及对应使用说明；旧工具名、action 枚举、description/task_name/task_id 字段及类型、required 列表不变。旧参数说明中的“create 必须 description”已同步改为兼容说明，不能与新模型说明互相冲突。其他工具 schema、领域、风险、并发、缓存、可见性不变。

## 2. 当前架构与接口

```text
聊天用户请求 + 当前上下文
  → 主模型通过 ToolSpec 生成 action + definition/recipient + task_id
  → Registry / Policy / Dispatcher
  → ToolExecutor → ChatTaskManager(structured_input=True)
  → task_definition_input（从 ToolSpec 读取字段契约，不调用模型）
      缺项 → 原 FormPart（保留内容，只补缺项）→ 原表单提交服务
      完整 → 原 ScheduledTaskChangeSetService.begin / complete
  → 原权限、计划、预检、ChangeSet 确认规则、commit_scheduled_task_changeset
  → 原 ChangeSetCard / WS 状态及回放
  → 到时由原调度器领取 → 原 ScheduledTaskAgent / ToolExecutor → 原结果与通知投递

面板“AI 智能创建”
  → POST /scheduled-tasks/parse {structured_fields: true, operation: create}
  → parse_structured_task_request（一次 Qwen 字段提取）
  → 同一 task_definition_input → 填写现有表单 → 原 ChangeSet 提交
```

“没有第二次解析”限定任务需求提取：新的交互式模型入口在 auto/ask/plan 和直接提交开关关闭时都使用结构化输入；未传 definition 明确返回可修正的错误，不转调 description 解析。计划发布仍可能调用原 Planner/create_plan 来规划执行步骤，它不替换任务 prompt。定时执行时原 Agent 仍使用模型。

新调用示例（不执行）：

```json
{"action":"create","definition":{"name":"付款订单日报","prompt":"查询昨天的付款订单数，按平台汇总","schedule_type":"daily","time_str":"08:00"},"recipient":"我"}
{"action":"update","task_id":"现有任务ID","definition":{"time_str":"10:00"}}
{"action":"update","task_id":"现有任务ID","definition":{"output_format":"表格"}}
{"action":"pause","task_id":"现有任务ID"}
```

- `definition` 只允许 name/prompt/schedule_type/time_str/weekdays/day_of_month/run_at/output_format。缺项可省略，非法类型、枚举、范围、日期时区、矛盾时间字段明确拒绝；不能传身份、权限、工具执行策略。单次任务需要完整日期时间，周期任务沿用日历 cron。
- 更新只合并传入字段。只改输出形式时附加格式要求，保留原始业务指令。只改每周几/月几号/频率会按原固定钟点重新计算实际 cron，不沿用与新字段冲突的旧 cron。复杂 cron 不猜钟点。
- 缺项更新表单仅携带本次补丁、已经定位的 task_id 和兼容标记，不重发整条任务；单次日期继续走原时区转换。`_structured_input: true` 是表单载荷格式标记，不是授权；`_submission_mode` 仍经过原功能开关和提交服务检查，proposal 不被提升为直接提交。
- recipient 是用户意图；后端从当前用户可用的通知目标中解析真实 ID，权限在原服务复验。明确群/同事无法唯一匹配时要求选择，不能改投自己。个人网页/企微与现有身份绑定一致；面板新增请求不继承上一草稿渠道。
- 提供 task_id 后未命中时，新聊天路径不再根据同时传入的名称改操作另一个任务；名称或短 ID 有歧义继续列出候选。未找到/字段错误返回失败状态，不能显示为业务成功。
- 普通明确创建/修改按已有授权规则处理；删除保持卡片确认。暂停停止后续自动调度，已开始的执行按原生命周期完成；恢复和手动执行沿用原接口。`applied` 才反馈成功，检查中、待确认、失败分别显示真实状态。
- 旧 `ChatTaskManager` 调用默认 structured_input=False；旧 description-only、parse_task_nl、parse_task_request、未传 operation 的 parseNL 调用继续兼容。旧 config 指导文案从 Spec 派生。保留的是语言/表单/业务适配，授权、预检和提交没有新增旁路。
- 面板手工编辑继续展示并提交用户看到的整份编辑表单；聊天的补丁语义不改写此旧 API。面板 AI 输入目前只在创建时可见，后端保留 update 提取接口兼容能力。

## 3. 逐项证据

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| ST-26-01 | 主模型字段直接入工具；完整/缺项/非法请求不再调用第二个解析器 | 真实 Registry/Dispatcher/ToolExecutor 入口通过；缺项保留业务内容；非法字段未进入提交；交互入口不同模式均无 description 回退 | 通过 | `test_scheduled_task_structured_input.py` 的 complete_chat、incomplete_form、bad_fields、model_create_has_no_description_fallback、interactive_model_never_reparses 用例 |
| ST-26-02 | 只改指定字段，日历和实际 cron 一致 | 时间、频率、周几、月几号、输出形式补丁通过；矛盾时间字段拒绝，单次日期与 proposal/功能开关保留 | 通过 | 同文件 update_submits、calendar_patch、output_format、incompatible_calendar、structured_form_keeps_confirmation 用例 |
| ST-26-03 | 身份/收件目标/任务定位安全，操作可追溯 | 本人网页/企微映射、未知收件人补选、未知 ID 不改投名称任务、占位店名需核对通过；原组织/用户/域矩阵与 Planner/preflight 对照通过 | 通过 | structured_input 与 `test_tool_definitions_07.py`、`test_tool_policy.py` |
| ST-26-04 | 创建/修改/暂停/恢复/删除经过同一提交链，有真实状态和幂等回执 | 5 个操作从实际工具入口经过真实 ChangeSet/授权/校验/预检至 RPC 边界；只有删除待确认，重建执行器模拟请求重放不重复提交；通知事件和结果一致 | 通过 | `test_real_tool_changeset_authorization_commit_and_replay`；模型计划、数据库仓储及 RPC 使用隔离替身，不冒充生产落库 |
| ST-26-05 | 前后端字段、表单和结果回放一致，面板只调用一次提取 | 后端真实生成的补丁 fixture 在 FormBlock 渲染、必填校验、事件提交；task_id、布尔标记和仅变更字段保持；面板指定本人企微、失败保留输入、旧 service 调用兼容通过 | 通过 | `test_missing_update_field_form_contains_only_patch_and_resolved_id`、`FormBlockChangeSet.test.tsx`、`TaskForm.test.tsx`、`scheduledTaskStructured.test.ts` |
| ST-26-06 | 不重写调度/业务内核，实际数据库生命周期无回退 | 27 项临时 PostgreSQL 测试通过：运行中暂停、领取竞争、恢复、手工执行保持暂停、删除约束、授权/版本冲突、原 invocation uncertain/replay 正文 | 通过 | [PostgreSQL 日志](scheduled-task-structured-evidence/postgres.txt)；原 tests/test_scheduled_task_upgrade_postgres.py、test_scheduled_task_draft_delete_integration.py |
| ST-26-07 | 现用模型能直接产生新工具字段，真实模型联调 | 已准备 6 个“学习建议”合成样本；新增调用未获明确回复，本次没有发起；ST-25 的 3 次旧协议实测不能充作新协议证据 | 未验证 | [待授权样本](scheduled-task-structured-evidence/pending-model-cases.json) |
| G-01 / A-07-02、04 | 实际改动与已授权范围一致，唯一事实来源和业务复用不退化 | schema/说明由 Spec 维护；新校验层从 Spec 派生；未改 ERP/文件/沙盒/媒体/调度执行及结算内核、RPC 或数据库 schema | 通过 | 实际 diff、`test_full_spec_contract_unchanged`（仅此次授权的任务 schema 例外） |
| G-02 / A-07-03 | 成功、失败、缺项、歧义与授权边界覆盖，端到端验收齐备 | 自动化行为覆盖通过；新模型协议实测待补，不能据此宣布总体技术验收完成 | 未验证 | ST-26-01～07 |
| G-03 / A-07-05 | 当前相关回归通过，不用过时测试制造完成 | 后端 1685 通过、1 项原环境开关跳过；该跳过正文已在临时 PG 复验通过；另 PG 27 通过；前端相关 43 项及新增表单链路用例通过；构建/类型/定向 ESLint 通过 | 通过 | [后端](scheduled-task-structured-evidence/backend.txt)、[前端](scheduled-task-structured-evidence/frontend.txt)、[表单](scheduled-task-structured-evidence/form-wire.txt)、[构建](scheduled-task-structured-evidence/build.txt) |
| G-04 / A-07-01 | 全量目录、旧参数/导入/投影、handler-only、域/风险/并发对照 | 35 Spec、33 公开 schema、35 handler，覆盖错误 0；全量旧参数类型/枚举/required 不变，任务新增字段及说明是明确例外；旧帮助器和 Planner 派生通过 | 通过 | [当前目录](scheduled-task-structured-evidence/catalog.json)、test_tool_definitions_07.py |
| G-05 / A-07-06 | 架构、兼容入口、回退和未验证事项可交接 | 本文、ARCHITECTURE_07、HANDOFF、ACCEPTANCE_07、CURRENT_ISSUES 同步；01～07 原记录仍保留，08 不启动 | 通过 | 本文第 5、6 节及交接链接 |

## 4. 环境、复验与问题记录

后端使用 Python 3.12、工作树代码及 testing 配置；前端使用现有依赖与 Vitest/Vite。精确命令、被测文件摘要和源文件指纹见 [validation.json](scheduled-task-structured-evidence/validation.json)。普通测试不访问生产库、ERP 或通知渠道。实际数据库测试在临时目录启动 Unix socket 专用 PostgreSQL，关闭 TCP 监听，结束后删除测试集群。

| 问题编号 | 复现 | 根因/影响 | 所属 | 处理结果 | 复验证据 |
|---|---|---|---|---|---|
| ST-25 | 完整口语请求 prompt 被清空 | 来源片段校验不接受“钟/看” | 07 用户反馈 | 保留此前兼容修复；新正常聊天不经过该解析器 | 原 ST-25 真实记录及本次 colloquial 离线回放 |
| ST-26-A | 同一请求经历主模型改写及第二模型分段，再被校验裁剪 | 多层语义所有权、工具参数说明矛盾 | 07 用户授权升级 | 主模型输出结构化字段；工具不重新解释；旧说明同步 | 59 项结构化契约与实际工具/表单链测试 |
| ST-26-B | 更新周几或月几号可能保留旧 cron | 适配器原来只识别 time_str 更新 | 同上 | 相关日历字段改变时重算 cron，保留原固定钟点 | calendar_patch 四组 |
| ST-26-C | 未命中的 task_id 可能回退到名称；错误显示成功 | 查找兼容回退及文字结果未标错误 | 同上 | 新模型入口禁止 ID 失败后转名，文本失败映射 AgentResult.error | supplied_unknown_id 四组；工具错误回归 |
| ST-26-D | 面板模型提取本人企微后无法匹配本人渠道 | 收件匹配只处理姓名/群名 | 同上 | 仅解析当前用户已有绑定；无绑定仍补选 | 前后端本人渠道用例 |
| ST-26-E | PostgreSQL 首轮 27 项 setup error | 沙箱拒绝 shmget，共享内存初始化未完成 | 验证环境 | 自动审批允许隔离数据库测试后原命令复验 27 通过；非业务失败 | sandbox-failure / postgres 两份日志 |

原依赖“模型调用 description 后再解析”的入口断言已改为新结构化调用及 parser.assert_not_awaited；旧 description-only、旧解析接口的行为测试仍保留。旧全量 schema 对照只放行此次已授权的两个可选字段及说明，保留其他工具、参数及策略的原始快照。未删除失败测试或扩大业务权限来凑通过。

前端故意模拟解析失败的测试输出一条错误日志；Vite 提示部分包超过 500 kB，构建仍成功。没有浏览器连接生产、创建真实任务或发送通知。新增真实 Qwen 6 次联调仍待授权，不能把本地替身结果当作模型稳定性证明，也不能承诺主模型永远不遗漏或误解自然语言。

## 5. 用户验证单（部署版本待填写）

仅在用户另行指令“提交部署”后验证对应候选：

1. 聊天创建：“创建一个定时任务。查询昨天的付款订单数按照平台划分，每天八点钟发给我看”。卡片业务内容只保留查询、日期和按平台汇总；每日 08:00；普通授权请求最终显示已创建，任务列表一致。无需为了创建先执行一次查询。
2. 只说“创建一个定时任务，查询昨天的付款订单数按照平台划分”。缺项表单应保留业务内容，补选频率和时间后提交；未选不能提交，失败不能清空输入。
3. 对同一任务说“时间改到十点”，再说“输出改成表格”。原店铺/条件/日期/分组不变；任务 ID 不变。再改每周一，核对实际下一次执行时间与卡片一致。
4. 选择自己企微或网页，运行一次，检查原结果卡片和对应通知渠道；随后暂停应有成功反馈且不再自动触发，恢复保留同一任务；删除展示确认，确认后从列表消失。真实业务测试资源和发送渠道由用户指定。

## 6. 结论、回退与交接

实现和本地自动化复验已完成。**总体技术验收尚未通过：ST-26-07 新模型协议实测未验证；未部署、未完成用户验收，板块 07 不关闭，08 不启动。** 用户可以授权最多 6 次合成解析后补齐模型证据，或明确将该项改为部署后的用户验证范围；未得到回复前不代为豁免。

无新数据库迁移，无新持久化任务定义字段：definition/recipient 在输入阶段转换为原任务字段，任务与 ChangeSet/RPC/通知载荷继续使用原协议。表单新增 `_structured_input` 标记，因此完整功能回退应回退工具 Spec/工具入口/表单适配/面板解析调用同一批代码至 `d16adf19`，不能只撤后端工具参数而保留新版面板。旧版不识别该标记，新版未提交的补丁表单在回退后应取消并重新发起；不宣称旧版本能正确提交这类未完成表单。已落库的任务、ChangeSet 和执行授权快照维持旧协议，不需要数据迁移。ST-25 修复可独立保留在旧解析兼容层。

旧的 parse_task_nl/parse_task_request、ChatTaskManager 默认构造、config/helper 和旧路由继续属于业务/输入兼容入口；策略事实仍来自 Registry/ToolSpec，最终提交和确认仍使用原服务。部署与关闭继续使用受控脚本，不直接运行 deploy.sh。

## 7. 本次提交部署指令

用户在收到上述实现结果及真实 Qwen 联调未验证说明后，明确指令“提交部署”。按此指令执行前后端技术发布供测试；该指令不记作模型复验通过、不代替用户验收关闭，也不额外调用 Qwen。最终候选 SHA、构建/测试及线上自动验证结果以本次受控发布的 RELEASE_RESULT 和交付消息为准。
