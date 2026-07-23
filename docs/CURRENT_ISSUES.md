# 当前问题 (CURRENT_ISSUES)

## 2026-07-23 企业空间图片失败后持续显示“生成中” — 已修复，待生产验证

- KIE 异步失败后 task 与 assistant message 已正确落为 `failed`，单图失败事件也携带
  `org_id`；但图片批次和单图重新生成的最终 `message_done` 漏传 `org_id`，导致企业空间
  WebSocket 连接收不到退出 `pending` 的终态事件。
- `BatchMessageFinalizer` 的普通批次与 `regenerate_single` 分支现统一把任务租户传给
  `send_to_task_or_user`；个人空间继续显式使用 `org_id=None`。
- 待生产分别验证企业空间真实 KIE 失败、失败后重新生成，以及个人空间图片成功链路。

## 2026-07-23 多租户通用 Agent Session Runtime — 技术设计确认

- 已确认采用方案 A：保留 PostgreSQL Conversation Actor 作为第一期唯一 Turn Executor，
  在其上增量建立 Session Kernel、AgentDefinition、Policy/Config Resolver、
  Capability Snapshot 和 Extension Runtime。
- 全局管理员、企业、个人、Session 四层配置与策略继承已冻结；企业员工保留个人能力，
  但在企业 Session 中必须与企业策略求交。
- Skill 必须支持系统发布、企业共享、个人私有，以及推荐、自动、强制和禁止四种绑定；
  MCP、Goal 和 Subagent 同期进入 Runtime 总体合同。
- 冲突消解完成：继续复用既有 `agent_*` 数据模型和 `backend/services/agent_runtime/`
  目标目录，禁止新增平行 `runtime_sessions/runtime_goals` 或第二套 Event Store。
- WebSocket 前置门禁已完成前两个子阶段：连接固定绑定 `(user_id, org_id)`，非法企业
  请求直接拒绝；订阅/steer 按用户与精确企业（个人为 null）校验；前端切换企业会
  关闭旧连接、清空旧订阅并重连；本地与 Redis 投递已使用精确 org 和
  `(task_id, org_id)` 复合订阅键，`org_id=null` 只表示个人空间。所有正式消息
  生产者已显式贯通 `org_id`，并增加静态架构合同测试防止新增漏传。
- Tool Confirm 已使用 `(tool_call_id, user_id, org_id)`，Steer 已使用
  `(task_id, org_id)` 复合等待键。
- Tool Confirm 与 Steer 已增加短期 Redis 响应队列：Web Worker 可唤醒独立
  Conversation Actor 进程中的等待者；Redis key 使用复合租户身份的 SHA-256，
  不暴露 user_id/org_id，队列带 120 秒 TTL。本地 Event 保留为同进程快速路径，
  Redis 暂时不可用时按原超时/无打断语义降级。
- 其余实施前置门禁仍包括迁移账本/checksum 和租户数据库纵深防御。
- 详细设计见 `docs/document/TECH_SESSION_RUNTIME多租户通用Agent架构.md`。

## 2026-07-21 快麦 ERP 归档 TEXT[] 序列化修复

- 热表查询返回的 `exception_tags` 为 Python list，通用数据库客户端会按 JSON 编码，导致冷表 `TEXT[]` 拒绝写入。
- 归档边界现统一恢复 PostgreSQL 数组字面量，且写入失败时不删除热表数据。
- ERP 日维护业务已统一收敛到 `ErpSyncExecutor`；旧 Worker 只负责调度与锁。归档 SELECT/DELETE 在企业模式下均按 `org_id` 隔离，`org_id=None` 保留全局兼容语义。

## 2026-07-21 用户资产历史媒体类型归一

- 历史 Web 上传可能以 `type=file`、`mime_type=image/*|video/*` 写入消息。
- 用户资产回填现按 MIME 将这类旧记录归一为 `image` 或 `video`，避免同一存储对象因历史类型漂移触发 canonical 身份冲突。

> 本文档记录项目中当前存在的已知问题、待修复的Bug、技术债务等。

### 2026-07-22 统一生成 Turn 事务与任务生命周期 — 实施完成，待部署验证

- 生产最近 7 天记录到 31 次图片 `_save_task` CRITICAL，全部为
  `TURN_MESSAGE_RELATION_MISMATCH`；其中 30 次集中于 2026-07-22。
- 根因是 Web 生成链分步创建用户消息、助手占位、task 和 Turn；历史助手缺少
  `turn_id/reply_to_message_id` 时，时间邻近 fallback 又为已有 Turn 的用户消息生成随机新 Turn。
- 生产只读审计发现 12,224 条缺完整锚点且有此前用户消息的助手记录，其中 79 条按旧 fallback
  会确定冲突。
- 已确认采用统一 `prepare_generation` 原子入口建立 request/Turn/input/output/local task；Chat Actor、
  图片、视频和电商图保留专用执行状态机。媒体必须先有本地 task，再调用供应商。
- 历史数据只按 task/显式 reply/一致 Turn/唯一关系分级回填；默认 dry-run，模糊关系禁止自动修改。
- 详细设计见 `docs/document/TECH_统一生成Turn事务与任务生命周期.md`；计划使用迁移 148，当前未开发、
  未修改生产数据。
- Phase 1.1 已完成：新增迁移 148 与 rollback，建立 `prepare_generation`、
  `attach_generation_external_task`、`fail_prepared_generation_task`，并用 `preparing` 状态保证媒体本地
  task 先于供应商提交存在；迁移 contract 专项 25 个用例通过。应用调用链尚未切换，不可部署。
- Phase 1.2 已完成：新增类型化 `GenerationLifecycle`/`GenerationPreparation`，统一 Jsonb payload、
  RPC 返回校验、ContextAnchor 构造及 attach/fail 业务上下文日志；专项 20 个用例通过，服务语句覆盖率
  100%。Web、Actor 和媒体 Handler 尚未接入，不可部署。
- Phase 2 已完成：Web Chat 主链先原子准备 request/Turn/input/output/task，再携带数据库权威
  `ContextAnchor` 入队 Conversation Actor；旧客户端缺失 ID 时按租户、对话和幂等键确定性补齐，
  Chat Retry 不再调用最近用户消息 fallback。媒体 Handler 尚未接入，不可部署。
- Phase 3.1 普通图片已完成：Web 图片批次在供应商调用前一次性创建 1–4 个 preparing task；积分锁定
  使用稳定本地 task ID，跨模型重试保留同一逻辑 task 和多次账本记录，最终成功原子 attach 实际模型。
  明确拒绝退款并失败，网络超时保持 preparing/锁定并记录 `submission_unknown`，等待补偿处理。
  ImageHandler 已删除“先调供应商、再保存 task”的旧提交/重试路径，缺少 prepared task/batch
  时在 adapter 创建前失败关闭；视频旧 provider-first 路径也已删除，缺少预创建任务时在余额检查和 adapter 创建前失败关闭。
- Phase 3.2 视频已完成：Web 视频在供应商调用前创建 preparing task，积分锁定与跨模型重试复用
  稳定本地 task，成功后 attach 实际模型和最终交易；明确失败退款并终态化，结果未知保持 preparing
  并记录 `submission_unknown`。旧 provider-first 路径已删除。
- Phase 4.2 错误边界已完成：`prepare_generation` 声明的消息、Turn、task、request 与锚点关系冲突
  （含 `TURN_MESSAGE_RELATION_MISMATCH`）统一脱敏映射为 `GENERATION_PREPARE_CONFLICT` / HTTP 409；
  连接、超时和未知数据库故障仍保留为 5xx，避免把基础设施故障误报成客户端冲突。
- 生产验收发现迁移 148 在更新 `messages.content` 时把 JSONB payload 与 TEXT 列直接用于 `COALESCE`，
  导致请求返回 500。迁移 149 显式执行 JSONB→TEXT 并替换同签名函数；前端同时将明确 HTTP 500
  收口为 rejected，清除 streaming/订阅并恢复草稿。网络、超时及 502/503/504 继续保持 uncertain。
- 失败媒体 retry 已统一复用首次生成的媒体占位恢复入口；`pending` 媒体占位文字的渲染优先级高于
  空消息“已取消”提示，避免任务和灰色占位符已恢复但文字仍显示旧终态。
- AI 失败消息无正文时的英文兜底已统一为“生成失败，请点击「重新生成」重试”；媒体失败占位符内部
  仍保留具体中文原因，兼顾操作引导与故障可解释性。
- Phase 3.3 电商图已完成：Phase 1 策划任务与 Phase 2 生图批次均在 Handler 执行前通过
  `prepare_generation` 原子绑定 Turn/input/output/local task，Handler 不再直接调用
  `_insert_task_with_turn_binding`。统一事务上限放宽为 16，普通图片仍在路由层保持 1–4，
  电商批次保留现有最多 8 张语义。历史回填仍未完成，不可部署。
- Phase 4.1 历史 Turn 回填工具已完成：`backfill_generation_turns.py` 默认 dry-run，按已绑定
  task、显式 reply、相同 Turn、唯一前序输入四级证据确定关系；冲突或歧义不写入。
  支持 keyset 分页、维护窗口阻塞行锁、批次事务、checkpoint、无正文审计和前后不变量统计；
  禁用 `SKIP LOCKED`，避免 checkpoint 越过被锁历史行。
  脚本尚未在生产执行 dry-run/apply，部署前仍需审核生产分类结果。

## 问题状态

**🔴 严重** | **🟡 中等** | **🟢 轻微** | **技术债务**：均无

---

## 会话交接记录

### 2026-07-19 Agent Runtime 统一 Session 与上下文加载总体设计

- Grok Build 核验基线已更新到 `7cfcb20d2b50b0d18801a6c0af2e401c0e060894`；确认其运行方式是 Session Actor 持有 Model/Tool 循环，50% ToolResult 确定性 Pruning、85% LLM Compaction、首轮 Memory 固定和 `x_grok_conv_id` 前缀缓存，而不是“轻 Prompt + 单一压缩器”。
- 总体设计采用 A+：保留 PostgreSQL Conversation Actor、revision、Curated Memory、Artifact、媒体异步任务和多租户权限，在其上收口统一 Session Runtime、ContextPlan、Context Epoch、ModelStep 和 Provider Cache 合同。
- Context Epoch 首期逻辑派生，不新增权威表；同一 Epoch 内 Provider 输入只追加，Compaction 是正常流程中唯一允许重写活动前缀的入口。当前时间、位置和附件作用域必须成为可持久 TurnContext。
- 后续按 Shadow → Context 单一投影 → Pruning/Compaction 收口 → Session Model Loop → Action Runtime → Skill/Goal/Subagent/MCP → 旧链退出分波次实施；禁止新旧链同时产生外部副作用。
- Wave 0 子任务 1.1 已完成：旧历史组装结果改名为 `HistoryAssemblyPlan`，Provider 影子回执新增 `ContextEpoch` 与 `CacheIdentity`，开始分别归因稳定前缀、动态后缀和 Tool Schema；仍未改变 Provider payload。
- Wave 0 子任务 1.2 已完成：PromptBuilder 显式输出稳定前缀消息数，并经 Handler/RuntimeState 传入每次 Provider Receipt；生产主链不再依赖稳定前缀启发式推断。
- Wave 0 子任务 1.3 已完成：每个 ModelStep 的 prompt/completion/cache Token 已回填 Receipt；迁移 147 通过 v2 原子提交 RPC 持久 ContextEpoch、CacheIdentity 和 ProviderUsage。代码部署前必须先应用 138–147 迁移。
- Wave 1 子任务 2.1–2.2 已完成：`ProviderContextPlan` 已成为真实 Provider messages/tools 的唯一投影来源；旧直接发送路径和回退分支已删除，Plan 构建失败或投影不一致会在 Provider 调用前终止。
- Wave 2 子任务 3.1 已完成：聊天共用工具循环改由唯一 `prune_context()` 在可用输入 50% 时确定性裁剪旧 ToolResult，固定保护最近 3 个用户 Turn、完整并行工具组和孤立结果；删除主链原有按 Turn 归档、Tool 桶和历史打分三段重复裁剪，PruningReceipt 绑定下一 Provider ModelStep。85% LLM Compaction、跨 Worker 摘要与 92% emergency gate 留待后续收口。
- Wave 2 子任务 3.2 已完成当前 Run LLM Compaction 收口：`compact_context()` 成为聊天工具循环及旧跨轮压缩调用的唯一 85% 摘要实现，返回类型化 CompactionReceipt 并绑定下一 Provider ModelStep；旧 `context_compressor/summary.py` 与 `compact_loop_with_summary` 已物理删除，无回退入口。跨 Turn Redis+CAS 持久摘要和 92% emergency gate 仍待后续子任务。
- Wave 2 子任务 3.2 质量门禁已闭环：原 708 行 `test_context_compressor.py` 拆为基础归档、Runtime Compaction、预算兜底三个职责文件（均低于 500 行），定向 50 个测试保持通过。
- Wave 2 子任务 3.3a 已完成历史回填只读门禁：`verify_conversation_context_backfill.py` 核对可投影消息覆盖率、孤立 ToolResult、重复 sequence、非法 revision/sequence 及缺失 Artifact 引用；支持单会话诊断，任一不变量违规返回非零退出码。脚本尚未连接生产执行，读取路径尚未硬切换。
- Wave 2 子任务 3.3b 已完成运行时硬切：ContextSnapshot 只读取 `conversation_compactions + conversation_context_items`，既有 revision 缺失投影时失败关闭；Redis 历史信封升级 v6 并删除 summary revision；PromptBuilder、消息路由和成功后置任务不再读取、透传、注入或更新 `context_summary`。旧 `summary_manager.py`、`context_summarizer.py` 与跨 Worker summary coordination 已物理删除，数据库旧字段/RPC 仅为回滚保留。生产迁移、回填和 3.3a 门禁仍未执行，因此当前代码不可部署。
- 迁移 138–144 尚未应用仍是生产部署门禁；总体设计完成不代表可以直接部署。

