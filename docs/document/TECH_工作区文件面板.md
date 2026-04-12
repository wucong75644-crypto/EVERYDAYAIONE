# 技术方案：工作区文件面板

> 版本：V2.0 | 日期：2026-04-11
> 基于：设计系统 V3（3 层 token + cva 组件库 + framer-motion）+ 全屏切换布局
> 历史：V1.0 → V1.8 经七轮审查共修正 32 项问题，但因项目设计系统重构（V3）+ 布局需求变更（drawer → 全屏切换），整体重写为 V2.0
> 历史审查记录另存于：`docs/document/archive/TECH_工作区文件面板_V1.8.md`（如需查阅）

---

## 一、产品定位

每个用户拥有独立的云端工作区（类似电脑里的"我的文件"），AI 可以像在本地电脑一样读写、整理这个空间。

**核心场景**：
- 用户上传 CSV/Excel/Word/PDF 等文件
- AI 通过 `file_read` 工具读取分析文件内容
- AI 通过 `file_write` 工具生成新文件（CSV 表格、Excel 报表、Markdown 报告等）
- 用户在工作区看到所有自己上传的和 AI 生成的文件，可整理、下载、再次发给 AI

**不做**（划入第十节延伸任务）：
- 企业共享知识库 / 团队共享空间
- 数据库智能查询面板

---

## 二、整体布局

### 2.1 两种视图模式

**模式 A：对话视图（默认）**
```
┌──────────┬─────────────────────────────────┐
│          │  ChatHeader (含 📁 工作区按钮)   │
│          ├─────────────────────────────────┤
│ Sidebar  │                                  │
│  w-64    │       MessageArea                │
│          │                                  │
│          ├─────────────────────────────────┤
│          │       InputArea                  │
└──────────┴─────────────────────────────────┘
```

**模式 B：工作区视图（点 📁 后切换）**
```
┌──────────┬─────────────────────────────────┐
│          │  WorkspaceHeader (路径 + 返回)   │
│          ├─────────────────────────────────┤
│ Sidebar  │                                  │
│  w-64    │      工作区文件浏览器              │
│          │   （列表/图标视图，铺满）         │
│          │                                  │
│          │                                  │
└──────────┴─────────────────────────────────┘
```

- **左侧 Sidebar 始终在**：方便切回对话或切到其他对话
- **主内容区切换**：MessageArea+InputArea 整体隐藏，WorkspaceView 接管
- **状态保留**：切换时不卸载组件（用 CSS `hidden` 或条件渲染均可，但要保留 InputArea 的输入框内容）
- **过渡动画**：framer-motion crossfade（200ms）
- **入口**：所有用户可见（不像定时任务限企业用户）
- **prompt 保留**：InputArea 的输入框内容在切换 view 时**不丢失**（采用 B 方案，状态提升到 Chat.tsx，详见 2.3）

### 2.2 状态管理（Chat.tsx）

```typescript
const [view, setView] = useState<'chat' | 'workspace'>('chat');

// ChatHeader 触发
<ChatHeader onOpenWorkspace={() => setView('workspace')} />

// 主内容区根据 view 切换（用条件渲染但保留 InputArea state）
<div className="flex-1 flex flex-col min-w-0">
  <ChatHeader ... onOpenWorkspace={() => setView('workspace')} />

  {view === 'chat' ? (
    <>
      <MessageArea ... />
      <InputArea ... workspaceFiles={pendingWorkspaceFiles} ... />
    </>
  ) : (
    <WorkspaceView
      onBack={() => setView('chat')}
      onSendToChat={handleSendFromWorkspace}
    />
  )}
</div>
```

### 2.3 prompt 状态提升（B 方案，已确认）

**问题**：条件渲染 `view === 'chat' ? <InputArea /> : <WorkspaceView />` 会在切换时卸载 InputArea，其内部的 `prompt` useState 销毁，用户切换 view 后输入框清空。

**解决**：把 prompt state 从 InputArea 提升到 Chat.tsx，作为 props 受控传递。

```typescript
// Chat.tsx
const [prompt, setPrompt] = useState('');

// 传给 InputArea
<InputArea
  prompt={prompt}
  onPromptChange={setPrompt}
  ...
/>
```

```typescript
// InputArea.tsx 改造
interface InputAreaProps {
  prompt: string;                          // 新增：从父组件接收
  onPromptChange: (value: string) => void; // 新增：受控更新
  // ... 其他 props
}

export default function InputArea({ prompt, onPromptChange, ... }: InputAreaProps) {
  // 删除：const [prompt, setPrompt] = useState('');
  // 改用 props
}
```

