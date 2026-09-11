# 板块 05：生产验收问题链路与修复方案

> 后续实施：用户已要求恢复上下文相关逻辑。S-03/S-04 的结果保留、逐调用状态与摘要替换现已实施，并补齐后续预算/循环摘要会再次丢失事实的链路。当前 986 项相关回归通过，实际接口和 A/G 状态见 [恢复记录](TECH_05_上下文结果恢复.md)。没有恢复此前位置/目标文案补丁；S-05 真实模型验证仍未完成。下文未实施描述保留为方案提出时的快照。

最新状态（2026-09-11）：用户已授权第 1、2 项，删除迁移与统一文件调用信息已实现并完成 608 项相关回归；上下文/状态部分只授权对照调查。已撤回未验证的附件位置与目标提示补丁。详见 [本轮实施及 Grok Build 对照](TOOL_UNIFICATION_05_ITEMS12_AND_ACTOR_COMPARISON.md)。以下方案保留调查时的历史状态；其未实施建议不等于当前代码，整体技术验收仍未通过。

代码基准：任务分支 `codex/task/20260910221341-tool-unification-05`，HEAD / 本次故障的已部署版本 `887b28ae4aedb5d4dde5f0cbbd1bcb4484fda9ba`；前置基座 origin/main 为 `6c0737ab78f2d0cb3b2a8376498e7825b0829431`。调查同时检查 HEAD 原实现与本工作树未提交差异，不把提示补丁误当作线上代码。

## 1. 结论与证据强度

三个现象发生在三个不同边界：数据库任务生命周期、模型构造文件参数、模型决定后续工作目标。没有证据表明它们由一个 Actor 故障统一造成。

| 编号 | 事实与定位 | 能作出的结论 | 不能作出的结论 |
|---|---|---|---|
| D-01 | 两次删除均在 `commit_scheduled_task_changeset` 的 DELETE 上触发 `scheduled_task_drafts_confirmed_task_id_fkey`；隔离 PostgreSQL 能重现 | 旧创建草稿的外键生命周期与删除任务不一致，是确定根因 | HTTP 200 不是业务删除成功；不能靠前端移除列表项修复 |
| D-02 | 最新输入带正确新附件；模型首次提交正确 fid，同时把文件名放进 resource_ref；准备阶段严格拒绝，随后搜索并复制正式引用就读取成功 | 这次参数错误首先由模型构造；错误没有发生在文件解析器读取内容时 | 没有证据证明工作区插入丢路径、文件损坏，或应该忽略无效选择器 |
| D-03 | 最后一次输入确实是“读取文件”；固定 revision 的历史、本轮 manifest、模型消息尾部一致；成功读取后出现新的统计/导出/画图调用 | 实际发生了任务目标扩张；不是简单显示重复，也不是 Actor 把旧输入当新输入 | 无法从一次生产轨迹证明模型为何选择旧目标的唯一诱因 |
| D-04 | closed assistant 历史仅保留 text；表单独占回复投影为空，生成文件独占回复也为空；本次 rev6 的表单回复实际消失 | 历史结果投影不完整，是确定代码缺陷 | 它必然导致本次 rev7 统计旧任务，尚未证实 |
| D-05 | ToolLoopContext 仅追加失败工具名；后续成功不改变失败提示；模型上下文仍出现“上轮失败工具” | 失败状态摘要失真，是确定代码缺陷 | 清掉失败提示就能防止任务目标扩张，尚未证实 |
| D-06 | 本轮文件清单位于历史前，附件 XML 又位于最新输入前；已有“以最新消息为准”提示；auto 同时要求做合理假设 | 输入组织与指令优先级有可改进之处；再次加一句提示不足以证明修复 | 不能把位置调整本身称为已经验证的模型根因修复 |
| D-07 | ToolPolicy 检查身份、组织、模式、权限、资源、风险和确认；`code_execute` 声明为 analysis。Chat 在工具返回后通常继续让模型选工具 | 权限约束与任务目标判断是不同职责；当前没有判断任意代码是否超出最新目标的执行机制 | 不能把 policy allow 理解为“用户本轮明确要求了这段代码”；也不能据此断言权限系统失效 |