### 2026-07-19 管理员图片空间超时与统一资产索引 — 实施中

- 管理员资产列表扫描 `messages.content` 并聚合 `image_generations`、`tasks` 后内存分页，
  生产请求已在前端 30 秒超时处取消。
- 已确认采用 canonical `user_assets` 本体 + `user_asset_refs` 来源关联；聊天继续从
  消息事实展示，管理端改用 `(created_at,id)` 游标分页。
- 上传后尚未发送的文件也进入资产索引，后续补充消息关联；所有来源使用稳定幂等键。
- 用户决定不做灰度或双读。生产采用短维护窗口停写、回填、对账和直接切换；新版本
  删除管理员旧扫描逻辑，但不删除消息、任务和生成记录等业务事实。
- 已完成双表 migration/RPC、个人/企业/企微群聊 canonical identity resolver、现阶段
  六类写入接点、管理员游标 API、安全资产 ID ZIP 和前端切换；前端不再提供按会话
  URL 批量打包入口。
- Registry 已切换为 canonical identity + `register_user_asset` 原子 RPC；四类登记 helper
  已分离资产本体和来源 ref，企微 staging 外部合同保持不变。
- 管理列表已切换 `list_admin_user_assets` RPC，通过 ref 过滤并按 canonical 资产游标
  分页；ZIP 已改为先按 `user_asset_refs` 复验完整归属，再读取 ready 资产。
- 历史回填/对账脚本已完成：五类来源独立 keyset checkpoint、默认 dry-run、显式
  apply、RPC savepoint、失败批次不推进游标，并输出创建/复用/冲突/失败/orphan 统计。
- 历史临时 Provider URL 作为可解释 skipped，不登记 ready 资产；受信公开 object key 与
  历史 workspace_path 冲突时以 object key 为 canonical 身份并丢弃索引中的陈旧本地路径。
- 管理员旧 `/uploads`、`/generations` 扫描端点及仅旧链使用的 URL 映射 helper
  已删除；管理员会话视图继续保留自己的消息 ContentPart 解析。
- 待完成全量验证和生产维护窗口执行。
- 详细设计见 `docs/document/TECH_统一用户资产索引与管理员图片空间重构.md`。

---

### 2026-07-19 测试分层与 AI Token 治理

- 后端测试按 Small/Medium/Large/External 分层，默认 pytest 使用简洁输出并排除 Large/External；统一入口为 `scripts/run_tests.sh`。
- 真实 PostgreSQL、DashScope 和路由评测必须显式进入 External；仅存在 `DATABASE_URL` 不再触发生产/测试库连接。
- 两条各约 30 秒的单元测试已修正过期 mock 边界，相关 ERP 集合 24 项耗时 0.31 秒，企微回调 9 项耗时 0.43 秒。
- `AGENTS.md` 只保留 Skill 路由大纲，详细测试选择、覆盖率和输出规则按需加载 test-coverage Skill，避免每轮常驻上下文膨胀。
- PR 测试门禁耗时 116.43 秒：7,781 passed、15 skipped、13 deselected、4 xfailed；4 个既有 fixture 失败与改造前一致，测试环境未再尝试连接真实 PostgreSQL。

### 2026-07-19 Grok 式通用记忆运行时 — Phase 1 通用 Flush 已接入

- Phase 2 任务 2.1 已建立 additive 数据库协议：`memory_session_logs` 固定 from/through revision、prompt version 幂等键和有界 JSON；`memory_atoms` 增加通用 lifecycle、Session lineage、valid time、hash、recall 与 Skill 来源字段。迁移尚未应用，运行时接线将在任务 2.2 完成。
- Phase 2 任务 2.2 已接入 Actor 原子提交返回的 `closed_revision`（旧终态结果兼容使用 `base_context_revision + 1` 推导）：Session Flush 从 cursor 后读取最多 20 条已闭合 user/assistant 消息；非法输出、空窗口和模型失败均不推进 cursor，`NO_MEMORY` 作为有效可审计日志推进。进程内 keyed lock 避免重复模型调用，跨 Worker 由数据库 `FOR UPDATE`、唯一键和 cursor CAS 保证单次成功提交。迁移 140/141 均尚未应用；无 revision 入口已在 Phase 4.2a 失败关闭。
- Phase 2 任务 2.3 已完成 Session exact/semantic dedup 与 shadow 对账：claim 经 NFKC/casefold/空白归一化生成 hash，与最近 25 份 Session Log 及 50 条 active 旧 atom 比较；非 exact 候选单次批量 embedding，以 0.92 阈值去重，同批候选也互相去重。Embedding 失败不写、不推进 cursor。Session Log 的 Receipt 记录输入、接受、exact/semantic 重复数、旧/新比较数和逐候选 outcome。PromptBuilder 仅在旧记忆会话缓存 miss 时读取新 Session 候选并记录数量/字面重合指标，不注入模型、不改变回答。
- Phase 3 任务 3.1a/3.1b 已完成通用 Consolidation：Run 强制关联 3–25 份 Session Log，以 user/source hash 幂等；同用户进程内 singleflight，跨进程由 Session/Curated 行锁兜底；距上次完成至少 4 小时才运行。模型只能在 novel/duplicate/supersedes/conflicts 中判关系，不能生成或改写事实；所有候选再次校验精确 user 原文，非 explicit、关系非法或 embedding 失败均不提交。迁移 142/143 尚未应用，下一步为 Phase 3.2 通用 Search/Get 召回。
- Phase 3 任务 3.2a 已完成通用 Search/Get 内核：底层不再执行 domain 业务过滤；Search/Get 都只允许 active、未删除、已经生效且尚未过期的 Curated Memory。向量与 BM25 经统一相关性、0.3 硬阈值、180 天时间衰减和字符 MMR 后返回；Get 必须同时匹配 memory_id、org_id、user_id 并返回原始消息溯源。兼容层暂保留未生效的 `domain` 参数，待 Phase 3.3 清理旧 API；PromptBuilder fail-closed 注入接线属于 3.2b。
- Phase 3 任务 3.2b 已完成召回接线：PromptBuilder 新会话首轮自动注入上限为 3 条，取消旧 LLM 精排失败后回退未过滤结果；Context Compaction 后删除旧 Session cache 并按当前问题重新 Search，失败返回空而不是复用旧记忆。个人上下文允许时主 Agent 获得只读 `memory_search/memory_get`，Search 最多 6 条并返回 `memory:<atom_id>` 稳定 ref；个人上下文关闭或组织 scope 缺失时工具隐藏/拒绝。下一步为 Phase 3.3 退出旧 L2 Scene、L3 Persona 权威写入和默认注入。
- Phase 3 任务 3.3 已完成旧权威链退出：Scheduler 已删除业务关键词/数字门禁及全部 L2/L3 timer、生成和状态触发入口。MemoryService 不再读取 Persona 构建 Prompt，PromptBuilder 对新检索和旧 Redis cache 都强制忽略 Persona。`memory_scenes/memory_personas` 历史表及显式管理读取 API 暂保留只读兼容，没有生产写入或默认注入调用；物理删除需等待迁移 140–143 应用、旧数据对账和回滚窗口结束后另行执行。
- Phase 4 任务 4.1 已完成停用代码清理：物理删除零生产调用的 L2 Scene/L3 Persona manager 与 Prompt 文件，移除对应模型/定时配置、旧 Prompt 测试及通用召回接口中未生效的 `domain` 参数。历史数据库表和显式管理读取 API 仍保留，避免破坏旧数据查看与回滚能力。
- Phase 4 任务 4.2a 已切断旧 L1 直写链：Scheduler 只接受闭合 revision，缺失 revision 时不读取或更新 pipeline state；`L1Extractor` 仅保留无副作用候选提议，旧 Dedup 服务/Prompt/配置和关键词质量门已删除。Consolidation 使用独立通用模型配置；Scene/L2/L3 历史数据库字段保留但 Runtime 不再维护。
- Phase 4 任务 4.2b 已清除召回旧语义：`ScoredMemory`、Search/Get SQL、MemoryService V2 输出、Agent 工具和 Prompt 注入均只使用通用 `kind`；历史 kind 缺失时固定为 `memory`，不回退 `persona/episodic/scene_name`。零生产调用的 V2 Atom 手动 CRUD 与底层直接插入函数已删除。当前真正的 `/memories` 和企微手动管理仍使用 Mem0，必须在 Phase 4.3 通过独立数据库/API迁移切换，不能视为已完成。
- Phase 4 任务 4.3a 已建立手动 Curated Memory 数据库协议：`memory_atoms.org_id` 允许 `NULL` 表示个人 scope，`source_kind` 区分 conversation/manual/skill；四个 RPC 使用 null-safe scope、事务级并发锁、100 条容量上限、内容哈希去重和软删除，并撤销 `PUBLIC` 执行权，仅授权 `service_role`。迁移 144 尚未应用，公共 `/memories` 与企微入口仍未切换；下一步为 4.3b 服务层与个人 scope 召回接线。
- Phase 4 任务 4.3b 已完成服务与个人召回内核：`ManualMemoryService` 直接保存用户原文，不调用 LLM 改写；embedding 或数据库失败时关闭写入且不回退 Mem0。Search/Get、Prompt 自动注入和 Agent Memory 工具均允许 `org_id=NULL`，并使用 user_id + NULL-safe scope 隔离。公共 `/memories` 与企微仍使用旧 `MemoryService`，需在 4.3c 切换依赖后才会使用新服务。
- Phase 4 任务 4.3c 已完成入口切换：Web `/memories` 设置与 CRUD、企微文本指令及卡片查看/清空均使用 `ManualMemoryService(self.db)`，保持请求、响应与 scope 传递兼容；记忆开关不再依赖 Mem0 可用性。旧 `MemoryService` 代码仍保留供 4.3d 做零调用确认与清理；迁移 140–144 未应用前不得部署当前应用代码。
- Phase 4 任务 4.3d 已完成旧 Mem0 运行时退出：删除旧 CRUD/提取/精排服务、Mem0 配置与缓存、应用启动预热、`mem0ai` 依赖及专属测试；通用 Session 级 Redis cache 保留并改用 Curated Memory 命名。`core.config` 的三个 `memory_filter_*` 字段仍被非记忆的 `suggestion_generator.py` 复用，暂不能在本任务删除，建议后续独立重命名。迁移 140–144 仍未应用，当前代码不得部署。
- L1 提取协议已由业务化 Scene/三类旧记忆 JSON 改为通用 `NO_MEMORY/CANDIDATES`；仅允许显式、可复用信息，禁止电商领域分类和 assistant/tool 单独举证。
- 所有候选必须引用本轮真实 user message ID 与精确原文；任一候选格式、类型、证据或时效校验失败时整批拒绝。
- Actor 后置钩子只传递 conversation 与闭合 revision；Session Flush 从数据库窗口读取真实消息 ID 和原文，不再信任调用方拼装的消息证据。
- 旧 L1 去重直写已经删除；去重与关系判断统一由 Session Flush 和 Consolidation 的失败关闭链路执行。
- 兼容策略：通用 kind 暂映射到旧 `persona/episodic/instruction` 表字段；Session Memory、Consolidation 和旧 L2/L3 退出仍按后续阶段实施。

### 2026-07-19 Grok 式通用记忆运行时 — Phase 0 契约与误提取基线

- 已确认 Memory Runtime 去除电商关键词、固定 domain/category 和业务专属权威层；领域差异后续仅通过受限 Skill Profile 提供。
- 新增通用 `MemoryCandidate`、Evidence、ValidationResult 协议及纯函数 Evidence Validator；长期记忆必须是显式表达并包含可在 user 原消息中精确定位的引用。
- 已固定未知类型、缺失来源、伪造引用、assistant 单独支持、假设、示例、问题、临时长期规则、非法有效期和重复消息 ID 等负例；当前仅建立契约与测试，尚未接入生产提取和写入链路。
- 下一阶段将替换旧提取 Prompt、删除所有 `fallback_store_all` 并接入 fail-closed Validator；未完成前不能宣称生产误提取已解决。

### 2026-07-19 统一会话上下文方案 B — 数据库持久层已完成

- 已固化 Web、企业微信共用的 ConversationItem、Artifact、ContextReceipt 和
  Compaction 实施合同；所有工具统一消费，不按工具名设置上下文白名单。
