# 技术设计：工作区分类筛选 + 图片/视频预览 + 批量下载 ZIP

> 版本：v1.0 · 创建：2026-06-22 · 任务等级：A 级

## 1. 需求摘要

在工作区面板加入分类筛选 Tab（全部 / 文档 / 图片与视频），支持图片双击放大预览、视频双击全屏预览、多选批量下载 ZIP、单文件夹打包下载。所有目录均显示 Tab；客户端筛选不递归子目录；图片/视频上下张仅在同类型内循环；全局默认按修改时间倒序。

## 2. 关键设计决策（已确认）

| 决策点 | 选择 | 行业依据 |
|--------|------|---------|
| Tab 容器位置 | 单独一行，紧贴 Header 下方 | 企微 / 飞书 / Google Drive / Dropbox |
| 分类判定逻辑 | 扩展名白名单为主，mime_type 兜底 | VSCode / macOS Finder / nginx |
| 上下张范围 | 基于当前筛选后列表 | iPhone 相册 / macOS Preview / Google Photos |
| 批量下载实现 | 后端 zipstream-ng 流式 ZIP | Dropbox / 飞书 / Google Drive |
| 批量下载入口 | 仅右键菜单（不加浮层） | 沿用项目「轻量文字提示」哲学 |
| Tab 视觉风格 | 蓝色下划线 | 仿企微截图，与现有 UI 权重协调 |
| 视图模式联动 | 切「图片与视频」自动 grid；切回恢复用户偏好 | 记住偏好 |
| 排序持久化 | 默认 `modified desc`，用户切换后 localStorage 保存 | — |

## 3. 项目上下文

### 架构现状
工作区采用「FastAPI 路由层 + FileExecutor 服务层 + NAS 本地存储 + OSS/CDN 镜像」分层架构。前端是 `WorkspaceView` + `useWorkspace` Hook + 平铺组件树，所有路径操作经 `_get_executor()` 工厂 + `resolve_safe_path()` 防越权。

### 可复用模块
- 前端：`useFileSelection`（多选状态）、`ImagePreviewModal`（已支持上下张/缩略图）、`FileContextMenu`（已支持批量模式 `isBatch = selectedCount > 1`）、`downloadFile`（fetch+blob 单文件下载）
- 后端：`FileExecutor.resolve_safe_path()` 路径安全校验、`asyncio.to_thread()` 同步 IO 模式、`StreamingResponse` 流式响应

### 设计约束
- 后端路由必须用 `_get_executor(ctx)` + Pydantic 请求体 + `ValidationError`/`AppException` 风格
- 前端组件复用 `var(--s-*)` 设计 token
- 同步阻塞 IO 必须放 `asyncio.to_thread`
- **`file.py` 当前 640 行，新增 ~80 行 ZIP 端点会推到 ~720 行**，触发拆分阈值（下一迭代处理）

### 潜在冲突
- 无相关已知 issue
- `WORKSPACE_ALLOWED_EXTENSIONS` 不含 mp4/mov 是**上传白名单**，不影响 AI 生成视频在 NAS 落地与展示

## 4. 边界场景

| 场景 | 处理策略 | 涉及模块 |
|------|---------|---------|
| 空目录切 Tab | 显示「该分类下暂无文件」+ 说明文案（非 `WorkspaceEmptyState`） | WorkspaceView |
| Tab 切换时正在重命名 | 取消重命名 + 清空选中 | WorkspaceView useEffect |
| 切目录时 Tab 状态 | 重置到「全部」(不持久化) | useWorkspace |
| 图片预览上下张到边界 | `hasPrev/hasNext` 控制箭头隐藏 | ImagePreviewModal |
| 视频预览无 mime / 损坏 | `<video onError>` 回调显示「视频加载失败，点击下载」 | VideoPreviewModal |
| 批量下载选中 0 项 | 菜单项 disabled，不发请求 | FileContextMenu |
| 批量下载选中 1 个文件 | 走原 `downloadFile`，不打 ZIP | WorkspaceView |
| 批量下载选中含文件夹 | 后端递归打包，ZIP 内保留目录结构 | 后端 ZIP endpoint |
| 选中含已删除/失效文件 | 后端跳过 + 写 `_errors.txt` 入 ZIP | 后端 ZIP endpoint |
| ZIP 中途网络中断 | 浏览器自动放弃 blob | 前端 fetch |
| 超过 500 文件 / 2GB | 后端预检 → 413 + 友好提示 | 后端 ZIP endpoint |
| ZIP 内中文文件名 | UTF-8 flag bit + RFC 5987 Content-Disposition | 后端 ZIP endpoint |
| 双击 PNG 之前走下载（bug） | `handleOpen` 增加图片分支 → `ImagePreviewModal` | WorkspaceView |
| 排序默认改 modified desc 但用户切换 | 切换后保持用户选择（localStorage 持久化） | useWorkspace |