生产轨迹来自本次已授权只读调查保存的消息、checkpoint、ChangeSet 和日志。实际模型记录为 `qwen3.5-plus`；原数据留在本机受限临时目录，不复制业务表格、客户信息或完整引用到仓库。原始证据位置与数据库复现见 [生产问题记录](TOOL_UNIFICATION_05_PRODUCTION_FIXES.md)。本方案没有额外调用生产模型。

本轮另核对十个相关源文件在前置 main 与部署 05 之间逐字节一致，见 [源码对照](tool-unification-evidence/05-diagnosis-source-comparison.json)。这证明上述历史加载、失败提醒、参数契约和模板行为早于 05；不证明它们与模型错误没有关系，也不免除本次验收修复责任。

## 2. 实际调用链与各环节职责

### 2.1 插入文件、进入 Actor、冻结本轮输入

```text
工作区“发送到聊天” / 输入框 @ 选文件
  → useWorkspaceItemActions.toAttachment / InputArea.handleMentionSelect
  → addWorkspaceFile（name、workspace_path、URL、mime、size）
  → useInputSubmission：本轮文本 + 有序附件
  → useTextMessageHandler → sendMessage → messages.content
  → 绑定 turn、input_message_id、base_context_revision → Actor claim
  → ChatGenerationExecutor._load_input_content（校验消息、会话、turn、role）
  → execute_chat → prepare_chat_stream
```

前端不会生成 resource_ref，也不会把上一次聊天指令拼进这次正文。流式期间无附件的文本可能成为 steer；带附件仍按新输入提交。本次数据库已固定为新的 turn，故不符合误走 steer 的解释。

`resolve_execution_scope` 区分 actor_user_id 与 workspace_owner_id；不能为修复意图误判合并二者。`ContextAnchor` 固定消息和闭合历史 revision；`ResourceManifest` 读取该输入对应附件，历史附件名只是叙述，不自动进入本轮 manifest。

代码：[文件插入](../../frontend/src/components/workspace/useWorkspaceItemActions.ts)、[输入提交](../../frontend/src/components/chat/input/useInputSubmission.ts)、[Actor 执行器](../../backend/services/handlers/chat/executor.py)、[上下文快照](../../backend/services/handlers/context_snapshot.py)、[资源清单](../../backend/services/handlers/resource_manifest.py)。

### 2.2 构造历史和模型输入

```text
ChatContextMixin._build_llm_messages
  ├─ 从本轮 ContentPart 提取 workspace_files，注册路径缓存
  ├─ build_context_snapshot
  │   ├─ 按 org / conversation / revision / through_message 校验闭合历史缓存
  │   ├─ 缓存未命中 → history_loader.build_context_messages
  │   └─ build_resource_manifest → 当前输入附件
  └─ PromptBuilder.build
      ├─ static：role / rules / workflow / tool_strategy / modes
      ├─ session：mode / preferences / facts / memory
      ├─ current time、历史、摘要、附件描述、最新用户原话
      └─ history/tool/total budget → 模型消息
```

线上 HEAD 的顺序是：静态/会话/时间 → **本轮工作区清单** → 历史 → 可选摘要 → 最新优先提示 → 本轮附件 XML → 最新 user。此前实验候选曾把清单移到历史之后；2026-09-11 已撤回，当前顺序与 HEAD 一致。

历史完成消息默认剥掉完整工具调用，只留用户可见 text，避免已执行代码重新成为模板；这个方向应保留。但 `_row_to_oai_messages` 对表单、文件等没有文字的完成消息返回空数组，随后 `_append_tool_digest` 也找不到 assistant 可附加。不能用恢复全部历史工具输入代码来弥补这一缺口。

