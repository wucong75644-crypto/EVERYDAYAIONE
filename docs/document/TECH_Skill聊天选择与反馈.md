# Skill 第一期第 4 步：聊天手动选择与运行反馈

P2-3 当前界面新增独立“固定到当前会话”入口，见 [Skill 会话预绑定](TECH_Skill会话预绑定.md)。原“仅本条消息”的手动选择不变，只有明确绑定操作会持久保存固定版本；下文“不把选择保存为会话设置”为 P1-4 的原阶段边界。

任务分支：`codex/task/20260918225125-skill-p1-4-chat-ui`。基座与代码回滚点：`b41ebd698b4af585bb93d8f794b86e0e50f1be29`。P1-4 初次实现没有数据库迁移；2026-09-19 故障修复新增 258 失败快照迁移。不修改旧 Runtime 平台路径、Skill 编辑/审核、计划任务或 MCP，也不把选择保存为会话设置。

2026-09-19 后续：用户提供“基于参考图的多方案生图提示词生成器”作为首个示例，已整理为[参考图多方案提示词 v1](../../examples/skills/README.md)，明确分配给“蓝创”。启用阶段将正式构建 UI 默认打开，开发/测试默认关闭，保留显式构建开关。不新增编辑或管理功能。

## 2026-09-19：首次手动调用超时与反馈丢失（本地修复，未部署）

生产只读证据：21:39:05.201 请求模型，21:39:06.411 收到首块，21:40:05.202 超时。失败任务已选择 `reference-image-prompts/v1`，保存了成功的 `skill_step` 和 4,897 字符正文；交付快照共 309 个序号，助手消息落库内容为空。不是 SkillResolver 拒绝，也不是首次请求完全没有模型响应。

根因与修复：

- Gateway 把普通模型默认 60 秒设成整次请求 deadline，覆盖了聊天已有的整轮执行预算。现在有执行预算、未显式指定 timeout 的请求按排队/首包和后续分块空闲计时，持续输出仍受不可续期的整轮预算约束（默认 600 秒）。显式 timeout、无预算辅助调用仍保留总 deadline；排队不续期，取消、并发槽位和不重试部分输出的规则不变。此修复位于共享 Gateway，适用于所有采用同一预算合同的调用，不是 Skill 特殊放宽。
- 顶层 MessageItem 漏识别 skill_step；失败分支也跳过结构化块渲染。已补齐完整消息的执行中、完成、失败展示和 UI 开关验证。
- Actor 失败 RPC 只改状态，没有把 fencing 进度写入助手消息；WebSocket 失败处理还会覆盖内容并丢弃最后一批文字。258 在原事务边界保存 Skill/工具块与去重后的部分正文，追加失败提示，重复/过期执行者不重复写入。失败事件携带规范化消息快照；客户端优先接受该快照，兼容没有快照的旧事件并保留缓冲文字。超时改成中文提示，不向用户展示模型网关内部字段。

迁移只替换 fail_generation_turn，不新增表、权限、索引或生产历史回填。此前失败的消息不会被自动重跑或补写，复验需重新选择 Skill 并发送。

验证：后端 266 项通过，另 5 项既有积分数据库集成测试因专用测试库配置缺失跳过；新增临时 PostgreSQL 8 项均实际运行通过，包括快照合并、普通聊天、失败 Skill、过期执行权、范围不匹配、取消竞态保护、幂等与回滚。前端定向 59 项通过；TypeScript 和正式构建通过（仅原有大 chunk 提示）。Gateway 回归以缩短的时间窗口重现“持续输出超过默认上限”，没有调用生产模型或改变生产数据。

生产验证（下一次明确“提交部署”后）：

1. 用受控 release.sh 发布任务提交并应用 258；核对生产提交、服务健康和 CDN 配置摘要。
2. 在蓝创重新选“参考图多方案提示词”，上传原图并发送“需要提示词”。应在模型开始回答前看到“已启用 Skill · 参考图多方案提示词 · v1”，长回答不因持续输出到 60 秒而被截断，完成/刷新后提示保留。
3. 若真实发生超时，页面应保留已启用提示与部分正文，并显示中文错误；刷新后内容一致。下一条不选 Skill 时仍是普通聊天。故障注入只在隔离测试中执行。

回滚点：本轮前已部署提交 `13d7c59842799a1098d6b373243a9ed4c4838a6a`（含 CDN 修复）；数据库配套 `backend/migrations/rollback/258_conversation_failure_snapshot_rollback.sql` 恢复旧函数，不删除已有消息快照。当前没有提交、部署、合并 main 或清理工作树。

改动文件：

