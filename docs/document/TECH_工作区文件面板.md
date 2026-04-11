# 技术方案：工作区文件面板

> 版本：V1.8（终版） | 日期：2026-04-09

## 一、产品定位

每个用户拥有独立的云端文件空间（类似电脑里的"我的文件"），AI 可以像在本地电脑一样读写、整理这个空间。前端提供可视化的文件浏览器面板，支持列表/图标两种视图。

## 二、整体布局

```
┌──────────┬───────────────────────┬──────────────┐
│ 会话列表  │      聊天区域          │  📁 工作区    │
│ Sidebar  │  ChatHeader           │  工具栏+视图  │
│ w-64     │  MessageArea          │  文件浏览器   │
│          │  InputArea            │  w-80        │
└──────────┴───────────────────────┴──────────────┘
```

- **位置**：主内容区右侧，可收起的侧边面板
- **触发**：ChatHeader 右侧新增"文件夹"图标按钮，点击展开/收起
- **宽度**：`w-80`（320px），收起时完全隐藏，聊天区自动填满
- **动画**：`transition-all duration-300` 滑入滑出
- **响应式**：移动端暂不做特殊适配（Phase 4）

## 三、前端设计

### 3.1 面板结构

```
WorkspacePanel
├── 顶部栏
│   ├── "工作区" 标题
│   ├── 视图切换（列表 ☰ / 图标 ⊞）
│   └── 关闭按钮 ✕
│
├── 工具栏
│   ├── 面包屑路径导航（. > uploads > reports）
│   ├── 新建文件夹按钮
│   └── 上传按钮（支持多文件）
│
├── 文件区域（可滚动）
│   │
│   ├── 【列表模式】
│   │   ├── 📁 文件夹行（图标 + 名称 + 时间 + 操作菜单）
│   │   └── 📄 文件行（图标 + 名称 + 大小 + 时间 + 操作菜单）
│   │
│   └── 【图标模式】
│       ├── 📁 文件夹格子（大图标 + 名称）
│       └── 📄 文件格子（类型图标 + 名称 + 大小）
│       └── 网格排列：grid grid-cols-3 gap-2
│
├── 加载状态
│   └── 骨架屏 / spinner（首次加载或切换目录时）
│
├── 空状态
│   └── "还没有文件，上传或让 AI 帮你创建"
│
└── 拖拽上传遮罩
    └── "拖放文件到这里上传"
```

### 3.2 两种视图模式

**列表模式**（默认，信息密度高）：
```
📁 reports/                    04-05
📊 sales_data.csv    128KB     04-03
📄 README.md         3.2KB    04-01
```

**图标模式**（直观，类似桌面）：
```
┌────────┐  ┌────────┐  ┌────────┐
│   📁   │  │   📊   │  │   📄   │
│reports │  │ sales  │  │README │
│        │  │_data   │  │ .md   │
└────────┘  └────────┘  └────────┘
```

- 视图偏好存 localStorage，用户切换后记住
- 切换按钮用 `LayoutList` / `LayoutGrid` 图标（lucide-react）
- 两种视图共享同一份数据，纯 CSS 切换

### 3.3 交互细节

| 操作 | 行为 |
|------|------|
| 点击文件夹 | 进入子目录，面包屑更新 |
| 点击面包屑 | 跳转到对应层级 |
| 点击文件 | 可预览的文件（CSV/Excel/文本/PDF）弹出预览弹窗，其他触发下载 |
| "发送到对话" | 将文件加入待发送队列，下次发消息时自动携带（详见 5.1） |
| 拖拽文件到面板 | 上传到当前目录（从电脑拖入） |
| 拖拽文件到文件夹 | 移动文件到该文件夹（面板内拖拽，Phase 4，先用右键菜单"移动到"替代） |
| 上传按钮 | 选择文件上传到当前目录（支持多选） |
| 新建文件夹 | 弹出输入框，输入名称后创建 |
| 删除 | 二次确认弹框 |
| 重命名 | 行内编辑（双击文件名触发） |

