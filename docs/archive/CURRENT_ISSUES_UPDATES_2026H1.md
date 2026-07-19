# CURRENT_ISSUES 较早更新记录（2026 H1）

> 从 `docs/CURRENT_ISSUES.md` 归档；仅为控制主文档长度，不改变历史结论。

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
- **2026-07-11**：后端测试基线已对齐当前 PromptBuilder、清洗层与 PermissionMode 协议；相关变更测试 518 passed、5 skipped、4 xfailed。真实 LLM 文件分析测试改为仅在 `RUN_LLM_INTEGRATION=1` 时执行，避免单元测试依赖外网。