## 5. 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| `useWorkspace` 加 `categoryFilter` + setter | `useWorkspace.ts` → `WorkspaceView.tsx` | 接 Tab 组件、过滤渲染列表 |
| `useWorkspace` 默认 sort 改 `modified desc` | `useWorkspace.ts:91-92` | 持久化 sortField/sortOrder 到 localStorage |
| Tab 切「图片与视频」自动切 grid | `useWorkspace.ts` | `setViewMode('grid')` 联动；切回恢复用户偏好 |
| `handleOpen` 增加图片/视频分支 | `WorkspaceView.tsx:66-81` | 引入 `canPreviewImage/canPreviewVideo` + 2 个 Modal state |
| `FileContextMenu` 加「下载选中」菜单项 | `FileContextMenu.tsx` | 新增 `onBatchDownload` prop + 批量模式分支 |
| `WorkspaceFileGrid/List` 传递新 props | 2 个文件 | 透传 `onBatchDownload` |
| 后端新 endpoint 注册 | `backend/api/routes/file.py` | router 已有，自动注册 |
| 新增前端 API 调用 | `frontend/src/services/workspace.ts` | 加 `downloadWorkspaceZip(paths)` |
| WorkspaceView 测试 | `__tests__/`（如有） | 补 Tab 切换、批量下载用例 |

## 6. 架构影响评估

| 维度 | 评估 | 风险等级 | 应对措施 |
|------|------|---------|---------|
| 模块边界 | 分类纯前端、下载新增 1 个后端 endpoint，边界清晰 | 低 | — |
| 数据流向 | client-side filter，无新依赖 | 低 | — |
| 扩展性 | 文件数 ≤2000 客户端过滤 <10ms；ZIP 流式无内存压力 | 中 | 2GB 上限保护内存 |
| 耦合度 | 仅与 workspace 模块耦合 | 低 | — |
| 一致性 | 复用现有 `_get_executor` / `resolve_safe_path` / `StreamingResponse` 模式 | 低 | — |
| 可观测性 | ZIP 端点 logger.info 记录 user/文件数/总大小/耗时 | 低 | — |
| 可回滚性 | 纯增量，无 DB 迁移 | 低 | — |
| 文件膨胀 | file.py 640 → 790 行 | ✅ **已拆分** | 本次实施时一并拆分为 file_common/upload/browse/manage/download 五个模块（file.py 改为 25 行聚合入口） |

✅ 无高风险项。

## 7. 方案选型（已确定）

**ZIP 打包方案**：**B — zipstream-ng 真流式**（已确认）

- 真流式（生成器 yield 字节），第一字节 < 100ms
- 不占磁盘，多用户并发安全
- 库成熟（90+ stars，纯 Python 实现）
- 唯一代价：新增依赖 `zipstream-ng==1.7.1`（轻量）

## 8. 技术栈

- 前端：React 19 + TypeScript + Zustand + TailwindCSS 4 + Radix UI
- 后端：Python 3.x + FastAPI + **zipstream-ng==1.7.1（新增）**
- 存储：NAS + Aliyun OSS/CDN（不变）
- 无 DB 改动

## 9. 目录结构

### 新增文件（4 个）

| 路径 | 职责 | 预估行数 |
|------|------|---------|
| `frontend/src/components/workspace/WorkspaceCategoryTabs.tsx` | Tab UI（全部/文档/图片与视频） | ~80 |
| `frontend/src/components/chat/media/VideoPreviewModal.tsx` | 视频全屏 Modal | ~120 |
| `frontend/src/utils/fileCategory.ts` | `categorize()` + `IMAGE_EXTS` / `VIDEO_EXTS` 白名单 | ~40 |
| `docs/document/TECH_工作区分类与批量下载.md` | 本文档 | — |

