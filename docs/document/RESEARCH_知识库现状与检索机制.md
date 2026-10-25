# 知识库现状、公开机制与旧库退役调查

> 历史研究记录：以下“当前”指 ea97ae93 主线，不代表生产。2026-10-25 已按用户要求完成生产只读核查；现状、数据规模与旧库使用以 [生产核查](./PROD_知识库只读核查与方案修订.md) 为准，实施建议以 [修订提案](./TECH_AI知识自动检索机制_提案.md) 为准。生产已存在自动范围、语义检索、派生和媒体提取，不应按本历史缺口清单重复建设。

日期：2026-10-25，Asia/Shanghai。状态：研究结论与建议，未实施、未获部署授权。
代码基线：`ea97ae936b2d8b0506a7eccfc65cc8c13f4e6100`（调查开始时最新 origin/main）。
配套：[自动检索独立机制提案](./TECH_AI知识自动检索机制_提案.md)。

## 1. 调查边界与此前结论更正

用户要求先充分查清，再给出新库自动查询与旧库保留/清理方案，并单独设计自动检索机制。本次未修改应用代码、依赖或数据库迁移，未连接生产、未读取生产业务内容、未调用付费模型。

此前使用的主工作树 HEAD 为 `97be5248`，落后于最新主线。最新主线已增加 V2/V3：文件绑定、正文抽取、检索、主聊天 @ 根引用、快照、四个知识工具和来源定位。此前“新库没有接入聊天”仅适用于旧版本，不能代表当前主线。现有生产部署 SHA、真实数据量和调用频率本次未核验，本文不将主线等同生产。

证据等级：P=当前代码直接证明；T=本次隔离实验验证；H=既有开发记录，未在本次重演；E=实际打开的官方外部资料；R=基于上述证据的推荐。不存在可证明“与大厂一样准确”的现有测量结果。

## 2. 当前真实调用链（P/T）

### 2.1 新库不是未接入，而是显式引用后按需读取

1. 前端 @ 候选来自知识根查询，引用以 `knowledge_refs` 独立字段发送和保存，不只是文本标签。
2. `knowledge_workspace/message_intent.py::prepare_knowledge_intent` 验证引用、当前会话与身份；只有非空引用才写入 `_knowledge_refs_required`；清除客户端伪造的内部参数。
3. `knowledge_workspace/runtime.py::prepare_knowledge_run` 在 required=false 时立即返回，不读取知识库；这也是 `test_plain_chat_does_not_acquire_knowledge_scope` 的预期。
4. `KnowledgeRun.start` 从持久化输入消息读引用，创建/复用固定版本快照，冻结对象、文件、真实关联和适用规则元数据，不把所有正文注入提示词。
5. `handlers/chat/stream_setup.py` 注入快照入口和导航说明；`tools/runtime_context.py` 设置 `knowledge_refs_present`；`tools/legacy.py` 才向模型展示四个新库工具。
6. 模型可自行 outline/search/read/follow_reference。统一 Registry/Policy/Dispatcher 做调用授权；新工具禁止通道个人上下文越界。
7. `execution_engine.py` 在结束时读取实际回执生成来源尾注；结束/异常清理本轮已物化副本。

结论：当前是“用户明确引用总分支 → AI 在该范围自主导航和查询”；没有“未引用时主动发现业务知识”的入口。旧 `search_knowledge` 与新 `knowledge_search` 是两个不同工具、不同数据源。

### 2.2 已有保护应复用

- 原消息引用、幂等摘要、Actor 恢复与知识快照分工已经存在。
- 固定内容版本与实时权限分离；撤权、文件解绑、根失效后拒绝新读取。
- root/item/link handle 与服务端快照对应，模型不能传 actor/org 获得权限。
- 个人库不因组织管理员身份开放；团队/项目沿用容器 ACL；新库数据库 FORCE RLS。
- 目录、片段、局部正文、完整正文、引用跟随、原件物化分别记回执。
- 文件进入当前执行者有限的知识输入目录，不开放上传者私人工作区。
- `related/reference` 只跟随实际关联目标，不自动开放目标整个容器；有引用深度、数量和游标边界。
- 新工具 `cacheable=False`，没有套用旧库的十分钟结果缓存。

### 2.3 精准度和体验的实际缺口