表单状态会经 `transition_chat_form_state` / `attach_chat_form_changeset` 更新原消息；ChangeSet 状态由卡片另行读取。历史投影不能把“已展示表单”直接当成“业务任务已创建”，也不能把可变表单状态无版本地混入已冻结的历史缓存。

缓存是闭合消息的投影，不是任务输入权威。修改历史投影时必须处理缓存版本，避免用户继续命中旧的空投影；不能因此重写 ledger/checkpoint 格式。

代码：[历史加载](../../backend/services/handlers/chat_context/history_loader.py)、[内容提取](../../backend/services/handlers/chat_context/content_extractors.py)、[缓存](../../backend/services/handlers/conversation_cache.py)、[PromptBuilder](../../backend/services/prompt_builder/builder.py)、[UserLayer](../../backend/services/prompt_builder/layers/user_layer.py)、[表单交互](../../backend/api/routes/ws.py)。

### 2.3 工具选择、参数准备和文件读取

```text
prepare_tool_turn → Registry advertised schemas → ModelGateway
  → DashScopeChatAdapter.stream_chat（messages 原样放入请求体）
  → assistant tool calls → ChatToolMixin._execute_tool_calls
  → ToolRuntime.execute
      身份/权限 → JSON/作用域 → resolve_file_call → 准备/版本校验
      → Policy / 必要确认 → 锁内再次校验 → 缓存/ledger → Dispatcher
  → FileToolMixin → file_analysis_service.analyze_file
  → 稳定源快照 → 转 Parquet → 登记路径和结构信息 → AgentResult
```

三个选择器仍有各自明确含义：`file_id` 是兼容短标识；`resource_ref` 是绑定工作区和文件版本的签名定位信息；`path` 是兼容名称/相对路径。多个同时提供时必须合法并指向同一目标。引用是定位信息，不能授予权限。

本次首次调用在 `validate_selectors` 就被拒绝，Handler 未开始；错误后成功的搜索和读取证明有效 fid 与搜索引用路径都存在。ToolResult 保留错误及恢复信息，但 Chat 循环的状态摘要只消费 `工具名 + display_text + is_error`，没有充分使用这些信息。

读取结果含结构、样本、Parquet 访问示例，约 1.2 万字符。本次轨迹中这些内容正常回填；没有发现 ToolResult 被变成对象字符串或错误投影替换成历史结果。访问示例不等于用户新下达的统计/导出任务。

代码：[schema](../../backend/config/file_tools.py)、[文件参数准备](../../backend/services/tools/file_calls.py)、[定位器](../../backend/services/file_resources.py)、[资源权限](../../backend/services/tools/resource_access.py)、[运行时](../../backend/services/tools/runtime.py)、[策略](../../backend/services/tools/policy.py)、[读取实现](../../backend/services/agent/file_analysis_service.py)。

### 2.4 工具结果、继续执行、展示和持久化

```text
AgentResult / FileReadResult / FormBlockResult / str / exception
  → ToolResult
  ├─ Chat：原 to_message_content → 模型 tool 消息；图片另走多模态注入
  ├─ ToolLoop：原 to_tool_content → 定时模型消息
  ├─ 既有 emit / sink → tool_step / file / chart / form 等 WS blocks
  ├─ 既有审计写入器（从统一结果取值，不重复）
  └─ legacy_persistence_value → 旧 ledger；messages/blocks → 旧 checkpoint
```

Chat 结束条件主要为模型不再调用工具、预算用尽、表单终止、资源止损、取消/控制命令及 uncertain 等。`file_analyze` 成功不表示所有用户任务已结束：用户可能本来要求读取后汇总。因此不能直接把它改成固定终止工具。

本次在读取成功之后，模型新发出了四次 code_execute 调用，包含统计、汇总表和图表。前端收到的是实际执行产生的产物；仅隐藏下载卡片或图表不会解决目标误判，也会违反“不丢产物”。