- 超时与服务端交付：`backend/services/model_gateway.py`、`backend/core/config.py`、`backend/services/conversation_execution.py`、`backend/services/conversation_delivery.py`、`backend/schemas/websocket_builders.py`。
- 失败落库：`backend/migrations/258_conversation_failure_snapshot.sql`、`backend/migrations/rollback/258_conversation_failure_snapshot_rollback.sql`。
- 聊天显示：`frontend/src/components/chat/message/MessageItem.tsx`、`MessageBubbleContent.tsx`（同目录）、`frontend/src/contexts/wsTaskMessageHandlers.ts`。
- 后端回归：`backend/tests/test_model_gateway_stream_budget.py`、`test_conversation_failure_snapshot_postgres.py`、`test_conversation_execution.py`、`test_conversation_delivery.py`（同目录）。
- 前端回归：`frontend/src/components/chat/__tests__/MessageItem.test.tsx`、`frontend/src/contexts/__tests__/WebSocketContext.test.tsx`。
- 文档：本文、`docs/document/TECH_ModelGateway_T6.md`、`docs/CURRENT_ISSUES.md`。

## 用户行为

聊天输入工具栏在高级设置旁增加 Skill 图标入口，复用现有 Popover、字号和颜色。列表显示服务端返回的名称、版本和描述；可选择一个或取消手动选择。选择不代表激活成功，发送后由服务端结果确认。

输入区标签交互：选择后，完整 Skill 名称以浅蓝标签显示在输入文字前，用户接着输入需求；底部入口固定显示“Skill”，用于更换。选择完成后焦点回到输入框，点击标签 × 只取消选择并保留文字和附件。窄屏空间不足时文字输入区域自然换行。标签不拼入消息正文，仍使用原有 `selected_skill` 请求字段；发送后清空规则不变。本次仅调整前端交互，回滚点为已部署的 `ec05d6c98e22cc0e8fbf591b42e661374125336d`，其中包含已验收的 CDN 修复。

列表每次打开重新请求当前会话的目录，不按组织、工具列表或 `model_selectable` 在客户端筛选。新聊天点击入口时先通过已有接口创建会话，再查询该会话的可见摘要；没有点击入口的普通新聊天仍在发送时创建会话。加载、空目录、请求失败和重试都有轻量提示。切换会话、退出聊天模式或开始流式执行后清空选择，不复用另一个会话的迟到目录结果。运行期间禁止更改本 Turn 的 Skill，现有 steer 流程保持原行为。

发送文本、附件或语音时消费一次选择，下一条默认不保留；明确发送失败后也需重新选择。选择不写入 chat_settings/localStorage。提交未开始、输入校验未通过时不消费选择。

成功显示“已启用 Skill · 名称 · vN”；失败显示“Skill 未启用 · 可理解的原因”。消息执行中和最终结果/刷新后复用同一个内容块。手动失败继续普通聊天，不伪装成功；需要模板参数的 Skill 本期提示暂不支持直接启用，不增加参数编辑表单。

## 请求与服务端边界

`GET /api/skills/available?conversation_id=...` 在现有摘要白名单追加 `skill_id=skill_key`，不暴露数据库 package/revision 主键、正文、路径或策略。原认证、会话归属、成员、业务权限和 Feature Flag 核验不变。

生成请求新增顶层字段：

```json
{"selected_skill":{"skill_id":"report","revision":"v2"}}
```

字段只允许标识和所见版本，不接受组织、权限、工具、正文或参数。HTTP 适配器覆盖客户端伪造的内部 `params._selected_skill`；只有通过类型校验的顶层意图进入当前任务参数。Actor executor 取出后构造 `ChatExecutionRequest.selected_skill`，不会透传为模型参数。幂等指纹区分不同选择；普通请求的既有指纹不变。

运行目录仍由 Actor 的服务端 ToolContext 建立。手动候选优先占用既有有界目录预算；`model_selectable=false` 的条目仅在本次明确手动选择时纳入，不向模型目录广告，模型调用也不能激活该条目。首轮模型前使用 `activate_manual → activate → ActorSkillSource.load → SkillResolver` 重新核验身份、范围、权限、开关和固定版本；所见版本已变化则失败，绝不悄悄改用新版本。正文加载与工具交集沿用 P1-3。

激活成功或失败生成一个安全白名单 `skill_step`，先在 `AFTER_SKILL_ACTIVATION` 保存 checkpoint，再对外投递。恢复时核验原版本和权限，从第 0 个模型回合继续，不重复激活；失去执行权时不发布激活结果。checkpoint 仅在手动选择时增加 `manual_skill_id`；无手动选择不写该字段。原有模型控制调用同样展示安全 `skill_step`，不再把控制调用参数、工具上限或原始内部结果当作普通 tool_step 展示。

公开块仅含 `type/step_id/status/name/revision/reason`；失败不回显未授权 Skill 名称或原始异常。前端协议额外字段会被剥除；WebSocket、持久化消息解析与流式 store 均接纳新类型，并按 step_id 去重。