### 3.4 文件类型图标映射

已有实现：`frontend/src/utils/fileUtils.ts` 的 `getFileIcon()` + `formatFileSize()`

需扩展颜色映射（图标模式下更醒目）：

| 类型 | 扩展名 | 图标 | 背景色 |
|------|--------|------|--------|
| PDF | .pdf | 📄 | red-50 |
| Excel/CSV | .xls, .xlsx, .csv, .tsv | 📊 | green-50 |
| Word | .doc, .docx | 📃 | blue-50 |
| PPT | .ppt, .pptx | 📃 | orange-50 |
| 代码 | .py, .js, .ts, .html, .css, .sql | 📃 | purple-50 |
| 文本 | .txt, .md, .log, .json, .yaml, .xml | 📃 | gray-50 |
| 压缩包 | .zip | 📦 | yellow-50 |
| 文件夹 | — | 📁 | blue-50 |

### 3.5 已有可复用组件

调研发现以下组件已存在，直接复用：

| 已有组件 | 功能 | 复用方式 |
|---------|------|---------|
| `FileCard.tsx` + `FileCardList` | 消息中的文件卡片（下载+预览） | 消息附件展示直接复用 |
| `FilePreviewModal.tsx` | 文件在线预览（Excel/CSV/文本/PDF） | 面板中点击文件复用此弹窗 |
| `fileUtils.ts` | getFileIcon() + formatFileSize() | 面板图标和大小格式化 |
| `downloadFile.ts` | 文件下载工具函数 | 面板下载功能复用 |
| `FilePart` 类型 | types/message.ts 已定义 | 消息中文件附件类型 |
| `workspaceUpload.ts` | 工作区上传 + 列表 API | 合并到新的 workspace.ts（仅 InputControls 动态 import 引用，无其他依赖） |
| `MessageMedia.tsx` | 已引入 FileCardList | **已具备消息文件渲染能力** |

**重要发现**：`MessageMedia.tsx` 已导入 `FileCardList` 并在底部渲染文件卡片（L492），但 `MessageItem.tsx` 没有提取 `FilePart` 传给 `MessageMedia`，所以消息中文件不显示。修复只需在 MessageItem 中提取 files 并传递。

### 3.6 新增前端文件

| 文件 | 职责 | 行数估计 |
|------|------|---------|
| `components/chat/WorkspacePanel.tsx` | 面板主组件（顶部栏 + 工具栏 + 文件区域 + 空状态 + 拖拽上传） | ~280 |
| `components/chat/WorkspaceFileItem.tsx` | 单个文件/文件夹条目（列表模式 + 图标模式 + 操作菜单） | ~150 |
| `components/chat/WorkspaceBreadcrumb.tsx` | 面包屑路径导航 | ~50 |
| `hooks/useWorkspace.ts` | 状态管理（路径、文件列表、视图模式、loading 状态、CRUD 操作、面板复用缓存） | ~180 |
| `services/workspace.ts` | 后端 API 调用（整合 workspaceUpload.ts，新增 delete/mkdir/rename/move） | ~100 |

### 3.7 修改现有文件

详见第六节完整文件清单。前端共修改 11 个文件。

## 四、后端补全

### 4.1 新增 API

在 `backend/api/routes/file.py` 新增四个端点：

```python
# 1. 删除文件/空目录（用 POST 而非 DELETE，避免部分代理/客户端丢弃请求体）
POST /files/workspace/delete
Body: { "path": "uploads/report.csv" }
Response: { "success": true }

# 2. 新建文件夹
POST /files/workspace/mkdir
Body: { "path": "reports/2026-04" }
Response: { "success": true, "path": "reports/2026-04" }

# 3. 重命名
POST /files/workspace/rename
Body: { "old_path": "uploads/old.csv", "new_path": "uploads/new.csv" }
Response: { "success": true }

# 4. 移动文件（拖拽到文件夹）
POST /files/workspace/move
Body: { "src_path": "uploads/data.csv", "dest_dir": "reports/" }
Response: { "success": true, "new_path": "reports/data.csv" }
```