**关键点**：
- Chat.tsx 不会因 view 切换卸载，所以 prompt state 始终保留
- 切回 chat 时，新的 InputArea 实例从 props 拿到上次的 prompt
- 这是 React 标准的"状态提升"模式（ChatGPT/Claude/Notion 都是这么做的）
- 改动量：约 10-15 行（InputArea 删 useState + 改用 props，Chat.tsx 加 state + 传 prop）

---

## 三、前端设计

### 3.1 WorkspaceView 结构

```
WorkspaceView (主组件，铺满主内容区)
├── WorkspaceHeader (sticky top)
│   ├── 返回对话按钮（← Back，调 onBack）
│   ├── 面包屑路径（. > uploads > reports）
│   ├── 视图切换（☰ 列表 / ⊞ 图标）
│   └── 工具按钮组（新建文件夹 / 上传文件）
│
├── 文件区域（可滚动，铺满剩余空间）
│   ├── 【列表模式】WorkspaceFileList
│   │   每行：图标 + 名称 + 大小 + 修改时间 + 操作菜单
│   └── 【图标模式】WorkspaceFileGrid
│       网格：大图标 + 文件名（响应式 grid-cols-4 ~ grid-cols-8）
│
├── 加载状态：骨架屏（首次加载/切换目录）
├── 空状态：图标 + "还没有文件，上传或让 AI 帮你创建"
└── 拖拽上传遮罩：覆盖整个 WorkspaceView（拖入显示）
```

### 3.2 视图模式

**列表模式**（默认）：
```
☐ 名称                        大小     修改时间        ⋯
📁 reports/                              04-05 14:32   ⋯
📊 sales_data.csv             128KB    04-03 09:15   ⋯
📄 README.md                  3.2KB    04-01 18:00   ⋯
```

**图标模式**：

铺满主内容区的大空间允许更大网格。响应式：
- `lg:grid-cols-8`（大屏）
- `md:grid-cols-6`（中等）
- `sm:grid-cols-4`（小屏）

每个格子约 120×140px，含大图标 + 截断文件名。

视图偏好存 localStorage（key: `workspace_view_mode`，值 `list` / `grid`）。

### 3.3 交互细节

| 操作 | 行为 |
|------|------|
| 点 📁 工作区按钮 | 主内容区切换到工作区视图 |
| 点"返回对话"按钮 | 切回聊天 |
| 点击文件夹 | 进入子目录，面包屑更新 |
| 点击面包屑 | 跳转到对应层级 |
| 点击文件 | 可预览（CSV/Excel/文本/PDF）→ 弹 FilePreviewModal；不可预览 → 触发下载 |
| **"插入到聊天"** | 加入待发送队列 → 自动切回对话视图 → 文件出现在 InputArea 输入框上方（详见 5.1） |
| 拖拽文件到 WorkspaceView | 上传到当前目录 |
| 上传按钮 | 文件选择器（支持多选） |
| 新建文件夹 | 行内输入框 → 提交 |
| 删除 | 二次确认（用 Modal） |
| 重命名 | 双击文件名 → 行内编辑 |

### 3.4 颜色 token 映射（设计系统 V3）

**所有颜色禁止硬编码**，全部用 `var(--s-*)`：

| 用途 | Token |
|------|------|
| 主内容区背景 | `bg-[var(--s-surface-base)]` |
| WorkspaceHeader 背景 | `bg-[var(--s-surface-overlay)]` |
| 文件行 hover | `hover:bg-[var(--s-hover)]` |
| 选中文件 | `bg-[var(--s-selected)]` |
| 主文字 | `text-[var(--s-text-primary)]` |
| 次文字 | `text-[var(--s-text-secondary)]` |
| 弱文字（时间/大小） | `text-[var(--s-text-tertiary)]` |
| 边框 | `border-[var(--s-border-default)]` |
| 强调（按钮主色） | `bg-[var(--s-accent)]` |
| 成功 | `text-[var(--s-success)]` |
| 错误 | `text-[var(--s-error)]` |
| Drop shadow | `shadow-[var(--s-shadow-drop-xl)]` |

参考已实现的 SearchPanel.tsx 的 token 用法。

### 3.5 文件类型图标

复用 `frontend/src/utils/fileUtils.ts` 的 `getFileIcon()` + `formatFileSize()`，新增 `getFileIconColor()`：

| 类型 | 扩展名 | 图标 (lucide-react) | 颜色 |
|------|--------|------|------|
| PDF | .pdf | `FileText` | `text-red-500 dark:text-red-400` |
| Excel/CSV | .xls/.xlsx/.csv/.tsv | `Sheet` | `text-green-500 dark:text-green-400` |
| Word | .doc/.docx | `FileText` | `text-blue-500 dark:text-blue-400` |
| PPT | .ppt/.pptx | `FileText` | `text-orange-500 dark:text-orange-400` |
| 代码 | .py/.js/.ts/.html/.css/.sql | `FileCode` | `text-purple-500 dark:text-purple-400` |
| 文本 | .txt/.md/.log/.json/.yaml/.xml | `FileText` | `text-[var(--s-text-secondary)]` |
| 压缩包 | .zip | `FileArchive` | `text-yellow-600 dark:text-yellow-400` |
| 文件夹 | — | `Folder` | `text-blue-500 dark:text-blue-400` |

