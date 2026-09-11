# 05 上下文结果恢复

> 2026-09-11 最新生产复验：1df0e01c 的工作区插入与重新上传均出现模型未识别当前附件。已准备当前 user 消息绑定附件的候选，718 项回归及 6 次真实模型合成对照通过，尚未部署，原生产场景待复验；05 不得标记技术通过或进入 06。见 [附件绑定调查与增量验收](TOOL_UNIFICATION_05_ATTACHMENT_BINDING.md)。以下保留此前实施记录。

2026-09-11：用户已要求恢复架构中被破坏的结果保留与状态更新逻辑。在原 05 工作树、HEAD 887b28ae 加既有第 1/2 项差异上实施，不回退整个历史提交。

恢复的约束：完成回复有无正文不应决定结果是否存在；最近工具状态来自本次调用结果，不能由累计失败工具名推断。Grok Build 的独立工具记录与逐次结果回填提供机制对照；本项目仍使用原 messages blocks 和旧 ledger/checkpoint，不引入 Grok 存储格式。

实施地图：

1. `chat_context/history_outcomes.py`：从现有 blocks 生成交付事实，独立于正文；覆盖表单、文件、表格、图表、媒体、工具返回/失败/取消。工具步骤只取名称、调用 ID、明确状态，不回灌输入代码或原始输出。产物记录只取必要识别字段，去除重复记录，不复制表格数据或表单内容。表单只记稳定的交付事实，不把可变 submitted 状态当任务已生效。
2. `history_loader.py`：完成轮次加入上述结果；无正文也生成 assistant 历史；保留最新中断恢复协议。缺少可用 blocks 的旧 digest 仍有独立落点，不重复追加已有工具状态。
3. `conversation_cache.py`：隔离新的历史投影缓存 key，保持 v2 缓存载荷结构；旧进程/旧投影不能污染新 key，数据库消息不改。
4. `tool_loop_context.py` / `chat/tool_loop.py`：一次接收完整结果批次，保留 call 与 ToolResult；由真实业务/执行状态生成最近批次摘要，不做跨文件恢复推断。原失败记录和工具消息仍保留。替换唯一的动态摘要块，包括旧 checkpoint 中可识别的旧摘要，不删除正常 system 提示。

链路补充：新增压缩测试证实，只修历史入口后，总预算、历史预算、循环摘要三处仍会丢失已补回的结果。同步修改 `context_compressor/budget.py`、`summary.py`，归档正文时保留同一条 assistant 的确定性交付事实；不把是否保留交付事实交给模型摘要。普通正文、旧工具结果和最近工具配对继续原预算规则。

兼容边界：不改 Actor lease/输入锚点、PromptBuilder 顺序、权限、工具参数、模型原工具结果投影、WS/delivery、表单终止、emit/sink、审计 writer 或旧 serializer。现有冻结 checkpoint 不重算历史；恢复后产生的新动态摘要按正常工具批次更新。

验证先固定同一输入在原代码复现，再执行历史→缓存→快照→模型请求的集成；比较失败→成功、不同文件、并行部分失败、六业务状态、取消、uncertain、重复动态提示；复验原 05 协议/产物/ledger/表单终止和历史预算。用合成文件和离线模型传输，不把 Mock 响应当真实模型意图验证。真实模型若无独立测试凭据则明确未验证，不能宣布旧目标误用已根治。

回退：应用回到 887b28ae；旧代码使用旧缓存 key，不读取新投影。ledger/checkpoint 格式无变更。既有 253 迁移保持独立回退规则。本轮不部署、不合并、不清理；最终结果与 A/G 验收在实施后补齐。

## 实施结果与版本

以上代码已完成。分支 `codex/task/20260910221341-tool-unification-05`，工作树 `/Users/wucong/EVERYDAYAIONE/worktrees/tool-unification-05`，被测版本是 HEAD `887b28ae4aedb5d4dde5f0cbbd1bcb4484fda9ba` 加本任务未提交差异，包含此前第 1、2 项。当前没有新的已部署提交，生产仍是旧候选。

恢复的是结果与正文解耦、逐调用状态、压缩后完成事实仍在这几个行为；没有整段回滚 7 月/8 月提交，也没有移植 Grok 的 Rust Actor 或持久化格式。既有表单终止和中断恢复保留。

实际接口：