### 4.2 改造现有上传接口

当前 `POST /files/workspace/upload` 固定上传到 `uploads/` 子目录。改造为支持指定目标目录：

```python
# 新增可选参数 target_dir（通过 Form field 传递，非 query param）
POST /files/workspace/upload
FormData: file + target_dir（默认 "."，即当前面板目录）

# 后端签名示例
async def upload_to_workspace(
    ctx: OrgCtx,
    db: ScopedDB,
    file: UploadFile = File(...),
    target_dir: str = Form(default="."),  # 新增
):
```

前端 workspace.ts 调用：
```typescript
const formData = new FormData();
formData.append('file', file);
formData.append('target_dir', currentPath);  // 当前面板路径
```

### 4.3 改造现有列表接口

当前 `GET /files/workspace/list` 返回的 `WorkspaceFileItem` 不含 CDN URL。
需新增 `cdn_url` 字段，面板中文件预览/下载依赖此 URL。

```python
class WorkspaceFileItem(BaseModel):
    name: str
    is_dir: bool
    size: int = 0
    modified: str = ""
    cdn_url: Optional[str] = None   # 新增：文件的 CDN 下载地址
    mime_type: Optional[str] = None  # 新增：MIME 类型（前端预览弹窗需要）
```

**实现要点**：遍历目录时拼接 `relative_path = f"{path}/{item.name}"`（path 是查询参数），调用 `executor.get_cdn_url(relative_path)` 生成 CDN URL。MIME 类型用 Python `mimetypes.guess_type()` 获取。

### 4.4 认证方式

所有 workspace 端点统一使用 `OrgCtx`（非 `CurrentUser`），提供 `user_id` + `org_id`：
- `OrgCtx` 已含 `user_id` 和 `org_id`，直接构建 `FileExecutor`
- 与现有 upload/list 端点保持一致

### 4.5 CDN 未配置降级

`get_cdn_url()` 在 `oss_cdn_domain` 未配置时返回 None。需要处理：

- **list 接口**：`cdn_url` 为 None 时，前端面板中该文件不显示"预览"和"下载"按钮，只显示文件名和大小
- **"发送到对话"**：无 CDN URL 时，前端只发 workspace_path，后端仍能注入 AI 提示（AI 用 file_read 读取不依赖 CDN）
- **生产环境必配 CDN**：这是降级保底，正常部署 CDN 一定有

### 4.6 安全约束

- 所有路径操作复用 `FileExecutor.resolve_safe_path()` 防穿越
- 删除：文件直接删除，目录仅允许空目录（非空返回错误提示）
- 重命名：不允许跨目录（跨目录用 move）
- 移动：目标必须是已存在的目录，且在 workspace 内
- **文件名冲突**：rename/move 时目标路径已存在 → 返回 409 错误，前端提示"文件已存在"
- 文件大小限制：单文件 50MB

### 4.7 修改后端文件

| 文件 | 改动 |
|------|------|
| `api/routes/file.py` | 新增 4 个端点（delete/mkdir/rename/move），改造 upload 支持 target_dir，list 返回 cdn_url |
| `services/file_executor.py` | 新增 `file_delete()` / `file_mkdir()` / `file_rename()` / `file_move()` 方法 |

## 五、AI 感知文件

### 5.1 "发送到对话"完整数据流

#### 组件通信链路

WorkspacePanel 和 InputArea 是 Chat.tsx 的兄弟组件，需要通过父组件中转：