文件类型颜色用 Tailwind 直接色（红/绿/蓝），不需要语义 token —— 这是文件类型的视觉编码而非 UI 状态色。

### 3.6 已有可复用资源

| 资源 | 路径 | 复用方式 |
|------|------|---------|
| `Button` | `components/ui/Button.tsx` | 工具栏按钮（cva variant: ghost/accent/danger） |
| `Card` | `components/ui/Card.tsx` | 图标模式文件格子 |
| `Input` | `components/ui/Input.tsx` | 重命名/新建文件夹输入框 |
| `Dropdown` | `components/ui/Dropdown.tsx` | 文件操作菜单 |
| `Badge` | `components/ui/Badge.tsx` | 状态标签 |
| `FileCard` / `FileCardList` | `components/chat/media/FileCard.tsx` | 消息中文件附件渲染（5.6） |
| `FilePreviewModal` | `components/chat/modals/FilePreviewModal.tsx` | 文件预览弹窗（待确认实际路径） |
| `fileUtils` | `utils/fileUtils.ts` | getFileIcon + formatFileSize |
| `downloadFile` | `utils/downloadFile.ts` | 文件下载 |
| `motion` 系列 | `utils/motion.ts` | FLUID_SPRING / SOFT_SPRING / fadeVariants |
| `cn` | `utils/cn.ts` | className 合并 |

**参考实现**：
- `chat/search/SearchPanel.tsx` — token 用法 + framer-motion 模板
- `scheduled-tasks/ScheduledTaskPanel.tsx` — 业务面板组合模式

### 3.7 新增前端文件

| # | 文件 | 行数估计 | 职责 |
|---|------|---------|------|
| 1 | `components/workspace/WorkspaceView.tsx` | ~250 | 主组件，铺满主内容区 |
| 2 | `components/workspace/WorkspaceHeader.tsx` | ~120 | 顶部栏 |
| 3 | `components/workspace/WorkspaceFileList.tsx` | ~150 | 列表模式渲染 |
| 4 | `components/workspace/WorkspaceFileGrid.tsx` | ~120 | 图标模式渲染 |
| 5 | `components/workspace/WorkspaceFileItem.tsx` | ~150 | 单个条目（含操作菜单） |
| 6 | `components/workspace/WorkspaceBreadcrumb.tsx` | ~60 | 面包屑导航 |
| 7 | `components/workspace/WorkspaceEmptyState.tsx` | ~40 | 空状态 |
| 8 | `components/workspace/WorkspaceDropZone.tsx` | ~80 | 拖拽上传遮罩 |
| 9 | `hooks/useWorkspace.ts` | ~200 | 状态管理（路径/列表/视图模式/loading/CRUD） |
| 10 | `services/workspace.ts` | ~120 | 后端 API 调用 |

新建一级目录 `components/workspace/`（不放在 chat/ 下，因为它是独立视图而非聊天子组件）。

---

## 四、后端补全

### 4.1 新增 4 个 API

```python
# 1. 删除文件/空目录（用 POST 而非 DELETE，避免代理丢弃请求体）
POST /files/workspace/delete
Body: { "path": "uploads/report.csv" }
Response: { "success": true }

# 2. 新建文件夹
POST /files/workspace/mkdir
Body: { "path": "reports/2026-04" }
Response: { "success": true, "path": "reports/2026-04" }

# 3. 重命名（同目录下改名，跨目录用 move）
POST /files/workspace/rename
Body: { "old_path": "uploads/old.csv", "new_path": "uploads/new.csv" }
Response: { "success": true }

# 4. 移动文件
POST /files/workspace/move
Body: { "src_path": "uploads/data.csv", "dest_dir": "reports/" }
Response: { "success": true, "new_path": "reports/data.csv" }
```

### 4.2 改造 upload 接口

```python
async def upload_to_workspace(
    ctx: OrgCtx,
    db: ScopedDB,
    file: UploadFile = File(...),
    target_dir: str = Form(default="."),  # 新增
):
```

前端：
```typescript
const formData = new FormData();
formData.append('file', file);
formData.append('target_dir', currentPath);
```

### 4.3 改造 list 接口

```python
class WorkspaceFileItem(BaseModel):
    name: str
    is_dir: bool
    size: int = 0
    modified: str = ""
    cdn_url: Optional[str] = None     # 新增：CDN 下载地址
    mime_type: Optional[str] = None   # 新增：MIME 类型
```

