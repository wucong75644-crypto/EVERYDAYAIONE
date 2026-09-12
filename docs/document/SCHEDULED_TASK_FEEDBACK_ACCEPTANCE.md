# 定时任务通知、结果与操作反馈修复验收

## 1. 范围与版本

2026-09-12，用户反馈通知渠道不能选择、执行成功无内容、创建后仍显示检查中、删除无响应、暂停无文字反馈。本批在原任务工作树 `codex/task/20260911215117-tool-unification-07` 增量修复。基准/当前 HEAD 为已部署的 `002286f0057ee53bd7b96ece9ab031a543efce97`；本批候选是该提交加工作树未提交差异，不能把基准 SHA 当作本次已测试候选。

没有部署、合并或关闭任务，没有生产业务写入、删除、重新执行、通知发送或真实模型调用。生产只读核验确认：测试任务已创建并暂停；手动运行成功，保存了 149 字摘要及 table/file/chart 三种结果；删除请求仍为 awaiting_approval。精确 ChangeSet 引用检索发现，创建表单在历史消息中，聊天删除和暂停的引用未保存。未读取业务表格行、报告正文或文件 URL。扩大读取聊天片段的请求曾被自动审批拒绝，随后改为精确测试任务/变更的元数据查询，未绕过拒绝。

## 2. 问题与逐项证据

| 编号 | 场景、根因及修复后预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| ST-19 | 补全表单把已有默认通知目标隐藏；面板有企微绑定时自动选企微。现在补全表单保留通知目标下拉框；面板“推送给我自己”明确选择网页/企业微信个人，未绑定企微不可选，新建默认网页，编辑保留原渠道；既有同事/群入口继续使用原权限 | 两种选择均进入相应 DTO；编辑和未绑定边界通过 | 通过 | `test_chat_task_manager.py::test_completion_form_keeps_default_notification_target_selectable`；`TaskForm.test.tsx` |
| ST-20 | `result_files` 实际含 emit table/chart/file，前端只读 url/name；手动运行没有展开结果。历史 API 在原鉴权后增加兼容 `content_blocks` 投影，复用 emit 转换器；前端复用表格/图表/文件组件，保留完整已存摘要和旧文件链接。手动运行自动展开，执行中每 5 秒补查；结束/卸载停止，交互不会收起结果 | 混合结果 API、真实表格组件、旧文件、漏事件刷新与停止通过 | 通过 | `test_scheduled_tasks_routes.py::TestRunsAndChatTargets::test_list_runs_returns_history`；`TaskRunHistory.test.tsx`；`TaskCardChangeSet.test.tsx` |
| ST-21 | 已提交表单的 result_message 是初始受理回执，无法随 ChangeSet 状态更新。已关联 ChangeSet 的提交表单由实时卡片独占结果展示；消息模型补齐 form.change_set_id，保持刷新关联 | 真实 FormBlock + ChangeSetCard 显示“已创建”，没有旧检查提示；最终序列化保留关联 | 通过 | `ChangeSetCard.test.tsx`、`FormBlockChangeSet.test.tsx`、`test_chat_outcome_builder.py` |
| ST-22 | 删除已生成待确认变更，但最终消息转换不支持 changeset，丢弃卡片；后端 ContentPart 也缺该类型。补齐 ChangeSetPart、判别联合及最终转换，只存 id/resource_type 引用，状态仍由服务端读取 | 旧版同一输入最终变空，新版保留；legacy/actor、5 种状态、最终序列化和 API 判别读取通过；删除确认策略未变 | 通过 | [合成断点对照](scheduled-task-feedback-evidence/completion-probe.json)；`test_scheduled_task_chat_results.py`；`test_scheduled_task_direct_submission.py` |
| ST-23 | 聊天暂停卡片同样丢失；面板收到 applied 后仅刷新列表。补齐卡片保存，面板 applied 后显示“已暂停/恢复/删除『任务名』”；受理手动运行显示开始提示 | 聊天 applied 引用保留，面板暂停成功 toast 和原提案 API 断言通过 | 通过 | `test_scheduled_task_chat_results.py`；`ScheduledTaskPanel.test.tsx`；`TaskCardChangeSet.test.tsx` |

ST-20 展示的是已经保存的摘要和结构化产物，不宣称补回未保存的完整执行逐字文本。`push_status=skipped` 对网页目标不能直接认定为通知发送失败；本批未改通知内核或企微发信流程。

## 3. 验证环境与摘要

