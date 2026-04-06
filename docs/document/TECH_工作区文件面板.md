# 技术方案：工作区文件面板

> 版本：V1.0 | 日期：2026-04-06

## 一、产品定位

每个用户拥有独立的云端文件空间（类似电脑里的"我的文件"），AI 可以像在本地电脑一样读写、整理这个空间。前端提供可视化的文件浏览器面板。

## 二、整体布局

```
┌──────────┬───────────────────────┬──────────────┐
│ 会话列表  │      聊天区域          │  📁 工作区    │
│ Sidebar  │  ChatHeader           │  面包屑导航   │
│ w-64     │  MessageArea          │  文件/文件夹   │
│          │  InputArea            │  上传按钮     │
│          │                       │  w-72        │
└──────────┴───────────────────────┴──────────────┘
```

- **位置**：主内容区右侧，可收起的侧边面板
- **触发**：ChatHeader 右侧新增"文件夹"图标按钮，点击展开/收起
- **宽度**：`w-72`（288px），收起时完全隐藏
- **动画**：`transition-all duration-300` 滑入滑出

## 三、前端设计

### 3.1 面板结构

```
WorkspacePanel
├── 顶部栏
│   ├── "工作区" 标题
│   ├── 存储用量（如 12.3MB / 500MB）
│   └── 关闭按钮 ✕
│
├── 工具栏
│   ├── 面包屑路径导航（. > uploads > reports）
│   ├── 新建文件夹按钮
│   └── 上传按钮（支持多文件）
│
├── 文件列表（可滚动）
│   ├── 📁 文件夹条目（点击进入）
│   │   ├── 文件夹图标 + 名称
│   │   ├── 修改时间
│   │   └── 右键/更多菜单（重命名、删除）
│   │
│   └── 📄 文件条目
│       ├── 类型图标（PDF/CSV/Excel/代码/文本）
│       ├── 文件名 + 大小
│       ├── 修改时间
│       └── 右键/更多菜单（下载、重命名、删除、发送到对话）
│
├── 空状态
│   └── "还没有文件，上传或让 AI 帮你创建"
│
└── 拖拽上传遮罩
    └── "拖放文件到这里上传"
```

### 3.2 交互细节

| 操作 | 行为 |
|------|------|
| 点击文件夹 | 进入子目录，面包屑更新 |
| 点击面包屑 | 跳转到对应层级 |
| 点击文件 | 文本类文件弹出预览（只读），其他类型触发下载 |
| "发送到对话" | 在输入框插入文件引用 `[📎 文件名](uploads/xxx.csv)`，AI 自动用 file_read 读取 |
| 拖拽文件到面板 | 上传到当前目录 |
| 上传按钮 | 选择文件上传到当前目录（支持多选） |
| 新建文件夹 | 弹出输入框，输入名称后创建 |
| 删除 | 二次确认弹框 |
| 重命名 | 行内编辑（类似对话标题双击编辑） |

### 3.3 文件类型图标映射

| 类型 | 扩展名 | 图标颜色 |
|------|--------|---------|
| PDF | .pdf | 红色 |
| Excel | .xls, .xlsx, .csv, .tsv | 绿色 |
| Word | .doc, .docx | 蓝色 |
| PPT | .ppt, .pptx | 橙色 |
| 代码 | .py, .js, .ts, .html, .css, .sql | 紫色 |
| 文本 | .txt, .md, .log, .json, .yaml, .xml | 灰色 |
| 压缩包 | .zip | 黄色 |
| 文件夹 | — | 蓝色文件夹 |

### 3.4 新增前端文件

| 文件 | 职责 |
|------|------|
| `components/chat/WorkspacePanel.tsx` | 面板主组件（文件列表 + 工具栏 + 空状态） |
| `components/chat/WorkspaceFileItem.tsx` | 单个文件/文件夹条目（图标 + 名称 + 操作菜单） |
| `components/chat/WorkspaceBreadcrumb.tsx` | 面包屑路径导航 |
| `hooks/useWorkspace.ts` | 状态管理（当前路径、文件列表、CRUD 操作） |
| `services/workspace.ts` | 后端 API 调用（整合现有 workspaceUpload.ts） |
| `utils/fileIcons.ts` | 文件类型 → 图标/颜色映射 |

### 3.5 修改现有文件

| 文件 | 改动 |
|------|------|
| `pages/Chat.tsx` | 新增 `workspacePanelOpen` 状态，布局加右侧面板 |
| `components/chat/ChatHeader.tsx` | 右侧加"文件夹"图标按钮（FolderOpen） |
| `components/chat/UploadMenu.tsx` | "上传到工作区"改为打开面板并触发上传 |
| `components/chat/InputControls.tsx` | 移除独立的 workspace 上传逻辑，统一走面板 |
| `utils/messageUtils.ts` | 新增 `getFileUrls()` 提取文件附件 |
| `components/chat/MessageItem.tsx` | 渲染消息中的文件附件卡片 |