```
WorkspacePanel —— 用户点击"发送到对话"
    ↓ onSendToChat(file) 回调
Chat.tsx —— pendingWorkspaceFiles 状态
    ↓ props
InputArea —— 合并 workspace 文件到发送流程
    ↓ handleSubmit()
useTextMessageHandler.handleChatMessage(text, convId, images, files)
    ↓
messageSender.createTextWithFiles(text, images, files)
    ↓ 构建 ContentPart[] 含 FilePart(workspace_path=...)
sendMessage() —— POST /conversations/{id}/messages/generate
```

#### Chat.tsx 状态管理

```typescript
// Chat.tsx 新增
const [pendingWorkspaceFiles, setPendingWorkspaceFiles] = useState<WorkspaceFile[]>([]);

// 传给 WorkspacePanel（按 workspace_path 去重，防止同一文件重复添加）
<WorkspacePanel onSendToChat={(file) => setPendingWorkspaceFiles(prev =>
  prev.some(f => f.workspace_path === file.workspace_path) ? prev : [...prev, file]
)} />

// 传给 InputArea
<InputArea workspaceFiles={pendingWorkspaceFiles} onWorkspaceFilesConsumed={() => setPendingWorkspaceFiles([])} />
```

#### InputArea 合并逻辑

```typescript
// InputArea.handleSubmit() 中（L280 附近）
const fileData = [
  ...(uploadedFileUrls || []),       // PDF 上传的文件（已有，来自 useFileUpload）
  ...(workspaceFiles || []).map(f => ({
    url: f.cdn_url || '',
    name: f.name,
    mime_type: f.mime_type || 'application/octet-stream',
    size: f.size,
    workspace_path: f.workspace_path,
  })),
];
// 发送后清空
onWorkspaceFilesConsumed();
```

#### InputControls 集成要点

现有判断逻辑 `hasContent`（L247）：
```typescript
const hasContent = prompt.trim().length > 0 || images.length > 0 || files.length > 0;
```

需扩展为：
```typescript
const hasContent = prompt.trim().length > 0 || images.length > 0 || files.length > 0 || workspaceFiles.length > 0;
```

InputControls 需新增 `workspaceFiles` prop，用于：
1. `hasContent` 判断（影响发送按钮是否可点击）
2. 在 FilePreview 区域渲染 workspace 文件卡片（转为 UploadedFile 格式，`isUploading=false`）

转换方式：workspace 文件直接映射为 `UploadedFile`（id 用 `ws_` 前缀 + workspace_path，url 用 cdn_url，isUploading=false），复用已有 FilePreview 组件渲染。

**移除 workspace 文件**：FilePreview 的 `onRemove(fileId)` 需区分两种来源：
- `ws_` 前缀的 id → 从 `pendingWorkspaceFiles` 移除（调 Chat.tsx 传下来的回调）
- 其他 id → 从 `useFileUpload` 移除（现有逻辑）

实现方式：InputControls 合并两个列表时标记来源，`onRemoveFile` 里判断前缀分发。

#### FilePart 结构

```json
{
  "type": "file",
  "url": "https://cdn.xxx.com/workspace/org/.../reports/sales_data.csv",
  "name": "sales_data.csv",
  "mime_type": "text/csv",
  "size": 131072,
  "workspace_path": "reports/sales_data.csv"
}
```

- `url`：CDN URL，用于前端预览/下载（`FilePreviewModal` 直接 fetch 此 URL）
- `workspace_path`：workspace 相对路径，后端用于注入 AI 提示

**类型变更**：
- `frontend/src/types/message.ts`：`FilePart` 新增 `workspace_path?: string`
- `backend/schemas/message.py`：`FilePart` 新增 `workspace_path: Optional[str] = None`
- `frontend/src/services/messageSender.ts`：`createTextWithFiles()` 透传 `workspace_path`
- `frontend/src/hooks/handlers/useTextMessageHandler.ts`：files 参数类型新增 `workspace_path?`

#### 后端处理

`chat_context_mixin.py` 的 `_build_llm_messages()` 中：