实现要点：
```python
for item in sorted(target.iterdir()):
    relative_path = f"{path}/{item.name}".lstrip('./')
    cdn_url = executor.get_cdn_url(relative_path) if item.is_file() else None
    mime_type = mimetypes.guess_type(item.name)[0] if item.is_file() else None
    items.append(WorkspaceFileItem(
        name=item.name,
        is_dir=item.is_dir(),
        size=...,
        modified=...,
        cdn_url=cdn_url,
        mime_type=mime_type,
    ))
```

### 4.4 认证

所有 workspace 端点统一用 `OrgCtx`（与现有 upload/list 一致）。`OrgCtx` 提供 `user_id` + `org_id`，构建 `FileExecutor` 不需额外查询。

### 4.5 安全约束

- 所有路径操作复用 `FileExecutor.resolve_safe_path()` 防穿越
- 删除：文件直接删除，目录仅允许空目录（非空返回错误）
- 重命名：不允许跨目录（跨目录用 move）
- 移动：目标必须是已存在的目录，且在 workspace 内
- **文件名冲突**：rename/move 时目标已存在 → 返回 409，前端提示"文件已存在"
- 文件大小上限：单文件 50MB

### 4.6 CDN 未配置降级

`get_cdn_url()` 在 `oss_cdn_domain` 未配置时返回 None：
- list 接口的 `cdn_url` 为 None 时，前端隐藏"预览"和"下载"按钮
- "插入到聊天"无 CDN URL 时，仅发 workspace_path，AI 仍可通过 file_read 读取
- 生产环境必配 CDN，这是降级保底

### 4.7 修改后端文件

| 文件 | 改动 |
|------|------|
| `api/routes/file.py` | +120 行：4 个新端点 + upload 支持 target_dir + list 返回 cdn_url/mime_type |
| `services/file_executor.py` | +80 行：file_delete / file_mkdir / file_rename / file_move 方法 |

---

## 五、AI 感知文件

### 5.1 "插入到聊天"完整数据流

V2.0 全屏切换布局下，"发送到对话"改为"插入到聊天"：

```
WorkspaceView 用户点击文件菜单"插入到聊天"
    ↓ onSendToChat(file)
Chat.tsx pendingWorkspaceFiles 加入文件（按 workspace_path 去重）
    ↓
自动 setView('chat') 切回对话视图
    ↓
InputArea 接收 workspaceFiles prop，FilePreview 区显示文件卡片
    ↓ 用户输入文字（可选），点发送
handleSubmit() 合并 pendingWorkspaceFiles 到 fileData
    ↓
useTextMessageHandler.handleChatMessage(text, convId, images, files)
    ↓
messageSender.createTextWithFiles(text, images, files) 构建 FilePart(workspace_path=...)
    ↓
sendMessage() POST /conversations/{id}/messages/generate
    ↓
后端 chat_context_mixin._build_llm_messages() 注入 AI 提示
    ↓
LLM 自动调用 file_read 读取文件分析
```

**Chat.tsx 关键代码**：

```typescript
const [pendingWorkspaceFiles, setPendingWorkspaceFiles] = useState<WorkspaceFile[]>([]);

const handleSendFromWorkspace = (file: WorkspaceFile) => {
  // 按 workspace_path 去重
  setPendingWorkspaceFiles(prev =>
    prev.some(f => f.workspace_path === file.workspace_path)
      ? prev
      : [...prev, file]
  );
  setView('chat');  // 自动切回对话
};
```

### 5.2 InputArea 集成

接收 `workspaceFiles` + `onRemoveWorkspaceFile` + `onWorkspaceFilesConsumed` prop：

```typescript
// handleSubmit 中
const fileData = [
  ...(uploadedFileUrls || []),       // PDF 上传
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

**两处独立判断都要扩展**：
- L266 `handleSubmit` 中：`hasFiles || workspaceFiles.length > 0`
- L343 `getSendButtonState` 第 3 参数同上

### 5.3 InputControls 集成

`hasContent` 扩展为：
```typescript
const hasContent = prompt.trim().length > 0
  || images.length > 0
  || files.length > 0
  || (workspaceFiles?.length ?? 0) > 0;
```

FilePreview 渲染时把 workspace 文件转为 `UploadedFile` 格式（id 用 `ws_` 前缀 + workspace_path），`onRemoveFile` 按前缀分发到不同 handler。

### 5.4 FilePart 类型扩展

```typescript
// frontend/src/types/message.ts
export interface FilePart {
  type: 'file';
  url: string;
  name: string;
  mime_type: string;
  size?: number;
  workspace_path?: string;  // 新增
}
```

```python
# backend/schemas/message.py
class FilePart(BaseModel):
    type: Literal["file"] = "file"
    url: str
    name: str
    mime_type: str
    size: Optional[int] = None
    workspace_path: Optional[str] = None  # 新增