- `project_completed_assistant(content)` 从现有 blocks 生成一条 assistant 历史，正文缺失不再导致表单/文件/表格等消失；显式 tool_step 的状态优先于旧 digest 推断。completed 只表示调用已返回，不推导业务成功。
- `archived_outcome_content(content)` 在总预算、历史预算、循环摘要压缩中保留确定性交付记录，普通正文仍压缩；记录留在原 assistant 位置。表格行/图表代码/表单 fields 不回灌；同名但内容不同的表格不合并，同一引用重复展示记录去重。
- `ToolLoopContext.update_from_batch(results)` 保留完整本批 call/ToolResult，模型摘要分别表示业务 status 与 execution.status。`recent_results` 只存在内存；原调用参数、错误、retry_context、tokens、thinking、metadata 仍在原结果中。保留旧 `update_from_result` 门面及历史 failed_tools，但不再把累计失败解释为当前失败。
- `is_tool_context_message` / `replace_context_prompt` 为 prepare、预算去重与循环摘要共用；只处理本组件消息。旧 checkpoint 中的既有动态提示可识别替换；普通 system 规则不会因提及“失败工具”而被删。
- 缓存 key 从 `conv:msgs` 隔离为 `conv:msgs:outcomes-v1`，载荷仍是原 v2。旧 key 自行过期，不清除数据库历史；旧代码和新代码滚动期间各写自己的投影。

## 验收证据

新增 [44 项恢复测试](../../backend/tests/test_context_result_restoration.py)，同一份最终测试在干净导出的 HEAD 上 **44 failed**，当前全部通过。包括无正文的 10 类代表结果（blocks/JSON 两种编码）、重复/同名产物、可变表单状态不冒充业务成功、旧 digest、八类结果/执行状态、不同文件、并行批次、正常规则保护、缓存隔离、三条压缩路径，以及真实 history→cache→ContextSnapshot→DashScope 请求体→旧 checkpoint 链路。

最终相关回归 **986 passed、0 failed/error/xfail、3 skipped**。3 项是旧 `TestBuildLlmMessagesGatherDegradation` 整类已有 skip，原因是 V1 gather 已由 PromptBuilder 取代；AST 与 HEAD 相同，未改 skip 或其中断言。当前 PromptBuilder 与实际快照链路已执行测试。没有把这 3 项计入通过。

命令：

```bash
bash docs/document/tool-unification-evidence/run-05-context.sh
PYTHONPATH=backend /Users/wucong/EVERYDAYAIONE/backend/venv/bin/python \
  docs/document/tool-unification-evidence/check-05-context.py
git diff --check
```

环境：Python 3.12.12，现有 backend/venv；测试工作树没有应用 .env，DATABASE_URL/Redis 为不可用测试地址，仅使用合成记录、文件、假 Redis/DB 响应与离线 HTTP transport。缓存和输入投影函数实际执行，非 Mock 替代。配置与依赖弃用警告保留。

证据：[原代码 44 项失败](tool-unification-evidence/05-context-before.txt)、[最终 986 项结果](tool-unification-evidence/05-context-regression.txt)、[源码/18 份未修改协议夹具/旧 schema/缓存结构/skip 对照](tool-unification-evidence/05-context-source-checks.json)。第 1 项迁移及数据库测试文件指纹与前次 8 项 PostgreSQL 通过时一致，沿用该证据，不重复执行生产迁移。

中间失败及复验：最初测试误用了结果属性名，并把取消当作可普通返回，按现有 `display` 与取消传播契约修正测试；缓存集成最初受到全局 cache-miss fixture 拦截，后显式绑定真实缓存函数，只替换 Redis IO。扩大回归发现旧测试桩缺少新批次方法，更新对应桩；旧空图片用例同步断言“尚无可用结果”，继续禁止虚构生成成功。修复后完整相关组复验通过。没有增加跳过、删协议字段、改金样或用无条件默认成功通过测试。

`R` 为新增恢复测试，`I` 为 [05 原消费集成](../../backend/tests/test_tool_result_consumption.py)，二者均在当前 986 项中。

