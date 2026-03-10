# UI 设计文档：PDF 文档上传

## 0. 现有 UI 分析

### 可复用组件
- **UploadMenu** — 已有"上传文档"按钮（当前 `disabled`），直接启用即可
- **UploadErrorBar** — 上传错误提示条，PDF 上传错误可复用
- **ImagePreview 模式** — 预览区布局（输入框顶部横向排列），PDF 预览可参考此模式
- **useDragDropUpload** — 拖拽上传 hook，可扩展支持 PDF 文件

### 样式约束
- 圆角：`rounded-lg` / `rounded-2xl`（输入区域）
- 间距：`gap-2`（预览区间距）
- 主色：`blue-600`（按钮激活态）
- 灰色：`gray-200`（边框）、`gray-400`（禁用文字）、`gray-500`（图标）
- 缩略图尺寸：`h-14 w-14`（图片）→ PDF 采用横向卡片（更适合文件名展示）
- 动画：`animate-popupEnter` / `animate-popupExit`（菜单弹出/关闭）

### 布局模式
- 输入区域：`图片/PDF预览 → 输入框 → 底部工具栏`
- 工具栏：`左侧（模型/设置/深度思考）| 右侧（计费/上传/发送或语音）`

### 交互惯例
- 上传中：缩略图半透明 + spinner
- 上传失败：红色边框 + 错误图标
- 删除：右上角小圆圈 × 按钮
- 菜单关闭：带 150ms 退出动画

---

## 1. 页面结构

### 影响页面：聊天页（无新增页面/路由）

修改区域集中在输入区域底部：

```
┌──────────────────────────────────────────┐
│  [PDF 预览卡片] [图片缩略图] [图片缩略图]  │  ← 附件预览区（PDF + 图片混合）
├──────────────────────────────────────────┤
│  输入框：发送消息...                       │
├──────────────────────────────────────────┤
│  [模型▾][⚙][🧠深度思考] ... [💰][📎][➤]  │  ← 工具栏不变
└──────────────────────────────────────────┘
```

点击 📎 弹出上传菜单：
```
┌──────────────────┐
│ 🖼 上传图片       │  ← 已有
│   支持 PNG,JPG    │
├──────────────────┤
│ 📄 上传文档       │  ← 启用！（当模型支持 PDF 时可点击）
│   支持 PDF (≤50MB)│
├──────────────────┤
│ 📷 屏幕截图       │  ← 保持禁用
│   暂不支持        │
└──────────────────┘
```

### 组件层级

```
InputArea
├── UploadErrorBar          ← 复用（PDF 错误也走这里）
├── InputControls
│   ├── AttachmentPreview   ← 新增（统一预览区，含图片+PDF）
│   │   ├── ImagePreview    ← 复用（图片缩略图）
│   │   └── FilePreview     ← 新增（PDF 文件卡片）
│   ├── textarea            ← 不变
│   ├── ToolBar             ← 不变
│   │   └── UploadMenu      ← 修改（启用"上传文档"按钮）
│   └── <input type="file"> ← 新增一个 PDF 专用的隐藏 input
```

---

## 2. 交互流程

### 流程 1：上传 PDF 文件

1. 用户点击 📎 按钮 → 弹出 UploadMenu
2. 点击「上传文档」→ 触发隐藏的 `<input type="file" accept=".pdf">`
3. 选择 PDF 文件
4. **前端校验**：
   - 文件类型：仅 `application/pdf`
   - 文件大小：≤ 模型的 `maxPDFSize`（Gemini 为 50MB）
   - 数量限制：最多 1 个 PDF（MVP）
5. 校验通过 → 显示 PDF 预览卡片（文件名 + 大小 + 上传进度）
6. 调用上传接口 `POST /files/upload` → 返回 CDN URL
7. 上传完成 → 预览卡片状态变为"已就绪"
8. 用户输入文字 + 点击发送 → PDF URL 作为 `FilePart` 发给后端

### 流程 2：拖拽上传 PDF

1. 用户拖拽 PDF 文件到输入区域
2. 显示拖拽提示：「拖放文件到这里」「支持 PNG, JPG, PDF」（更新提示文案）
3. 后续流程同流程 1 的步骤 4-8