- 迁移 138 新增四张租户隔离事实表，并以 12 参数 `commit_generation_turn` 重载复用
  现有 Actor fencing、积分、消息和 revision 原子提交；旧 7/8 参数入口保持不变。
- 历史回填脚本默认 dry-run，使用稳定 UUID、内容哈希和唯一约束保证幂等；历史大工具
  结果建立 `message_slice` Artifact，不复制或截断完整消息事实。
- 当前只完成持久层和回填边界，运行时尚未调用新 RPC，因此未改变生产聊天链路。
- 新增 16 项迁移与回填测试全部通过，回填脚本语句覆盖率 89%；排除 manual 的自动化
  回归为 7588 passed、24 skipped、4 xfailed，6 项既有失败与修改前记录一致。
- Artifact Runtime 已接入 Chat 唯一工具结果消费点：任意工具返回都会形成完整 Run-local
  Artifact；小结果保持原模型协议，大于 40KB 的结果返回稳定引用、首尾预览和分页读取
  指令。`artifact_search/get/read` 从对话开始注册，首个大结果后可立即调用。
- 当前 Artifact 仍只在本次 Run 内完整可读，尚未接入迁移 138 的 Actor 原子提交，也尚未
  替换旧 `tool_result_envelope` 的工具名预算；这两项分别属于后续持久化接线和旧逻辑
  清理阶段，不能提前宣称跨 Turn 已完成。
- Actor 持久化接线已完成：`ChatExecutionResult -> GenerationOutcome -> 12 参数
  commit_generation_turn` 同时携带 ConversationItem、Artifact、ContextReceipt 和
  Evidence。≤64KB Artifact 内联，大结果在提交前上传租户隔离 OSS；上传失败不提交消息
  终态。RPC 只接受 task 已绑定的 input/output message 作为 ContextItem 来源。
- 当前尚未实现下一 Turn 从 `conversation_context_items/conversation_artifacts` 重建模型
  上下文，因此“已经原子写入”不等于“跨 Turn 已被模型消费”；消费主链属于下一阶段。
- Actor 持久化新增模块定向覆盖率 98%，Context/Actor/Artifact 相关回归 118 项通过；
  幂等审查已取消“相同内容跨调用合并”，Artifact ID 由会话、tool_call 和内容共同稳定
  生成，避免第二次相同结果被去重后产生悬空引用。
- 仓库默认全量 pytest 会收集 `backend/tests/manual` 的真实依赖 E2E，7 分 28 秒仅完成
  8 项；自动化基线需排除 manual，真实依赖场景在部署前 smoke 单独执行。

### 2026-07-18 通用任务交付运行时与跨 Turn 数据证据 — 工具边界校验收口

- 已纠正生产临时方案的职责越界：删除关键词 Data Validator、提前计算和 Grounded Final
  抢答路径；恢复原模型上下文、ERP、沙盒、工具 Observation 和模型最终表达。
- 行业实现复核后删除最终答案 Claim Guard：不再从 Markdown 反向提取数字、猜测字段或
  阻断模型收尾。Web 与 Actor 均恢复“模型无 Tool Call 即正常结束”的原生语义。
- 只保留确定性的工具边界：参数 Schema、执行状态、结构化 ToolOutput、沙盒安全、
  超时、权限和预算；可恢复错误继续通过原 Tool Observation 返回模型。
- `conversation_data_evidence`、ArtifactLedger、Actor 原子提交和固定 revision 恢复继续保留；
  历史证据目录继续进入模型上下文，完整行仍只留在 Runtime/受控文件；Ledger 只负责
  证据来源、状态、完成判断和审计，不解释最终自然语言。
- Web/Actor 定向回归 85 项通过；全量后端回归 7609 项通过、24 项跳过、4 项 xfail。
  现存 6 项失败与修改前基线一致：4 项测试夹具目录/字段问题，2 项生产数据库网络隔离。

### 2026-07-18 旧生产临时方案记录（已被工具边界方案取代）

- 已将通用 Run 交付治理与 ERP 跨 Turn 数据上下文合并为唯一运行时标准；原 `AgentResult`、模型 observation、emit payload 和 ContentPart 消费协议保持不变。
- Phase 1 已在 Web 与无头 Chat 共用的 `apply_tool_results` 消费点接入观察模式，结构化 `AgentResult.data/file_ref/columns/metadata` 旁路登记到 Run 内 ArtifactLedger。
- Markdown、普通字符串和模型回答不会升级为可信数据证据；非 ready 数据不进入 ready 账本；稳定 fingerprint 防止同一结果重复登记。
- Phase 2 已接入统一 CompletionGate：只有调用方显式 `_run_contract` 才启用；缺少必需产物时继续执行，证据满足后只允许一次 `tools=[]` 文字收尾，预算耗尽记录 fallback/blocked。
- Phase 3 已实现确定性 `data_compute`：按完整 artifact_id 消费当前 Run 的可信数据，支持 eq/ne/in/not_in、分组、sum 和 count；模型只看到证据目录和字段，不复制整份数据。
- `data_compute` 仅在当前 Run 存在 ready DATA_RESULT 时注入，两套 Chat 循环通过原 ToolExecutor 消费同一 RuntimeState；普通 ToolExecutor 构造调用保持兼容。
- 付款订单验收样例已固定：排除拼多多后总订单 1439、有效订单 1056、明细和结论一致、重复计算稳定。
- Phase 4 新增迁移 135 和回滚：8 参数 `commit_generation_turn` 在同一事务复用原 7 参数 Actor 终态，并按 closed revision 幂等写入最多 20 项、每项最多 200 行的 ready 数据证据；失权、租约过期和非 committed 结果不写证据。
- ContextSnapshot 按 base revision 加载最多 50 项数据证据，PromptBuilder 只注入证据目录；RuntimeState 恢复完整结构化行供 `data_compute` 使用。历史证据标记 persisted，可计算但不会重复提交。
- Phase 5 已实现 Grounded Final：存在历史数据证据时，高置信度“排除/合计/切换有效指标/重新计算”追问必须进入 data_compute；Web 与无头流在验证前缓冲模型文本和 thinking，不向用户发送模型猜测数字。
- data_compute 成功后关闭工具，最终数字或明细表由 ready 计算证据确定性生成；即使模型收尾输出错误的 1457，持久消息和用户可见结果只保留 1056。
- 模型拒绝计算直到预算耗尽时，不再调用模型 wrap-up，而是返回固定的“无法完成可信重算”降级说明。
- Web 流、Actor/企微 Sink、取消、预算降级、消息持久化、ContextSnapshot 和旧工具消费兼容回归 145 项通过；Grounded Final 与流边界定向覆盖率 94%。
- 功能阶段已完成；下一阶段执行全量测试、质量门禁、迁移审查和任务完成后的全方位审查。

---

### 2026-07-18 Agent Runtime 全项目对标 — 调研中