### 修改文件（6 个）+ 后端拆分

| 路径 | 修改内容 |
|------|---------|
| `frontend/src/hooks/useWorkspace.ts` | 加 `categoryFilter`；默认 sort 改 modified desc；持久化；切目录重置 Tab；Tab=images 联动 grid |
| `frontend/src/components/workspace/WorkspaceView.tsx` | 接 Tab、`handleOpen` 分发 3 个 Modal、`handleBatchDownload`、空分类提示 |
| `frontend/src/components/workspace/FileContextMenu.tsx` | 批量模式加「下载选中」+ 单文件夹「下载（ZIP）」 |
| `frontend/src/components/workspace/WorkspaceFileGrid.tsx` + `WorkspaceFileList.tsx` | 透传 `onBatchDownload` |
| `frontend/src/services/workspace.ts` | 加 `downloadWorkspaceZip(paths)` |
| `backend/api/routes/file.py` | **整体拆分为聚合入口（25 行）**，按职责落到 5 个新文件 |

### 后端 file.py 拆分（本次顺带完成）

| 路径 | 行数 | 职责 |
|------|------|------|
| `backend/api/routes/file.py` | 25 | 聚合入口（include 各 sub-router） |
| `backend/api/routes/file_common.py` | 80 | 共享 schema + `get_executor` + 常量 |
| `backend/api/routes/file_upload.py` | 251 | `/upload` + `/workspace/upload` |
| `backend/api/routes/file_browse.py` | 201 | `/workspace/list` + `/search` + `/preview` |
| `backend/api/routes/file_manage.py` | 150 | `/workspace/delete` + `/mkdir` + `/rename` + `/move` |
| `backend/api/routes/file_download.py` | 167 | `/workspace/download_zip` |

main.py 注册不变（`file.router` 聚合后路由总数仍为 10 条）。

## 10. API 设计

### POST /api/files/workspace/download_zip

**请求**：
```json
{ "paths": ["下载/image1.png", "下载/data/", "上传/2026-06/report.xlsx"] }
```

**Pydantic**：
```python
class WorkspaceDownloadZipRequest(BaseModel):
    paths: list[str] = Field(..., min_length=1, max_length=500)
```

**成功响应（200）**：
- `Content-Type: application/zip`
- `Content-Disposition: attachment; filename*=UTF-8''xxx.zip`
- Body：流式 ZIP 字节

**错误码**：

| HTTP | code | message |
|------|------|---------|
| 400 | `EMPTY_PATHS` | 未选择任何文件 |
| 403 | `FILE_WORKSPACE_DISABLED` | 文件功能未启用 |
| 404 | `FILE_NOT_FOUND` | 所有选中文件都不存在 |
| 413 | `TOO_MANY_FILES` | 文件数超过 500，请分批下载 |
| 413 | `TOO_LARGE` | 总大小超过 2GB，请分批下载 |
| 422 | `INVALID_PATH` | 路径越权或不合法 |

**文件名规则**：
- 单个文件夹 → `{folder_name}.zip`
- 多个文件混合 → `workspace-{YYYYMMDD-HHmmss}.zip`

## 11. 前端类型与工具

### 分类工具（utils/fileCategory.ts）

```ts
export const IMAGE_EXTS = new Set([
  'png','jpg','jpeg','gif','webp','svg','bmp','avif','heic'
]);
export const VIDEO_EXTS = new Set([
  'mp4','mov','webm','mkv','avi','m4v'
]);

export type FileCategory = 'image' | 'video' | 'document';
export type CategoryFilter = 'all' | 'images' | 'documents';

export function categorize(item: { name: string; mime_type: string | null }): FileCategory {
  const ext = item.name.split('.').pop()?.toLowerCase() ?? '';
  if (IMAGE_EXTS.has(ext) || item.mime_type?.startsWith('image/')) return 'image';
  if (VIDEO_EXTS.has(ext) || item.mime_type?.startsWith('video/')) return 'video';
  return 'document';
}

export function matchesFilter(item, filter: CategoryFilter): boolean {
  if (filter === 'all') return true;
  const cat = categorize(item);
  if (filter === 'images') return cat === 'image' || cat === 'video';
  if (filter === 'documents') return cat === 'document';
  return true;
}
```

### useWorkspace 扩展