后端在工作树 `backend` 使用主项目现有 venv Python 3.12，测试专用 `DATABASE_URL=postgresql://test:test@127.0.0.1/test`、`JWT_SECRET_KEY=local-test-key`、`APP_ENV=testing`，不使用生产连接；最终定向回归 **478 passed，无失败/跳过**。前端在工作树 `frontend` 使用本地依赖，**146 passed，无失败/跳过**；最后仅调整测试等待异步选项，TaskForm 11 项再次通过。`tsc -b`、`npm run build`、`git diff --check` 通过。准确命令和候选源码指纹见 [验证摘要](scheduled-task-feedback-evidence/validation.json) 与 [源码指纹](scheduled-task-feedback-evidence/source-checks.json)。

首次新增保存边界测试把旧 golden 的耗时占位字符串直接送进 schema，导致 10 项测试失败；已在测试边界还原数值耗时，保留全部卡片断言。首次 TS 检查发现 DiagramBlock 缺展示标识，已使用独立 scheduled-run 标识补齐，复验通过。

补充 ESLint 检查有 1 项**未通过**：ScheduledTaskPanel 原有关闭时同步重置 state 的 `react-hooks/set-state-in-effect`。用 `git show HEAD:frontend/src/components/scheduled-tasks/ScheduledTaskPanel.tsx | eslint --stdin --stdin-filename ...` 复现同一错误（基准第 69 行，候选第 70 行），该代码未改，未屏蔽规则，不阻塞本批反馈目标。构建有大 chunk 提示；面板测试的 framer-motion DOM mock 有原有 whileHover/whileTap 提示。没有以此声称仓库全部 lint 或全量测试通过。

## 4. 共同约束与接口

| 编号 | 证据 | 状态 |
|---|---|---|
| G-01 | 只改消息落盘投影、表单/结果视图及反馈；通知默认值与可选渠道已明示。无 ERP、沙盒、媒体、调度/提交内核重写；无板块 08 | 通过 |
| G-02 | ST-19～23 逐项覆盖成功、缺绑定、旧数据、删除确认、漏事件与卸载；真实生产界面需部署后用户复验 | 通过 |
| G-03 | 478 后端、146 前端及类型/构建通过；补充 lint 的基准复现、影响判断如上，没有删正确断言或改为跳过 | 通过 |
| G-04 | 工具名/schema/参数别名、Policy/Planner 授权和风险不变；结果/回放、Registry、ChangeSet 权限测试通过。WS 的 changeset 形状原已存在；本批补齐后端最终 ContentPart。历史 API 原字段保留，只增加可选 content_blocks；fetchTasks 原无参调用保留，新增 quiet 选项仅减少加载闪烁。没有全局请求数据或新权限执行入口 | 通过 |
| G-05 | 下述调用链、回退限制、原问题恢复入口及用户验证均记录；07 总体验收继续保留原状态 | 通过 |

实际链路：ChatTaskManager → 原 ToolExecutor/ToolResult → ChatToolResultMixin 引用 → execution_engine → outcome_builder/ContentPart → 最终消息/API → ChangeSetCard → 原带授权的 ChangeSet API。修复落在最后两处缺失的消息转换。定时运行仍由原 Executor 保存结果，`GET /scheduled-tasks/{id}/runs` 在原组织与 task.view 校验后兼容投影，TaskRunHistory 展示；不重新执行工具生成展示结果。

## 5. 回退与限制

无数据库迁移，不回填或改写历史任务/消息。旧记录可从运行历史展示已有产物；此前已经丢弃的聊天卡片不能凭空重建。生产原删除请求仍可从“待处理变更”查看并由用户确认/取消，过期或冲突沿用原恢复机制。

**不能在新 changeset 消息写入后直接整包回退到 002286f0。** 合成 [回退兼容探针](scheduled-task-feedback-evidence/rollback-probe.json) 证明旧后端 ContentPart 拒绝该已有 WS 类型（union_tag_invalid），而新后端可读。若回退 UI/历史结果投影，须保留本批 `schemas/message.py` 的 ChangeSetPart/Form 关联兼容和 `outcome_builder.py` 转换后生成新候选；原前端已经支持 changeset。不得通过删除消息进行回退。本轮未进行生产回滚演练。

未验证项：新候选生产浏览器观感、真实企微个人/群到达、真实文件下载/图表交互；全部为部署后用户验证，不能把 mock 当实际送达。没有新增渠道提供商或扩大收件权限。

## 6. 用户验证与交接

