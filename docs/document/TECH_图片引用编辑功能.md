# 技术设计：图片引用编辑功能

## 1. 技术栈

- 前端：React + TypeScript + Zustand + TailwindCSS
- 后端：Python 3.x + FastAPI（**无需改动**）
- 数据库：Supabase PostgreSQL（**无需改动**）
- 通信：自定义事件 `chat:quote-image`（复用已有 `chat:scroll-to-bottom` 模式）

---

## 2. 核心架构决策

### 2.1 组件通信：自定义事件

引用动作发生在 `MessageArea → AiImageGrid/AiGeneratedImage`，需通知 `InputArea`。
两者是兄弟组件，无直接 props 通道。

**方案**：复用已有的 `window.dispatchEvent` 自定义事件模式。

```
AiImageGrid (右键引用)
  → window.dispatchEvent(new CustomEvent('chat:quote-image', { detail: { url, messageId } }))

InputArea (监听)
  → window.addEventListener('chat:quote-image', handler)
  → addQuotedImage(url)
  → 触发模型自动切换
```

### 2.2 后端零改动

后端 `ImageHandler._extract_image_urls(content)` 已从 ContentPart[] 中提取所有 `{type:'image', url}` 对象。
引用图片只需作为 ContentPart 加入 content 数组，后端自动处理。`_build_input_params()` 根据模型类型正确传递 `image_urls`。

### 2.3 模型自动切换

现有 `useModelSelection.ts` L121-143 已有逻辑：
- `hasImage && selectedModel.type === 'image' && !imageEditing` → 切 edit 模型

**缺口**：用户在 chat 模型下引用图片时不会触发（`selectedModel.type !== 'image'`）。

**方案**：新增 `hasQuotedImage` 参数传入 `useModelSelection`，当 `hasQuotedImage=true` 时，无论当前模型类型，均切到 `nano-banana-edit`（除非已在 edit/pro 模型上）。

---

## 3. 目录结构

### 新增文件

| 文件 | 职责 |
|------|------|
| `frontend/src/components/chat/ImageContextMenu.tsx` | 图片右键上下文菜单（引用/复制/下载） |
| `frontend/src/components/chat/__tests__/ImageContextMenu.test.tsx` | ImageContextMenu 单测 |
| `frontend/src/components/chat/__tests__/ImagePreview.test.tsx` | ImagePreview 引用标识单测 |
| `frontend/src/hooks/__tests__/useImageUpload.test.ts` | useImageUpload.addQuotedImage 单测 |

### 修改文件

| 文件 | 改动 | 行数影响 |
|------|------|---------|
| `frontend/src/hooks/useImageUpload.ts` | 新增 `addQuotedImage()`、`hasQuotedImage`、扩展 `UploadedImage` 类型 | +25行 |
| `frontend/src/components/chat/ImagePreview.tsx` | 引用图片蓝色角标 + 引号图标 | +15行 |
| `frontend/src/components/chat/AiImageGrid.tsx` | GridCell 添加 `onContextMenu` 事件 | +10行 |
| `frontend/src/components/chat/MessageMedia.tsx` | AiGeneratedImage 添加 `onContextMenu` 事件 | +10行 |
| `frontend/src/components/chat/MessageItem.tsx` | 传递 `onQuoteImage` 回调 | +5行 |
| `frontend/src/components/chat/InputArea.tsx` | 监听 `chat:quote-image` 事件 | +15行 |
| `frontend/src/components/chat/InputControls.tsx` | 有引用图时切换 placeholder | +3行 |
| `frontend/src/hooks/useModelSelection.ts` | 接收 `hasQuotedImage`，新增切换分支 | +10行 |

---

## 4. 数据库设计

**无需变更。** 引用图片通过 `content` 字段的 `ContentPart[]` 传递，与上传图片格式一致。

---

## 5. API 设计

**无需新增接口。** 复用现有 `POST /conversations/{id}/messages/generate`：
- 引用图片作为 `{type: 'image', url: 'cdn_url'}` 加入 `content` 数组
- 后端 `_extract_image_urls(content)` 自动提取
- `_build_input_params()` 根据模型正确传递给 KIE API

---

## 6. 前端类型变更

### 6.1 扩展 UploadedImage（useImageUpload.ts）