## 三层 Feature Flag

| 层 | 开关 | 默认与作用 |
| --- | --- | --- |
| catalog | `SKILL_CATALOG_ENABLED` | false；关闭返回空目录，不读 Skill 表或 NAS |
| runtime | `SKILL_RUNTIME_ENABLED` | false；须同时开启 catalog 才能激活，关闭时手动意图产生“暂未开放”反馈 |
| UI | `VITE_SKILL_UI_ENABLED` | 正式构建默认 true、开发/测试默认 false；显式设置时仅 true 展示选择器与 skill_step，构建时注入 |

UI 关闭不修改任何后端开关、目录接口、请求契约、激活或工具权限行为。关闭 UI 后既有服务端结果仍保存，只是不展示该 UI 块。初次代码部署保持三层关闭；后续按用户指定的蓝创示例执行生产启用。

## 验证

输入区标签调整（2026-09-19）：`InputControlsSkill`、`SkillSelector`、`useInputSubmission`、`Popover` 共 39 项定向测试通过；TypeScript 与正式构建通过。真实组件本地浏览器检查覆盖 1280px 桌面、390px 窄屏，以及取消后多行文字保留和焦点恢复。窄屏无横向溢出；临时预览文件和服务已清理。本轮未运行生产业务请求。

本轮改动文件：`frontend/src/components/chat/input/InputArea.tsx`、`InputControls.tsx`、`InputControls.types.ts`、`SkillSelector.tsx`、`__tests__/InputControlsSkill.test.tsx`（后四项同属该输入目录），以及 `frontend/src/components/primitives/Popover.tsx` 和本文档。发布后验证：选择蓝创示例，检查标签位于文字前；输入文字后取消或更换，文字保持；发送后标签清空，下一条不选择时不携带 Skill。

后端定向组覆盖手动激活顺序、只允许手动选择的条目、权限撤销后的 Resolver 复核、错误脱敏、版本变化、开关组合、精确恢复与 fencing、HTTP 内部参数伪造、幂等、Actor 入队/投递及普通聊天。

前端定向组覆盖可见摘要、空目录、加载失败/重试、首条消息创建会话、跨会话迟到响应、一次性选择、独立 UI 开关、发送链路、协议白名单、流式投影与去重、最终结果渲染及普通聊天。TypeScript 和正式构建通过；构建保留现有大型依赖 chunk 警告。新增组件通过 ESLint。

本地浏览器使用实际组件和模拟摘要检查桌面与 390px 窄屏：选择弹层无溢出，发送后入口恢复未选择，成功/失败反馈正常。临时验证页和服务已移除/停止；此检查不是生产端到端验证。没有运行生产数据库、NAS 或真实模型调用。

测试结果：后端扩大组 195 项通过，checkpoint 最终调整后复跑相关 73 项全部通过；前端扩大组 275 项通过，后续追加协议/WS/创建去重 3 项，总计覆盖 278 项。新增与最终修改的子集已重新验证。UI 默认关闭和显式开启均完成正式构建；没有以重复运行次数累计测试数量。

在 backend 目录执行的扩大组：

```bash
DATABASE_URL=postgresql://unused JWT_SECRET_KEY=skill-tests-only python -m pytest \
  tests/test_skill_manual_selection.py tests/test_skill_runtime.py \
  tests/test_skill_runtime_actor.py tests/test_skill_runtime_source.py \
  tests/test_skill_resolver.py tests/test_skill_available_api.py \
  tests/test_chat_execution_engine.py tests/test_chat_generation_executor.py \
  tests/test_chat_actor_enqueue.py tests/test_chat_actor_sink.py \
  tests/test_chat_outcome_builder.py tests/test_message_idempotency_service.py \
  tests/test_message_routes.py tests/test_chat_stream_runner.py tests/test_chat_tool_loop.py -q
```

在 frontend 目录执行的扩大组与构建：

```bash
npm run test:run -- src/components/chat/input/__tests__ \
  src/components/chat/message/__tests__/MessageContentBlocks.test.tsx \
  src/hooks/handlers/__tests__/messageHandlers.test.tsx \
  src/services/__tests__/messageSender.test.ts src/services/__tests__/messageSenderRetry.test.ts \
  src/services/__tests__/messageSenderRollback.test.ts \
  src/stores/slices/__tests__/streamingThinking.test.ts \
  src/schemas/__tests__/messageProtocol.test.ts src/contexts/__tests__/wsMessageHandlers.test.ts \
  src/utils/__tests__/messageUtils.test.ts
npm run build
VITE_SKILL_UI_ENABLED=true npm run build
```

本机已有虚拟环境缺少 YAML 依赖，测试通过临时 PYTHONPATH 复用了与 requirements 一致的 PyYAML 6.0.2；未改动项目依赖。测试使用隔离的虚拟连接配置，无生产数据库访问。