```

### 5.5 后端 chat_context_mixin 处理

```python
# backend/services/handlers/base.py 新增方法
def _extract_workspace_files(self, content: List[ContentPart]) -> List[dict]:
    """提取含 workspace_path 的文件（用于注入 AI 提示）"""
    from schemas.message import FilePart
    files = []
    for part in content:
        if isinstance(part, FilePart) and part.workspace_path:
            files.append({
                "workspace_path": part.workspace_path,
                "name": part.name,
                "size": part.size,
                "mime_type": part.mime_type,
                "url": part.url,
            })
        elif isinstance(part, dict) and part.get("type") == "file" and part.get("workspace_path"):
            files.append({
                "workspace_path": part["workspace_path"],
                "name": part.get("name", ""),
                "size": part.get("size"),
                "mime_type": part.get("mime_type", ""),
                "url": part.get("url", ""),
            })
    return files
```

```python
# backend/services/handlers/chat_context_mixin.py 修改 _build_llm_messages
file_urls = self._extract_file_urls(content)
workspace_files = self._extract_workspace_files(content)

if workspace_files:
    # 从 file_urls 中排除 workspace 文件（避免重复传给 LLM）
    workspace_urls = {f["url"] for f in workspace_files if f.get("url")}
    file_urls = [u for u in file_urls if u not in workspace_urls]

    # 注入系统提示
    file_list_str = "\n".join(
        f"- {f['workspace_path']} ({format_size(f.get('size'))}, {f.get('mime_type', '未知类型')})"
        for f in workspace_files
    )
    messages.insert(0, {
        "role": "system",
        "content": (
            f"用户上传了以下文件到工作区，你可以使用 file_read 工具读取分析：\n"
            f"{file_list_str}\n"
            f"请先读取文件内容，再回复用户。"
        )
    })