**现有逻辑**（保留）：
```python
file_urls = self._extract_file_urls(content)  # 返回 URL 列表
# file_urls 作为 image_url 传给 LLM（PDF 等多模态场景）
```

**新增逻辑**：
```python
workspace_files = self._extract_workspace_files(content)  # 新方法，返回有 workspace_path 的 FilePart
if workspace_files:
    # 从 file_urls 中排除 workspace 文件（避免重复传给 LLM）
    # 注入系统提示让 AI 用 file_read 读取
```

需在 `base.py` 新增方法：
```python
def _extract_workspace_files(self, content: List[ContentPart]) -> List[dict]:
    """提取含 workspace_path 的文件（用于注入 AI 提示）"""
    # 返回 [{"workspace_path": "...", "name": "...", "size": ..., "mime_type": "..."}]
```

注入的系统提示：
```
用户上传了以下文件到工作区，你可以使用 file_read 工具读取分析：
- reports/sales_data.csv (128KB, CSV 表格)
请先读取文件内容，再回复用户。
```

- 有 workspace_path 的文件：注入提示，**不**作为 image_url 传给 LLM
- 无 workspace_path 的文件（纯 CDN PDF 等）：保持现有 image_url 逻辑

### 5.2 消息中显示文件附件

**已有能力**：`MessageMedia.tsx` 已支持渲染 `FileCardList`（含下载+预览）。
**缺失环节**：`MessageItem.tsx` 未提取 FilePart 传给 MessageMedia。

修复方式：
1. `messageUtils.ts` 新增 `getFiles()` → 返回 `FilePart[]`
2. `MessageItem.tsx` 调用 `getFiles()` 提取文件，传给 `MessageMedia` 的 `files` prop

修复后，用户消息和 AI 消息中的文件都会显示为可下载/可预览的卡片。

### 5.3 AI 创建文件后面板刷新

AI 通过 `file_write` 创建文件后，面板需要刷新显示新文件：

- 方案：`file_write` 工具执行成功后，通过 WebSocket 发送 `workspace_changed` 事件
- 前端 `useWorkspace` Hook 监听此事件，自动重新加载当前目录
- 低优先级，Phase 4 实施

## 六、完整文件清单

### 新增文件（5 个前端 + 0 个后端新文件）

| # | 文件 | 行数估计 |
|---|------|---------|
| 1 | `frontend/src/components/chat/WorkspacePanel.tsx` | ~280 |
| 2 | `frontend/src/components/chat/WorkspaceFileItem.tsx` | ~150 |
| 3 | `frontend/src/components/chat/WorkspaceBreadcrumb.tsx` | ~50 |
| 4 | `frontend/src/hooks/useWorkspace.ts` | ~180 |
| 5 | `frontend/src/services/workspace.ts` | ~100 |

### 修改文件（16 个）

**前端**

| # | 文件 | 改动 |
|---|------|------|
| 1 | `pages/Chat.tsx` | +30 行：workspacePanelOpen + pendingWorkspaceFiles 状态 + 右侧面板布局 + 回调传递 |
| 2 | `components/chat/ChatHeader.tsx` | +15 行：文件夹按钮 + onToggleWorkspace prop |
| 3 | `components/chat/UploadMenu.tsx` | ~10 行：`onWorkspaceUpload` → `onOpenWorkspace` |
| 4 | `components/chat/InputControls.tsx` | -30 行：移除 workspace 独立上传逻辑（函数/ref/状态/input） |
| 5 | `components/chat/InputArea.tsx` | +25 行：接收 workspaceFiles + onRemoveWorkspaceFile prop，合并到 handleSubmit 发送流程，发送后清空，`getSendButtonState` 第 3 参数扩展含 workspace 文件，`hasFiles` 判断扩展 |
| 6 | `components/chat/MessageItem.tsx` | +15 行：提取 FilePart 传给 MessageMedia 的 files prop |
| 7 | `utils/messageUtils.ts` | +15 行：新增 `getFiles()` 返回 FilePart[] |
| 8 | `utils/fileUtils.ts` | +20 行：`getFileIconColor()` 背景色映射（图标模式用） |
| 9 | `types/message.ts` | +1 行：FilePart 新增 `workspace_path?: string` |
| 10 | `services/messageSender.ts` | ~5 行：`createTextWithFiles()` 透传 workspace_path |
| 11 | `hooks/handlers/useTextMessageHandler.ts` | ~3 行：files 参数类型新增 `workspace_path?` |