### 流程 3：模型不支持 PDF 时

1. 用户选择了不支持 PDF 的模型（如 DeepSeek）
2. UploadMenu 中「上传文档」按钮灰色禁用
3. 提示文字显示：「当前模型不支持」
4. 如果用户已经上传了 PDF，然后切换到不支持的模型：
   - 显示 ConflictAlert 提示：「当前模型不支持 PDF 文档，建议切换到 Gemini 模型」
   - 提供快速切换按钮

### 流程 4：删除 PDF 附件

1. 点击 PDF 预览卡片右上角 × 按钮
2. PDF 卡片移除
3. 清除相关错误状态

---

## 3. 状态设计

### PDF 预览卡片状态

| 状态 | 触发条件 | 显示内容 | 可操作性 |
|-----|---------|---------|---------|
| 上传中 | 选择文件后 | 文件名 + 大小 + spinner | 可删除 |
| 已就绪 | 上传成功 | 文件名 + 大小 + ✓ | 可删除 |
| 上传失败 | 网络/服务器错误 | 文件名 + 红色错误 | 可删除/重试 |

### UploadMenu「上传文档」按钮状态

| 状态 | 条件 | 样式 |
|-----|------|------|
| 可用 | 模型 `pdfInput === true` | 正常样式，可点击 |
| 禁用 | 模型不支持 PDF | 灰色，`cursor-not-allowed`，提示「当前模型不支持」 |
| 已满 | 已上传 1 个 PDF | 灰色，提示「最多上传 1 个文档」 |

---

## 4. 组件清单

| 组件名 | 功能 | 复用/新建 | 文件路径 |
|--------|------|----------|---------|
| UploadMenu | 上传菜单（启用文档按钮） | **修改** | `components/chat/UploadMenu.tsx` |
| FilePreview | PDF 文件预览卡片 | **新建** | `components/chat/FilePreview.tsx` |
| InputControls | 集成 PDF 上传入口 | **修改** | `components/chat/InputControls.tsx` |
| InputArea | 集成 PDF 上传 hook | **修改** | `components/chat/InputArea.tsx` |
| ImagePreview | 图片预览（不变） | 复用 | — |
| UploadErrorBar | 错误提示（不变） | 复用 | — |
| ConflictAlert | 模型冲突提示 | 复用（扩展 PDF 冲突类型） | — |

### 新增 Hook

| Hook | 功能 | 文件路径 |
|------|------|---------|
| useFileUpload | PDF 文件选择、校验、上传、状态管理 | `hooks/useFileUpload.ts` |

---

## 5. FilePreview 组件视觉设计

```
┌─────────────────────────────────┐
│ 📄  report.pdf          [×]    │
│     2.3 MB · 已就绪             │
└─────────────────────────────────┘
```

- 尺寸：高度 `h-14`（与图片缩略图齐平），宽度自适应（`max-w-[200px]`）
- 背景：`bg-gray-50`，边框 `border border-gray-200`，圆角 `rounded-lg`
- 图标：📄 或 SVG 文件图标（红色 PDF 图标）
- 文件名：单行截断 `truncate`
- 大小：灰色小字 `text-xs text-gray-500`
- 上传中：文件名右侧显示 spinner
- 删除按钮：同图片的 `-top-1.5 -right-1.5` 小圆圈样式

---

## 6. 数据流概览

```
用户选择 PDF
    ↓
useFileUpload.handleFileSelect()
  - validateFile(type, size)
  - 创建 UploadedFile 记录
  - 显示预览卡片
    ↓
uploadFile() → POST /files/upload (FormData)
    ↓
返回 CDN URL → 更新状态
    ↓
用户点击发送
    ↓
content: [
  { type: 'text', text: '请分析这份报告' },
  { type: 'file', url: 'https://cdn.../xxx.pdf', name: 'report.pdf', mime_type: 'application/pdf', size: 2400000 }
]
    ↓
POST /conversations/{id}/messages/generate
```

---

**确认后进入技术设计（`@3-dev-doc`）**