```typescript
export interface UploadedImage {
  id: string;
  file: File;
  preview: string;        // ObjectURL 或 CDN URL（引用图）
  url: string | null;     // 服务器 URL
  isUploading: boolean;
  error: string | null;
  isQuoted?: boolean;     // 新增：是否为引用图片
}
```

### 6.2 自定义事件类型

```typescript
// 事件名: 'chat:quote-image'
interface QuoteImageDetail {
  url: string;       // 图片 CDN URL
  messageId: string; // 来源消息 ID（用于溯源，暂不展示）
}
```

---

## 7. 前端状态管理

### 7.1 useImageUpload 新增方法

```typescript
/**
 * 添加引用图片（已有 CDN URL，无需上传）
 * - 如果已有引用图，替换（每次只能引用一张）
 * - 引用图的 file 为空 File 对象（不影响序列化）
 * - isUploading=false, url 直接赋值
 */
addQuotedImage(url: string): void

/**
 * 是否存在引用图片
 */
hasQuotedImage: boolean
```

### 7.2 useModelSelection 参数扩展

```typescript
interface UseModelSelectionParams {
  hasImage: boolean;
  hasQuotedImage: boolean;    // 新增
  conversationId?: string | null;
  conversationModelId?: string | null;
  onAutoSaveModel?: (modelId: string) => void;
}
```

自动切换逻辑新增分支：

```typescript
// 已有逻辑：图片模型 + 有图片 → 切 edit
if (hasImage && selectedModel.type === 'image' && !selectedModel.capabilities.imageEditing) { ... }

// 新增逻辑：任意模型 + 有引用图 → 切 edit（除非已在 edit/pro 模型）
const isEditCapableModel = selectedModel.id === 'google/nano-banana-edit' || selectedModel.id === 'nano-banana-pro';
if (hasQuotedImage && !isEditCapableModel) {
  modelBeforeUpload.current = selectedModel;
  const editModel = ALL_MODELS.find(m => m.id === 'google/nano-banana-edit');
  if (editModel) {
    queueMicrotask(() => switchModel(editModel, true));
  }
}
```

---

## 8. 组件设计

### 8.1 ImageContextMenu（新建）

```typescript
interface ImageContextMenuProps {
  x: number;
  y: number;
  imageUrl: string;
  messageId: string;
  closing?: boolean;
  onClose: () => void;
}
```

菜单项：
- **引用**：dispatch `chat:quote-image` 事件
- **复制**：`navigator.clipboard.write()` 写入图片 blob
- **下载**：复用现有下载逻辑（fetch → blob → download link）

样式：复用现有 `ContextMenu` 的视觉规范（`bg-white dark:bg-gray-800 rounded-lg shadow-lg`）

### 8.2 GridCell 右键扩展（AiImageGrid.tsx）

```typescript
// GridCell 新增 props
interface GridCellProps {
  // ...现有 props
  onQuoteImage?: (imageUrl: string, messageId: string) => void;  // 新增
}

// GridCell render 中添加
onContextMenu={(e) => {
  if (imageUrl && imageLoaded) {
    e.preventDefault();
    // 打开 ImageContextMenu
  }
}}
```

### 8.3 ImagePreview 引用标识（改造）

引用图缩略图与上传图的区别：
- 左上角：蓝色引号图标（`"` 或 Quote SVG icon）
- 边框：`ring-2 ring-blue-400`
- 左下角角标：蓝底白字 "引用"（替代数字序号）
- 删除按钮位置不变（右上角 X）

### 8.4 InputControls placeholder 切换

```typescript
placeholder={
  hasQuotedImage
    ? '描述你想要的修改...'
    : requiresImageUpload
      ? '该模型需要先上传图片才能生成哦～'
      : '发送消息...'
}
```

---

## 9. 交互流程（数据流）

