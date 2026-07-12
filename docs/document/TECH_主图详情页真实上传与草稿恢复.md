# 主图详情页真实上传与草稿恢复技术设计

> 版本：v1.0
> 日期：2026-07-12
> 状态：技术方案已确认
> 任务等级：A级
> 前置文档：`UI_主图详情制作页面.md`、`TECH_主图详情制作页面_UI第一阶段.md`

## 一、目标与边界

### 1.1 目标

将 `/detail-page` 当前本地 Mock 图片状态替换为真实上传、工作区图片引用和服务端草稿恢复。文件资产继续由现有 Workspace + OSS 双轨体系管理，详情项目只保存业务设置和 `workspace_path` 引用。

### 1.2 本阶段包含

- 第一次成功关联图片时创建草稿项目。
- 新图片复用现有 `/api/images/upload` 串行上传。
- 支持从工作区选择 JPG、PNG、WebP 图片，不复制文件。
- 产品图必填，参考图可选，合计最多 9 张。
- 单张最大 10MB，前后端双重校验。
- 保存图片分类、顺序和页面全部生成设置。
- 页面刷新、重新登录或换设备后恢复最近未完成草稿。
- 移除图片只解除项目引用，不删除工作区和 OSS 原图。
- 工作区原图删除后显示失效状态，继续复用既有恢复与 OSS 延迟清理。

### 1.3 本阶段不包含

- AI 产品分析、图片规划和图片生成。
- 积分锁定、结算和退款。
- WebSocket 和任务恢复。
- 历史项目管理页。
- 新上传服务、新 OSS 客户端或新目录规则。
- Chat 页面、Chat Hook 和消息附件结构改造。

## 二、项目上下文

### 2.1 架构现状

- 后端为 FastAPI + 阿里云自建 PostgreSQL，企业与个人请求统一经 `OrgCtx`、`OrgScopedDB` 隔离。
- 图片上传已由 `/api/images/upload` 完成工作区落盘、OSS 同步、缩略图和双轨元数据返回。
- Workspace 由 `FileExecutor.resolve_safe_path()` 控制用户目录边界，并提供列表、搜索、预览、下载、移动、删除和恢复能力。
- 前端使用 React、TypeScript、Zustand；详情页已有独立 Store 和组件契约，可替换 Action 而不改变 UI 层次。

### 2.2 直接复用模块

| 模块 | 复用方式 |
|---|---|
| `POST /api/images/upload` | 新图片上传，不修改接口 |
| `uploadImageFile()` | 前端上传调用 |
| `UploadImageResponse` | 获取 URL、路径和文件元数据 |
| `FileExecutor.resolve_safe_path()` | 验证工作区路径归属 |
| `resolve_upload_relpath()` | 保持 `上传/{YYYY-MM}` 目录规则 |
| `OSSService.sync_workspace_file()` | 现有上传接口内部复用，本模块不直接调用 |
| Workspace 列表/搜索/预览 | 工作区已有图片选择器 |
| 图片 URL 规则 | 原图、缩略图和下载语义 |
| 工作区删除/恢复 | 原文件生命周期管理 |
| `oss_purge_task` | 30 天 OSS 延迟清理 |
| `record_user_activity()` | 业务操作审计 |

### 2.3 设计约束

- 通用上传接口继续允许其既有格式和大小，详情页限制在建立项目引用时二次校验。
- 后端不能相信前端提交的 URL、MIME、文件大小或归属，只接受 `workspace_path` 作为待验证输入。
- 项目不创建独立工作区目录或 OSS 前缀。
- 草稿读取不能产生数据库写入。
- 项目引用失败不能自动删除已经上传成功的工作区文件。
- 所有新增租户表必须加入 `OrgScopedDB.TENANT_TABLES`。
- API 响应使用 `success/data/error/meta` 统一结构。
- 新文件不超过 500 行，函数不超过 120 行，TypeScript 禁止 `any`。

### 2.4 潜在冲突

