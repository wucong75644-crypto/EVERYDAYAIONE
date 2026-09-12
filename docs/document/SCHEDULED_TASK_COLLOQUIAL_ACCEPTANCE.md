# ST-25：完整口语请求被清空的修复验收

> 后续 ST-26 已获用户授权并在同一工作树实现：本文是旧解析协议的历史实测，口语兼容修复仍保留；新交互入口和当前验收以 [ST-26](SCHEDULED_TASK_STRUCTURED_ACCEPTANCE.md) 为准。原三次实测不能替代新工具协议实测。

## 1. 范围与版本

2026-09-12，分支 `codex/task/20260911215117-tool-unification-07`，基准 HEAD/已部署版本 `d16adf195d4e93df6007239eba4bdbec34f4ca9d`。本记录对应该基准上的未提交差异，不把基准当作修复后的候选。此前 ST-19～24 已随该版本完整部署，旧文档的“未部署”属于当时快照。

原话：“创建一个定时任务。查询昨天的付款订单数按照平台划分，每天八点钟发给我看”。截图显示执行内容为空，时间字段已隐藏。

实际链路：ToolExecutor 取当前用户原话 → ChatTaskManager → parse_task_request → Qwen 返回 changes/evidence/request_parts → execution_content 校验 → 缺项表单或原 _begin_request/ChangeSet 提交。首次真实调用证明 Qwen 正确返回业务原文、daily、08:00，但 execution_content 不接受时钟尾词“钟”和发送尾词“看”，返回 None；parse_task_request 因而移除 prompt，表单最终显示空白。这是校验语法覆盖不足导致正确模型结果被拒绝，并非模型未理解需求或前端丢字段。

仅修改 `task_request_content.py`：识别已有时间语义中的口语字符；按原文钟点位置接纳“钟”；识别发送句尾“看/看看/看一下/查收”；在来源片段漏掉这些尾词时按边界恢复。保留“看退款率”等带业务对象的要求，统一发送片段的标点裁剪位置，避免输出格式前缀截断。原词段来源、重叠拒绝、时间值核验保持；不以模型改写或整个原请求兜底。

## 2. 逐项验收

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| ST-25-01 | 截图原话得到业务执行内容、daily、08:00，无 prompt 缺项 | 修改前真实调用缺 prompt；修改后两次真实调用 missing_fields 均为空，业务原文完整 | 通过 | [修改前](scheduled-task-colloquial-evidence/before.json)、[复验 1](scheduled-task-colloquial-evidence/after-1.json)、[复验 2](scheduled-task-colloquial-evidence/after-2.json) |
| ST-25-02 | 普通/口语钟点、发送句尾、短证据与漏尾词，不能把管理话语留入执行内容 | 25 种钟点/发送组合及短片段两项通过；保留朝晚时间区别 | 通过 | `test_scheduled_task_colloquial_request.py::test_clock_and_delivery_grammar_preserve_business`、`test_short_clock_evidence_and_omitted_grammatical_suffix` |
| ST-25-03 | 业务条件、输出格式不得误删；错时间继续补全，缺频率不补默认值 | 三种业务后缀、带标点输出格式、错误时钟、缺时间表单通过；原错误分段/遗漏/重叠保护仍通过 | 通过 | 同文件的 delivery、wrong_clock、missing_schedule 用例及 `test_scheduled_task_request_content.py` |
| ST-25-04 | 真实工具入口把三次模型输出交给既有提交边界，保留当前用户原文和结果卡片引用 | Registry/Dispatcher/ToolExecutor/ChatTaskManager/解析器真实执行，_begin_request 收到正确业务、时间、当前用户网页目标，返回 ChangeSet 引用 | 通过 | `test_reported_request_reaches_original_submission_boundary` 三组；仅模型/身份目标读取及最终副作用边界替身，不是真实落库证明 |
| ST-25-05 | 既有解析、表单、授权、提交和结果行为不回退 | 1072 passed，0 failed/skipped；含六条此前真实模型原始输出离线重放，与已验证结果完全一致 | 通过 | [完整定向回归日志](scheduled-task-colloquial-evidence/regression.txt)、[命令和指纹](scheduled-task-colloquial-evidence/validation.json) |
| ST-25-06 | 部署后用户原话创建，核对卡片和实际任务内容 | 当前候选未部署，未写生产任务 | 未验证 | 第 5 节用户验证单 |