```

### 5.6 消息中文件附件渲染（独立 bug 修复）

`MessageMedia.tsx` 已导入 `FileCardList`（L492）并默认 `files=[]`，但 `MessageItem.tsx` 没传 files prop，导致消息中文件不显示。

修复：
1. `messageUtils.ts` 新增 `getFiles(message: Message): FilePart[]`
2. `MessageItem.tsx` 调用 `getFiles()` 传给 `MessageMedia` 的 `files` prop

修复后用户和 AI 消息中的文件都会显示为可下载/可预览的卡片。

---

## 六、完整文件清单

### 新增前端文件（10 个）

| # | 文件 | 行数 |
|---|------|------|
| 1 | `frontend/src/components/workspace/WorkspaceView.tsx` | ~250 |
| 2 | `frontend/src/components/workspace/WorkspaceHeader.tsx` | ~120 |
| 3 | `frontend/src/components/workspace/WorkspaceFileList.tsx` | ~150 |
| 4 | `frontend/src/components/workspace/WorkspaceFileGrid.tsx` | ~120 |
| 5 | `frontend/src/components/workspace/WorkspaceFileItem.tsx` | ~150 |
| 6 | `frontend/src/components/workspace/WorkspaceBreadcrumb.tsx` | ~60 |
| 7 | `frontend/src/components/workspace/WorkspaceEmptyState.tsx` | ~40 |
| 8 | `frontend/src/components/workspace/WorkspaceDropZone.tsx` | ~80 |
| 9 | `frontend/src/hooks/useWorkspace.ts` | ~200 |
| 10 | `frontend/src/services/workspace.ts` | ~120 |

### 修改前端文件（11 个）

| # | 文件 | 改动 |
|---|------|------|
| 1 | `pages/Chat.tsx` | +50 行：`view` 状态 + `pendingWorkspaceFiles` 状态 + **`prompt` 状态提升** + 主内容区切换渲染 + 回调 |
| 2 | `components/chat/layout/ChatHeader.tsx` | +15 行：`onOpenWorkspace` prop + 📁 按钮（FolderOpen） |
| 3 | `components/chat/input/UploadMenu.tsx` | ~10 行：`onWorkspaceUpload` → `onOpenWorkspace`（点击切换视图） |
| 4 | `components/chat/input/InputControls.tsx` | -30 行 +5 行：移除 workspace 独立上传逻辑，新增 `workspaceFiles` prop，`hasContent` 扩展 |
| 5 | `components/chat/input/InputArea.tsx` | +30 行：**prompt 改为受控（删 useState，改用 props）** + 接收 workspaceFiles + onRemove + onConsumed prop，合并到 handleSubmit，两处 hasFiles 判断扩展 |
| 6 | `components/chat/message/MessageItem.tsx` | +15 行：调 getFiles() 传给 MessageMedia |
| 7 | `utils/messageUtils.ts` | +15 行：新增 `getFiles()` 返回 FilePart[] |
| 8 | `utils/fileUtils.ts` | +20 行：新增 `getFileIconColor()` |
| 9 | `types/message.ts` | +1 行：FilePart 新增 `workspace_path?: string` |
| 10 | `services/messageSender.ts` | ~5 行：`createTextWithFiles()` 透传 workspace_path |
| 11 | `hooks/handlers/useTextMessageHandler.ts` | ~3 行：files 参数类型新增 `workspace_path?` |

### 修改后端文件（5 个）

| # | 文件 | 改动 |
|---|------|------|
| 12 | `api/routes/file.py` | +120 行：4 个新端点 + upload 支持 target_dir + list 返回 cdn_url/mime_type |
| 13 | `services/file_executor.py` | +80 行：file_delete / file_mkdir / file_rename / file_move 方法 |
| 14 | `schemas/message.py` | +1 行：FilePart 新增 `workspace_path: Optional[str] = None` |
| 15 | `services/handlers/base.py` | +20 行：`_extract_workspace_files()` 方法 |
| 16 | `services/handlers/chat_context_mixin.py` | +25 行：识别 workspace 文件，注入 AI 提示，排除 image_url 重复 |

### 删除文件（1 个）

| 文件 | 原因 |
|------|------|
| `frontend/src/services/workspaceUpload.ts` | 功能合并到 workspace.ts，仅 InputControls 1 处动态 import，已移除 |

**注意**：合并前需先 grep 确认 workspaceUpload.ts 的所有引用都已迁移到 workspace.ts。

---

## 七、实施阶段

### Phase 1：后端 API 补全（B 级，~2h）

- `services/file_executor.py` 新增 4 个方法：
  - `file_delete(path)` — 文件直删，目录需空
  - `file_mkdir(path)` — 创建目录（含中间路径）
  - `file_rename(old_path, new_path)` — 同目录改名，409 冲突检查
  - `file_move(src_path, dest_dir)` — 跨目录移动，409 冲突检查
- `api/routes/file.py`：
  - 新增 4 个端点（POST delete/mkdir/rename/move）
  - upload 改造：新增 `target_dir: str = Form(default=".")` 参数
  - list 改造：`WorkspaceFileItem` 新增 `cdn_url` 和 `mime_type` 字段
- 单元测试：`backend/tests/test_file_executor.py` 补充 4 个新方法的测试

### Phase 2：前端工作区视图（A 级，~5h）

**2.1 基础设施**
- `services/workspace.ts`：整合 `workspaceUpload.ts` 全部功能 + 新增 delete/mkdir/rename/move/upload 携带 target_dir
- `hooks/useWorkspace.ts`：状态（path/items/loading/viewMode）+ CRUD 操作
- 删除 `services/workspaceUpload.ts`

**2.2 组件**
- `components/workspace/` 目录下 8 个组件
- `WorkspaceView` 主组件参考 SearchPanel.tsx 的 token 用法
- 列表/图标视图都用 ui/Card + ui/Button + ui/Dropdown 拼

**2.3 集成到 Chat.tsx**
- 新增 `view` 状态（'chat' | 'workspace'）
- 主内容区根据 view 条件渲染（注意 InputArea state 丢失风险，决定是否提升 prompt 状态）
- crossfade 动画（framer-motion AnimatePresence）

**2.4 ChatHeader 加按钮**
- 新增 `onOpenWorkspace` prop
- 加 📁 FolderOpen 按钮（放在 🔍 搜索按钮旁边）

**2.5 改造 UploadMenu + InputControls**
- UploadMenu：`onWorkspaceUpload` 改名 `onOpenWorkspace`，点击切换到工作区视图
- InputControls：移除 workspace 独立上传逻辑（4 项：函数 + ref + 状态 + input）
- 验证：`grep` 确认无残留引用

**2.6 MessageItem 修复（可选拆分到 Phase 3）**
- 修复消息中文件不显示的 bug

### Phase 3：AI 感知 + 插入到聊天（B 级，~2h）

- `types/message.ts` + `schemas/message.py`：FilePart 新增 workspace_path
- `services/messageSender.ts` + `useTextMessageHandler.ts`：透传 workspace_path
- `InputArea` 接收 `workspaceFiles` + `onRemoveWorkspaceFile` + `onWorkspaceFilesConsumed` prop
- `InputControls` 接收 `workspaceFiles`，扩展 `hasContent`
- `Chat.tsx` 实现 `handleSendFromWorkspace`（去重 + 切回对话）
- `base.py` 新增 `_extract_workspace_files()` 方法
- `chat_context_mixin.py` 修改 `_build_llm_messages()` 注入 AI 提示
- 端到端测试：上传文件 → 插入到聊天 → AI 调用 file_read → 返回结果

---

## 八、暂不做（Phase 4 后续）

| 项目 | 原因 |
|------|------|
| 存储配额管理 | 当前用户量小，按用户/企业的存储统计后续做 |
| AI 创建文件后面板实时刷新 | 需要 WebSocket 事件，体验优化项 |
| 文件拖拽排序 | 列表/图标都按名称排序，自定义排序优先级低 |
| 批量操作（多选删除/移动） | 后续根据使用反馈再加 |
| 文件版本历史 | 复杂度高，暂不需要 |
| 移动端全屏适配 | 当前主要用桌面端 |
| 面板内拖拽文件到文件夹 | Phase 2 用右键菜单"移动到"替代，拖拽 Phase 4 |

---

## 九、关键假设清单（V2.0 已验证）

- [x] `FileExecutor.get_cdn_url()` 已实现，可生成 workspace 文件 CDN URL
- [x] `resolve_safe_path()` 已实现路径穿越防护，新端点直接复用
- [x] `FilePreviewModal` 支持 Excel/CSV/文本/PDF 预览（fetch CDN URL）
- [x] `FileCard` + `downloadFile` 已实现下载功能
- [x] `workspaceUpload.ts` 仅 InputControls 1 处动态 import
- [x] `lucide-react` 已安装，FolderOpen / Folder / FileText / FileCode / Sheet / FileArchive 等图标可直接引用
- [x] 设计系统 V3 已完成，所有 `--s-*` token 可用
- [x] `ui/Button` `ui/Card` `ui/Input` `ui/Dropdown` `ui/Badge` 已有
- [x] `motion.ts` 提供 FLUID_SPRING / SOFT_SPRING 等 preset
- [x] SearchPanel.tsx + ScheduledTaskPanel.tsx 是现成参考
- [x] ChatHeader 已支持可选 onOpenSearch / onOpenScheduledTasks，新增 onOpenWorkspace 模式一致
- [x] `OrgCtx` 同时提供 `user_id` 和 `org_id`
- [x] `handleChatMessage` 第 4 参数 files 已接受对象数组（非纯 URL），扩展类型兼容
- [x] `MessageMedia` 已有 `files` prop（默认空数组），接入只需 MessageItem 传值
- [x] Content 存储为 JSONB，新增 `workspace_path` 字段自动持久化无需迁移
- [x] 生产环境已配置 `oss_cdn_domain`

---

## 十、未来延伸任务（本任务完成后启动）

本次任务聚焦"私人工作区文件管理"，以下两个相关产品方向已确认拆分为独立任务，后续单独立项实施。

### 延伸任务 1：企业共享知识库

**与私人工作区的区别**：

| 维度 | 私人工作区（本次） | 企业共享知识库（延伸 1） |
|------|------------------|------------------------|
| 本质 | 个人文件管理器 | 企业数据资产库 |
| 存储模型 | 文件系统目录树 | 文档 + 元数据 + 向量索引 |
| 使用者 | 每人独立 | 全员共享 |
| AI 集成 | file_read/write 直接读写 | RAG 检索增强 |
| 权限 | 仅自己 | 角色分级 + 部门隔离 |
| 典型场景 | "帮我分析这份 Excel" | "公司去年 Q4 业绩报告里写了什么" |

**核心能力**：
- 老板/管理员上传企业文档（产品手册、SOP、历史报告、客户资料）
- 全员可检索引用，权限按部门/角色分级
- AI 对话时自动 RAG 检索相关文档作为上下文
- 文档版本管理 + 审计日志
- 支持文件夹分类、标签、搜索

**技术栈预估**：
- 向量数据库（pgvector 或独立向量存储）
- 文档解析（pdf/docx/xlsx → 文本分块）
- Embedding 模型 + 检索 Pipeline
- 复用现有权限模型 V1（task.* 权限模式）

### 延伸任务 2：输入框 @ 文件快捷引用

**核心能力**：在 InputArea 输入框输入 `@` 时弹出工作区文件搜索菜单，快速选中文件插入到消息。

**与本次工作区按钮的区别**：

| 方式 | 适用场景 |
|------|---------|
| **📁 工作区按钮（本次）** | 用户想"逛一下"工作区，浏览/整理文件，临时找到想用的 |
| **@ 文件引用（延伸 2）** | 用户已经知道要哪个文件，直接 @文件名 快速插入，不离开输入框 |

两个互补，不是替代。本次先做工作区按钮（更基础），@ 功能后续做。

**核心交互**：
```
用户输入: 帮我分析@
            ↓ 检测到 @
