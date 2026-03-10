# 智能 Agent 开发路线图

## 终极愿景：企业 AI 中枢

> EVERYDAYAI = 企业员工工作中枢，一个对话窗口完成聊天、生图、生视频、数据查询、数据分析。

```
企业微信对话 ──┐
               ├──→ AI 知识库（自主学习积累）──→ 智能计算/推理 ──→ 主动通知
快麦 ERP 数据 ─┘                                                    ↓
                                                              企业微信推送给相关人员
```

**数据隔离**：企业级共享知识库 + 用户角色隔离（员工管多店铺，管理层看团队数据）

---

## 已完成

### ✅ 记忆智能过滤（2026-03-07）
- Mem0 向量检索设相似度阈值（0.5）做初筛
- 独立千问调用做二次精排（降级链：turbo → plus → 跳过）
- 过滤后的记忆才注入 chat 上下文
- 实现文件：`memory_filter.py`、`memory_service.py`

### ✅ 对话历史摘要压缩（2026-03-07）
- 对话超过 20 条时，用千问将早期消息压缩为 ≤500 字摘要
- 摘要缓存到 conversations 表，每 10 条新消息更新一次
- 摘要作为 system prompt 注入，实现低成本"长记忆"
- 降级链：qwen-turbo → qwen-plus → 跳过（退回"最近 20 条"模式）
- fire-and-forget 异步更新，不影响聊天主流程
- 实现文件：`context_summarizer.py`、`chat_context_mixin.py`
- DB 迁移：`migrations/add_context_summary.sql`

### ✅ 智能模式 — 千问路由 + Agentic Retry（2026-03-09）
- 大脑（千问）通过 Function Calling 判断意图 + 选择工作模型 + 生成人设
- Agentic Retry Loop：工作模型失败 → 回传大脑重新判断 → 选新模型重试
- 动态模型选择：千问通过 ROUTER_TOOLS 的 model enum 自选工作模型
- 实现文件：`agent_loop.py`、`intent_router.py`、`smart_model_config.py`

### ✅ Agent 自主知识库 — 基础设施（2026-03-09）
- 知识库 DB 表：`knowledge_nodes`（向量检索）+ `knowledge_edges`（关系图）+ `knowledge_metrics`（指标记录）
- 种子知识：24 个模型知识 + 3 个工具参数 + 2 个经验模式，启动时自动加载
- 路由知识注入：`_enhance_with_knowledge()` 查询 top-5 相关知识注入大脑 system prompt
- 指标记录：Chat/Image/Video 任务完成后 fire-and-forget 记录到 `knowledge_metrics`
- 实现文件：`knowledge_service.py`、`knowledge_config.py`、`knowledge_metrics.py`、`seed_knowledge.json`
- DB 迁移：`migrations/023_add_knowledge_base.sql`

### ✅ 搜索架构重构（2026-03-10）
- web_search 从同步工具改为终端工具：大脑判断"需要搜索" → 立即退出 → 系统路由到搜索模型
- 按能力匹配搜索模型：从 `smart_models.json` 的 `web_search` 分类按优先级取，零硬编码
- Google Search Grounding：ChatHandler 按需注入 Google Search tool
- 搜索响应从 ~25 秒降到 ~5 秒
- 实现文件：`agent_loop.py`（`_build_search_result`）、`message.py`（透传标志）、`chat_handler.py`（注入搜索工具）

### ✅ 模型能力注册（2026-03-10）
- ModelConfig 新增 4 个能力字段：`supports_search`、`supports_thinking`、`supports_structured_output`、`supports_audio`
- 19 个模型全部标注正确能力（跨 KIE/DashScope/OpenRouter/Google 四个 provider）
- 修复 gemini-3-flash 搜索能力标记（False → True）
- 为未来按能力匹配模型提供数据基础
- 实现文件：`types.py`、`factory.py`、`kie/configs.py`、`kie_models.py`

---

## 开发计划（按顺序执行）

### ✅ Agent 自主知识库 — 动态评分（2026-03-10）
- 每小时从 `knowledge_metrics` 聚合模型表现（成功率、P75 延迟、重试率）
- EMA 平滑（α=0.2）+ 7 天滑动窗口，自然衰减旧数据，抗网络抖动
- 评分作为 `source="aggregated"` 知识节点写入，路由时自动注入大脑参考
- 异常保护：最小样本量门槛、P75 抗抖动、错误码分类、冷启动 confidence 分级（<10→0.3, <50→0.7, ≥50→0.9）
- 审核日志：`scoring_audit_log` 表记录每次评分变化，Δ≥0.1 标记 `pending_review` 暂不生效
- 审核页面属于超级管理员功能，待管理后台设计后实现（见第五步），暂用 SQL 直接操作
- 实现文件：`model_scorer.py`、`background_task_worker.py`（`_run_model_scoring`）
- DB 迁移：`migrations/025_add_scoring_audit_log.sql`

---