**后端**

| # | 文件 | 改动 |
|---|------|------|
| 12 | `api/routes/file.py` | +120 行：4 个新端点 + upload 支持 target_dir + list 返回 cdn_url |
| 13 | `services/file_executor.py` | +80 行：file_delete / file_mkdir / file_rename / file_move 方法 |
| 14 | `schemas/message.py` | +1 行：FilePart 新增 `workspace_path: Optional[str] = None` |
| 15 | `services/handlers/base.py` | +15 行：新增 `_extract_workspace_files()` 方法 |
| 16 | `services/handlers/chat_context_mixin.py` | +20 行：识别 workspace 文件，注入 AI 提示，排除 image_url 重复 |

### 删除文件（1 个）

| 文件 | 原因 |
|------|------|
| `frontend/src/services/workspaceUpload.ts` | 功能合并到 workspace.ts，仅 InputControls 1 处动态 import，该引用已移除 |

## 七、遗漏项排查

### 已确认覆盖

| 问题 | 状态 | 对应 Phase |
|------|------|-----------|
| 工作区文件不可视化 | 本方案核心 | Phase 2 |
| AI 不知道文件路径 | workspace_path 字段 + 系统提示注入 | Phase 3 |
| 消息中文件不显示 | MessageItem 提取 FilePart → MessageMedia 已有 FileCardList | Phase 3 |
| 无文件删除接口 | 新增 POST /files/workspace/delete | Phase 1 |
| 无新建文件夹接口 | 新增 POST /files/workspace/mkdir | Phase 1 |
| 无重命名接口 | 新增 POST /files/workspace/rename | Phase 1 |
| 无移动接口 | 新增 POST /files/workspace/move | Phase 1 |
| 上传固定到 uploads/ | upload 接口新增 target_dir 参数 | Phase 1 |
| 视图模式 | 列表 + 图标双模式切换 | Phase 2 |
| "上传到工作区"体验割裂 | 统一走面板 | Phase 2 |
| InputControls workspace 逻辑冗余 | 移除，改为打开面板 | Phase 2 |

### 暂不做（Phase 4 后续）

| 项目 | 原因 |
|------|------|
| 存储配额管理 | 需要数据库记录用量，当前用户量小暂不需要 |
| AI 文件操作后面板实时刷新 | 需要 WebSocket 事件，体验优化项 |
| 文件拖拽排序 | 列表/图标模式都按名称排序，暂不需要自定义排序 |
| 批量操作（多选删除/移动） | 后续根据使用反馈再加 |
| 文件版本历史 | 复杂度高，暂不需要 |
| 移动端全屏抽屉适配 | 当前用户主要用桌面端 |

## 八、实施阶段

### Phase 1：后端 API 补全（B 级）
- `file_executor.py` 新增 delete/mkdir/rename/move 方法（含文件名冲突检查）
- `file.py` 新增 4 个路由端点 + upload 支持 target_dir + list 返回 cdn_url
- 单元测试

### Phase 2：前端面板 UI（A 级）
- WorkspacePanel + FileItem + Breadcrumb 组件
- useWorkspace Hook（路径导航、文件列表、上传、删除、重命名、新建文件夹、移动）
- workspace.ts API 服务（整合并删除 workspaceUpload.ts）
- Chat.tsx 布局改造（右侧面板 + 展开/收起 + pendingWorkspaceFiles 状态）
- ChatHeader 加入切换按钮
- UploadMenu + InputControls 改造（统一走面板）
- 列表/图标视图切换
- MessageItem 提取 FilePart → 传给 MessageMedia（已有 FileCardList，独立于面板的 bug 修复）
- messageUtils.ts 新增 getFiles()