- `/api/images/upload` 后端上限为 100MB且允许 GIF/BMP；详情项目关联接口必须收紧为 10MB、JPG/PNG/WebP。
- 现有详情页卸载会执行完整 `reset()`；接入草稿后必须调整为只释放 Blob URL，不清除服务端草稿。
- `OSSService` 已超过 500 行，但本阶段不修改该文件；这是既有结构问题，不在本任务范围内。
- 当前图片生成回调和失败状态仍待生产验证，本阶段不接触其任务与积分链路。

## 三、方案与数据流

### 3.1 已确认方案

采用两张规范化业务表：项目表保存页面状态，图片引用表保存 `workspace_path`、分类和顺序。文件实体仍只存在于 Workspace + OSS。

不采用项目 JSONB 图片数组，避免多标签页整段覆盖、重复引用难约束和后续规划阶段再次拆表。

### 3.2 上传新图片

```text
选择图片
  → 前端预检并创建 Blob 预览
  → 串行调用现有 /api/images/upload
  → 得到 workspace_path
  → 调用详情项目关联接口
  → 后端验证真实文件与归属
  → RPC 原子获取/创建草稿并插入引用
  → 返回最新完整草稿
```

### 3.3 选择工作区图片

```text
Workspace 列表/搜索
  → 用户选择 workspace_path
  → 不上传、不复制
  → 调用同一关联接口
  → 验证并建立项目引用
```

### 3.4 页面恢复

```text
进入 /detail-page
  → GET 当前草稿
  → 无草稿返回 project=null
  → 有草稿读取设置和引用
  → 逐条验证工作区文件状态并补齐 URL/元数据
  → 前端以服务端快照恢复 Store
```

## 四、数据库设计

### 4.1 `detail_projects`

| 字段 | 类型 | 约束/默认值 | 说明 |
|---|---|---|---|
| `id` | UUID | PK, `uuid_generate_v4()` | 项目 ID |
| `user_id` | UUID | NOT NULL, FK users | 所属用户 |
| `org_id` | UUID | NULL, FK organizations | 企业或个人空间 |
| `status` | TEXT | NOT NULL, `draft` | 项目状态 |
| `content_type` | TEXT | NOT NULL, `main_image` | 主图/详情图 |
| `platform` | TEXT | NOT NULL, `auto` | 目标平台 |
| `requirement` | TEXT | NOT NULL, `''` | 用户要求 |
| `language` | TEXT | NOT NULL, `zh-CN` | 默认中文 |
| `aspect_ratio` | TEXT | NOT NULL, `1:1` | 输出比例 |
| `quality` | TEXT | NOT NULL, `1k` | 清晰度 |
| `image_count` | SMALLINT | NOT NULL, 1, CHECK 1–9 | 生成数量 |
| `version` | INTEGER | NOT NULL, 1 | 乐观锁版本 |
| `created_at` | TIMESTAMPTZ | NOT NULL, NOW() | 创建时间 |
| `updated_at` | TIMESTAMPTZ | NOT NULL, NOW() | 修改时间 |

状态约束预留 UI 已确认状态：`draft/analyzing/plan_ready/generating/completed/failed/archived`。本阶段只写入 `draft`。

索引与约束：

- `(user_id, org_id, updated_at DESC)`：企业草稿恢复。
- `(user_id, updated_at DESC) WHERE org_id IS NULL`：个人草稿恢复。
- 企业空间 `(user_id, org_id) WHERE status='draft' AND org_id IS NOT NULL` 唯一。
- 个人空间 `(user_id) WHERE status='draft' AND org_id IS NULL` 唯一。

### 4.2 `detail_project_images`

| 字段 | 类型 | 约束/默认值 | 说明 |
|---|---|---|---|
| `id` | UUID | PK, `uuid_generate_v4()` | 引用 ID |
| `project_id` | UUID | NOT NULL, FK CASCADE | 所属项目 |
| `user_id` | UUID | NOT NULL, FK users | 二次归属保护 |
| `org_id` | UUID | NULL, FK organizations | 租户隔离 |
| `workspace_path` | TEXT | NOT NULL | 用户工作区相对路径 |
| `category` | TEXT | CHECK product/reference | 图片分类 |
| `sort_order` | SMALLINT | NOT NULL, CHECK 0–8 | 项目顺序 |
| `created_at` | TIMESTAMPTZ | NOT NULL, NOW() | 创建时间 |
| `updated_at` | TIMESTAMPTZ | NOT NULL, NOW() | 修改时间 |