## 3. 环境、回归与问题记录

使用工作树 backend、主项目 venv Python 3.12、APP_ENV=testing 和专用假数据库/密钥配置。定向测试不访问生产库、ERP 或通知。最终测试范围为解析/补全、创建适配、直接提交、聊天结果、任务路由、旧 helper/ToolExecutor、Registry/Policy 与生产工具入口；前端源码/协议无变更，本次未重跑前端构建或浏览器。

用户明确允许截图原话发送给项目现用 Qwen 最多三次：修改前一次、修改后两次，已全部使用。通过同一 parse_task_request/_call_llm 读取项目现用模型配置，仅解析文字；未调用任务创建、业务查询或通知。两次后验任务标题略有差异（一次模型未给名称，沿用既有内容截取默认名称），业务内容、时间、收件对象一致。原始输出已去除运行配置，仅保存获准文字和解析对象。

| 问题编号 | 复现 | 根因/影响 | 所属板块 | 处理结果 | 复验证据 |
|---|---|---|---|---|---|
| ST-25 | Qwen 输出正确但 UI 要求重填业务内容 | 元数据语法不接受口语尾词，整段校验失败后 prompt 被删除；旧测试没有覆盖这类口语和真实输出到创建分支 | 07 用户反馈 | 在原来源校验层增量修复，新增固定真实输出回放和提交分支断言 | 修改前新增 22 项失败：[日志](scheduled-task-colloquial-evidence/before-tests.txt)；最终 1072 项通过 |

测试建立时第一次因证据文件误放目录导致收集失败，修正文件位置后才执行上述修改前失败复现；未将收集失败当作根因证据。无本批已知未修代码问题；生产用户验证尚未完成。不宣称有限语法覆盖任意自然语言；来源分段不可靠时仍需补全表单。

## 4. 共同约束、接口与回退

| 编号 | 证据 | 状态 |
|---|---|---|
| G-01 | 唯一运行源码差异为 task_request_content.py；没有 ERP、沙盒、媒体、调度或提交内核重写，没有 08 内容 | 通过 |
| G-02 | ST-25-01～05 有成功、拒绝及边界断言；生产用户项单列未验证 | 通过 |
| G-03 | 基准失败、最终定向回归、真实模型前后证据完整；未删除/削弱旧断言或增加跳过 | 通过 |
| G-04 | 无工具 schema/参数别名、旧 API、WS、消息落盘或授权快照变更；原 Registry/Policy/Dispatcher/ToolExecutor 回归通过；无请求级全局变量或新权限路径 | 通过 |
| G-05 | 本记录、07 验收与交接同步；版本、命令、源码指纹、回退与限制可追溯 | 通过 |

无需数据库或消息迁移。本批可撤销 task_request_content.py 差异回到 d16adf19，同时保留已部署 ST-19～24 的消息兼容/表单修复；这会恢复本次口语缺陷。不要回退到缺少 changeset ContentPart 的更早 schema。legacy 仍只是原业务适配；本批未新增任何执行或授权入口。

## 5. 用户验证单

待用户“提交部署”后记录确定候选 SHA，使用获准测试任务验证：

1. 发送截图原话。任务摘要中的执行内容应为“查询昨天的付款订单数按照平台划分”，时间每天 08:00；不再出现要求重填执行内容的空白表单。检查原有提交结果卡片和列表中任务内容一致。
2. 发送去掉“每天八点钟”的同一句。应保留整理好的执行内容，只要求补齐执行频率/时间，不擅自推断每日。

实际定时执行、ERP 数据和通知不在本次解析授权内，未用模型解析通过冒充生产运行成功。旧截图中的任务/消息没有被回填或修改。

## 6. 结论与交接

本批根因已复现，本地修复与相关技术回归通过。修复候选未提交、未部署，用户验收仍待完成。07 定义迁移的既有验收证据仍在 [07 验收](TOOL_UNIFICATION_ACCEPTANCE_07.md)，本次反馈未关闭前不进入 08，不合并 main 或清理任务工作树。
