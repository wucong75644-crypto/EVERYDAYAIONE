# 企微附件资产与上下文资源治理 v2

> 状态：已确认
> 日期：2026-07-17

## 1. 目标

修复企业微信 FILE 回调缺少文件名时被错误保存为 `file.bin`，并补齐附件资产身份、
任务引用生命周期、当前资源上下文与生产部署生命周期。禁止以扩展名特判或 Prompt
补丁代替协议和状态机治理。

## 2. 现状与约束

- 企业微信 FILE 回调只保证 `url/aeskey`，文件名可缺失。
- PostgreSQL 是附件和任务事实源；Redis 只负责唤醒。
- Workspace 路径必须服从 user/channel ExecutionScope。
- 对话历史负责语义连续，当前资源必须使用独立不可变清单。
- 不新增第三方依赖；复用标准库、现有 OSS、FileExecutor 和 Actor 事务。

## 3. 规范资产身份

新增 `services/assets/file_identity.py`，以解密后的内容构建 `AssetIdentity`：

- `provider_name`：渠道提供的名字，可空。
- `canonical_name`：系统生成的稳定安全名称。
- `detected_mime_type`：内容检测结果。
- `detection_source`：provider/header/content/fallback。
- `content_sha256`：解密后内容摘要。
- `size`：解密后字节长度。

解析优先级：provider name → Content-Disposition → 内容结构检测 → 稳定兜底名。
内容检测覆盖 CSV/TSV、XLS/XLSX、PDF、DOCX、常见图片和视频。扩展名与内容冲突时
以内容为准并记录结构化日志。

`WecomMediaDownloader` 返回包含 bytes、HTTP 元数据的 `DownloadedMedia`，不再只返回
裸 bytes。所有 URL 和 aeskey 禁止进入日志。

## 4. 附件集合状态机

迁移 131/132 扩展 `conversation_attachment_refs` 并隔离渠道任务写入：

- `provider_name`
- `canonical_name`
- `detected_mime_type`
- `detection_source`
- `content_sha256`
- `attachment_set_id`
- `last_referenced_at`

新增 `task_attachment_refs`，以 task/turn/input_message/attachment 建立不可变绑定。

状态规则：

1. 连续上传且当前集合尚未绑定任务时，加入同一 collecting 集合。
2. 第一次文字请求绑定当前集合，但附件保持 active，可用于失败重试和继续追问。
3. 当前集合已被引用后再次上传文件，旧集合改为 replaced，新建 collecting 集合。
4. 同一 provider msgid + 相同哈希为幂等重放，可补全元数据。
5. 同一 provider msgid + 不同哈希为冲突，拒绝覆盖并记录告警。
6. expired 只由显式过期清理设置；任务失败不改变附件可用性。

迁移把每个会话最近的 referenced 集合恢复为 active，更早集合标记 replaced。

## 5. 资源上下文

新增 `services/handlers/resource_manifest.py`：

- `ResourceAsset`
- `ResourceManifest`
- `build_resource_manifest`

ContextSnapshot 固定 task 对应的 task_attachment_refs，生成当前资源清单。历史消息中的
文件块只保留叙事提示，不进入工具权限集合。`FilePart` 全链增加 `asset_id`。

ToolExecutor 接收 allowed asset IDs/paths：

- 默认 file_search/file_analyze 只操作当前 ResourceManifest。
- 无参数 file_search 只列当前资源。
- 用户明确要求搜索 Workspace 时才允许 workspace scope。
- 当前附件失败时返回结构化错误，不允许旧附件自动替代。

## 6. 历史修复

新增 `scripts/reconcile_wecom_attachments.py`：

- 默认 dry-run。
- 读取既有 Workspace 文件并重建 AssetIdentity。
- 事务更新附件 canonical metadata 和源消息 FilePart。
- 恢复每个会话最近有效集合。
- `--apply` 才执行写入；输出审计统计，不输出 URL 或密钥。

## 7. 部署治理

`deploy/deploy.sh` 使用固定服务清单：

- everydayai-backend
- everydayai-sync
- everydayai-wecom
- everydayai-conversation-actor

rsync 排除 `.env*`、SQLite、日志、缓存、运行时和外部目录。迁移后同步代码，依赖顺序
重启四项服务；等待 Uvicorn ready，校验公网/本机健康、Actor active、企微订阅和心跳。
任一步失败则部署失败。

## 8. 边界

- 空文件、超限、下载超时、AES 解密失败：不创建 ready 资产。
- Content-Length 和流式累计双重限额。
- 文件名清除路径、控制字符和双向文本控制符。
- CSV/TSV 至少两行、稳定分隔列数后才识别。
- Office 类型同时验证 magic 和容器结构。
- 多请求并发由 conversation 行锁串行冻结附件集合。
- 群附件只进入 channel Workspace 和 channel ResourceManifest。

## 9. 文件范围

新增：

- `backend/services/assets/__init__.py`
- `backend/services/assets/file_identity.py`
- `backend/services/handlers/resource_manifest.py`
- `backend/migrations/131_attachment_asset_lifecycle.sql`
- `backend/migrations/132_wecom_channel_task_enqueue.sql`
- `backend/migrations/rollback/131_attachment_asset_lifecycle_rollback.sql`
- `backend/migrations/rollback/132_wecom_channel_task_enqueue_rollback.sql`
- `backend/scripts/reconcile_wecom_attachments.py`

修改：

- `backend/services/wecom/media_downloader.py`
- `backend/services/wecom/message_normalizer.py`
- `backend/services/wecom/wecom_file_mixin.py`
- `backend/services/wecom/attachment_service.py`
- `backend/schemas/message.py`
- `backend/services/handlers/context_snapshot.py`
- `backend/services/handlers/chat_context_mixin.py`
- `backend/services/handlers/chat_context/content_extractors.py`
- `backend/services/agent/file_tool_mixin.py`
- `backend/services/agent/tool_executor.py`
- `deploy/deploy.sh`
- 对应测试与项目文档。

## 10. 实施顺序

1. 规范资产身份与真实 FILE 契约测试。
2. 迁移 131 与附件集合事务测试。
3. asset_id/ResourceManifest/工具资源权限。
4. reconciliation dry-run 与事务应用测试。
5. 部署服务清单和 readiness。
6. 全量回归、覆盖率、生产迁移与端到端验证。

## 11. 回滚

- 131 先扩展后切换；旧字段在迁移期仍保留有效语义。
- 代码回滚可继续读取 original_name/workspace_path。
- rollback 先删除 task_attachment_refs，再移除新增字段和函数。
- reconciliation 不删除文件，执行前输出 dry-run，更新在单事务内完成。

## 12. 企业微信图表交付

统一消息仍保存原始 `ChartPart`，Web 按 `spec_format` 渲染 ECharts、Plotly 或
Vega-Lite。企业微信不承担结构化图表展示职责，Outbox 在通道末端明确跳过所有
chart，不渲染 PNG、不上传图表素材，也不创建 chart 投递检查点。

文字、图片和视频继续沿用原始 content index 作为稳定检查点。文字与 chart 并存时
只投递文字；chart-only 消息不发送错误兜底，Outbox 直接完成。数据库消息事实不被
改写，因此 Web 刷新后仍能看到完整结构化图表。