- 已完成项目全景/组件装配、Session Actor 和 Agent 定义/能力装配第一轮源码对标；当前只更新研究文档，不修改业务实现。
- 阶段结论：保留 PostgreSQL Claim、Lease、Fencing 和原子终态；参考 Grok 补齐统一 Session 命令、持久 Prompt 队列、send-now 和等待交互。
- Agent 阶段结论：采用可版本化 `AgentDefinition` 与 Session-bound `AgentInstance` 分层，保留现有 PromptBuilder、多租户工具过滤和专业执行器；不复制 Grok 的超大集中式 Builder。
- 待验证风险：主 Chat 的 Agent 运行状态分散在 Handler、Prepared Stream 和 ContextVar，工具过滤分布在组织、Domain、Permission、Personal Context 与 Provider 多处，当前没有可审计、可恢复的最终能力快照。
- Model Loop 阶段结论：未来以通道无关 `execute_chat` 为唯一主循环，迁移旧 Web 循环的空输出恢复、steer 和分阶段取消，并提取 `ToolLoopExecutor` 的失败分类与停滞策略为共享 Policy。
- Model Loop 风险：新旧主 Chat 循环行为不等价；Tool Call 当前最终按 ID 而非原始 ordinal 排序；墙钟预算不会强制中断正在运行的模型流或工具；外层任务重试是否可能重复副作用需在 Persistence 和端到端板块继续核验。
- Policy 阶段结论：用户明确要求生成可作为本 Run 授权，但必须持久化为绑定原始消息、Action 类型、数量、成本上限和资源范围的 `AuthorizationGrant`；模型文本或 Prompt 本身不能授权执行。
- Policy 高风险候选：积分余额扣减与 `credit_transactions` 插入不是同一数据库事务；危险工具在 headless 或确认异常时存在 fail-open；Pending Confirm 依赖进程内状态；未登记 SafetyLevel 的新工具默认 SAFE。后续 ToolBridge、Executors 和 Persistence 板块必须复核并纳入重构。
- ToolBridge 阶段结论：当前工具可以调用，但 Schema Builder、选择 Registry、Safety 表、Executor handler 与结果协议并非同一事实源；目标采用 `Tool Catalog → EffectiveToolset → 薄 ToolBridge → 专业 Executor → ToolRunResult`，不建设万能执行器。
- ToolBridge 高风险候选：动态工具不在 `selected_tools` 时参数校验会跳过；`erp_api_search` 依赖结果文本正则发现工具；执行可返回 `str | AgentResult`；新增工具可能出现可见/可执行/可授权状态漂移。后续 Executors、MCP、Context 和 Persistence 板块继续核验迁移边界。
- Tool Executors 阶段结论：保留 Media、ERP、File、Sandbox 等专业执行器，统一外层 `submit → accepted/completed/unknown → progress → reconcile → settlement`；超过一秒且需要恢复的外部任务不应阻塞模型 ToolCall。
- Tool Executors 高风险候选：聊天 `generate_image/video` 同步等待链路绕过媒体异步 tasks 主链路；Provider submit 成功而任务落库失败会产生未归属任务；完成锁续期丢失不终止当前处理；ERP 写入只用 Redis 参数哈希做 10 分钟幂等；图片与视频 Workspace 持久化不一致。
- Goal Orchestrator 阶段结论：采用“小 Harness、大 Worker”，模型继续负责工作，Goal 层只持久化 Objective/Contract/预算/缺口/证据并确定性控制继续、等待、暂停、验证和完成；普通聊天不默认创建 Goal。
- Goal Orchestrator 高风险候选：现有 ExecutionBudget、StopPolicy 和循环检测只覆盖单次进程内 Tool Loop；ERP Plan、图片方案和 Permission plan 含义不同；模型自述无法证明完成；异步任务完成后没有父 Goal 的幂等续跑边界。
- Context Engineering 阶段结论：保留固定 revision ContextSnapshot 和工具/历史分桶优势，将 Web、企微及未来入口统一到唯一 `ContextPlan → ContextAssembler → ContextCompactor → ContextReceipt`；完整事实留在 DB/Workspace/Artifact Store，模型只接收常驻控制面、近期工作集、带覆盖边界的摘要和可按需 Get 的引用；全通道长期会话及 Hot/Warm/Cold 模型缓存策略见 `TECH_长期单会话上下文与Token治理.md`。
- Context Engineering 高风险候选：工具归档后缺少稳定原文引用；压缩失败没有 suppression/in-flight/fingerprint；两套 context compressor 公共能力仍并存。PromptBuilder 与工具循环已共用模型 ContextBudget，不再使用 Web 200K/企微 32K 分叉。
- Skills Runtime 阶段结论：Skill 定位为可发现、按需加载的指令/资源包，不是 Executor 或授权主体；目标采用 `Skill Registry → Catalog View → Resolver/Policy → Skill Instance → Agent/Tool Loop`，短流程使用 Instruction Skill，需异步恢复和幂等的长流程使用 Workflow SkillRun。
- Skills Runtime 高风险候选：产品 Agent 当前没有 Skill Registry/Selector/Tool，`backend/skills` 只是沙盒只读指南；开发 `.cursor/skills` 与产品 Skill 信任域不能混用；Grok 的 `allowed-tools` 在当前源码主要解析和展示，不能照搬为执行授权；Skill 热更新、脚本、Prompt 注入和多段生成重复扣费需要 hash 固定、Policy 求交及步骤幂等。
- MCP / Plugins / Hooks 阶段结论：MCP 是外部能力协议、Plugin 是安装和版本单元、Hook 是生命周期拦截点；SaaS 目标采用平台/租户 Extension Registry、共享隔离 MCP Gateway、渐进式 Tool/Resource Catalog 和强类型 Runtime Hook，所有外部调用仍回到 Core Policy、Action、Artifact 与审计协议。
- MCP / Plugins / Hooks 高风险候选：产品 Runtime 当前没有 MCP/Plugin；现有 LoopHook 只覆盖部分 Agent 循环；租户任意 stdio、6000 秒同步超时、本地目录自动信任及 Hook fail-open 都不能直接照搬；MCP 写操作超时必须支持 Unknown/状态查询，Plugin trust 不能等价为数据或副作用授权。
- Subagents / Background 阶段结论：Subagent 是带输入/输出合同、独立上下文、受限能力和预算的 Child Run，只用于上下文隔离、独立并行和专业化；顺序工作流由 Goal/SkillRun 管理，媒体和外部等待继续使用 Background Action。目标在现有 Actor/branch claim/lease/fencing 上新增持久 `SubRun`，默认深度 1、只选择必要上下文并回传摘要、Artifact 与证据。
- Subagents / Background 高风险候选：产品当前没有通用 SubRun/委派协议；`BackgroundTaskWorker` 是媒体轮询器而非通用后台运行时；父授权是否可委派、Child usage 结算、取消/完成竞态、共享 Workspace 写冲突和 Parent wake event 尚无统一边界；不能照搬 Grok 进程内 Coordinator、600 秒 Web 前台等待或默认 full capability。
- Persistence 阶段结论：采用“当前状态表 + append-only RuntimeEvent + Transactional Outbox + Artifact Store + Checkpoint/Projection”的混合持久化，不做纯事件溯源；保留现有 Actor、revision、lease/fencing 和原子终态，逐步新增 Run、Action/Attempt、Artifact、Goal/SkillRun/SubRun 关系，避免继续膨胀万能 `tasks`。
- Persistence 高风险候选：ToolCall/Action 尚无可恢复实体；Artifact 缺统一 lineage；Goal/SkillRun/SubRun 和 Parent wake 不存在；进度、UI、审计事件信封未统一；不同 Worker 混用 PostgreSQL、Redis 和进程锁；后续必须通过双写、版本化信封和分阶段切读迁移，禁止一次性重写现有任务主链。
- Protocol / UI 阶段结论：保留现有 ContentPart、WebSocket、Actor 进度恢复和企微 Outbox，新增版本化 `RuntimeEvent` 信封、Run 单调 sequence、Snapshot + Replay、持久 Interaction 与 Channel Capability Adapter；高频 token/progress 可合并，Action/Artifact/Interaction/Run terminal 必须可重放。
- Protocol / UI 高风险候选：当前 WS 无 event ID、sequence、durability 和 aggregate version，`last_index/current_index` 已停用却仍留在协议；前端按到达顺序拼 chunk，`stream_end` 先于数据库终态即标记 completed；确认请求只在进程内且前端仅能容纳一个；Goal/SubRun/Background Action 无可恢复 UI Projection。
- Observability / Config 阶段结论：建立 vendor-neutral `TelemetryContext + Typed Telemetry Schema + UsageLedger + 多 Sink`，复用 Loguru、Sentry、Langfuse、ToolAudit、KnowledgeMetrics 和 Error sink；配置采用 `Config Catalog → Layered Resolver → Policy Clamp → EffectiveConfigSnapshot`，按 immediate/next action/next run/restart/immutable 控制生效。
- Observability / Config 高风险候选：当前 trace 主要等同 task ID，Langfuse 仅局部接入；ToolAudit/KnowledgeMetrics/专项日志互不统一，Error sink 满时静默丢弃，Sentry 缺显式全字段脱敏，企微反馈只写日志；Settings、OrgConfig、注册表和模块常量没有统一 source/revision/snapshot，同一超时和预算概念存在多组参数且运行时无法回溯实际值。
- Testing / Operations 阶段结论：采用确定性状态机、真实依赖契约、Runtime Trace 回放、Eval/Chaos、Release Evidence、灰度和对账回滚的分层体系。
- Testing / Operations 高风险候选：部署脚本允许测试失败后继续；迁移多为 SQL 文本断言，尚缺统一 Trace、自动真实数据库竞态、Actor 排空、按组织 canary 和自动回滚门槛。
- 端到端阶段结论：保留 Conversation Actor、`execute_chat`、媒体/ERP/文件专业执行器和企微 Outbox，以统一 Session、Run、Action、Artifact、RuntimeEvent 消除状态机断层。
- 端到端高风险候选：Skill、Goal、通用 Subagent、MCP 尚未进入产品主链；媒体任务、ERP 内部 Loop 和前端恢复各自成岛，缺少统一幂等、对账、事件序列与恢复快照。
- Validation / Recovery 阶段结论：采用独立纯判断 Runtime，统一 Tool Call 输入、终态结果、错误分类、失败追踪、恢复决策、Completion 与 Receipt；主 Chat 和 ToolLoopExecutor 分阶段从观察模式切到同一权威内核，Provider 重试、上下文恢复和副作用 UNKNOWN 保持独立 Owner。
- Validation / Recovery 高风险候选：当前 `stop_policy.py` 主要依赖关键词分类，动态工具缺 Schema 时会跳过参数校验，主 Chat 未消费 FailureTracker，专业 Agent 未消费主 Chat Completion/Artifact 状态；实施时必须阻止嵌套重试和副作用 UNKNOWN 自动重放。
- Validation / Recovery Phase 1内核：统一协议、Normalizer、Tracker、Recovery纯函数、Observation、Effect映射和Run门面已实现。33项核心专项测试通过，核心包覆盖率100%；自动测试基线存在6个既有失败，其中4个为旧fixture问题、2个因沙盒禁止连接生产数据库。
- Validation / Recovery 主Chat观察接入：`apply_tool_results` 在保持原模型Observation、Tool Step、Artifact和Evidence协议不变的前提下记录分类、恢复决策和Receipt；观察器异常fail-open。接入专项35项及关联Actor、上下文回归通过；尚未接管循环决策或持久化Receipt。
- Validation / Recovery ToolLoopExecutor观察接入：ERP与ScheduledTask共用执行器每次Run建立隔离的ValidationRuntime，真实工具结果在不改变messages、Hook、steer及旧StopPolicy的前提下旁路分类；观察器异常fail-open。工具循环、ERP和定时任务相关245项通过，新拆分执行模块覆盖率93%；尚未接管循环决策或持久化Receipt。
- Validation / Recovery Phase 1决策对比：ToolLoopExecutor按模型轮次比较旧StopPolicy与新Recovery的“继续/停止”控制意图，同时记录原始决策；并行工具采用最保守的新决策聚合，比较器异常fail-open。4项专项测试覆盖一致、真实分歧、并行聚合和异常；该数据当前仅在Run内及结构化日志中观察，不持久化、不改变循环。
- Validation / Recovery副作用边界：主Chat与ToolLoopExecutor共用现有SafetyLevel到ToolEffect的确定性映射，DANGEROUS按非幂等写处理，元数据缺失安全默认只读；观察日志不记录工具结果或异常正文。当前仍不自动重放任何副作用工具。
- Validation / Recovery Phase 1部署前验收：聚焦回归385项通过；后端全量自动测试7679 passed、24 skipped、4 xfailed、6个既有失败，与修改前失败集合一致；统一Validation包覆盖率100%，ToolLoop执行模块93%。观察模式未改变生产循环控制，可进入生产部署验证。
- 目标架构候选：采用模块化单体 Runtime，不扩充巨型 ChatHandler、不提前拆微服务；PostgreSQL 持有 Session/Run/Action/Event 事实，现有能力通过单向 compatibility adapter 渐进接入。
- 目标架构待评审风险：新旧 task/action、消息投影、积分和媒体完成器双映射必须保持单终态 owner；状态机与数据库原子边界未冻结前不得开始实现。
- 状态机候选：业务状态与 lease/attempt 分离；Run、Action、Message、Delivery 各自建模，Action `unknown` 非终态，Run 取消不隐含已受理外部 Action 取消。
- 状态机待评审风险：所有 Tool Call 持久 Action 会增加写放大；必须用单终态 owner、状态 CAS、callback inbox、reconcile SLA 和 Goal 唯一 continuation owner 控制重复副作用。
- 数据库候选：新增 `agent_*` 状态表并以 RPC-only CAS 推进，旧 `tasks/messages` 通过映射和 shadow write 渐进接入；状态与 RuntimeEvent/Projection Outbox 同事务。
- 数据库待评审风险：表/RPC 数量、租户冗余字段、事件写放大和新旧双写均为高风险，必须固定锁顺序、JSON 大小、single owner、真实 PostgreSQL 并发验证和 additive 回滚。
- 待验证风险：Actor steer 当前使用进程内状态，API 与独立 Actor Worker 的跨进程贯通尚无源码证据；历史 `pending_interaction` 表已由迁移 112 删除，但 API 启动仍保留降级清理引用。
- 后续按 Agent、Model Loop、Policy、ToolBridge、Goal、Context、Skills、MCP、Persistence 和 UI Event 顺序继续调研，全部完成后再提交总体重构方案。

---

### 2026-07-18 管理员用户活跃时间与排序 — 已完成

- 根因是 `record_user_activity` 将 JSONB 参数作为 Python `dict` 传给 psycopg，导致活跃事件写入和 `users.last_active_at` 更新一并失败；该旁路异常不影响登录、发消息、创建任务或上传文件主流程。
- `p_metadata` 已改用 psycopg `Jsonb` 适配，覆盖显式 metadata 和缺省空对象；管理员接口继续按 `last_active_at DESC NULLS LAST, created_at DESC` 排序，前端无需修改。
- 回归测试覆盖 JSONB 参数适配、空 metadata、异常降级和管理员排序契约。

### 2026-07-18 企业微信历史孤儿账号清理 — 已完成

- 生产巡检发现 4 个 `created_by='wecom'` 但无 `wecom_user_mappings` 的历史账号，均创建于 2026 年 4 月、从未登录，且没有可可靠还原的企微 userid。
- 逐项核对全部 30 个用户外键：无会话、消息、任务、组织、附件、定时任务或其他业务引用，仅有 58 条注册赠送/历史积分调整流水；当前原子创建 RPC、合并 RPC和唯一索引均已生效，2026 年 5 月后新增孤儿数为 0。
- 删除前已在生产服务器生成 root-only 用户与积分流水备份并记录 SHA-256；随后通过 SERIALIZABLE 单事务锁定、保护条件复核和精确 ID 删除 4 个历史账号，积分流水按外键级联清理。
- 提交后数据库复核 `orphan_users=0`、目标用户和目标积分流水均为 0；手动运行 `WecomDuplicateMonitor` 返回 `orphan_users=0`、`duplicate_groups=0`。

### 2026-07-18 旧文件元数据提取器退役清理 — 已完成

- `file_metadata_extractor.py` 已增长至 1,217 行，但生产调用早已分别从 workspace prompt 和 file_list/file_search 链路移除；全仓仅剩孤立测试和过期文档引用。
- 删除旧提取器及 819 行专属测试，并从沙盒 E2E 测试移除 3 个退役元数据场景；保留的 6 个结果分流场景继续通过。
- 旧模块全仓后端引用清零；文件结构概览和两份相关技术文档已同步到最终实施状态。
- 旧超时分支引用未定义变量 `spreadsheets` 位于退役死代码中，随模块删除一并消除，无需引入新的兼容实现。

### 2026-07-18 Web 用户消息向企微同步 — 已部署，待用户验收

- 根因是 Web 输入仅写入共享数据库并入队 Actor，没有创建企微投递事件；AI 终态由既有企微结果链路发送，因此表现为“只同步回复”。
- 迁移 134 在 Web task 入队事务内创建 `web_user_message` Outbox，目标只复制同会话、同租户、且与 `conversation_channel_bindings` 一致的真实企微 task 上下文；无真实目标时跳过，不扫描或猜测地址。
- 镜像主动消息固定标识“来自 Web”，并移除历史 stream 字段，防止覆盖上一条企微流消息。企微投递失败独立重试，不回滚 Web 消息或 AI 任务。
- 生产迁移、四项服务、健康接口、企微 Worker 连接和部署文件哈希均已验证；现有 2 条历史 dead 记录保持不变，未经确认未修改生产投递数据。

### 2026-07-17 历史工具内容污染治理 — 已完成

- 已关闭 Turn 仅向模型注入用户问题、助手可见回答和结构化 Tool Digest，不再恢复原始 tool call、代码、长输出或失败堆栈。
- 只有最新且带 interrupt marker 的中断 Turn 保留完整工具协议，用于任务恢复和 orphan tool-call 配对。
- `code_execute` Digest 不保存代码；兼容读取旧 Digest 时丢弃代码提示和历史 staging 路径。当前任务资源仍由 ResourceManifest、附件和本轮 `file_analyze` 提供。

### 2026-07-17 企微与 Web 实时同步治理 — 已完成

- Redis publisher 与本进程 subscriber listener 生命周期解耦，无头企微和 Conversation Actor 进程也可跨进程发布。
- 企微 Actor 过程事件与终态不再被特殊屏蔽，跨进程事件保留 `org_id` 做租户隔离。
- 数据库继续作为事实源；Redis 或 WebSocket 暂时不可用时仅丢失实时通知，刷新后从数据库恢复。

### 2026-07-17 企微附件资产与上下文资源治理 — 阶段 1-5 已完成