定时 ToolLoop 使用已确认任务定义和 execution_policy，不从聊天历史推定授权。其状态分类、停止策略应回归，但不能把聊天的一句“只读取”提醒无条件灌入定时任务。

代码：[Chat 循环](../../backend/services/handlers/chat/execution_engine.py)、[结果回填](../../backend/services/handlers/chat/tool_loop.py)、[状态摘要](../../backend/services/handlers/tool_loop_context.py)、[统一结果](../../backend/services/tools/result.py)、[定时停止策略](../../backend/services/agent/stop_policy.py)。

### 2.5 定时任务删除的业务链

```text
ScheduledTaskPanel.handleChangeRequested / ChatTaskManager._handle_delete
  → 创建 delete ChangeSet → 权限、revision、非 running 校验
  → ChangeSetCard / 聊天卡片确认 → ChangeSetService._commit
  → ScheduledTaskAdapter.commit → commit_scheduled_task_changeset
  → 锁住目标任务 → 再查 revision/status → DELETE → 幂等回执
  → ChangeSet applied → 前端刷新任务列表
```

244 迁移的 `confirmed_task_id` 使用默认 NO ACTION；245 的 `source_task_id` 却使用 CASCADE。新建 ChangeSet 不再写旧草稿，因此只测试新任务不能暴露老任务被旧草稿阻止删除的问题。异常会使业务事务回滚并转成 failed ChangeSet；传输成功和业务状态是两个层次。

现有 253 迁移选择 CASCADE，与 source_task_id 的生命周期一致。预检 run 随 draft 删除；ChangeSet 与独立 `scheduled_task_change_receipts` 不依赖任务外键，仍保留。它不是所有历史记录的无损保留方案。

简单 SET NULL 虽能解除 DELETE 阻塞，却会留下 confirmed 且无目标的旧草稿；旧 confirm RPC 的重复确认分支与 API 后续读任务将不一致，因此不能只换成 SET NULL 就声称完整修复。若产品要求永久保留旧草稿/预检，需要另外定义删除后状态和读取规则，当前没有该要求。

另外发现旧 store 的 deleteTask 调用旧 DELETE API 后直接移除列表项，而该 API 现在返回提案。当前面板实际走 proposeChange，搜索到的 store.deleteTask 调用方只有旧测试，故它不是本次生产删除根因；记录为兼容入口问题，不混入当前推荐修复。

代码：[面板](../../frontend/src/components/scheduled-tasks/ScheduledTaskPanel.tsx)、[业务适配器](../../backend/services/scheduler/scheduled_task_change_adapter.py)、[提交 RPC](../../backend/migrations/249_scheduled_task_changeset_adapter.sql)、[旧生命周期](../../backend/migrations/245_scheduled_task_lifecycle_integrity.sql)。

## 3. 推荐方案与现有补丁处置

推荐修复已证实的机制缺陷，同时把模型行为对照测试设为必需验收；不立即增加另一套通用意图审批/执行平台。原因是当前证据不足以证明需要重写模型循环，而新增一个由模型判断的“意图合同”也不天然成为可靠的授权事实。

### S-01：修正旧任务关联生命周期

- 保留 253 的 CASCADE 方向及回退迁移，不修改旧编号迁移，不改 ChangeSet 确认/权限/幂等链路。
- 上线前核对该外键仍为预期定义，核对关联影响范围；迁移事务失败就退出，不静默继续。
- 复用已通过的六项真实 PostgreSQL 测试；追加旧创建草稿经原 confirm RPC 创建任务后再删除的完整入口用例，以及已删除旧草稿重新确认的明确 missing/404 行为，覆盖页面刷新和重复确认。
- 业务失败继续是 failed/conflicted，不能通过 HTTP 状态或 UI 乐观更新冒充删除成功。