### 🔥 紧急修复：路由提示词 Bug
- 提示词自相矛盾：工具描述包含"修改"，但"重要"段只列了"生成/画/制作"
- 工具描述从关键词枚举改为能力描述（让模型语义理解，而非匹配关键词）
- "重要"段改为"路由原则"（按用户目标路由 + 不确定就问）
- ask_user 增加选项引导规则（禁止开放式提问，必须给具体选项）
- 关键词兜底补充同义词
- 详细方案：`docs/document/TECH_路由提示词修复+自主进化闭环.md`

### 第 1.5 步：自主进化闭环 — 信号接入 + 意图学习
- ~~**阶段 A — 信号接入**~~ ✅ 已完成（2026-03-10）— 路由决策/AgentLoop/用户反馈/记忆检索/Image-Video耗时+重试 全部接入 knowledge_metrics
- **阶段 B — 意图学习**：大脑不确定 → 选项引导用户 → 记录确认结果 → 写入知识库 → 下次直接路由
- **阶段 C — 定期提炼**：定时任务用大模型从意图模式中归纳通用规则，全用户共享
- 前置依赖：✅ 知识库基础设施 + ✅ 动态评分 + 路由提示词修复
- 详细方案：`docs/document/TECH_路由提示词修复+自主进化闭环.md`

---

### 第二步：工具编排

> AI 自主调用多个 API 完成复杂任务（从"聊天机器人"变成"真正 Agent"，让系统活过来）

**核心能力**：
- AI 通过 Function Calling 自主决定调用哪些工具、调几次、怎么组合结果
- 用户一句话触发多步骤任务，AI 自动拆解执行

**典型场景**：
- "帮我生成 5 张不同角度的猫" → AI 自动生成 5 个不同提示词，调 5 次生图 API
- "用刚才的图片生成新图" → AI 从历史中取出图片 URL，调用生图 API
- "搜索最新新闻并总结" → AI 调搜索工具 → 拿到结果 → 调 LLM 总结

**前置依赖**：
- ✅ 智能路由（已完成，千问 Function Calling + Agentic Retry）
- ✅ 上下文记忆（已完成，Mem0 + 摘要压缩）
- ✅ Agent 自主知识库（基础设施 + 动态评分已完成）
- ✅ 搜索能力（已完成，web_search 终端工具 + Google Search Grounding）
- 需要：定义工具注册表（tool registry）、工具执行引擎、结果回传机制

---

### 第三步：ERP + 企业微信数据接入

> 用工具编排 + 知识库打通企业数据，实现智能查询和主动预警

**核心能力**：
- 对接快麦 ERP API：查询店铺数据、库存、订单
- 数据接入方式（混合模式）：
  - 静态数据（商品目录、店铺信息）→ 定时同步到知识库
  - 动态数据（今日销量、实时库存）→ 通过工具编排实时查 ERP API
- 员工通过网页版对话查询对应店铺数据
- 对接企业微信：自动提取对话信息 + 推送预警通知

**典型场景**：
- "XX 商品还有多少库存？" → AI 调 ERP API → 返回真实数据
- 库存低于阈值 → 自动推送企业微信通知给采购
- "这个月哪个店铺销量最好？" → AI 查 ERP 数据 → 分析汇总
- 每日异常数据计算警报，通知到相关人员

**前置依赖**：
- ✅ 知识库（存储商品/店铺等基础数据）
- ✅ 工具编排（AI 自主调 ERP API）
- 需要：快麦 ERP API 接入、企业微信 API 接入、权限体系

---

### 第四步：自主工具创建

> Agent 自己开发 API 给自己调用（终极形态）

**核心能力**：
- Agent 发现现有工具无法完成任务时，自动编写新工具代码
- 新工具注册到工具库，后续可复用
- 每次执行完自动评估效果，迭代优化工具

**典型场景**：
- 用户需要定期爬取某网站数据 → Agent 自动写爬虫脚本并注册为工具
- 用户需要特定格式的报表 → Agent 自动写数据处理函数

**前置依赖**：
- 第二步工具编排（Agent 先会用工具，才能造工具）
- 第三步 ERP 数据接入（有真实业务数据驱动）
- 沙箱执行环境（安全运行 Agent 生成的代码）

---

### 第五步：超级管理员后台

> 平台运营管理界面，集中管控模型、用户、审核等系统级功能。

**核心功能**：
- 模型评分审核：查看自动聚合的评分变化，一键批准/撤回（对接 `scoring_audit_log`）
- 模型管理：查看/编辑模型配置、能力标记、优先级
- 用户管理：用户列表、积分管理、权限分配
- 系统监控：API 调用量、模型成功率、延迟趋势图
- 知识库管理：查看/编辑/删除知识节点，手动添加经验
- 意图学习看板：查看用户确认的意图模式、提炼规则、覆盖率

**前置依赖**：
- ✅ 模型评分聚合（第一步完成后，审核数据已有）
- 需要：管理后台 UI 设计、权限体系（超级管理员角色）
