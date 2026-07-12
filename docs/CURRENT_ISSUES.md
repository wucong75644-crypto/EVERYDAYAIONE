# 当前问题 (CURRENT_ISSUES)

> 本文档记录项目中当前存在的已知问题、待修复的Bug、技术债务等。

## 问题状态

**🔴 严重** | **🟡 中等** | **🟢 轻微** | **技术债务**：均无

---

## 会话交接记录

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