弹出下拉:
  📊 sales_2026Q1.csv
  📊 inventory.xlsx
  📄 report.md
            ↓ 用户输入 sa 过滤
  📊 sales_2026Q1.csv  ← 选中
            ↓ 回车/点击
插入文件 + 关闭菜单 + 输入框光标继续
```

**技术栈预估**：
- 前端：textarea 内 @ 触发器（监听 keydown + 计算 caret 位置弹下拉）
- 复用 `services/workspace.ts` 的 list API 做文件搜索
- 复用本次新增的 `pendingWorkspaceFiles` 状态（@ 选中和工作区按钮"插入到聊天"走同一条路径）
- 后端：可能需要新增 `GET /files/workspace/search?q=xxx` 接口（按文件名模糊匹配）

**前置依赖**：本次工作区任务必须先完成（@ 功能复用了 90% 的基础设施）

### 延伸任务 3：数据库智能查询面板

**核心能力**：打通 ERP/订单/库存等业务数据库，提供两种查询方式：

1. **手动搜索**：可视化筛选器（类似 Notion Database），按字段过滤、排序、分组
2. **AI 对话查询**：自然语言"上周哪个店铺销量最高" → AI 生成 SQL → 执行 → 返回结果
3. **AI 数据计算**：在查询结果上做二次计算（聚合、对比、预测）

**与本次工作区的区别**：
- 本次工作区：用户上传文件 → AI 分析文件
- 延伸 2：AI 直接读业务数据库 → 生成结果（结果可写入工作区文件）

**两者衔接点**：
- 延伸 2 生成的查询结果可"导出到工作区"，复用本次工作区的文件管理
- 工作区里的 AI 分析也可以"调用延伸 2 的查询能力"补充上下文

**技术栈预估**：
- 数据库 Schema 文档化 + 字段语义标注
- Text-to-SQL 模型 + 安全沙箱（防止破坏性查询）
- 现有 ERP Agent 的 ToolLoopExecutor 可直接复用
- 前端：Notion 风格的 Database View 组件

---

## 十一、V1.x 历史审查记录摘要

V2.0 完全重写了布局和组件，但 V1.x 七轮共修正的 32 项问题大部分仍然适用（除了"drawer 抽屉"相关的）。以下是仍然有效的关键修正：

| 类别 | V1.x 发现的问题 | V2.0 沿用的修正 |
|------|--------------|----------------|
| 后端认证 | 写错 `CurrentUser` | 统一用 `OrgCtx`（已应用 4.4） |
| 文件协议 | `workspace://` 无法 fetch | 改为 CDN URL + workspace_path 字段（已应用 5.4） |
| API 设计 | DELETE 请求体被代理丢弃 | 用 POST /files/workspace/delete（已应用 4.1） |
| API 设计 | upload target_dir 传参 | Form field（已应用 4.2） |
| API 设计 | rename/move 文件名冲突 | 返回 409（已应用 4.5） |
| API 设计 | list 缺 cdn_url + mime_type | 已应用 4.3 |
| 数据流 | "发送到对话"链路未定义 | V2.0 重新定义为"插入到聊天"（已应用 5.1） |
| 类型 | FilePart 缺 workspace_path | 前后端各加（已应用 5.4） |
| 类型 | files 参数类型不含 workspace_path | useTextMessageHandler 扩展（已应用 5.2） |
| 后端处理 | `_extract_file_urls()` 只返回 URL | 新增 `_extract_workspace_files()`（已应用 5.5） |
| 状态一致性 | InputControls hasContent 不含 ws 文件 | 扩展（已应用 5.3） |
| 状态一致性 | InputArea 两处独立 hasFiles 判断 | 都扩展（已应用 5.2） |
| 状态一致性 | FilePreview onRemove 无法区分来源 | `ws_` 前缀分发（已应用 5.3） |
| 重复发送 | 同一文件连点两次 | 按 workspace_path 去重（已应用 5.1） |
| UI 状态 | 首次加载闪空状态 | useWorkspace 加 loading（已应用） |
| 文件管理 | workspaceUpload.ts 处置 | 合并到 workspace.ts 后删除（已应用 6） |
| Bug 修复 | MessageItem 不传 files prop | 修复（已应用 5.6） |

V2.0 在 V1.x 基础上**新增**的修正：
- 布局从 drawer 改为全屏切换 → 重新设计 5.1 数据流
- 适配设计系统 V3 → 全部用 `var(--s-*)` token + ui/ 组件
- 适配重构后的目录结构 → 路径全部更新到 `chat/{layout,input,message,...}`
- WorkspaceView 不放在 chat/ 下，新建 `components/workspace/` 一级目录
- InputArea 切换 view 时 prompt state 是否丢失的问题（Phase 2 实施时决定方案）