- 智能机器人 FILE 回调不再假设存在文件名；缺名是合法的上游协议输入。
- 下载层保留响应 Content-Type/Content-Disposition，且日志不再输出临时下载 URL。
- 新增内容优先的统一资产识别：CSV/TSV、Office、PDF、图片和视频由解密后字节确定类型，错误扩展名会被纠正，未知内容才进入 `.bin`。
- 文件规范名、MIME、SHA-256 和大小在同一识别边界生成；同一 msgid 重放复用稳定 Workspace 资产。
- 迁移 131/132 引入附件集合和 `task_attachment_refs`：任务绑定不再消费 active 附件，失败/重试可继续使用；已绑定集合之后的新上传才替换旧集合；同 provider msgid 不同哈希明确冲突。
- 企微 user/channel 会话拥有独立的 Actor task 写入函数，不再复用只允许个人 owner 的旧核心校验。
- ContextSnapshot 新增不可变 `ResourceManifest`：企微从 task_attachment_refs 加载，Web/旧任务只从固定输入消息回退；`FilePart.asset_id` 不再被 Pydantic 丢弃。
- `file_search` 和 `file_analyze` 默认只能访问当前任务资源，只有显式 `scope=workspace` 才能检索工作区；历史消息仅保留附件名叙事，不再向模型注入旧 `workspace_path`。
- 新增历史调和脚本，默认 dry-run；按真实 Workspace 内容重建名称、MIME、SHA-256 和大小，显式 apply 才在单事务内同步附件事实及源消息 FilePart。脚本不改物理文件、不输出 URL/密钥，并拒绝越权路径和缺失的群绑定。
- 部署脚本固定重启并校验 backend、sync、wecom、conversation-actor 四项服务；缺失服务立即失败，后端使用有界 readiness，rsync 保护 `.env*`、数据库、缓存、临时输出和外部运行目录。
- 验证：资源与上下文相关回归 254 项通过、5 项跳过；调和脚本覆盖率 85%；迁移状态机已在生产 Schema 上完成单事务行为测试并回滚。

### 2026-07-17 企微上下文治理结构约束 — 已完成

- 工具结果、文件分析、媒体生成和电商图片 Agent 已完成职责拆分。
- 目标文件均满足文件不超过 500 行、函数不超过 120 行；核心回归 942 通过、15 跳过，新增文件分析服务覆盖率 84%。

### 2026-07-16 结构化消息运行时边界与渲染治理 — 已完成

**根因**：`rehype-highlight` 将代码文本转换为 React 节点后，渲染组件调用 `String(children)`，最终得到 `[object Object]`。同时 WebSocket、HTTP 恢复与 Store 曾依赖 TypeScript 断言，缺少运行时协议边界。

**架构治理**：
- 使用 Zod 在 WebSocket、历史消息、任务恢复和图片局部更新入口校验 `ContentPart`。
- Store 只接收已验证的 `ContentPart`；非法块隔离并记录业务上下文，结构化 text 兼容恢复为 JSON 文本。
- Markdown 原始代码、高亮 HTML和剪贴板内容分离；原始代码是唯一可信数据源。
- Form、Table、Spreadsheet 统一经过安全展示适配器，禁止对象隐式字符串化。

**验证**：前端完整回归 110 个测试文件、1170 个测试通过；TypeScript 构建检查通过。协议核心覆盖率 98.57% statements / 100% functions；展示适配器 100% statements / 100% functions。

**后续治理完成（2026-07-17）**：
- Markdown/CodeBlock 达到 97.54% 行、88.4% 分支、100% 函数覆盖率。
- 结构化消费者组合达到 90% 行、80.52% 分支、81.63% 函数覆盖率。
- `streamingSlice` 从 500 行拆为 254 行主 slice + 三组 action factory，公开 Store shape 不变。
- WS 完成、失败和 routing handler 复杂度降至 15 以下；移除相关既有 `any`。
- `FormBlock`、Spreadsheet、Chart 相关主函数均降至 120 行以下，Fast Refresh 与 Hook Lint 清零。

**关联文档**：[TECH_结构化消息运行时边界与渲染安全.md](document/TECH_结构化消息运行时边界与渲染安全.md)

---

### 2026-07-16 用户消息气泡浏览器兼容性 — 已修复

**问题**：部分浏览器无法渲染用户消息气泡的 CSS 渐变，透明背景与白色文字叠加后表现为消息内容空白。

**修复**：为用户消息气泡增加固定紫色背景作为渐变降级；支持渐变的浏览器保持原有效果，不支持时仍能保证文字可见。

**验证**：新增用户气泡纯色背景、渐变背景和白色文字样式共存的组件测试。

---

### 2026-07-16 聊天附件统一架构 — 开发完成，待人工验收

**问题**：
- 工作区图片通过右键“插入到聊天”或 `@`提及后，在提交层被统一构造为 `FilePart`，聊天消息显示文件卡片而非图片。
- 错误类型同时会影响聊天模型的多模态图片注入。
- 图片、视频和电商图模式只消费本地上传/引用图片 URL，工作区图片存在提交后被静默丢弃的风险。

**设计结论**：
- 不修改后端 `ContentPart` 协议、API 或数据库；前端建立 `ChatAttachment` 草稿领域模型。
- 上传、引用、工作区右键插入和 `@` 提及统一经过 `ChatAttachmentProvider` 命令门面。
- 图片预览统一为缩略图；工作区图片不显示文件名，引用图片保留“引用”标记。
- 聊天、图生图、图生视频、电商图和外部电商事件共用同一个提交快照，模型只接收原图 URL。
- 草稿附件统一移出和恢复，明确拒绝时合并恢复，结果未知时遵循幂等语义不重复恢复。

**关联文档**：[TECH_聊天附件统一架构.md](document/TECH_聊天附件统一架构.md)

**当前进度**：
- 已完成统一类型、来源适配、Context、预览、删除、能力判断、草稿事务和提交转换。
- 已删除旧图片/文件/工作区三套输入预览和旧 `attachmentNormalization` 提交旁路。
- 已收口 `messageSender` 中两份重复图片协议映射。
- 专项覆盖：47 passed；语句 94.02%、分支 80.21%、函数 92.30%。
- 前端全量：107 files / 1147 passed；TypeScript、变更范围 ESLint 与生产构建通过。
- 待人工验收工作区右键插入、`@` 提及、图片引用、图文混合和图生图/图生视频流程。

---

### 2026-07-16 消息发送草稿事务与幂等协议 — 开发完成，迁移待执行

**问题**：
- 输入框文本和附件在 `/messages/generate` 返回后才清理，导致慢路由场景中消息已乐观显示但草稿仍留在编辑器。
- 网络超时被统一当作发送失败，但服务端可能已经创建消息和任务，存在重复发送、重复生成和重复扣费风险。
- `messages.client_request_id` 当前只有普通索引，不具备请求指纹、原子执行权和响应重放能力。

**设计结论**：
- 前端增加独立草稿事务：校验通过后立即隐藏；明确拒绝恢复；已接受或已记录失败不恢复；结果未知时冻结并安全重试。
- 后端新增专用 `message_generation_requests` 表和原子 claim RPC，相同幂等键重放原结果，不同请求指纹返回 422，并发处理中返回 409。
- 自动重试复用同一组 request/task/message ID，覆盖文字、图片、视频和电商图统一发送链路。

**关联文档**：[TECH_消息发送草稿事务与幂等协议.md](document/TECH_消息发送草稿事务与幂等协议.md)

**当前进度**：
- 已新增 119 正向/回滚迁移，claim RPC 按现有 `db.rpc()` 约束返回 JSONB。
- 已实现后端请求指纹、原子抢占、处理中冲突、成功/失败重放，并在任务槽位前接入统一消息 API。
- 前端统一发送器已固定 request/task/message ID，发送 `Idempotency-Key`，并对 timeout、网络错误和无业务错误码的 502/503/504 使用同一请求最多重试 2 次。
- 结果未知时不回滚乐观消息或取消任务订阅；明确拒绝和已记录媒体失败仍沿用现有回滚/失败卡片逻辑。
- 输入框在校验通过后立即移出文本、图片、普通文件和工作区文件；明确拒绝时与等待期间的新草稿合并恢复，不覆盖新内容。
- 引用图片临时 ID 改用 `crypto.randomUUID()`，消除草稿恢复去重时的毫秒级碰撞。
- 普通未知异常会最佳努力落为脱敏的可重放 500 终态，不覆盖原始异常；进程强杀产生的 `processing` 记录仍坚持 24 小时内不接管，避免重复副作用。
- 新增每小时 TTL 清理循环和数据库清理函数，删除超过 24 小时的幂等记录，清理失败不影响 API 主链路。
- 前端全量 102 个测试文件、1118 个用例通过，后端幂等与清理专项 23 个用例通过，生产构建通过；数据库迁移尚未执行。
- 部署顺序固定为：先执行 119 迁移，再部署后端，最后部署前端。

---

### 2026-07-15 KIE 余额不足企业微信告警 — 开发完成，待生产验证

**目标**：
- KIE 返回 HTTP 402 或响应体 `code=402` 时，向平台 `super_admin` 推送企业微信告警。
- 覆盖图片、视频和聊天调用，30 分钟内跨用户、模型和任务只推送一次。

**实现**：
- KIE Client 在统一错误分类入口记录脱敏的 `KIE_INSUFFICIENT_BALANCE` 事件，并继续抛出原有 `KieInsufficientBalanceError`。
- 修正异步任务 HTTP 200 + body `code=402` 被误分类为普通 API 错误的问题。
- 全局错误监控使用固定事件指纹复用现有 Redis 去重和企微管理员推送，不改变退款、失败收尾或备用模型逻辑。

**验证**：
- KIE Client、错误监控、错误分类、图片/视频重试及聊天流回归：146 passed。
- 专项覆盖测试：49 passed；覆盖 HTTP/body 402、脱敏日志、固定指纹、unknown 模型及 401/429 不误报。

**待生产验证**：
- 受控触发一次 KIE 402，确认 super_admin 收到企微告警。
- 30 分钟内重复触发，确认错误次数累计但企微不重复推送。

---

### 2026-07-11 KIE 回调立即处理 + 现有轮询兜底 — 开发完成，待生产验证

**目标**：
- KIE 生成完成后主动回调，后端立即启动媒体持久化和消息更新。
- 回调接口不等待 NAS/OSS/积分处理，快速返回 200。
- 保留生产现有轮询配置和逻辑，仅作为回调丢失或处理中断的兜底。

**实现**：
- `CALLBACK_BASE_URL` 与 `CALLBACK_TOKEN` 必须同时配置，否则不向 Provider 下发回调 URL。
- Webhook 使用常量时间比较验证 Token，并托管后台任务异常。
- `TaskCompletionService.process_result()` 增加 Redis 分布式锁和定期续期，防止回调与轮询错时重复处理。
- 数据库终态检查和 version 乐观锁仍保留为第二层幂等保护。

**验证**：
- Webhook/回调/轮询/重试相关回归：137 passed。
- 新增 9 个专项用例，覆盖鉴权、快速响应、终态幂等、任务不存在、Redis 异常和两通道竞态。

**待生产验证**：
- 配置回调环境变量后，使用一张低成本图片确认 KIE 回调、快速 200、OSS 持久化和前端替换时序。
- 重放同一回调，确认不重复结算积分或写入消息。
- 生产已确认 KIE Market 回调为 `{code,msg,data}` 统一信封；图片/视频解析器已改为严格从 `data` 读取，不保留顶层旧格式兼容。

---

### 2026-07-11 图片生成失败状态统一 — 开发完成，待生产验证

**问题**：
- 图片任务提交前失败会被前端转换为文字错误，实时显示与刷新后的媒体占位符不一致。
- retry / 单图 retry 会在积分检查前修改原消息，积分不足可能破坏原失败内容。

**当前进度**：
- 后端已增加消息变更前积分预检，正式提交时仍保留二次余额校验。
- 模型任务未创建成功时，数据库消息会收尾为统一失败图片快照。
- 本轮后端结构化失败与 WebSocket 相关回归测试 61 passed。
- 前端全量测试 80 files / 1030 passed，TypeScript 与生产构建通过。
- 前端已统一中文业务错误、媒体失败结构、retry 判断、多图失败格子和失败原因显示。
- 同步积分不足时保留输入文字、参考图、附件和工作区引用，不发送临时消息。
- 异步失败的 `error_code` 已贯通任务 JSON、WebSocket、最终消息与刷新恢复。
- `INSUFFICIENT_CREDITS` 失败占位符保持原布局，改为警告三角、固定文字“积分不足”和原重新生成按钮。

**待生产验证**：
- 历史成功图片刷新显示。
- 新图片成功、模型提交失败、异步超时、积分不足和重新生成。

**上线后技术债**：
- `frontend/src/services/messageSender.ts` 与 `frontend/src/contexts/wsMessageHandlers.ts` 为历史超长文件；本次按用户确认暂不扩大拆分，上线验证稳定后单独处理。

---

### 2026-07-01 图片原图/缩略图规则统一 — 完成

**背景问题**：
- 对话内图片和 NAS/工作区图片在点开预览、下载时仍可能使用带 `x-oss-process` 的缩略图 URL。
- 根因：聊天、工作区、后台、发送到模型等入口分别处理图片 URL，旧缩略图工具容易被误用。

**修复方案**：
- 新增统一规则入口：`toOriginalImageUrl` / `toThumbnailImageUrl`。
- 预览、下载、传模型统一使用原图规则；小图展示、缩略条、列表网格统一使用缩略图规则。
- 删除旧前端缩略图入口 `ossThumbUrl.ts`，避免后续继续误用。

**验证**：
- 相关前端回归测试：131 passed。
- TypeScript 构建检查：通过。
- 前端生产构建：通过。

---

### 2026-07-02 图片 URL 消费规则收口 — 完成