约束与索引：

- `(project_id, workspace_path)` 唯一，防止重复引用。
- `(project_id, sort_order)` 唯一，保证稳定顺序。
- `(project_id, sort_order)` 查询索引。
- 不保存 OSS URL、缩略图、MIME 和大小，避免与工作区真实状态分叉。

### 4.3 原子关联 RPC

新增 `attach_detail_project_image`：

1. 按 `user_id + org_id` 获取或创建唯一草稿。
2. 对项目行加锁。
3. 校验项目为 `draft`。
4. 校验路径未重复。
5. 事务内统计引用数并拒绝第十张。
6. 使用下一个 `sort_order` 插入引用。
7. 项目 `version + 1` 并更新 `updated_at`。
8. 返回 `project_id/version/image_id`。

文件系统归属、格式、大小和真实内容验证在 FastAPI Service 中完成后才调用 RPC。

权限模型与现有自建 PostgreSQL 保持一致：不使用 Supabase `auth.uid()` 或 RLS。API 通过
`OrgScopedDB` 限制 `org_id`并通过登录上下文限制 `user_id`；RPC 额外校验用户存在以及企业
成员为 `active`，保持 `SECURITY INVOKER`，并撤销 `PUBLIC` 执行权限。

## 五、API 设计

统一成功响应：

```json
{"success": true, "data": {}, "error": null, "meta": {}}
```

错误继续由全局 `AppException` 处理，并保持可识别业务错误码。

### 5.1 获取当前草稿

`GET /api/detail-projects/current`

- 无草稿：`data.project = null`，不创建数据。
- 有草稿：返回设置、版本和按顺序补齐的图片状态。
- 文件不存在时图片 `status=missing`，不删除引用。

### 5.2 关联图片

`POST /api/detail-projects/current/images`

请求：

```json
{"workspace_path":"上传/2026-07/a.png","category":"product"}
```

Service 校验：安全路径、普通文件、非符号链接、真实图片、JPG/PNG/WebP、≤10MB、可读取尺寸、当前用户/组织归属。成功后调用原子 RPC，并返回最新草稿。

### 5.3 保存设置

`PATCH /api/detail-projects/{project_id}`

请求携带 `version` 和部分 `settings`。后端只允许白名单字段，使用 `id + user_id + version + status=draft` 条件更新；无匹配时返回 409 并要求前端刷新。

### 5.4 移除引用

`DELETE /api/detail-projects/{project_id}/images/{image_id}`

- 携带 `version`。
- 只删除引用并重新压紧排序。
- 不调用 Workspace 删除或 OSS 删除。

### 5.5 切换分类

`PATCH /api/detail-projects/{project_id}/images/{image_id}`

请求包含 `version/category`，成功返回最新草稿。

### 5.6 保存排序

`PUT /api/detail-projects/{project_id}/images/order`

请求包含 `version/image_ids`。后端校验 ID 集合与当前项目引用完整一致，在事务中更新排序并增加版本。

### 5.7 错误码

| 错误码 | HTTP | 场景 |
|---|---:|---|
| `DETAIL_IMAGE_NOT_FOUND` | 404 | 工作区文件不存在 |
| `DETAIL_IMAGE_FORBIDDEN` | 403 | 路径越权 |
| `DETAIL_IMAGE_INVALID_TYPE` | 400 | 格式不支持 |
| `DETAIL_IMAGE_TOO_LARGE` | 413 | 超过 10MB |
| `DETAIL_IMAGE_INVALID_CONTENT` | 400 | 文件内容不是合法图片 |
| `DETAIL_IMAGE_DUPLICATE` | 409 | 重复引用 |
| `DETAIL_IMAGE_LIMIT_EXCEEDED` | 409 | 超过 9 张 |
| `DETAIL_PROJECT_NOT_FOUND` | 404 | 项目不存在 |
| `DETAIL_PROJECT_NOT_EDITABLE` | 409 | 项目不再是草稿 |
| `DETAIL_PROJECT_VERSION_CONFLICT` | 409 | 多标签页版本冲突 |