### S-02：统一资源信息与可调用参数的来源

- 在现有文件参数/展示边界抽取一个共享的调用指引构造函数：附件给出可直接使用的 fid 参数；搜索结果给出其已经签发的 resource_ref 参数。均只给一个首选选择器，名称和路径保留作展示与沙盒读取信息。
- 附件短清单、XML、工具 schema 文案使用同一语义：本轮附件用 fid；搜索引用原样复制；合法多选择器输入继续兼容且必须一致。不得生成新的任意短引用服务或让模型重新签发引用。
- 现有 `<read_call>` 补丁可作为交互呈现的起点，但实现应收敛到共同来源；不能仅凭文案测试就判为有效。
- 校验失败仍在 Handler 之前返回原错误；保留 ToolError.kind、retry_context 与 not_started。循环把正确恢复动作和当前范围带回模型，不偷偷删除错误字段重试、不自动扩大 workspace、不换文件。
- schema 合法参数和旧别名不删；无效签名、同名歧义、多选择器冲突、跨组织/owner 越权继续拒绝。是否避免了模型首次填错，要由真实模型试验判断，不能靠容错吞错制造成功。

### S-03：补齐闭合历史的结果投影

- 为完成的 assistant 回复增加确定性的非文本结果摘要：已提供表单、已返回文件/表格/媒体；保留识别结果所需的名称、稳定引用与真实完成信息，且按类型去重。
- 有正文时补充必要缺失的结果信息；没有正文时仍生成一条可理解的 assistant 结果消息。不要重新注入已完成的工具代码、完整输入参数或把产物内容复制两遍。
- 对表单使用稳定事实“已提供配置/变更表单”；提交或生效状态只能来自当时可验证的状态，不能从 tool success 或 submitted 推导 active。尚无版本化状态的历史只记录表单交付事实，当前业务状态走现有 ChangeSet 查询。保留原表单终止和操作协议。
- 历史缓存增加投影版本隔离，旧缓存未命中新版本时从数据库重建；不清空用户历史，不修改已冻结 checkpoint。更新预算回归，避免保留历史 user 目标却丢失对应 assistant 完成事实。
- 最新中断任务的恢复协议单独保持；不因修复完成历史而把 cancelled/uncertain 当成成功或可重试。

### S-04：让本轮状态摘要反映真实的调用结果

- `apply_tool_results` 将本批调用及完整 ToolResult 交给状态管理器，保留 call_id、工具、参数来源、业务 status、执行 status 和 error/retry_context；停止从“工具名曾失败”推导“上轮仍失败”。
- 失败提醒按实际最近批次表达。若需表达未解决失败，只能在同一可信资源/操作上关联恢复；无法关联时仅陈述历史尝试，不能声称已恢复，也不能用另一文件的成功抹掉原失败。
- 旧错误仍留在原工具消息、tool_step 和审计；改变的是摘要是否声称它是当前未解决状态，不删除错误历史、不重复记账。
- 动态摘要只维护一个当前块，避免先去重再 append 导致相邻重复提醒；成功、empty、partial、plan 分别表示，不把无异常等同于任务目标完成。
- 当前未提交的附件位置修正保留方向；把目标约束收敛到稳定模板和本轮边界：auto 允许选择实现方法，不能自动扩大最终产出；历史可用于指代和事实，完成的目标只有用户明确继续时才延续；工具使用示例仅说明能力。
- 不在工具清单里按“读取”关键词封杀 code_execute。PDF/Word/文本读取和表格辅助计算仍需合法路径；file_analyze 成功也不能无条件强制终止复杂任务。

S-03 / S-04 修复的是已经证明的输入信息丢失和状态失真；它们能否消除本次模型目标扩张，要由 S-05 验证。这一限定属于方案的完成条件，不是部署后的“后续关注”。

### S-05：用可重复的真实模型评估决定是否足够