等待用户指令“提交部署”，记录新候选 SHA 后，以同一测试任务核验：

1. 新建/编辑中选择网页或已绑定的企微个人通知，确认摘要显示对应渠道；未绑定时企微不可选。已有同事/群入口按当前账号权限可见。
2. 点一次“立即执行”：立即展开结果并提示已开始；结束后看到摘要、表格/图表和文件。刷新页面再次展开仍可见。
3. 聊天补全并创建：卡片最终显示“已创建”，不同时保留“正在检查创建请求”。
4. 暂停/恢复：聊天有成功卡片，面板有文字提示；刷新状态一致。
5. 删除专用测试任务：先出现待确认卡片；取消不删除，确认后显示已删除并更新列表。此前丢失的待确认请求从“待处理变更”进入，不需要重复创建删除请求。

本批本地修复目标验证通过；补充 lint 原有问题保留如上。尚未部署和用户验收，不能将 07 标记用户通过或进入 08。01～07 原验收入口仍见 TOOL_UNIFICATION_HANDOFF.md，本文件是本次用户复验缺陷的增补证据。


## 7. 提交部署门禁失败及复验（2026-09-12）

首轮候选 `b4f03f925d7397ec6b75fa20ae70bd845b832429`：前端已发布，后端全量 9913 passed、1 failed、37 skipped、4 xfailed，失败于 `test_scheduled_task_request_content.py::test_form_exposes_the_extracted_instruction_before_user_submits_schedule`。共用 JSON fixture 仍把 push_target 写为 hidden，与 ST-19 的 select 契约不一致。修复只更新该字段及对应测试：保留完整表单相等断言，新增目标 select/options 断言；前端由“唯一 combobox”改为分别定位频率与通知选项，测试动画层使用 DOM mock，保留可见性与提交字段检查。

后端 `test_scheduled_task_request_content.py test_chat_task_manager.py` 68 passed；前端 `StructuredConsumers.test.tsx` 12 passed。生产旧候选已失效，没有进入后端同步或重启；已只读确认主要服务 active、无在途部署，发布锁所有者对应本地进程 6312 已退出，随后精确匹配该 owner 后释放锁。正在按用户原“提交部署”授权重新生成候选并完整发布，不能将首轮部分发布视为验收成功。


## 8. ST-24：未选频率的创建请求（2026-09-12）

基准为已部署 `d851d93eb47f28c0cde5f77938b2505a8322c3b9`，候选为本节对应的未提交差异。截图中“执行频率”仍为“请选择”，08:00 只是时间，不能据此推断每天执行。根因是 FormBlock.handleSubmit 未消费 required 标记，直接派发 WS 提交事件；Adapter 正确拒绝缺失频率，但报出技术字段名。没有证据表明用户已选频率后丢值，未把该情形认定为根因。

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| ST-24-01 | 未选频率时本地提示“请选择执行频率”，不发请求，内容/时间保留；改选后提交 | 基准两项定向复现失败，修复后真实 FormBlock 交互通过 | 通过 | StructuredConsumers.test.tsx 的 missing frequency 用例 |
| ST-24-02 | 仅检查当前可见必填项；单次任务需日期，隐藏的每日时间不阻止；取消不受校验阻止 | 日期缺项提示、取消和已有单次提交用例通过 | 通过 | StructuredConsumers.test.tsx |
| ST-24-03 | 绕过前端时后端仍拒绝空频率，提示用户可理解，不能补默认频率或进入创建 | None/空字符串均抛“请选择执行频率”，既有适配器和 WS 表单回归通过 | 通过 | test_scheduled_task_changeset_adapter.py |

前端 30 passed、后端 86 passed，TS、本次修改组件 ESLint 与生产构建通过（保留已有大 chunk 提示）。精确命令及四个源码指纹见 [本批证据](scheduled-task-feedback-evidence/required-fields.json)。验证用合成消息和测试 DB 替身，不读取或改写生产任务、不调用真实模型、ERP 或通知。

G-01～05 增补：仅恢复表单既有 required/visible_when 契约和错误文案，渲染与校验复用同一可见性判断；旧调用、隐藏字段、提交参数、服务端权限/调度/创建状态机保持原样，无迁移或新增持久化类型。可回退本次小补丁到 d851d93e；不得因此丢掉前批 ChangeSet 消息兼容补丁。本批本地行为回归通过，未部署、未用户验收，不进入 08。用户当前可先选择频率继续创建；新候选部署后再验证缺项不会发请求、补选成功以及单次日期条件。