## 六、前端状态设计

图片状态：`local/uploading/attaching/ready/failed/missing`。

`DetailLocalImage` 保留 `id/category/previewUrl/error`，增加 `workspacePath/originalUrl/thumbnailUrl/status/sortOrder`，并将 `file` 调整为本地上传态可选字段。

Store 新增：

- `projectId/projectVersion/isHydrating`
- `hydrateDraft()`
- `uploadAndAttachImages()`
- `attachWorkspaceImage()`
- `retryImage()`
- `updateImageCategory()`
- `reorderImages()`
- `saveSettings()`
- `removeProjectImage()`
- `releaseLocalPreviews()`

设置保存使用 500ms 防抖和 AbortController 清理；关键分类、移除和排序操作立即提交。收到 409 时重新拉取服务端最新草稿并持续显示冲突提示，不静默覆盖。

页面卸载只释放 Blob URL和取消前端监听，不删除服务端草稿。

## 七、工作区选择器

新增轻量 `WorkspaceImagePicker`，复用：

- `listWorkspace()`
- `searchWorkspace()`
- `getWorkspacePreviewUrl()`
- `WorkspaceFileItem`
- 现有 Modal、图片 URL 规则和工作区预览语义

选择器只过滤图片、控制剩余可选数量并返回 `workspace_path`，不实现上传、复制、移动、删除或新文件管理逻辑。

## 八、边界与极限场景

| 场景 | 处理策略 | 模块 |
|---|---|---|
| 无草稿 | 返回空状态，不写库 | API |
| 第一张上传成功 | 关联 RPC 创建草稿 | Service/RPC |
| 第一批全部失败 | 不产生项目 | Store |
| 上传成功、关联失败 | 文件保留，允许重试关联 | Store |
| 第十张 | 前端预检，RPC 最终拒绝 | Store/RPC |
| 多标签页同时添加 | 项目行锁后计数 | RPC |
| 多标签页改设置 | version 冲突后刷新 | API/Store |
| 只剩参考图 | 草稿保留，禁止分析 | UI |
| 工作区原图删除 | 返回 missing | Service |
| 原路径恢复 | 下次加载恢复 ready | Service |
| 文件移动/重命名 | 标记 missing，不猜测路径 | Service |
| Token 过期 | 复用 API 拦截器 | Client |
| OSS 暂不可用 | 工作区文件保留，关联不可用时允许重试 | Upload/Store |
| 页面卸载 | 释放 Blob，禁止卸载后 setState | Store/Page |
| 重复选择 | 返回重复错误并聚焦已有卡片 | API/UI |
| 最多 9 条数据 | 无需分页和虚拟滚动 | API/UI |

## 九、连锁修改清单

| 改动点 | 影响文件 | 同步内容 |
|---|---|---|
| 新租户表 | migration、`org_scoped_db.py` | 表、索引、租户集合 |
| 新 API | main、route、schema、service | 注册、契约、错误码 |
| 图片类型扩展 | types、store、组件、测试 | 上传和恢复状态 |
| Store Action 替换 | DetailPage、上传区、设置区 | Hydrate、真实调用、清理 |
| 工作区选择 | service、picker、上传区 | 现有文件直接关联 |
| 新公共函数/文件 | 文档索引 | Overview、Function Index、API Reference |

## 十、文件范围

### 10.1 新增

- `backend/migrations/118_detail_projects.sql`
- `backend/schemas/detail_project.py`
- `backend/services/detail_project_service.py`
- `backend/api/routes/detail_project.py`
- `backend/tests/test_detail_project_service.py`
- `backend/tests/test_detail_project_api.py`
- `frontend/src/services/detailProject.ts`
- `frontend/src/services/__tests__/detailProject.test.ts`
- `frontend/src/components/detail-page/WorkspaceImagePicker.tsx`
- `frontend/src/components/detail-page/__tests__/WorkspaceImagePicker.test.tsx`

### 10.2 修改