| 编号 | 当前实现证据 | 影响 |
|---|---|---|
| F01 | 没有 knowledge_refs 则 prepare_knowledge_run 返回 | 普通聊天不会主动发现新库 |
| F02 | chat.py search 使用 `query.lower() in (title+' '+body).lower()` | 完整问句、多词、同义表达容易漏检 |
| F03 | 快照对象按 UUID 顺序；搜索过滤后分页，没有相关性排序 | 首页结果不代表最有用证据 |
| F04 | 搜索逐项 `_text` 并授权；read 后 `_coverage` 又检查范围内各项 | 目录较大时存在大量数据库往返；本次没有做规模压测，不能断言具体延迟 |
| F05 | introduction 要求先 outline，但未预读适用规则；实际规则随 read 返回 | 模型直接回答或先调用执行工具时，规则可能尚未取得 |
| F06 | evidence_footer 在模型生成结束后追加来源/未读提醒 | 来源列表不等于逐结论有据；不能阻止先输出无依据结论或先执行 |
| F07 | `_coverage` 对整个输入 items 计算 full；未区分本次问题必要证据集合 | 定向问答也可能显示大量未读项；“读取完整”与“回答充分”混为一谈 |
| F08 | `rules_complete` 有截断状态，冲突判断是自然语言提醒 | 不能保证模型确实补读规则或识别冲突 |
| F09 | files.py 同步有界提取文本；PDF/DOCX/XLSX 有支持，图片/视频无 OCR/转写 | 能上传/预览不等于正文可检索；截断/失败必须单独处理 |
| F10 | 原始 XLSX 提取逐行转文本，未保留工作表名/精确行列锚点；PDF 缺页锚点 | 通用文本搜索不足以可靠分析表格或定位页码 |
| F11 | 定时 Agent 创建 ToolExecutor 未绑定 knowledge_run | schema 允许 scheduled 不代表现有定时链路已经支持知识读取 |

F02/F03 的具体实现位于 `backend/services/knowledge_workspace/chat.py::KnowledgeRun.execute`；画布 UI 的 `Repository.search` 也是字面包含搜索，按对象 UUID 分页，不是语义搜索。

### 2.4 本次验证（T）

使用已有 `backend/venv` Python 3.12，显式合成配置；测试自行启动临时 loopback PostgreSQL，创建合成账号、组织和数据，用真实 runtime/application 角色检查 RLS。没有使用应用 .env。

```bash
DATABASE_URL=postgresql://research:research@127.0.0.1:1/research \
JWT_SECRET_KEY=knowledge-research-synthetic-secret \
/Users/wucong/EVERYDAYAIONE/backend/venv/bin/python -m pytest \
tests/test_knowledge_chat_v3.py tests/test_knowledge_workspace_v2.py \
tests/test_knowledge_files.py -q --tb=short
```

结果：43 passed，4.87 秒。[保存的成功回归记录](./knowledge-retrieval-research/regression.log)。这是权限、版本、接口行为验证，不是模型检索质量评测。

补充[可复现探针](./knowledge-retrieval-research/probe.py)：在真实 KnowledgeRun 和临时数据库保存“客户签收后七天内可以申请退货。商品型号 AB-1023 不支持刻字。”。在backend目录沿用上述合成环境，增加 `PYTHONPATH=.`，运行 `python -m pytest ../docs/document/knowledge-retrieval-research/probe.py -q -s`。

| 查询 | 命中文档数 |
|---|---:|
| 退货 | 1 |
| 退货流程是什么 | 0 |
| 退货 七天 | 0 |
| 退回商品 | 0 |
| AB-1023 | 1 |
| AB1023 | 0 |

探针 1 passed，0.92 秒，记录 `/tmp/knowledge-research-probe.log`。只检索片段后调用 evidence_footer 返回“尚未读取正文”提示，不抛出阻止最终回答的异常。该探针验证边界，不代表所有长问句必定失败：模型可能自行把问句改短，但目前没有可保证或可测量的查询规划。

初次运行选用了缺少 multipart/docx 的根目录 Python 3.14 环境，失败属于环境；改用依赖齐全的既有 Python 3.12 环境后上述集合全部通过，没有修改共享环境。

既有 V3 记录含 qwen3.8-max 四个合成场景真实工具联调，但明确没有完整 Actor/WS 浏览器端到端及全提供商覆盖（H）。本次不把它扩写成全面质量证据。

## 3. 大厂公开机制与适用结论（E/R）

只能对齐公开能力、产品行为和测量方法，无法声称复制任何厂商未公开的内部架构；以下也不构成行业强制标准。