分开三类证据：协议/状态确定性测试、真实模型语义行为、用户实际 UI 与产物体验。不能把前者的通过数当作后两者通过。

1. 固定测试历史、最新输入、资源列表、工具 schema、文件分析返回样本、模型及参数；使用去标识化合成工作簿和模拟业务数据。首次保留生产轨迹的轮次结构；不用真实客户表格调用模型。
2. 对照三份候选：线上原版本、现有纯提示补丁、完成 S-02～S-04 的候选。另对历史投影、附件位置、失败摘要分别撤除一项，辨别哪项影响结果；某个对照不能复现时如实报告。若原版和候选均不出现错误，只能报告该样本未复现，不能据此证明修复有效；扩大有辨识力的历史/结果切片后再判断。
3. 模型使用真实调用；工具可在隔离环境执行或用固定响应替代，以检测真实参数和任务选择。记录哪些环节真实、哪些是假数据，绝不称为生产全链路验证。使用独立测试凭据和额度，不能加载生产配置作为测试配置。
4. 每个代表场景至少重复五次，记录首个无效参数率、超出本轮目标的调用数、正确续接率、工具步数和 tokens。五次零失败仅是这个样本集的观察，不是“保证模型永不犯错”。
5. 对本次原场景：首次读取参数合法；分析当前新文件成功；回复结构/字段/必要样本；没有额外统计、Excel 导出或图表。对应反向场景“按刚才的方式统计并生成汇总表和图”必须正常完成，防止靠禁止工具制造通过。
6. 若候选仍出现明确的目标扩张或正常继续被阻断，验收不通过，不部署成“已修复”。下一步才评估独立的本轮目标约束机制，明确解释来源、能力限制、误拒绝行为、steer 与恢复、成本；不能让模型自报的 intended_action 直接成为可信授权。

当前没有进行真实模型 A/B，这里给出的是具体试验方案。执行时需确定独立测试凭据/额度；不需要付费媒体生成。

## 4. 文件职责、兼容和实施顺序

| 批次 | 具体位置 | 改动职责 / 保持边界 |
|---|---|---|
| 1 | 253 迁移及 rollback、scheduled_task_draft_delete_integration、scheduled routes/UI 测试 | 修旧任务生命周期；不改审批流程 |
| 2 | `config/file_tools.py`、`handlers/chat_context/attachments.py`、现有文件结果格式化边界 | 统一可调用参数指引；保持 selector 校验与 Handler 不变 |
| 3 | `chat_context/content_extractors.py`、`history_loader.py`、`conversation_cache.py`、相关历史/预算测试 | 完成结果的文字投影及缓存隔离；不重放已完成工具 |
| 4 | `handlers/tool_loop_context.py`、`chat/tool_loop.py`、`prompt_builder/builder.py`、`templates/rules.md` / `modes.md` / `tool_strategy.md`、必要去重代码 | 从统一结果形成准确本轮摘要；统一任务范围规则，不另造权限模型 |
| 5 | 新增隔离语义评估数据/运行器、现有 05 协议测试、验收和交接文档 | 对照三个候选及归因试验；所有必需项通过后才成为发布候选 |

批次 2 的共享函数名与落点由实施时现有格式化模块决定，不在本方案中宣称尚不存在的接口已经上线。变更始终留在本任务工作树，不直接改主工作树。

必须保持：

- Chat 与 ToolLoop 分别用原模型结果投影；file_analyze 大结果本身及完整产物不为测试而删改。
- 既有 WS block 类型、字段、顺序、delivery 元数据、表单终止、ERP 交互 TABLE 去重和定时 TABLE 收集。
- 原 audit writer、tokens、thinking、错误及媒体 retry_context；展示失败不重做业务。
- ToolResult 仅为运行内对象，ledger/checkpoint 继续用经验证的旧兼容载荷；不得把本方案变成板块 06 的新回放协议。
- actor lease、输入锚点、revision/steer/cancel、业务权限和 sandbox 执行能力不重写。上下文缓存投影版本不等于 ledger/checkpoint 格式切换。