```
1. 用户右键 AI 生成图片
   → GridCell.onContextMenu(e) 打开 ImageContextMenu

2. 点击「引用」
   → ImageContextMenu dispatch('chat:quote-image', {url, messageId})
   → 菜单关闭

3. InputArea 监听事件
   → useImageUpload.addQuotedImage(url)
   → images 数组增加: {id, preview:url, url:url, isUploading:false, isQuoted:true}
   → hasQuotedImage = true

4. 自动切换模型
   → useModelSelection 检测 hasQuotedImage=true
   → 保存当前模型到 modelBeforeUpload.current
   → 切换到 nano-banana-edit

5. 用户输入提示词 + 可选上传更多图片

6. 点击发送
   → InputArea.handleSubmit()
   → uploadedImageUrls = [quotedUrl, ...uploadedUrls]
   → handleImageGeneration(conversationId, prompt, imageUrls)
   → createTextWithImages(prompt, imageUrls)
   → sendMessage({content, model:'google/nano-banana-edit', ...})

7. 后端处理
   → _extract_image_urls(content) 提取所有图片 URL
   → _build_input_params() 传给 NanoBananaEditInput(image_urls=[...])
   → KIE API 返回编辑后的图片

8. 结果展示
   → WebSocket image_partial_update / message_done
   → 新消息在对话流中展示（同普通生成）

9. 清理
   → handleRemoveAllImages() 清空预览区
   → hasQuotedImage=false → 模型自动恢复
```

---

## 10. 开发任务拆分

### 阶段 1：基础设施（无依赖）

- [ ] **任务 1.1**：扩展 `UploadedImage` 类型 + `useImageUpload` 新增 `addQuotedImage()` 和 `hasQuotedImage`
  - 文件：`useImageUpload.ts`
  - 测试：`useImageUpload.test.ts`（新建）

- [ ] **任务 1.2**：创建 `ImageContextMenu` 组件
  - 文件：`ImageContextMenu.tsx`（新建）
  - 测试：`ImageContextMenu.test.tsx`（新建）

### 阶段 2：图片组件集成（依赖 1.2）

- [ ] **任务 2.1**：`AiImageGrid` GridCell 添加右键菜单 + 状态管理
  - 文件：`AiImageGrid.tsx`
  - 测试：更新 `AiImageGrid.test.tsx`

- [ ] **任务 2.2**：`MessageMedia` AiGeneratedImage 添加右键菜单
  - 文件：`MessageMedia.tsx`
  - 测试：更新 `MessageMedia.test.tsx`

### 阶段 3：输入区集成（依赖 1.1）

- [ ] **任务 3.1**：`InputArea` 监听 `chat:quote-image` 事件
  - 文件：`InputArea.tsx`

- [ ] **任务 3.2**：`ImagePreview` 引用图片视觉标识
  - 文件：`ImagePreview.tsx`
  - 测试：`ImagePreview.test.tsx`（新建）

- [ ] **任务 3.3**：`InputControls` placeholder 切换
  - 文件：`InputControls.tsx`

### 阶段 4：模型切换（依赖 1.1）

- [ ] **任务 4.1**：`useModelSelection` 新增 `hasQuotedImage` 参数 + 切换逻辑
  - 文件：`useModelSelection.ts`
  - 需同步修改 `InputArea.tsx` 传参

### 阶段 5：集成测试 + 自查

- [ ] **任务 5.1**：全流程手动测试（引用→预览→发送→结果展示）
- [ ] **任务 5.2**：运行全部前端单测 `cd frontend && npx vitest run`
- [ ] **任务 5.3**：TypeScript 类型检查 `cd frontend && npx tsc --noEmit`
- [ ] **任务 5.4**：所有改动文件 ≤500行自查 + 代码规范检查
- [ ] **任务 5.5**：更新 FUNCTION_INDEX / PROJECT_OVERVIEW

---

## 11. 依赖变更

**无需新增依赖。** 所有功能基于现有技术栈实现。

---

## 12. 风险评估

| 风险 | 严重度 | 缓解措施 |
|------|--------|---------|
| 右键菜单与浏览器默认菜单冲突 | 低 | `e.preventDefault()` 阻止默认菜单 |
| 引用图片 URL 失效 | 极低 | OSS CDN URL 永久有效（已确认） |
| 模型自动切换导致用户困惑 | 低 | 取消引用时自动恢复原模型 |
| `navigator.clipboard.write` 兼容性 | 低 | 降级提示"请右键另存为" |
| 引用图 + 上传图总数超模型限制 | 低 | 复用现有 `maxImages` 校验逻辑 |

---

## 13. 文档更新清单

- [ ] FUNCTION_INDEX.md（新增 `addQuotedImage`、`ImageContextMenu`）
- [ ] PROJECT_OVERVIEW.md（更新组件列表）