```ts
interface UseWorkspaceReturn {
  // ... 现有字段
  categoryFilter: CategoryFilter;
  setCategoryFilter: (filter: CategoryFilter) => void;
}

// 默认 sort 变更
const [sortField, setSortField] = useState<SortField>(loadSortField() ?? 'modified');
const [sortOrder, setSortOrder] = useState<SortOrder>(loadSortOrder() ?? 'desc');
```

## 12. 开发任务拆分

### 阶段 1：基础设施
- [ ] 1.1 新建 `utils/fileCategory.ts` + 单测
- [ ] 1.2 `useWorkspace` 加 `categoryFilter` + 持久化 sort + Tab=images 联动 grid

### 阶段 2：分类 Tab UI
- [ ] 2.1 新建 `WorkspaceCategoryTabs.tsx`
- [ ] 2.2 `WorkspaceView` 接入 Tab + 客户端 filter + 空分类提示
- [ ] 2.3 切目录时重置 Tab 到「全部」

### 阶段 3：图片/视频预览
- [ ] 3.1 新建 `VideoPreviewModal.tsx`
- [ ] 3.2 `WorkspaceView.handleOpen` 分发 3 个 Modal（顺带修双击图片 bug）
- [ ] 3.3 上下张 `allImages` / `allVideos` 基于筛选后列表构建

### 阶段 4：后端 ZIP endpoint
- [ ] 4.1 `pip install zipstream-ng==1.7.1` + 写入 requirements.txt
- [ ] 4.2 新增 schema + `_collect_files` + `_resolve_archive_name`
- [ ] 4.3 新增 `POST /workspace/download_zip`
- [ ] 4.4 后端单测（单文件夹/多文件/混合/500 上限/2GB 上限/路径越权）
- [ ] 4.5 手测 500MB ZIP

### 阶段 5：前端批量下载入口
- [ ] 5.1 `services/workspace.ts` 加 `downloadWorkspaceZip(paths)`
- [ ] 5.2 `FileContextMenu` 批量模式加「下载选中」+ 单文件夹「下载（ZIP）」
- [ ] 5.3 `WorkspaceView.handleBatchDownload`（1 项走 downloadFile，≥2 项走 ZIP）

### 阶段 6：集成测试
- [ ] 6.1 端到端验证 13 项需求逐项过
- [ ] 6.2 跑 `/everydayai-test-coverage`

### 阶段 7：文档
- [ ] 7.1 更新 `FUNCTION_INDEX.md`
- [ ] 7.2 更新 `PROJECT_OVERVIEW.md`

## 13. 依赖变更

| 依赖 | 版本 | 用途 |
|------|------|------|
| `zipstream-ng` | `1.7.1` | 后端流式 ZIP 打包 |

## 14. 部署与回滚

- **数据库迁移**：无
- **API 兼容**：纯新增端点，对现有调用方零影响
- **前端兼容**：纯新增 UI + 修一个 bug（双击图片）
- **回滚步骤**：前后端可独立 revert，零耦合

## 15. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|-------|---------|
| 大 ZIP（≤2GB）前端 blob 占内存 | 中 | 限制 2GB；未来可升级 Service Worker 流式落盘 |
| zipstream-ng 库不稳定 | 低 | 成熟库 + 备选标准库 zipfile |
| ZIP 内中文文件名乱码 | 中 | UTF-8 flag bit + RFC 5987 |
| 视频无 mime / 浏览器不支持 | 中 | `<video onError>` 显示「加载失败，点击下载」 |
| file.py 突破 720 行 | 中 | 本次先加，下迭代拆分 |
| Tab 切换闪烁 | 低 | `useMemo` 缓存 filteredItems |
| 选中含已删除文件 | 低 | 后端 try-except 跳过 + `_errors.txt` |
| ZIP 慢导致前端超时 | 低 | StreamingResponse 持续吐字节保活 |

## 16. 文档更新清单

- [ ] `docs/FUNCTION_INDEX.md` — 新增 `categorize` / `matchesFilter` / `downloadWorkspaceZip` / 后端 `download_zip` / `VideoPreviewModal` / `WorkspaceCategoryTabs`
- [ ] `docs/PROJECT_OVERVIEW.md` — 新增 4 个文件路径
- [x] `docs/document/TECH_工作区分类与批量下载.md` — 本文档