### Phase 3：AI 感知 + 发送到对话（B 级）
- "发送到对话"完整链路：WorkspacePanel → Chat.tsx → InputArea → 发送
- types/message.ts + schemas/message.py：FilePart 新增 workspace_path 字段
- messageSender.ts + useTextMessageHandler.ts：透传 workspace_path
- InputArea：接收 workspace 文件，合并到 handleSubmit 发送流程
- base.py：新增 `_extract_workspace_files()` 方法
- chat_context_mixin.py：识别 workspace 文件，注入 AI 提示，排除 image_url 重复

## 九、方案审查记录

### V1.2 修正（第一轮代码核实）

| # | 问题 | 修正 |
|---|------|------|
| 1 | 认证方式写错 `CurrentUser` | 统一用 `OrgCtx`（含 user_id + org_id） |
| 2 | `workspace://` 协议无法 fetch | 改为 CDN URL 预览/下载 + `workspace_path` 字段供 AI 读取 |
| 3 | list 接口缺 cdn_url | list 返回每个文件的 CDN URL |
| 4 | 重复新建 fileIcons.ts | 扩展已有 `fileUtils.ts` |
| 5 | MessageMedia 已支持文件 | 只需 MessageItem 传 files prop |
| 6 | FilePart 缺字段 | 前后端各加 `workspace_path` 可选字段 |

### V1.3 修正（第二轮边界情况核实）

| # | 问题 | 修正 |
|---|------|------|
| 7 | DELETE 请求体可能被代理丢弃 | 删除接口改用 `POST /files/workspace/delete` |
| 8 | CDN 未配置时面板崩溃 | 新增 4.5 节降级策略：无 CDN URL 时隐藏预览/下载按钮，AI 仍可通过 file_read 读取 |
| 9 | 移动端适配描述矛盾 | 布局节删除"全屏抽屉"描述，统一归入 Phase 4 |
| 10 | `onWorkspaceUpload` prop 改造不清晰 | 明确：UploadMenu 改为 `onOpenWorkspace`，InputControls 移除 4 项（函数/ref/状态/input） |
| 11 | `workspaceUpload.ts` 删除风险 | 确认仅 InputControls 动态 import 引用，无其他依赖，安全合并到 workspace.ts |

### V1.4 修正（第三轮数据流推演）

| # | 问题 | 修正 |
|---|------|------|
| 12 | "发送到对话"数据流完全未定义 | 补充 5.1 完整链路：WorkspacePanel → Chat.tsx(pendingWorkspaceFiles) → InputArea → handleSubmit 合并 → 发送 |
| 13 | `handleChatMessage` files 参数缺 workspace_path | useTextMessageHandler.ts files 类型扩展，messageSender.ts createTextWithFiles 透传 |
| 14 | 修改文件清单严重遗漏 6 个文件 | 补充：types/message.ts、messageSender.ts、useTextMessageHandler.ts、InputArea.tsx、base.py、chat_context_mixin.py |
| 15 | `_extract_file_urls()` 只返回 URL 无法区分文件类型 | base.py 新增 `_extract_workspace_files()` 返回完整 FilePart 对象 |
| 16 | rename/move 文件名冲突未处理 | 安全约束新增：目标已存在返回 409 错误 |
| 17 | 遗漏项表格仍写 DELETE | 修正为 POST |
| 18 | workspaceUpload.ts 处置不明确 | 明确：合并后删除旧文件，文件清单新增"删除文件"节 |

### V1.5 修正（第四轮实现细节推演）

