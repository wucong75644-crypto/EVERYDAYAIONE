# 当前问题 (CURRENT_ISSUES)

> 本文档记录项目中当前存在的已知问题、待修复的Bug、技术债务等。

## 问题状态

**🔴 严重** | **🟡 中等** | **🟢 轻微** | **技术债务**：均无

---

## 会话交接记录

---

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
- **2026-07-17**：完成缓存与工具上下文隔离：Redis 从可变 messages 数组切换为 v2 闭合历史信封；仅 `revision + through_message_id` 精确匹配可命中，旧数组/损坏值主动失效，Redis 故障回源 DB；删除工具循环、legacy loader 和无锚点 PromptBuilder 对共享上下文的整数组覆盖。
- **2026-07-17**：完成 ContextSnapshot：Web/企业微信正式 task 使用 `base_context_revision` 构造不可变闭合历史，严格校验 `input_message_id/turn_id`，不读取共享 Redis 决定历史边界；相同文本不再被猜测去重；任务私有副本独立压缩；企微首轮恢复小预算配置；旧任务保留受监控 legacy 路径。
- **2026-07-17**：完成 Web/企业微信 Turn 绑定：所有正式 AI 生成 task 绑定 `input_message_id/turn_id/base_context_revision`；assistant 写入 `reply_to_message_id`；企微同步生成纳入正式 task 生命周期；成功关闭 Turn，失败不推进 revision；旧 retry 消息保留受监控的输入锚点降级。
- **2026-07-17**：完成 Turn/revision 数据库基础：messages/tasks/conversations 增加兼容字段与索引，新增幂等 `bind_generation_turn`、`close_generation_turn` 事务 RPC 及 rollback；业务链路将在后续阶段接入。
- **2026-07-17**：完成显式媒体协议收口第一阶段：删除普通模型文本 URL 和 `[FILE]` marker 扫描；Web 流式与企微非流式统一消费 `emit_*` 结构化 payload；多图网格完成态只按实际 ImagePart 渲染。历史错误消息不回填。
- **2026-07-12**：主图详情页 `118` 迁移预执行发现误用 Supabase `auth.uid()`，真实 RPC 验证继续发现返回列与表字段同名；前者事务回滚，后者通过表别名修复，迁移统一采用阿里云自建 PostgreSQL 的 `OrgScopedDB + user_id + RPC 成员校验` 权限模型。
- **2026-07-11**：修复后端全量部署误删未纳入主仓库的 `backend/external/mediacrawler` 运行目录；已恢复生产文件，并在 rsync `--delete` 中永久排除该目录。
- **2026-07-01**：图片缩略图根治改造（后端生成 `workspace-thumbnails` 独立缩略图对象；实时消息保留 `original_url/thumbnail_url/preview_url/download_url`；NAS 工作区返回 `thumbnail_url`；前端停止生成 `x-oss-process` 缩略图 URL）
- **2026-07-01**：完成历史图片 URL 数据回填（messages/tasks JSON 图片 payload 补 `original_url` + `thumbnail_url`，生产复扫待回填数为 0；`url:null` 占位对象保持不回填）
- **2026-07-01**：前端图片资产协议改造（聊天/预览/右键引用/管理员资产页改用 `ImageAsset{originalUrl, thumbnailUrl}`，小图展示用缩略图，放大/下载/模型传输用原图）
- **2026-07-01**：聊天消息渲染组件拆分（`MessageItem`/`MessageMedia` 均低于 500 行；多内容块和图片块渲染拆入独立组件，行为保持回归测试覆盖）
- **2026-06-30**：管理员列表“上次活跃”口径升级（新增 `user_activity_events` + `users.last_active_at`，核心登录/消息/任务/企微/上传链路写活跃事件，列表改按 `last_active_at` 展示和排序）
- **2026-04-11**：快麦同步加固（4 Bug + 5 技术债，3478 测试全绿，新增 22 测试）
- **2026-03-01**：修复刷新恢复场景僵尸消息（generation_params 类型 + WS 订阅 ID 不匹配 + debug print 清理）
- **2026-02-02**：完成阶段5-7（状态管理重设计、占位符持久化、性能优化）
- **2026-02-01**：完成聊天系统综合重构阶段0-4（缓存统一、发送器合并、轮询管理）
- **2026-01-31**：完成登录/注册弹窗化重构、消息重复修复、图片上传优化
- 2026-07-11：后端测试基线已对齐当前 PromptBuilder、清洗层与 PermissionMode 协议；相关变更测试 518 passed、5 skipped、4 xfailed。真实 LLM 文件分析测试改为仅在 `RUN_LLM_INTEGRATION=1` 时执行，避免单元测试依赖外网。