## 5. 验证矩阵与回退

| 编号 | 必须验证的行为 | 证据方式 / 当前状态 |
|---|---|---|
| V-01 | 老草稿创建任务后可删除；新 ChangeSet 任务也可删除；运行中、旧 revision、跨组织、重复确认正确 | 六项 PG 测试已通过；新增完整旧创建入口和 UI 状态用例待实施 |
| V-02 | 上传 / 工作区插入 / @ 附件三入口可读；同名、签名过期/伪造、多个选择器冲突严格拒绝 | 旧选择器回归已有；新共同参数指引和真实模型首次调用待验证 |
| V-03 | form/file/table/media 无正文时仍有历史完成事实；已有正文不重复；表单交付不冒充业务成功 | 本轮探针证明现缺陷，修复后断言待实施 |
| V-04 | 无历史读取 / 旧导出后新读取 / 新文件明确继续旧分析 / 仅文字追问旧图 / 多附件 / 中断后新目标 / steer 改目标 | 参数化协议 + 真实模型语义对照；真实模型尚未验证 |
| V-05 | 参数失败→修正成功；A 文件失败而 B 成功；并行批次部分失败；empty/partial/plan；timeout/cancel/uncertain | 新状态管理器测试待实施；原错误和审计不可抹掉 |
| V-06 | 新投影缓存未命中重建；既有 checkpoint 按原快照继续；长历史压缩不重新激活旧目标 | 集成回归待实施，保持旧 payload |
| V-07 | 模型投影、WS payload/delivery、产物解析/可打开/不重复、审计与持久化仍满足 A-05-01～05、G-01～05 | 复用未变更边界证据并重跑受影响项，人工与真实模型结果单列 |

本轮新增 [诊断探针](tool-unification-evidence/05-diagnosis-probe.py) 以合成数据调用现有函数，记录八项现状观察，结果见 [观察结果](tool-unification-evidence/05-diagnosis-observations.json)。它不是八项修复通过，也不计入此前 731 项回归。命令：

```bash
APP_ENV=testing DATABASE_URL=postgresql://tool_test@127.0.0.1:1/tool_test \
JWT_SECRET_KEY=tool-unification-test-only REDIS_PORT=1 PYTHONPATH=backend \
/Users/wucong/EVERYDAYAIONE/backend/venv/bin/python \
docs/document/tool-unification-evidence/05-diagnosis-probe.py
```

此前 731 passed / 3 既有 skipped 仅覆盖旧候选的代码回归与六项本地 PostgreSQL 行为；不能为本方案尚未实现的代码背书。

回退：应用回退到 887b28ae 时仍能读取旧 ledger/checkpoint；历史缓存按版本丢弃重建，不修改数据库聊天历史。253 回退恢复原外键规则，会恢复老任务删除阻塞；任何已级联删除的草稿/预检无法靠 rollback SQL 恢复，需事前备份。因此应用回退与数据库回退分开决策，不能自动绑定执行。

## 6. 范围决定和交接

本轮交付是方案，没有自动实施上面的批次。推荐 S-01～S-05；现有纯提示补丁只能作为对照候选，不直接发布为三项修复完成。若用户要求可证明的固定“仅预览文件”操作，可另行评估明确 UI 动作与受限工作流；不能假称自然语言推断具备同等确定性。

不建议现在实施：忽略无效 resource_ref、默认换文件/扩 scope、删除历史以避免干扰、每次 file_analyze 后强制终止、按关键词禁用代码、新增模型审批器并把其判断当权限、为所有聊天重建规划平台。

仍未关闭：真实模型参数错误与目标扩张的复验；新历史与状态修复的代码和测试；生产迁移及用户复验。板块 05 验收未通过，不能进入板块 06。