## 四、后端补全

### 4.1 新增 API

在 `backend/api/routes/file.py` 新增三个端点：

```python
# 1. 删除文件/空目录
DELETE /files/workspace/delete
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
```

### 4.2 安全约束

- 所有路径操作复用 `FileExecutor.resolve_safe_path()` 防穿越
- 删除目录仅允许空目录（非空需先清空）
- 重命名不允许跨目录移动（防止绕过权限）
- 文件大小限制：单文件 50MB，总空间配额化（后续 Phase）

### 4.3 修改现有文件

| 文件 | 改动 |
|------|------|
| `api/routes/file.py` | 新增 delete/mkdir/rename 三个端点 |
| `services/file_executor.py` | 新增 `file_delete()`、`file_mkdir()`、`file_rename()` 方法 |

## 五、AI 感知文件

### 5.1 "发送到对话"功能

用户在面板中点击"发送到对话"时，在输入框插入：

```
[📎 report.csv](uploads/report_a1b2c3.csv)
```

发送消息时，前端将其转为 `FilePart`：

```json
{
  "type": "file",
  "url": "workspace://uploads/report_a1b2c3.csv",
  "name": "report.csv",
  "mime_type": "text/csv",
  "size": 12345
}
```

### 5.2 后端处理 workspace:// 协议

`chat_context_mixin.py` 中识别 `workspace://` 前缀的文件 URL：
- 不作为 `image_url` 传给 LLM（大部分模型不支持直接读 CSV/Excel）
- 改为在用户消息前注入系统提示：

```
用户上传了以下文件到工作区，你可以使用 file_read 工具读取：
- uploads/report_a1b2c3.csv (12.3KB, CSV)
```

AI 就知道路径，能自动调用 `file_read` 读取内容进行分析。

### 5.3 消息中显示文件附件

`MessageItem.tsx` 渲染 `FilePart` 为可点击的小卡片：

```
┌────────────────────┐
│ 📊 report.csv  12KB │
└────────────────────┘
```

用户消息气泡上方展示（与图片预览同区域）。

## 六、文件清单

### 新增文件（6 个）

| # | 文件 | 行数估计 |
|---|------|---------|
| 1 | `frontend/src/components/chat/WorkspacePanel.tsx` | ~250 |
| 2 | `frontend/src/components/chat/WorkspaceFileItem.tsx` | ~120 |
| 3 | `frontend/src/components/chat/WorkspaceBreadcrumb.tsx` | ~60 |
| 4 | `frontend/src/hooks/useWorkspace.ts` | ~150 |
| 5 | `frontend/src/services/workspace.ts` | ~80 |
| 6 | `frontend/src/utils/fileIcons.ts` | ~50 |

### 修改文件（8 个）

| # | 文件 | 改动量 |
|---|------|-------|
| 1 | `frontend/src/pages/Chat.tsx` | ~20 行 |
| 2 | `frontend/src/components/chat/ChatHeader.tsx` | ~15 行 |
| 3 | `frontend/src/components/chat/UploadMenu.tsx` | ~10 行 |
| 4 | `frontend/src/components/chat/InputControls.tsx` | ~10 行（删除独立工作区逻辑） |
| 5 | `frontend/src/utils/messageUtils.ts` | ~10 行 |
| 6 | `frontend/src/components/chat/MessageItem.tsx` | ~30 行 |
| 7 | `backend/api/routes/file.py` | ~80 行 |
| 8 | `backend/services/file_executor.py` | ~60 行 |

## 七、实施阶段

### Phase 1：后端 API 补全（B 级）
- `file_executor.py` 新增 delete/mkdir/rename 方法
- `file.py` 新增 3 个路由端点
- 单元测试

### Phase 2：前端面板 UI（A 级）
- WorkspacePanel + FileItem + Breadcrumb 组件
- useWorkspace Hook（列表/上传/删除/重命名/新建文件夹）
- Chat.tsx 布局改造（右侧面板）
- ChatHeader 加入切换按钮

### Phase 3：AI 感知 + 消息展示
- "发送到对话"功能（workspace:// 协议）
- chat_context_mixin.py 注入文件提示
- MessageItem 渲染文件附件卡片
- getFileUrls() 工具函数

### Phase 4：体验优化（后续）
- 文件预览（文本类在线查看）
- 存储配额管理
- AI 创建文件后面板实时刷新（WebSocket 通知）
- 拖拽排序/批量操作