**背景问题**：
- 同一对话和工作区中，新旧图片数据结构不一致，旧消息可能在放大预览、引用或管理后台查看时误把 `thumbnail_url` 当成原图。
- 输入框引用链路此前未单独纳入规则：引用卡片显示缩略图是正确的，但点击放大和发送给模型必须使用原图。

**修复方案**：
- 增加统一原图选择函数 `pickOriginalImageUrl`，避免 `original_url || download_url || url` 在第一个候选是缩略图时提前短路。
- `ImageAdapter` 增加预览边界校验，主预览 URL 如果是 `/workspace-thumbnails/` 不进入大图弹窗。
- 工作区、聊天消息、右键引用、输入框引用预览、发送链路、管理后台资产空间和会话查看统一使用同一套原图/缩略图语义。

**验证**：
- 前端全量测试：76 files / 1017 passed。
- TypeScript 构建检查：通过。
- 前端生产构建：通过。

---

### 2026-05-23 Web 端上下文压缩改造 — 完成

**关联文档**：[TECH_Web端上下文压缩改造.md](document/TECH_Web端上下文压缩改造.md)

**背景问题**：
- Web 长对话中（10+ 工具调用轮次）schema 过早丢失，LLM 写错列名、错误调用 API
- 根因 1：`compact_stale_tool_results` 按 `assistant+tool_calls` 算轮次，用户说 1 句 LLM 调 5 个工具就算 5 轮，`keep_turns=10` 实际只保留 2-3 次用户对话
- 根因 2：按轮次触发，10 轮就开始压缩，不管上下文实际使用率

**修复方案**：
- 新增 `compact_stale_by_user_turns`：按用户对话回合切分 + 容量触发（70%）
- 新增配置：`context_web_keep_user_turns=10`、`context_web_compact_trigger=0.7`、`context_web_max_tokens=200000`
- 调用点按 `conversations.source` 分流：企微继续走旧函数，Web 走新函数
- 企微链路完全不动，零回归风险

**改动文件**（4 + 1 测试）：
- `backend/core/config.py`：+5 行（3 个 Web 配置项）
- `backend/services/handlers/context_compressor.py`：+95 行（`_identify_user_turns` + `compact_stale_by_user_turns`）
- `backend/services/handlers/chat_generate_mixin.py`：+50 行（`_get_conv_source` 带缓存 + source 分支）
- `backend/services/handlers/chat_handler.py`：+12 行（source 分支）
- 新增 `backend/tests/test_context_compressor_web.py`：326 行，17 个用例

**E2E 仿真验证**：
- 20 轮普通对话（37K tokens, 19%）→ 不压缩 ✓
- 20 轮重度对话（154K tokens, 77%）→ 压缩 10 条旧 tool，节省 47% ✓
- 企微 source=wecom 继续走旧函数 ✓

**额外发现**：file_analyze 归档后 `_extract_archive_meta` 自动保留字段名等元数据，schema 关键信息即使超出保留区也不会完全丢失，无需做白名单豁免。

---

### 2026-04-16 统一查询引擎（Filter DSL）— 全部完成

**关联文档**：[TECH_统一查询引擎FilterDSL.md](document/TECH_统一查询引擎FilterDSL.md)

**完成内容**：
- 7 个碎片工具合并为 1 个 `local_data` 统一查询工具（Filter DSL 模式）
- 新增 `erp_unified_query.py`（519行）+ `erp_unified_schema.py`（331行）
- RPC 升级：`erp_global_stats_query` 新增 `p_filters JSONB` 参数
- ERP_ROUTING_PROMPT 从 ~100 行精简到 ~55 行
- 删除 3 个旧实现文件 + 精简 erp_local_query.py（-287行）
- 净减少 ~700 行代码
- 测试：4055 passed, 0 failed

**待部署**：
- 在 Supabase 执行迁移 `080_unified_query_rpc.sql`

---

### 2026-02-01 聊天系统综合重构（阶段0-7完成，97%进度）

**关联文档**：[重构执行清单](docs/document/重构执行清单.md)

**完成内容摘要**：
- 阶段0：短期修复（9个任务）- 缓存写入、去重逻辑、模型切换、日志统一
- 阶段1：统一缓存写入（3个任务）- RuntimeStore 改用、兼容层
- 阶段2：合并发送器处理器（5/6任务）- mediaSender、useMediaMessageHandler
- 阶段3：统一轮询管理器（2个任务）- polling.ts 精简
- 阶段4：提取任务通知逻辑（2个任务）- taskNotification.ts

---

### 2026-02-02 阶段5-7：状态管理与性能优化（完成）

**阶段5 - 状态管理重设计**：
- 新建 `messageCoordinator.ts` 协调层，解耦 TaskStore 和 ChatStore
- 统一 `updateMessageId` 和 `markConversationUnread` 调用

**阶段6 - 占位符持久化**：
- tasks 表新增 `placeholder_created_at` 字段
- 页面刷新后任务恢复使用原始时间戳

**阶段7 - 性能优化**：
- 虚拟滚动（react-virtuoso）- 只渲染可见区域消息
- 消息合并算法 O(n²) → O(n)
- 图片加载失败指数退避重试

---

---

### 2026-03-01 刷新恢复场景僵尸消息修复（已解决）

**问题现象**：图片/视频生成任务 KIE 正常返回，但刷新页面后任务恢复时出现"僵尸消息"——占位符永远转圈、图片无法显示、出现多个"生成完成"文字气泡。

**根因分析**（3 个 Bug）：

| Bug | 严重度 | 根因 | 修复 |
|-----|--------|------|------|
| generation_params 类型不匹配 | 🔴 CRITICAL | Supabase JSONB 返回字符串，Pydantic `MessageResponse` 期望 dict → GET /messages 422 → 消息历史无法加载 | 添加 `field_validator` 自动 `json.loads` |
| 恢复订阅 ID 不匹配 | 🔴 HIGH | `taskRestoration.ts` 用 `external_task_id` 订阅 WS，但后端用 `client_task_id` 推送 → WS 订阅无法匹配 | `/tasks/pending` API 增加 `client_task_id` 返回，前端优先用 `client_task_id` 订阅 |
| 生产环境 debug print | 🟡 MEDIUM | 3 处 `print(f"🔥🔥🔥 ...")` 遗留在生产代码 | 删除 |

**修改文件**：
- `backend/schemas/message.py` — `field_validator('generation_params')` 自动转换
- `backend/api/routes/task.py` — select 增加 `client_task_id`
- `frontend/src/utils/taskRestoration.ts` — `PendingTask` 增加 `client_task_id`，订阅优先使用
- `backend/services/task_completion_service.py` — 删除 debug print

---

### 2026-04-11 快麦同步加固（已修复 4 个 Bug + 4 处技术债）

**修复内容**：
- 🔴 Bug 1: `sync_platform_map .limit(10000)` 导致 78% SKU 自 3-23 起未同步 → 加 `platform_map_checked_at` 列 + 1/4 增量
- 🟠 Bug 2: `except Exception` 吞掉 token 失效告警 → 异常分四类 + 未知错误接入 DLQ
- 🔴 Bug 3: `_TOKEN_EXPIRED_CODES` 漏 `invalid_session` 导致自动刷新永不触发 → 加白名单 + refresh 失败立即推企微告警
- 🟢 Bug 4: `sync_batch_stock` 死代码每天浪费 ~10k 次 API → 删除整条链路（保留 batch_stock_list 工具）

**配套技术债**：
- `master_handlers.py` 669 行 → 拆成 product/stock/supplier/platform_map 子包
- `dead_letter.py` 561 行 → 拆成 queue/consumer/platform_map_retry 子包
- `healthcheck ALERT_THRESHOLD` 10→3，告警延迟从 60h 降到 18h
- 加 SQL 表达式索引匹配 COALESCE 查询（cost 5000→1170）
- `_mock_svc` 默认 `_lock_extend_fn=None` 防 mock 隐藏 bug

**剩余技术债**：
- `client.py` 614 行（>500） — 单类设计，拆方法收益低，下次重构时再处理
- `record_dead_letter` 已 `dead` 状态时唯一索引冲突 — 现有架构限制（非本次引入）
- `erp_batch_stock` 表保留待将来 cleanup PR 一并 DROP

---

## 更新记录