- `backend/main.py`
- `backend/core/org_scoped_db.py`
- `frontend/src/types/detailPage.ts`
- `frontend/src/stores/useDetailPageStore.ts`
- `frontend/src/stores/__tests__/useDetailPageStore.test.ts`
- `frontend/src/pages/DetailPage.tsx`
- `frontend/src/pages/__tests__/DetailPage.test.tsx`
- `frontend/src/components/detail-page/ProductImageSection.tsx`
- `frontend/src/components/detail-page/GenerationSettings.tsx`
- 对应组件测试
- `docs/PROJECT_OVERVIEW.md`
- `docs/FUNCTION_INDEX.md`
- `docs/API_REFERENCE.md`
- `docs/CURRENT_ISSUES.md`

### 10.3 明确不修改

- `backend/api/routes/image.py`
- `backend/services/oss_service.py`
- `backend/services/file_executor.py`
- Chat 页面、Chat Hook 和消息 Store
- tasks、积分、WebSocket、Webhook 和生成链路

## 十一、架构影响评估

| 维度 | 评估 | 风险 | 应对 |
|---|---|---|---|
| 模块边界 | 新增详情项目业务模块 | 低 | 只引用 workspace_path |
| 数据流 | 上传后增加关联步骤 | 中 | 独立状态与重试 |
| 扩展性 | 每项目最多 9 张 | 低 | 无需分页 |
| 耦合度 | 依赖稳定 Workspace 契约 | 低 | 不依赖 Chat |
| 一致性 | 沿用 OrgCtx/ScopedDB/Pydantic | 低 | 新表注册租户隔离 |
| 并发 | 多标签页冲突 | 中 | RPC 行锁 + version |
| 可观测性 | 新增草稿写操作 | 低 | 结构化业务上下文日志 |
| 可回滚性 | 新增数据库表/RPC | 中 | 前端先回滚，表最后删除 |

无高风险架构问题。

## 十二、测试与验收

后端覆盖：空读取不建草稿、首次关联、企业/个人隔离、路径越权、伪造图片、大文件、9 张边界、并发第十张、重复引用、分类、排序、移除不删源文件、文件 missing、version 冲突和 RPC 回滚。

前端覆盖：空状态、草稿恢复、串行上传、单张失败后继续、关联失败重试、工作区直接关联、共享 9 张上限、卸载只释放 Blob、设置防抖、冲突刷新和 missing 状态。

核心新增逻辑覆盖率不低于 80%；必须运行后端相关测试、前端全量测试、TypeScript 和生产构建。

## 十三、开发拆分

1. Migration、RPC 与租户隔离登记。
2. 后端 Schema、Service、API 与测试。
3. 前端类型和 API Client。
4. Store 真实上传、关联、恢复和测试。
5. 工作区图片选择器。
6. 页面组件接入和边界测试。
7. 覆盖率、全量回归、构建和浏览器验收。
8. 文档、审查、提交和部署。

每个阶段完成后必须暂停汇报，等待用户确认后再继续。

## 十四、部署与回滚

部署顺序：迁移 → 后端 → API 冒烟 → 前端 → 内部账号验证。

回滚顺序：前端回到 Mock → 移除后端路由 → 保留表观察 → 确认无引用后删除 RPC、索引和两张表。Workspace 与 OSS 文件不受影响，无需数据修复。

无需新增依赖。

## 十五、文档更新

- `PROJECT_OVERVIEW.md`：新模块和设计文档。
- `FUNCTION_INDEX.md`：新 API、Service、RPC 与 Store Action。
- `API_REFERENCE.md`：详情项目接口。
- `CURRENT_ISSUES.md`：迁移与生产验证记录。

## 十六、设计自检

- [x] 需求、UI 和方案评审已确认。
- [x] 架构现状、复用模块、设计约束和潜在冲突完整。
- [x] 不修改现有上传、Workspace、OSS 和 Chat 链路。
- [x] API 使用统一响应信封。
- [x] 新表纳入 OrgScopedDB，并完成自建 PostgreSQL 索引与 RPC 权限设计。
- [x] 并发、失败、空值、权限、恢复和回滚均有策略。
- [x] 新文件预估均不超过 500 行。
- [x] 无新增依赖和模糊版本。

---

**本技术设计已经用户确认；开发必须严格按阶段执行，不得扩大范围。**