| 验收编号 | 场景及预期 | 实际结果 | 状态 | 证据 |
|---|---|---|---|---|
| A-05-01 | Chat/Loop 原模型投影，必要状态/引用保持 | 原结果投影逐值相等；历史交付与最新用户分别保留；不重新注入旧代码 | 通过 | R 请求体/中断/状态用例，I 原投影对照 |
| A-05-02 | WS blocks、事件顺序、delivery 元数据兼容 | 18 份原金样逐值一致，夹具未改；动态归一化规则未放宽 | 通过 | I `test_live_chat_protocol_matches_main`、源码检查 |
| A-05-03 | 不丢不重、表单原流程、长文本保留完整产物 | 原产物/表单集成及新历史/压缩用例通过；真实模型目标与人工观感尚未复验 | 未验证 | R 非文本/三压缩路径，I 文件/图片/ERP/表单/长文本；用户验证单 |
| A-05-04 | 六业务状态、取消、uncertain、重试及元数据准确 | 最近批次逐调用取值；旧失败仍留工具消息，其他文件成功不推导恢复；取消继续抛出，uncertain 不自动重试 | 通过 | R 八状态/不同文件/并行，I 原状态/审计/真实循环停止 |
| A-05-05 | 旧 ledger/checkpoint，审计与展示不重复 | 原持久化入口及模型结果投影未改；新历史与动态提示是旧 messages 字典，未持久化 ToolResult；缓存仅 key 隔离 | 通过 | R checkpoint 相等，I serializer/ledger/审计用例，源码检查 |
| G-01 | 授权范围内恢复上下文结果机制 | 已说明历史、缓存、状态、压缩共同变化；无 Actor lease/权限/业务引擎重写或 06 载荷切换 | 通过 | 实施地图与当前 diff |
| G-02 | 必需行为全部具备证据 | 确定性机制已验证；真实模型仍未验证 | 未验证 | A-05-03、下方限制 |
| G-03 | 原故障复现与相关回归 | 原代码 44 failed → 修复后 44 passed；相关总计 986 passed、3 既有 skip | 通过 | 两份日志、skip AST 对照 |
| G-04 | 原合法参数、API、模型/WS/持久化契约 | 合法参数结构/别名、Actor 输入、原结果/WS/ledger 不变；历史模型投影按本次授权恢复 | 通过 | 源码检查、R/I |
| G-05 | 实际接口、问题、验证、回退可交接 | 本文与交接/验收/问题表已同步 | 通过 | 本文及统一交接 |

## 问题清单与用户验证

| 问题编号 | 复现 | 根因/影响 | 所属板块 | 处理结果 | 复验证据 |
|---|---|---|---|---|---|
| P05-3a | 无正文完成回复下一轮为空 | 正文被当成结果存在条件 | 05 验收修复 | 本地机制已恢复 | R 非文本、真实快照/缓存/请求体 |
| P05-3b | 补回的结果经预算或摘要又消失 | 压缩整条 assistant，可能留下旧用户目标却丢掉交付事实 | 05 验收修复 | 三条压缩路径保留独立事实 | R 总预算/历史预算/循环摘要 |
| P05-4 | 失败后成功仍提示上轮失败，提示重复 | 累加失败名替代逐调用状态，旧提示未替换 | 05 验收修复 | 真实结果批次 + 单个归属明确的动态块 | R 状态、不同文件、并行、去重 |
| P05-3c | 最新“读取文件”后模型继续旧统计目标 | 输入锚点正确；模型目标选择的因果贡献尚未确定 | 05 生产验收 | 未验证，未关闭 | 无真实模型复验，不能以离线回复冒充 |

当前环境未提供独立模型测试凭据，没有加载生产密钥或调用生产模型。因此可以确认上下文信息和状态机制已恢复，不能宣称“模型继续旧目标”的现象已根治，整块技术验收仍未通过。

待后续获指令部署确定候选后，最少验证：

1. 保留旧统计/导出历史，插入新的测试表，仅说“读取文件”：应读取新文件，观察是否额外统计/导出；再明确说“按刚才方式统计并导出”作为反向对照。
2. 展示任务表单后继续聊天：模型应知道曾提供表单；表单仍可按原流程提交/取消，不能仅因展示表单就称任务已创建。
3. 核验文件/图片可打开、表格没有重复；参数错误后正确读取成功时，不应一直出现“上轮失败，换工具”。人工记录需绑定实际部署 SHA。

本轮已完成授权的上下文机制恢复与自动化验证；未提交部署、未合并清理。用户生产验收未关闭，不能进入 06。