- **2026-07-19**：前端 Chunk 治理任务 6 完成部署产物卫生治理：仓库继续全局忽略 `.DS_Store`，生产部署在前端构建前显式清理旧 `dist`，前端 rsync 增加 `.DS_Store` 排除规则；本地现存 Finder 元数据同步清理，避免旧产物或部署期间重新生成的系统文件进入服务器目录。
- **2026-07-19**：前端 Chunk 治理任务 5 完成主入口压缩：WebSocketProvider 下移为受保护 Chat 路由动态 Runtime，AuthModal 仅在打开后加载；认证 Store 通过同步 reset 注册表清理已加载的消息、记忆和订阅 Store，不再反向静态导入聊天状态链。生产构建主入口从 564.01 kB 降至 368.35 kB（gzip 180.19 → 123.92 kB），达到 350–400 kB 验收目标；AuthModal 为 19.49 kB、WebSocketContext 为 124.17 kB 动态入口，未使用 `manualChunks`。501.94 kB 同名 `index` 经 sourcemap 确认为 Mammoth/JSZip 文档预览动态包，不属于主入口。
- **2026-07-19**：前端 Chunk 治理任务 4 完成 ECharts/Mermaid 按需加载收口：ECharts 新增具名注册 Runtime，图表出现后才由 `EChartsRenderer` 动态加载，原 core/charts/components/Axis/graphic 等多入口收敛为单个 793.30 kB（gzip 263.34 kB）Runtime；失败 Promise 仍可清空重试。Mermaid 保持库级动态加载、SVG 安全清理与 50 条缓存，并补充相同源码不重复解析测试。生产 manifest 确认 Chat 静态依赖不含 ECharts/Mermaid，Chat 为 299.46 kB；无图表或 Mermaid 内容时不下载对应引擎。
- **2026-07-19**：修复 `useEChartsRender` 空配置分支在 Effect 内同步设置派生状态的问题；`fallback` 和“图表配置为空”现由 `hasOption` 直接派生，空配置不加载 ECharts，并补充有效配置切换为空配置的回归测试；同时清理该测试文件正则中的多余双引号转义。
- **2026-07-19**：前端 Chunk 治理任务 3 完成 Chat 主 Chunk 拆分：`MarkdownRenderer` 保留纯文本快速路径，仅在检测到 Markdown/公式语法时动态加载 `RichMarkdownRenderer`，KaTeX、highlight.js、react-markdown 与 unified/micromark 不再进入普通文本聊天静态依赖。生产构建 Chat 从 899.79 kB 降至 299.49 kB（gzip 273.74 → 87.71 kB），Chat CSS 从 35.16 kB 降至 4.85 kB；富文本渲染器为独立 340.87 kB 动态入口，达到 Chat `<500 kB` 验收目标，未使用 `manualChunks`。
- **2026-07-19**：前端 Chunk 治理任务 2 完成重型预览库隔离：Plotly、Vega、ExcelJS、XLSX 保持既有功能内动态导入；PDF/PPT 适配器拆为轻量匹配入口与动态渲染器，`react-pdf`/PDF.js/Worker 不再进入预览公共静态依赖。生产 manifest 显示 `PdfPreview`、`PptxPreview` 为独立动态入口，预览公共 `useFileSelection` Chunk 从 379.38 kB 降至 29.82 kB；Chat 静态依赖不包含上述重型库。Chat 自身仍为 899.79 kB，继续由后续聊天主 Chunk 拆分任务治理。
- **2026-07-19**：前端 Chunk 治理任务 1 完成导入语义收口：`react-hot-toast` 统一使用既有静态入口，Memory 作为聊天首屏能力统一静态加载；ECharts 主题入口已由现有 `ChartBlock → EChartsRenderer → useEChartsRender` 拆分消除混用。生产构建不再出现静态/动态导入冲突警告；Chat 仍为 899.83 kB，重型预览库隔离与聊天主 Chunk 拆分继续作为后续任务。
- **2026-07-19**：定位并修复统一 Artifact 上线后的工具 Turn 提交回滚：`materialize_artifacts` 曾同时输出 `inline_content/storage_ref`，非活动字段的 Python `None` 经 JSONB 成为 JSON `null`，不满足 `conversation_artifacts_content_check` 要求的 SQL `NULL`。因此工具结果已流式展示但 Actor commit 触发 `CheckViolation`，同一任务被重跑三次，后续消息排队且 Web 永久显示正在输出。现改为应用只输出活动存储字段，迁移 139 在数据库边界再次按 `storage_kind` 清除互斥字段；确定性 `IntegrityError` 立即走 fenced fail 并通知终态，租约尝试耗尽同步关闭 streaming assistant。全量回归 7,636 passed、24 skipped、4 xfailed，6 个失败与既有 Workspace 夹具/本地网络基线一致；核心模块覆盖率 90%。生产已应用迁移 139 并部署提交 `324ad4de`，三个遗留 task/message 均闭合为 failed，生产核心回归 85 passed，Actor/企微/同步错误日志为 0，公网健康正常。生产 Linux 另暴露 `test_wakeup_during_scan_triggers_next_scan_without_poll_delay` 的既有调度敏感断言（预期一次、实际两次），本次未修改 Worker，列为独立测试竞态问题。
- **2026-07-19**：修复统一长期上下文上线后的聊天阻断：执行入口已显式传递请求级 `model_id/org_id`，但 `ChatContextMixin._build_llm_messages` 仅接入 `model_id`，导致 Web/企微在上下文组装前抛出 `unexpected keyword argument`。现已补齐 `org_id` 正式接口并统一用于 Workspace 与 PromptBuilder 的租户范围，未显式传入的旧调用继续兼容 Handler 组织值；基础生成测试改为 autospec 校验真实方法签名，避免宽松 AsyncMock 再次掩盖调用协议漂移。
- **2026-07-19**：Web Actor 正式主链与统一长期上下文已部署生产。维护窗口内确认 Actor 活跃任务为 0，完成数据库 schema、上下文表数据和旧后端代码备份；迁移 136–138 以单事务正式应用，历史回填写入 130 条消息、433 个 ContextItem、87 个 Artifact，重复 sequence、缺失 Artifact 引用、无 group tool_result 和租户不匹配均为 0。生产相关测试 64 passed，外部 `/api/health` 正常，Backend、Conversation Actor、企微、同步四服务 active，启动后 5 分钟 error 日志为 0。发布提交为 `2a6bf6a8`。
- **2026-07-19**：统一上下文上线前审查修复四项接线/可靠性缺陷：历史回填 Artifact 稳定 ID 现包含 message、block 和 tool_call 身份，禁止相同内容跨调用静默合并；`prepare_chat_stream` 将 org_id 写入 RuntimeState，使跨轮 Artifact Repository 实际执行 conversation/revision/org 三重范围；大 Artifact 物化中途失败、Actor 丢失租约、提交异常或非 committed 结果会 best-effort 删除本次新上传的 OSS 对象，避免无数据库行的永久孤儿；迁移 138/rollback 仅在 `service_role` 存在时执行 Supabase 授权，兼容生产自建 PostgreSQL 的 `everydayai` owner 角色。群聊工具目录测试同步确认 Artifact Search/Get/Read 为通道通用能力，同时继续排除个人上下文与定时任务工具。全量排除 4 个既有 workspace 夹具失败和 2 个受沙箱网络限制的 PostgreSQL 集成测试后为 7,630 passed、24 skipped、4 xfailed；最终相关回归 64 passed。生产 PostgreSQL 16.13 已在 3 秒锁超时/60 秒语句超时的单事务中完成迁移及 rollback 预演，事务后确认 4 张表和 12 参数 RPC 均无遗留；真实历史只读 dry-run 扫描 130 条消息，生成 433 个 ContextItem 和 87 个 Artifact，未写入数据。尚未正式应用迁移、回填或部署。
- **2026-07-19**：统一会话上下文 Phase 6 完成结构化 Compaction 与最终 Assembler：按所选模型 ContextBudget 在软阈值压缩稳定旧前缀，至少保留最近两个用户 Turn 和完整 tool call/result；主/备摘要模型均失败时使用有界结构化确定性降级，required/protected 仍超过硬上限则明确失败，不再由 PromptBuilder 静默归档当前输入。压缩 payload 随 GenerationOutcome 进入 Actor fenced 原子提交；下一轮先读取最新 ready compaction，再分页读取其 `through_sequence` 后原始项。扫描超过 5,000 项明确失败，禁止固定 200 项静默丢旧事实。相关回归 137 passed，Assembler/loader 组合覆盖率 85%。迁移 138 尚未应用，未部署。
- **2026-07-19**：统一会话上下文 Phase 5 完成跨 Turn 消费：ContextSnapshot 在缓存未命中时优先从固定 `base_revision/summary_revision/org` 的 `conversation_context_items` 重建历史，只在新表完全没有事实时回退旧 messages，禁止两套历史混合；Redis 投影升级 v5。Artifact Search/Get/Read 同时覆盖当前 Run 和持久历史，并按会话/revision/org 读取 inline、OSS 或历史 `message_slice` 完整结果。聚焦测试 48 passed，新增模块覆盖率组合 82%。结构化 Compaction/Assembler 仍是下一阶段，迁移 138 尚未应用，未部署。
- **2026-07-18**：Agent Runtime Projection 与发布保障总体设计冻结：Web/企微统一消费有序 RuntimeEvent 和 Snapshot/Replay，持久 Interaction 采用数据库 CAS，`stream.closed` 不再冒充业务终态；测试按状态机、真实依赖、Trace、通道 E2E、Eval/Chaos 分层，发布采用不可变 ReleaseManifest、expand/contract、Actor drain、按组织 Canary 和自动门禁，应用回滚不得重提 Accepted/Unknown 外部 Action。
- **2026-07-18**：Agent Runtime 扩展层总体设计冻结：Skill、MCP、Plugin、Hook 和 Subagent 分别建模并统一接入 Catalog/Policy/Action/Executor/Artifact；产品 Skill 与开发 `.cursor/skills` 隔离，Skill 采用 Instruction/Workflow 双模式，MCP 通过多租户 Gateway 与 Secret Broker，Plugin 固定签名版本和租户启用，Hook 不能替代 Core Policy；Subagent 是受限 Child Run，默认 selected Context、read-only、深度 1，并与媒体 Background Action 明确分工。
- **2026-07-18**：Agent Runtime Executor 总体设计冻结：采用统一 SPI 与专业执行器，Action 经 Policy 后只携带受限 Capability；同步查询可直接完成，媒体/外部长任务返回持久 TaskRef，submit 超时进入 Unknown 对账；结果统一为 model/display/artifact/audit 四视图，并以 Action/Attempt/Provider request 贯穿幂等、回调、轮询、取消、结算和恢复，逐步收口聊天同步媒体与异步任务双链。
- **2026-07-18**：Agent Runtime Context 总体设计冻结：保留不可变 ContextSnapshot，将消息数组升级为可解释的 ContextPlan/Block/Receipt；完整事实、大 ToolOutput 和媒体进入 Artifact/Workspace，模型按预算接收摘要与稳定引用；预算由模型窗口、输出保留、工具 Schema 和控制面动态推导，并统一 Search/Get、压缩抑制、群聊隐私及 Skill/MCP/Subagent 隔离上下文。
- **2026-07-18**：Agent Runtime 总体设计冻结统一 Policy Gate：明确用户直接执行指令可形成有范围授权，提示词讨论不执行；模型、Skill、MCP、Hook 与子 Agent 均不能扩权；所有 Action 在调度前统一完成工具元数据、组织权限、数据范围、成本预留和持久 Interaction 决策，逐步替代默认 SAFE、仅日志 CONFIRM 和进程内 60 秒危险工具确认。
- **2026-07-18**：生产首轮验收发现 `data_compute` 作为模型工具时，模型可将“按平台划分”错误改写为无分组全局求和，数学结果正确但破坏用户输出意图。架构调整为内部 Data Validator：删除模型 Tool Schema、ToolExecutor handler、动态工具注入、`role=tool` 回填、前端工具步骤和历史证据 Prompt；Runtime 根据当前问题生成 ValidationPlan，结果只进入 ArtifactLedger，由 CompletionGate 成功后直接确定性输出，失败时阻断未经验证的数字。旧 `source=data_compute` 证据仅兼容读取历史操作，不再作为原始数据源。
- **2026-07-18**：完成通用任务交付运行时与跨 Turn 数据证据链：保留现有 `AgentResult` 和工具循环协议，在 Web/Actor 两条执行链内部统一接入 `RunContract → ArtifactLedger → Policy → CompletionGate`；只有结构化工具结果进入数据证据，历史证据按 `base_context_revision` 恢复，`data_compute` 仅在存在可计算证据时动态开放，高置信数据追问必须经确定性重算后输出。验收样例固定为排除拼多多总订单 `1,439`、有效订单 `1,056`，重复计算一致。新增迁移 135 及 rollback，迁移尚未应用；跨 Turn 证据持久化走 Conversation Actor 原子提交，旧 Web 同步链仅具备单次 Run 内校验，切换跨 Turn 能力需开启既有 Actor Web 路径。聚焦测试 75 passed、运行时覆盖率 89%；排除两组既有 helper 失败后全量测试 7,596 passed、24 skipped、4 xfailed，剩余 2 项需要外部 PostgreSQL 的企微并发测试因当前沙箱禁止联网而未完成。
- **2026-07-18**：ECharts/Mermaid 职责治理阶段 4：主 Agent 与 code_execute 提示词统一规定数据统计使用 `emit_chart(ECharts)`、逻辑关系使用 `emit_diagram(Mermaid)`，普通文字足够时不生成图形，同一内容不得重复生成；Plotly/Vega-Lite 调整为历史只读兼容。企微通道由“跳过图形”改为 chart 输出格式化 JSON、diagram 输出原始 Mermaid 源码，并合并进入原 stream；ECharts/Mermaid 错误日志仅记录消息 ID、内容类型、渲染器、错误类型和源码长度，不记录 option、DSL 或解析异常正文。本结论取代 2026-07-17 的企微 chart 跳过策略。
- **2026-07-18**：Plotly/Vega-Lite 退出审计暂不删除依赖：代码与测试确认沙盒自动 MIME hook 仍可接收显式 Plotly/Altair 输出，前端继续承担历史只读恢复；生产构建中 `plotly-basic` 约 1.12 MB、Vega embed 约 759 KB，均为独立异步 Chunk，不进入普通文本首屏。当前工作区无法证明生产数据库历史消息数量，删除前仍需只读统计 `spec_format` 分布并确认没有 ECharts 无法覆盖的业务场景。
- **2026-07-17**：修复企微附件跨轮污染：迁移 131 将已绑定任务的附件集合持续保留为 `active`，导致后续“你好”“天气”等纯文本消息仍被数据库拼入旧 CSV，并再次触发文件分析。迁移 133 恢复“当前附件单次消费”语义：首次任务先冻结 `task_attachment_refs`，再原子转为 `referenced`；部署时仅修复已有任务绑定的活动附件，未使用的待处理附件保持 `active`；重放继续复用冻结输入，rollback 不重新激活历史附件。Web 消息附件列表仍由当前请求显式决定，不受影响。
- **2026-07-17**：修复 PromptBuilder 重构遗漏的“最新用户消息优先”约束：Web 与企微共用链路在存在历史时明确禁止擅自续写或重复已完成任务；固定 revision 历史增加 `created_at/context_revision/role/id` 稳定排序，避免同时间戳 user/assistant 次序漂移；企微智能机器人移除“正在接收并排队处理…”文案，直接显示既有思考状态。新增有历史、无历史、附件邻接、固定 revision 排序和企微初始状态回归测试。
- **2026-07-17**：修复 `file_analyze` 的 Parquet 路径契约断裂：CSV/TSV 虽已实际转换为内容指纹命名的 Parquet，但简化 meta 没有 `xml_view`，结果边界退回旧视图后遗漏真实路径。统一由 `file_analysis_service` 使用转换函数实际返回的 `cache_path` 生成沙盒 `staging/...` 访问描述；Excel、CSV、TSV、首次生成和缓存命中共享同一契约，并拒绝不存在或越出当前 staging 的缓存路径。
- **2026-07-17**：企微智能机器人终态回复恢复原消息位置更新：Actor 入队持久化真实 `req_id/stream_id`，复用现有 `StreamKeepAlive` 保活，Outbox 合并全部 `TextPart` 后完成同一 stream；流过期或进程重启时回退主动消息。图片、视频、自建应用通道及 Web 展示不变，`ChartPart` 继续跳过；仅图表结果用简短完成提示结束占位。生产待部署验证。
- **2026-07-17**：回退企业微信结构化图表交付：Web 继续保留 ECharts、Plotly、Vega-Lite `ChartPart`，企微 Outbox 在通道末端明确跳过所有 chart，仅投递文字、图片和视频。删除企微 Playwright/Chromium/ECharts runtime、PNG 素材上传及部署安装链路；chart-only 消息不生成错误兜底，Outbox 可直接完成。生产 pending/dead 记录待部署后只读核查，未经确认不修改。
- **2026-07-17**：企微会话与附件上下文治理阶段 3.1：新增数据库派生的 `ExecutionScope`，分离真实发言人、会话作用域与 Workspace owner。群聊关闭个人 Memory/persona/偏好/位置注入，禁止个人积分、记忆、新对话和定时任务操作；ContextSnapshot 继续提供群共享历史。FILE、file_analyze、Sandbox、ERP 导出、普通图片生成和电商图片生成统一写入稳定 channel Workspace，积分和审计仍归真实发言人。`get_conversation_context` 因与 ContextSnapshot 重复且依赖个人 owner 权限，从群工具目录移除。受影响的三个超限工具文件已按纯辅助、会话读取和文件描述职责拆至 500 行以内。尚未部署。
- **2026-07-17**：企微会话与附件上下文治理阶段 2.2：TEXT/VOICE/IMAGE/MIXED 已统一进入 Conversation Actor，删除企微专属灰度开关和入站旧链路分发；迁移 130 在会话锁内仅为首次稳定消息 ID 消费 active 附件，冻结为输入消息 `FilePart` 并标记 referenced，重复投递复用原输入而不误消费新附件；群聊入队校验 channel binding 并保留真实发送人。同步生成、旧消息持久化、旧结果分发、旧记忆注入和旧直接扣积分尾链已完整删除，`wecom_message_service.py` 从 382 行降至 178 行；企微测试按入站与回复职责拆分，均低于 500 行。128-130 及 130 回滚已通过真实 PostgreSQL 单事务预演，尚未部署。
- **2026-07-17**：企微会话与附件上下文治理阶段 2.1：新增迁移 129 和 `conversation_attachment_refs`；FILE 按 msgid 幂等暂存为 completed 用户消息与 ready 活动附件，不再无指令启动模型。私聊文件进入个人 Workspace，群聊文件进入独立 channel Workspace。128/129 已通过真实 PostgreSQL 事务预演，尚未部署。
- **2026-07-17**：企微会话与附件上下文治理阶段 1.2：新增渠道会话绑定和 `user/channel` scope；私聊按稳定外部身份绑定并可事务认领最近未绑定历史企微对话，群聊创建无个人 owner 的共享 conversation，避免跨群复用及首位发言者删除导致群上下文丢失。迁移 128 尚未部署。
- **2026-07-17**：企微会话与附件上下文治理阶段 1.1：新增回调规范化边界，私聊 FILE 缺失 `chatid` 时稳定回退 `from.userid`，群聊缺失 `chatid` 明确拒绝，文件名同时识别 `filename/name`；`wecom_ws_runner` 不再直接拼装未校验消息。后续仍需完成渠道会话绑定、附件状态机和企微全量 Actor 化。
- **2026-07-17**：生产 FILE 冒烟发现 `OrgScopedDB` 自动注入 `p_org_id`，但 120/121/125 新增的四个租户 RPC 缺少对应签名，导致企微 FILE 无法入队、旧企微 Turn 无法绑定并遗留“思考中”。新增 127 租户 RPC 门面统一校验 org 后委托原子核心；Actor 入站统一建立/结束 stream，旧同步异常将 assistant 占位持久化为 failed。修复已通过全量回归与真实 PostgreSQL 事务预演，生产应用 127 后恢复。
- **2026-07-17**：完成 Conversation Actor 企微持久投递阶段 1.4：删除进程内 `_session_settings`，模型写入 `conversations.model_id`、思考模式由行锁 RPC 原子合并到 `chat_settings`，并按 user/org/source 强校验；企微 FILE 不再扫描或转写正文，原始字节按 msgid 稳定落共享 Workspace、同步 OSS，以标准 `FilePart` 原子入队，未知格式保留为二进制；无生产调用的旧企微 `file_parser.py` 及孤立测试已删除。FILE 已按决策取消旧链路兼容并固定进入 Actor，因此生产必须先应用 120-126 迁移、启动 Actor Worker 和企微 Outbox consumer，再部署本版本应用；当前尚未部署或应用迁移。
- **2026-07-17**：完成 Conversation Actor 企微持久投递阶段 1.3（默认关闭）：`wecom_ws_runner` 增加 PostgreSQL Outbox consumer；按 delivery lease/fencing 认领，长发送期间续租，文本/图片/视频逐项持久化检查点，失败指数退避并在上限后 dead；企微 Actor 改用无头进度 Sink，不再误发 Web 过程/终态事件；Outbox 适配器用发送结果与发送后连接状态共同识别本地 WS 失败。企微主动消息无业务幂等 ACK，仍是可审计的 at-least-once，发送成功与检查点提交之间崩溃可能产生极小概率重复。迁移未应用、开关未开启。
- **2026-07-17**：完成 Conversation Actor 阶段 5.2（默认关闭）：Web Chat 可通过稳定内部 UUID 幂等 enqueue；新增独立 Actor Worker 入口和 Worker/Web 双开关；Worker 扫描与数据库 claim 双重限定 `delivery_context.actor=true`；用户取消改走 fencing RPC，旧 orphan recovery/超时清理跳过 Actor；刷新与迟到 WS 订阅识别 cancelled。尚未应用迁移、启动生产 Worker或打开 Web 流量。
- **2026-07-17**：完成 Conversation Actor 阶段 5.1 Web 基础设施（默认关闭）：新增 `update_generation_progress` fencing RPC 与 rollback、ActorWebSink 流式事件/恢复进度、原子终态后的 best-effort WS 投递及任务槽释放；ConversationExecutionService 只在数据库确认终态后调用 observer，丢权与续约失败不产生外部终态。新增 `conversation_actor_web_enabled=false`，尚未切换 Web enqueue 或启动 Worker。
- **2026-07-17**：完成 Conversation Actor 阶段 4.2 生成内核：新增通道无关 `execute_chat`、过程事件 Sink 和 `ChatGenerationExecutor`；Actor 从 `input_message_id` 恢复并校验完整多模态输入，显式映射 ContextAnchor，响应租约取消且只返回 GenerationOutcome；企微 `generate_complete` 删除独立工具循环并复用 Web 已采用的 prepare/apply/compact/outcome 原语。Actor 异步队列 DB 与现有 Handler 同步上下文 DB 在 Executor 边界隔离。尚未接入 Worker 生命周期、Web enqueue 或企微持久投递。
- **2026-07-17**：完成 Conversation Actor 阶段 4.1 Web ChatHandler 等价拆分：旧 `_stream_generate` 保持原签名并收口为兼容门面；执行前准备、单轮流读取、多轮工具循环、emit/form、上下文压缩、预算结果收尾、错误清理和旧终态持久化按职责拆入 `handlers/chat/`。尚未实现 GenerationExecutor、接入 Worker 或改变 Web/企微生命周期。
- **2026-07-17**：完成 Conversation Actor 阶段 3.2 Worker：新增以数据库为事实源的有界扫描与调度，serial 按 conversation 去重、branch 按 task 去重；Redis 仅作 best-effort 唤醒并支持断连退避重连；停机先停止认领、限时等待后取消本地执行。尚未接入应用生命周期或现有 Web/企微业务链路，生产行为不变。
- **2026-07-17**：完成 Conversation Actor 阶段 3.1 执行协调器：新增类型化 GenerationClaim/GenerationOutcome/GenerationExecutor 与 ConversationExecutionService；统一 serial/branch claim、独立租约续期、连续续约失败丢权、本地执行取消及原子 commit/fail 出口。尚未新增扫描 Worker、Redis 唤醒或应用生命周期接入，现有业务行为不变。
- **2026-07-17**：完成 Conversation Actor 数据库阶段 2.2：新增原子 commit/fail/cancel RPC 与 rollback；完成提交将消息、积分、Turn revision、task 终态及 owner 释放纳入同一事务；取消采用数据库终态先到先得并立即使旧 token 失效。现有 Web/企微链路仍未切换，真实 PostgreSQL 并发验证留待测试库/部署阶段。
- **2026-07-17**：完成 Conversation Actor 数据库阶段 2.1：新增兼容队列字段、稳定序列和索引；实现原子 enqueue、serial/branch claim、租约续期与 fencing token 协议及 rollback。现有 Web/企微链路尚未调用这些 RPC；原子 commit/fail/cancel、Worker 和业务切换仍待后续阶段。
- **2026-07-17**：完成 Conversation Actor 持久执行架构设计：普通 Chat 使用数据库事实队列串行认领，内部 branch 固定快照并行；执行权采用租约与 fencing token；消息、积分、Turn revision、task 终态和 owner 释放纳入原子完成协议；Redis 仅唤醒，Web/企业微信统一通过持久 Worker 执行。实现将按数据库、协调器、Chat 拆分、Web、企微、恢复七阶段推进。
- **2026-07-18**：长期单会话上下文治理完成 Phase 2：Redis 闭合历史升级为滚动兼容的 v4 key，缓存精确绑定 `summary_revision + base_revision + through_message_id`；正常历史恢复最近 3 个安全工具对；摘要仅覆盖连续闭合 Turn，并通过 `expected_summary_revision` CAS 原子提交。ContextSnapshot 先冻结可用摘要边界，再只查询 `(summary_revision, base_revision]`；PromptBuilder 始终按“active summary → recent history”消费。循环摘要只总结当前 Run 实际淘汰的 stale tool messages；删除 Web 独占且会混入保留轮次的请求级 Session Memory，使 Web、企微和 Headless 压缩语义一致。
- **2026-07-18**：长期单会话上下文治理 Phase 3 已统一初始输入与工具循环预算：新增模型能力驱动的 ContextBudget，按 `context_window - max(max_output, 12.5% window) - max(2048, 5% window)` 得到 usable input，并生成 75%/85%/92% 阈值；model_id 由 Web/企微共用执行入口透传，未知模型按 128K/8K 保守能力处理。ContextBudget 随 PreparedChatStream 进入流式及 Headless 循环，依次控制旧工具归档、工具/历史桶与循环摘要、保尾总量兜底；循环不再读取 conversation source 或通道固定 Token 常量。
- **2026-07-18**：长期单会话上下文治理 Phase 3 完成稳定前缀与缓存 usage：PromptBuilder 固定按静态规则、会话稳定层、活动摘要、recent history、Evidence、动态时间/位置、当前资源和用户输入组装，动态时间不再截断历史前缀；统一 StreamChunk 新增 cached_tokens/cache_creation_tokens，DashScope、Google、OpenRouter 解析实际 Provider 字段，Web 流式与 Headless 共用累积逻辑并随既有 usage 持久化。未创建 24 小时显式缓存，缓存 miss 仍只影响成本和延迟。
- **2026-07-18**：长期单会话上下文治理 Phase 4 子任务 1 完成当前 Run 压缩可靠性：流式与 Headless 均以 task_id 作为 suppression scope，对实际 stale messages 生成稳定 SHA-256；相同 prefix 单 in-flight，主/备摘要失败后本 Run 不重复调用，摘要返回时 prefix 变化则放弃应用并保留原消息；Run 结束统一清理协调状态，失败集合有 1024 项上限。跨 Worker 的跨 Turn 摘要仍待 Redis 分布式锁与 5 分钟失败 suppression。
- **2026-07-18**：长期单会话上下文治理 Phase 4 子任务 2 完成跨 Worker 摘要协调：跨 Turn DB 摘要以 conversation、summary revision、through revision 的哈希前缀获取 60 秒 Redis 锁；锁冲突不等待，模型双路失败后相同 prefix 抑制 5 分钟，新 revision 可立即重试。Redis 全链路异常降级到既有数据库 revision CAS，不阻断 Web、企业微信或 Headless 聊天。
- **2026-07-19**：Web Conversation Actor 正式转正：`ChatHandler.start` 固定原子入队，删除 `conversation_actor_web_enabled`、`_stream_generate`、LegacyStreamRequest 以及旧 Web runner/loop/lifecycle。空输出恢复、steer、分阶段取消、智能换模型重试、Provider 熔断统计和终态后记忆/摘要/知识钩子均已迁入 Actor 主链；Web、企微和 Headless 继续共享 `prepare_chat_stream`、`execute_chat`、工具、上下文与预算能力。
- **2026-07-18**：长期单会话上下文治理完成统一观测：ContextReceipt、固定 revision 缓存、当前 Run 压缩和 Evidence Search/Get 统一输出 `gen_ai.context_*` 结构化事件；记录估算 Token、类型分布、hit/miss、压缩前后差值和检索 outcome，不写消息或证据正文。Provider 实际 input/cache Token 继续沿既有 usage 持久化。
- **2026-07-19**：取消长期上下文内部独立 `ContextRollout` 灰度及 org/channel/model allowlist；Evidence、Search/Get、当前 Run 与跨 Turn LLM compaction 统一进入 Web、企微和 Headless 共用主链。Agent Runtime 发布灰度设计保留，避免两套灰度控制面和 `shadow` 语义冲突。
- **2026-07-17**：完成 ContextSnapshot：Web/企业微信正式 task 使用 `base_context_revision` 构造不可变闭合历史，严格校验 `input_message_id/turn_id`，不读取共享 Redis 决定历史边界；相同文本不再被猜测去重；任务私有副本独立压缩；企微首轮恢复小预算配置；旧任务保留受监控 legacy 路径。
- **2026-07-17**：完成 Web/企业微信 Turn 绑定：所有正式 AI 生成 task 绑定 `input_message_id/turn_id/base_context_revision`；assistant 写入 `reply_to_message_id`；企微同步生成纳入正式 task 生命周期；成功关闭 Turn，失败不推进 revision；旧 retry 消息保留受监控的输入锚点降级。
- **2026-07-17**：完成 Turn/revision 数据库基础：messages/tasks/conversations 增加兼容字段与索引，新增幂等 `bind_generation_turn`、`close_generation_turn` 事务 RPC 及 rollback；业务链路将在后续阶段接入。
- **2026-07-17**：完成显式媒体协议收口第一阶段：删除普通模型文本 URL 和 `[FILE]` marker 扫描；Web 流式与企微非流式统一消费 `emit_*` 结构化 payload；多图网格完成态只按实际 ImagePart 渲染。历史错误消息不回填。
- 较早更新记录见 `docs/archive/CURRENT_ISSUES_UPDATES_2026H1.md`。