## 生产验证步骤（待明确“提交部署”后执行）

1. 通过受控 `deploy/release.sh` 发布当前任务，保留工作树。确认后端 catalog/runtime 开关与前端构建开关独立生效。
2. 使用已有获授权测试账号及已分配、无需模板参数的测试 Skill，打开现有会话和首次新会话的选择器。核对仅返回当前会话允许的摘要；请求仅携带 conversation_id，不携带客户端组织/权限条件。
3. 手动选择版本并发送，确认生成请求有顶层 selected_skill；流式首轮输出前出现成功反馈，刷新后结果仍显示名称与实际版本。下一条不选择直接发送，不携带该意图。切换会话后不残留选择。
4. 先加载目录，再撤销测试账号对应 Skill 的使用权限或切换其 assignment 版本；发送旧选择时看到不可用/版本变化反馈，不激活旧选择。由已有授权管理流程操作测试配置，不通过新 UI 编辑 Skill。
5. 分别关闭 runtime、catalog，核对手动旧选择出现失败反馈或目录为空，普通聊天可用。仅关闭 UI 并重新构建时，入口和展示隐藏，认证 API 与已开启后端执行行为保持不变。
6. 核对流式及最终 content blocks 仅有安全字段，无正文、路径、工具上限或内部策略；手动激活后暂停/恢复无重复块，权限撤销后的恢复按 P1-3 安全停止。

## 回滚

优先关闭 `VITE_SKILL_UI_ENABLED` 并重新构建前端，仅撤回界面。需要停止新 Skill 激活时再关闭 `SKILL_RUNTIME_ENABLED`，目录层可独立保留。暂停中的已激活 Turn 在 runtime 关闭后会安全停止，不回退到未约束执行。

代码回滚点为任务基座 `b41ebd698b4af585bb93d8f794b86e0e50f1be29`。回退前停止/完成含手动选择的在途任务并保留其 checkpoint：旧 P1-3 不认识 manual_skill_id，不能直接恢复这些新 checkpoint；不得删除该字段或改用最新版本绕过恢复核验。回退后使用新 Turn 验证普通聊天。本期无数据库 schema 或 Skill 文件变更，不需要回滚数据库或 NAS。

## 改动文件清单

共 41 个文件，按实际任务 diff 记录：

- `backend/api/routes/message.py`
- `backend/api/routes/skills.py`
- `backend/schemas/message.py`
- `backend/services/handlers/chat/execution_engine.py`
- `backend/services/handlers/chat/executor.py`
- `backend/services/handlers/chat/outcome_builder.py`
- `backend/services/message_idempotency_service.py`
- `backend/services/skills/feedback.py`
- `backend/services/skills/resolver.py`
- `backend/services/skills/runtime.py`
- `backend/services/skills/selection.py`
- `backend/tests/test_skill_available_api.py`
- `backend/tests/test_skill_manual_selection.py`
- `backend/tests/test_skill_resolver.py`
- `backend/tests/test_skill_runtime_source.py`
- `docs/document/TECH_ActorSkillRuntime.md`
- `docs/document/TECH_SkillCatalog与可见目录.md`
- `docs/document/TECH_Skill聊天选择与反馈.md`
- `frontend/src/components/chat/input/InputArea.tsx`
- `frontend/src/components/chat/input/InputControls.tsx`
- `frontend/src/components/chat/input/InputControls.types.ts`
- `frontend/src/components/chat/input/SkillSelector.tsx`
- `frontend/src/components/chat/input/__tests__/SkillSelector.test.tsx`
- `frontend/src/components/chat/input/__tests__/useInputSubmission.test.tsx`
- `frontend/src/components/chat/input/useInputSubmission.ts`
- `frontend/src/components/chat/input/useTurnSkillSelection.ts`
- `frontend/src/components/chat/message/MessageContentBlocks.tsx`
- `frontend/src/components/chat/message/__tests__/MessageContentBlocks.test.tsx`
- `frontend/src/config/featureFlags.ts`
- `frontend/src/contexts/__tests__/wsMessageHandlers.test.ts`
- `frontend/src/hooks/handlers/__tests__/messageHandlers.test.tsx`
- `frontend/src/hooks/handlers/useTextMessageHandler.ts`
- `frontend/src/schemas/__tests__/messageProtocol.test.ts`
- `frontend/src/schemas/messageProtocol.ts`
- `frontend/src/services/__tests__/messageSenderRetry.test.ts`
- `frontend/src/services/messageSendLifecycle.ts`
- `frontend/src/services/messageSender.ts`
- `frontend/src/services/skills.ts`
- `frontend/src/stores/slices/__tests__/streamingThinking.test.ts`
- `frontend/src/stores/slices/streamingSlice.ts`
- `frontend/src/types/message.ts`