| # | 问题 | 修正 |
|---|------|------|
| 19 | Section 3.7 与 Section 6 重复且过时（7 vs 11 文件） | Section 3.7 改为引用 Section 6 |
| 20 | Section 6 计数错（写 15 实际 16） | 修正为 16 |
| 21 | 函数命名不一致（getFileUrls vs getFiles） | 全文统一为 `getFiles()` |
| 22 | InputControls `hasContent` 不含 workspace 文件 → 发送按钮不亮 | 补充 InputControls 集成要点：扩展 hasContent + 新增 workspaceFiles prop |
| 23 | workspace 文件如何在 FilePreview 显示未定义 | 补充：转为 UploadedFile 格式复用 FilePreview |
| 24 | 面板内拖拽移动文件到文件夹复杂度高 | 推迟到 Phase 4，Phase 2 用右键菜单"移动到"替代 |

### V1.6 修正（第五轮用户场景走查）

| # | 问题 | 修正 |
|---|------|------|
| 25 | list 返回缺 mime_type → FilePreviewModal 无法判断文件类型 | WorkspaceFileItem 新增 mime_type 字段，后端用 mimetypes.guess_type() 填充 |
| 26 | list 返回 cdn_url 需要拼路径但未说明 | 4.3 节补充实现要点：`relative_path = f"{path}/{item.name}"` 传给 get_cdn_url() |

### V1.7 修正（第六轮细节审查 — 类型边界/状态一致性）

| # | 问题 | 修正 |
|---|------|------|
| 27 | FilePreview `onRemove` 无法区分 PDF 上传文件和 workspace 文件 | workspace 文件用 `ws_` 前缀 id，onRemoveFile 判断前缀分发到不同 handler |
| 28 | InputArea `getSendButtonState` 和 `handleSubmit` 的 hasFiles 不含 workspace 文件（两处独立判断，方案只修了 InputControls 的 hasContent） | InputArea 中 hasFiles 扩展：`hasFiles \|\| workspaceFiles.length > 0` |
| 29 | upload target_dir 传参方式未定义 | 明确用 `Form(default=".")` 接收，前端 FormData.append('target_dir', path) |
| 30 | 面板首次加载闪空状态 | useWorkspace 加 loading 状态，面板结构补充加载态（骨架屏/spinner） |
| 31 | 面板关闭再打开的缓存策略未定义 | useWorkspace 保持数据不清空（面板 hidden 不卸载），重新打开时静默刷新 |

### V1.8 修正（第七轮维度清单排查）

| # | 问题 | 修正 |
|---|------|------|
| 32 | "发送到对话"同一文件连点两次会重复添加 | onSendToChat 按 workspace_path 去重 |

### 确认无误的假设

- [x] `FileExecutor.get_cdn_url()` 已实现，可为 workspace 文件生成 CDN URL
- [x] `resolve_safe_path()` 已实现路径穿越防护，新端点直接复用
- [x] `FilePreviewModal` 支持 Excel/CSV/文本/PDF 预览（fetch CDN URL）
- [x] `FileCard` + `downloadFile` 已实现下载功能
- [x] `workspaceUpload.ts` 已有 upload + list API 封装，仅 InputControls 1 处动态 import
- [x] `lucide-react` 已安装，LayoutList/LayoutGrid/FolderOpen 可直接引用
- [x] Chat.tsx 使用 `flex` 布局，右侧加面板只需追加子元素
- [x] 无需新建数据库表，无需新建后端服务文件
- [x] 生产环境已配置 `oss_cdn_domain`
- [x] `OrgCtx` 同时提供 `user_id` 和 `org_id`
- [x] `handleChatMessage` 第 4 参数 files 已接受对象数组（非纯 URL），扩展类型兼容
- [x] `MessageMedia` 已有 `files` prop（默认空数组），接入只需 MessageItem 传值
- [x] Content 存储为 JSONB，新增 `workspace_path` 字段自动持久化无需迁移