| 来源 | 资料实际支持什么 | 本项目采用什么 |
|---|---|---|
| [OpenAI File Search](https://developers.openai.com/api/docs/guides/tools-file-search) | 模型决定使用文件工具时自动执行检索 | 用户不必用固定关键词要求搜索；工具选择本身仍需评测 |
| [OpenAI Retrieval](https://developers.openai.com/api/docs/guides/retrieval) | 属性过滤、排序阈值、语义与词法加权融合；删除存在最终一致窗口 | 调整召回与排序，实时数据库身份/版本校验不能交给异步索引 |
| [Anthropic Contextual Retrieval](https://www.anthropic.com/research/contextual-retrieval) | 片段补上下文、词法与向量召回、重排；厂商实验的收益依赖数据集 | 保留主题路径、商品/项目上下文；不能照搬论文 top-K 或宣称同等提升 |
| [Anthropic Context Engineering](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents) | 前置少量上下文与按需探索可以结合；自主探索也会走弯路 | 预读适用框架/规则，正文按需导航；保留预算与停止条件 |
| [Azure Agentic Retrieval](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview) | 结合对话规划子查询，并行检索、重排、合并证据和活动日志 | 复杂问题拆证据需求，记录每步检索原因与结果 |
| [Azure Retrieval Effort](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-how-to-set-retrieval-reasoning-effort) | 低/中/最少推理有成本延迟取舍，中档按证据不足迭代；页面标为 preview | 简单任务不套多轮复杂管线；以缺口决定补查，不无限循环 |
| [Google Answer API](https://docs.cloud.google.com/generative-ai-app-builder/docs/answer) | 查询分类、来源引用、低相关证据时回退 | 空结果/低相关/服务失败分开；不把勉强相关文档硬写成答案 |
| [Microsoft RAG Evaluation](https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/rag-evaluators) | 检索质量、回答依据、相关性和完整性分别评价 | 不用单一“准确率”掩盖触发、范围、检索和答案中的不同错误 |

关键词实现也要准确命名：[PostgreSQL 默认解析器](https://www.postgresql.org/docs/current/textsearch-parsers.html)负责分词；[pg_trgm](https://www.postgresql.org/docs/current/pgtrgm.html)提供三元组/LIKE 等索引。项目已依赖 jieba==0.42.1、pgvector，并在历史迁移使用 pg_trgm。可优先在现有 PostgreSQL 上试验，无需先引入外部向量库。

现有 memory/retrieval_pipeline.py 的所谓“BM25”实际 SQL 为 `ts_rank_cd`，不是 BM25。可复用分词、RRF思路，不能照搬其命名、异常吞为空或私人记忆权限作为新知识检索实现。

## 4. 旧库完整职责与实际消费者（P）

| 部件 | 写入/读取路径 | 调查结论 |
|---|---|---|
| knowledge_metrics | handlers/base、sandbox、memory、路由等记录；background worker 每小时聚合 | 结构化运行指标，有独立价值；目前可用性依赖 kb_enabled，不能直接关总开关 |
| 模型预置知识 | main.py 启动时删旧种子并重新加载 seed_knowledge.json | 与 smart_models.json/adapter registry 存在重复描述，不应当参数/价格权威 |
| 失败/重试知识提取 | base、message_mixin、chat_stream_support、sandbox、kuaimai dispatcher → extract_and_save → add_knowledge | 真实写入钩子仍在；会调用提取模型和 embedding |
| ERP经验 | erp_agent._record_experience → ExperienceRecorder.record | 成功/失败后持续写入；没有发现 ERP 专用经验读取入口 |
| 模型评分 | background worker → model_scorer → scoring_audit_log + knowledge_nodes | 审计日志供 EMA 读旧分数；未发现主模型选择直接使用评分节点的路径 |
| 旧工具检索 | ToolExecutor._search_knowledge → search_relevant | 最多5条，混合查询所有旧类别；只返回标题/内容，没有稳定来源/版本 |
| 旧首次路由增强 | IntentRouter.route → _enhance_with_knowledge | 全仓正式代码未发现该 route() 调用；实际 retry 走 route_retry，不走此增强 |
| 旧图谱 | seed/extractor 写 knowledge_edges | 全仓未发现 find_related/find_path/get_subgraph 正式读取调用；存在写入，收益未证实 |
| 旧相似度注入过滤 | chat_context/knowledge.py | 当前 PromptBuilder 不调用旧预注入；保留模块不代表仍生效 |

不能由“代码存在”推断生产确实有数据，也不能由“没有明显读取”推断历史数据可以直接删除。动态外部调用及生产用量仍需上线前盘点。

### 4.1 不宜复用到新库的语义和风险

1. ERP记录 scope='org'，旧检索默认 scope='global'，SQL只允许指定scope或global。组织过滤与scope过滤不是同一层；默认工具不读这些org经验。
2. extract_and_save没有org参数，add_knowledge默认scope=global/org=None；部分调用传db_source但没有明确知识归属，是否被RLS拒绝取决于实际连接身份。新库不能沿用这种默认全局写入。
3. 旧搜索会更新hit_count和confidence；词法/语义命中不是事实核验。工具却声明effects=none，契约与持久写入不一致。
4. 缓存只取query前100字符，且key不含threshold；可能跨不同查询/阈值复用结果。org隔离存在，但这不是完整查询身份。
5. 向量“去重”会覆盖相近条目的正文/metadata，没有文档版本，且按category而非全部业务边界查重；不适合制度和型号差异内容。
6. 种子启动时删除重建；不适合作为可追溯业务来源。
7. 无连接/无embedding/异常均可能返回[]，工具无法区分“确无资料”与“服务不可用”。
8. 自动归纳模型结果没有成功验证闭环；不能因为重试后成功就证明换模型是唯一原因。

## 5. 保留/暂停/退役建议（R，非已执行）

| 项目 | 推荐终态 | 前置条件 |
|---|---|---|
| 运行指标、错误码、耗时、重试轨迹 | 保留并从kb总开关/连接生命周期中解耦 | 验证指标生产者、消费者和失败隔离 |
| scoring_audit_log与评分计算 | 暂保留观测与审计，不宣称自动调度收益 | 拆开“写分数到知识节点”；后续用收益评测决定是否保留聚合 |
| 模型/工具静态说明 | 以注册配置生成必要说明 | 逐项对照seed与有效配置，确认没有缺失能力字段 |
| ERP成功/失败自动知识写入 | 暂停，原始执行审计保留 | 先确认现有工具审计足够，否则先补记录；历史内容不迁业务库 |
| LLM错误经验提取 | 暂停，保留原错误和重试事件 | 单独开关，不关指标；必要经验经验证后进入内部维护资料 |
| confidence命中自增 | 停止用于可靠性判断 | 使用次数与验证状态分开，不迁移旧置信度 |
| search_knowledge | 兼容期保持旧语义；新主聊天不默认展示，最后移除 | 查定时任务授权快照、技能capability和存量任务；不得同名静默改查新库 |
| 首次路由知识注入、旧预注入过滤、未消费图遍历 | 确认引用/测试后清理代码 | 不删除IntentRouter.route_retry等仍在用的模型重试职责 |
| knowledge_nodes/edges、dedup/extractor/scorer节点写入 | 停生产者→观察→归档→退役 | 所有消费者与外键盘点、恢复演练、明确数据删除授权；不在第一批DROP |

### 5.1 退役顺序与闸口

A. 只读盘点：部署SHA；按org/source/node_type/scope统计条数、最近写入/命中、大小和无embedding数；检查旧工具执行日志、定时任务授权/技能引用、实际角色和数据库依赖。只输出汇总，业务内容不进入研究日志。没有该证据，不执行删除。

B. 开关拆分：metrics、score aggregation、experience extraction、legacy search分别控制；先保证指标和模型重试不受影响。

C. 新机制验证与灰度通过后，从主聊天默认工具中去掉旧库搜索。未完成的旧授权任务保持可解释的兼容或明确不可用，不能自动获得新库权限。

D. 暂停旧写入，观察实际任务周期，确认没有业务读取回归；保留归档和代码回退路径。

E. 最后移除代码/表。旧scores日志引用节点的列与可能外键需实际核查；保留历史迁移，新增前向迁移，数据库删除单独授权。

## 6. 未核验与不可承诺项

- 本次未取得生产部署SHA、知识规模、有效业务资料、旧库命中收益和收费调用量，因此不报投资回报和可删条数。
- 未调用真实模型，不能报告自动触发/相关性/冲突判断准确率；43项通过不代表模型达到任何厂商水平。
- 未压测超大框架，数据库逐项读取有代码证据，但具体吞吐和延迟待测。
- 当前根候选只搜标题，项目无独立绑定在聊天请求中；浏览器画布选中项目不能偷偷变成聊天上下文。
- 自动无@检索、检索规模、可接受成本、跨项目规则仍是本提案的产品取舍；不把AI推荐写成已确认需求。

结论：保留新库V3可靠的权限/快照底座，新增独立自动检索控制层；旧库以职责退役，不合并数据、不复用模糊置信度，不用换工具名代替完整设计。
