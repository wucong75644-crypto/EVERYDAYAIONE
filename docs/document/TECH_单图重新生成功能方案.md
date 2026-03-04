# 技术设计：单图重新生成 + 回复格式优化 + 失败兜底

## 1. 技术栈

- 前端：React + TypeScript + Zustand + TailwindCSS
- 后端：Python 3.x + FastAPI + Supabase (PostgreSQL)
- 通信：WebSocket（复用现有 `image_partial_update` / `message_done`）
- 无新增依赖

---

## 2. 需求回顾

### 2.1 核心功能：单图重新生成

| # | 需求 | 说明 |
|---|------|------|
| 1 | 悬停显示操作按钮 | 已完成图片悬停 → 底部渐变条「↻ 重新生成」+「↓ 下载」并排 |
| 2 | 单图原位替换 | 点击重新生成 → 该图变脉冲占位符 → 完成后替换 |
| 3 | 其他图片不受影响 | 生成中其余图片的预览、下载、重新生成均可用 |
| 4 | 允许多张同时重新生成 | 并发安全（已验证） |
| 5 | 失败图片悬停重试 | 灰色裂开占位符 → 悬停「重新生成」 |
| 6 | 防抖/禁用 | 点击后 disabled，完成/失败后恢复 |
| 7 | 保留原始 prompt | 从关联用户消息取原始提示词 |
| 8 | 单图费用 | 1 张图的积分 |

### 2.2 回复格式优化

| # | 需求 |
|---|------|
| 9 | AI 回复始终显示引导文字（"好的，我来为你生成 X 张图片"） |
| 10 | 去掉"生成完成"/"生成失败"文字 |

### 2.3 失败兜底

| # | 需求 |
|---|------|
| 11 | 前端超时兜底（2 分钟）→ 灰色裂开占位符 |
| 12 | URL 加载失败 → 灰色裂开占位符（统一视觉） |
| 13 | 失败占位符：灰色底 + 裂开图片图标，无文字无 ⚠️ |
| 14 | 悬停按钮区分：生成失败/超时 →「重新生成」，URL 失败 →「重试加载」 |

---

## 3. 目录结构

### 修改文件

| 文件 | 改动说明 |
|------|---------|
| **后端** | |
| `backend/schemas/message.py` | MessageOperation 枚举新增 `REGENERATE_SINGLE` |
| `backend/api/routes/message.py` | 路由层分发 `REGENERATE_SINGLE` 操作 |
| `backend/api/routes/message_generation_helpers.py` | 新增 `handle_regenerate_single_operation()`；修改占位符文字 |
| `backend/services/handlers/image_handler.py` | 支持指定 `image_index` 创建单任务 |
| `backend/services/batch_completion_service.py` | 新增 `_finalize_single_image()` 合并更新 |
| **前端** | |
| `frontend/src/types/message.ts` | ImagePart 类型扩展 `failed?` / `error?` 字段 |
| `frontend/src/services/messageSender.ts` | 支持 `regenerate_single` 操作 |
| `frontend/src/hooks/useRegenerateHandlers.ts` | 新增 `handleRegenerateSingle()` |
| `frontend/src/components/chat/AiImageGrid.tsx` | GridCell 悬停按钮、失败 UI、超时兜底 |
| `frontend/src/components/chat/MessageItem.tsx` | bubbleTextInfo 改为引导文字、传递 onRegenerateSingle |
| `frontend/src/components/chat/MessageMedia.tsx` | 透传 onRegenerateSingle 到 AiImageGrid |

### 不新增文件

所有改动基于现有文件，无需新建。

---

## 4. 数据库设计

**无 Schema 变更**。

现有 `tasks` 表已有 `batch_id`、`image_index`、`request_params` 字段，足够支撑单图重新生成。
现有 `messages` 表的 `content` 为 JSONB 数组，结构灵活。

---

## 5. API 设计

### 复用现有端点

```
POST /api/conversations/{conversation_id}/messages/generate
```

#### 新增 operation 值：`regenerate_single`

**请求参数变更**：

| 参数 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| operation | string | 是 | `"regenerate_single"` |
| original_message_id | string | 是 | 要重新生成的 AI 消息 ID |
| content | ContentPart[] | 是 | 原始用户消息内容（prompt） |
| generation_type | string | 是 | `"image"` |
| model | string | 否 | 模型 ID（默认取原消息参数） |
| params | object | 是 | 必须包含 `image_index: number` |

**请求示例**：
```json
{
  "operation": "regenerate_single",
  "original_message_id": "msg_abc123",
  "content": [{"type": "text", "text": "一只可爱的猫"}],
  "generation_type": "image",
  "params": {
    "image_index": 2,
    "aspect_ratio": "1:1",
    "num_images": 1
  }
}
```

**响应**：与现有 `regenerate` 操作一致，返回 `task_id`。

---

## 6. 核心流程设计

### 6.1 单图重新生成 - 后端流程

```
前端点击「重新生成」(image_index=2)
    ↓
POST /generate (operation=regenerate_single, image_index=2)
    ↓
[message.py] generate_message()
    ├─ 不创建用户消息
    ├─ 不创建新 AI 消息
    └─ handle_regenerate_single_operation()
        ├─ 验证原消息存在且 status=completed
        ├─ 验证 image_index 在合法范围内
        ├─ 更新 content[2] = {type:"image", url:null}（DB 中标记为进行中）
        └─ 返回 (original_message_id, existing_message)
    ↓
[start_generation_task]
    └─ image_handler.start()
        ├─ 检测 params.image_index → 单图模式
        ├─ num_images 强制为 1
        ├─ 生成新 batch_id
        ├─ _create_single_task(index=2)  ← 使用目标 index，不是 0
        └─ 返回 task_id
    ↓
API/Webhook 回调
    └─ task_completion_service.process_result()
        └─ batch_completion_service.handle_image_complete()
            ├─ 确认积分
            ├─ 推送 image_partial_update (image_index=2)
            └─ 全部终态(1/1) → _finalize_single_image()  ← 新方法
                ├─ 读取现有消息 content 数组
                ├─ 替换 content[2] = new_result
                ├─ 重算消息状态
                ├─ UPDATE message（非 upsert，保留其他字段）
                └─ 推送 message_done
```

### 6.2 关键方法：`_finalize_single_image()`

```python
async def _finalize_single_image(self, batch_id: str, task: dict):
    """单图重新生成的最终处理（合并更新，非全量替换）"""
    message_id = task["placeholder_message_id"]
    image_index = task["image_index"]

    # 1. 读取现有消息（必须从 DB 读，不能用缓存）
    existing = self.db.table("messages") \
        .select("content, generation_params, status") \
        .eq("id", message_id).single().execute()
    content = existing.data["content"]  # list

    # 2. 替换指定 index
    if task["status"] == "completed" and task.get("result_data"):
        content[image_index] = task["result_data"]
    else:
        content[image_index] = {
            "type": "image", "url": None, "failed": True,
            "error": task.get("error_message", "生成失败"),
        }

    # 3. 重算消息状态（有任一成功图片 → completed）
    has_success = any(
        c.get("type") == "image" and c.get("url")
        for c in content
    )
    msg_status = "completed" if has_success else "failed"

    # 4. 更新消息（UPDATE，非 UPSERT）
    self.db.table("messages").update({
        "content": content,
        "status": msg_status,
    }).eq("id", message_id).execute()

    # 5. 推送 message_done
    ws_msg = build_message_done(...)
    await send_to_task_or_user(...)
```

### 6.3 与 `_finalize_batch()` 的区别

| 对比项 | `_finalize_batch` | `_finalize_single_image` |
|--------|-------------------|--------------------------|
| 触发场景 | 初始生成 / 整体重新生成 | 单图重新生成 |
| content 构建 | 从 batch_tasks **全量构建** | 从 DB 读取现有 content **合并替换** |
| 写入方式 | UPSERT (ON CONFLICT id) | UPDATE (仅更新 content+status) |
| generation_params | 重新构建 | 不修改 |

### 6.4 如何区分两种 finalize

在 `handle_image_complete()` / `handle_image_failure()` 中判断：

```python
batch_tasks = self._get_batch_tasks(batch_id)
completed_count, total_count = self._count_terminal(batch_tasks)

if completed_count >= total_count:
    # 判断：单图重新生成 OR 常规批次
    is_single_regen = (
        total_count == 1
        and batch_tasks[0].get("request_params", {}).get("operation") == "regenerate_single"
    )
    if is_single_regen:
        await self._finalize_single_image(batch_id, batch_tasks[0])
    else:
        await self._finalize_batch(batch_id, batch_tasks)
```

---

### 6.5 单图重新生成 - 前端流程

```
用户悬停已完成图片 → 显示「↻ 重新生成」按钮
    ↓
点击按钮
    ↓
GridCell → onRegenerateSingle(imageIndex)
    ↓
AiImageGrid → onRegenerateSingle(imageIndex)
    ↓
MessageMedia → onRegenerateSingle(imageIndex)
    ↓
MessageItem → handleRegenerateSingle(imageIndex)
    ├─ 找到关联的用户消息 (getPreviousUserMessage)
    ├─ 提取原始生成参数 (generation_params)
    └─ 调用 sendMessage({
         operation: 'regenerate_single',
         originalMessageId: message.id,
         content: userMessage.content,
         params: { ...originalParams, image_index: imageIndex }
       })
    ↓
messageSender.ts
    ├─ Phase 1: 乐观更新 → content[imageIndex] = {type:'image', url:null}
    ├─ Phase 1.5: 订阅 WebSocket
    ├─ Phase 2: 调用后端 API
    ├─ Phase 3: 更新 task_id
    └─ Phase 4: 创建任务追踪
    ↓
WebSocket: image_partial_update (image_index=2)
    → content[2] = new_content_part
    → GridCell[2] 从脉冲占位符 → 渐显图片
    ↓
WebSocket: message_done
    → 最终更新消息状态
```

### 6.6 messageSender.ts 改动要点

```typescript
// operation='regenerate_single' 的特殊处理
if (operation === 'regenerate_single') {
  // 复用原消息 ID（和 retry 类似）
  assistantMessageId = originalMessageId!;

  // 乐观更新：仅替换 content[imageIndex]，不清空整个 content
  const imageIndex = params?.image_index as number;
  const existing = messageStore.getMessage(conversationId, assistantMessageId);
  if (existing) {
    const newContent = [...existing.content];
    newContent[imageIndex] = { type: 'image', url: null } as ContentPart;
    messageStore.updateMessage(assistantMessageId, { content: newContent });
  }

  // 不创建占位符消息（消息已存在）
  // 不调用 setIsSending（其他图片仍可操作）
}
```

---

## 7. 前端组件设计

### 7.1 GridCell 改动（AiImageGrid.tsx）

#### 7.1.1 失败占位符统一

**改前**（灰色 + ⚠️ + 文字）：
```tsx
<AlertTriangle className="w-6 h-6 mb-1" />
<span className="text-xs">{error || '生成失败'}</span>
```

**改后**（灰色 + 裂开图片图标，无文字）：
```tsx
// 使用 lucide-react 的 ImageOff 图标
<ImageOff className="w-8 h-8 text-gray-300 dark:text-gray-500" />
```

三种失败场景统一视觉：

| 场景 | 渲染 |
|------|------|
| API 生成失败 (failed=true) | 灰色底 + ImageOff |
| 前端超时 (2min) | 灰色底 + ImageOff |
| URL 加载失败 (3次重试后) | 灰色底 + ImageOff |

#### 7.1.2 悬停按钮

**已完成图片**：底部渐变条显示 `RefreshCw`（重新生成）+ 下载按钮（现有）

```tsx
{/* 已完成图片的操作栏 */}
<div className="absolute bottom-0 left-0 right-0 flex justify-center gap-2 py-1.5
  bg-gradient-to-t from-black/50 to-transparent
  opacity-0 group-hover:opacity-100 transition-opacity">

  {/* 重新生成按钮 */}
  <button onClick={handleRegenerate} disabled={isRegenerating}>
    <RefreshCw className="w-3 h-3" />
  </button>

  {/* 下载按钮（现有） */}
  <button onClick={handleDownload} disabled={isDownloading}>
    <Download className="w-3 h-3" />
  </button>
</div>
```

**失败/超时占位符**：悬停仅显示 `RefreshCw`（重新生成）或「重试加载」

```tsx
{/* 失败占位符的操作栏 */}
{(failed || isTimedOut) && (
  <div className="absolute bottom-0 ... opacity-0 group-hover:opacity-100">
    <button onClick={handleRegenerate}>
      <RefreshCw className="w-3 h-3" />
    </button>
  </div>
)}

{/* URL 加载失败的操作栏 */}
{loadError && (
  <div className="absolute bottom-0 ... opacity-0 group-hover:opacity-100">
    <button onClick={handleRetryLoad}>
      <RefreshCw className="w-3 h-3" />
    </button>
  </div>
)}
```

#### 7.1.3 超时兜底

```tsx
const [isTimedOut, setIsTimedOut] = useState(false);

useEffect(() => {
  // 仅在「等待中」（url=null 且未失败）时启动计时
  if (!imageUrl && !failed) {
    const timer = setTimeout(() => setIsTimedOut(true), 2 * 60 * 1000);
    return () => clearTimeout(timer);
  }
  // url 到达或标记失败 → 重置
  setIsTimedOut(false);
}, [imageUrl, failed]);
```

超时后渲染与 `failed=true` 一致（灰色 + ImageOff），悬停显示「重新生成」。
若超时后 WebSocket 补到成功消息（url 变为非 null），自动恢复显示图片。

#### 7.1.4 GridCell 新增 Props

```typescript
interface GridCellProps {
  // ... 现有 props
  onRegenerateSingle?: (index: number) => void;  // 新增
}
```

### 7.2 MessageItem 改动

#### 7.2.1 bubbleTextInfo 简化

**改前**：
```typescript
if (genType === 'image') {
  if (hasImage) return { text: '生成完成', hasAnimation: false };
  if (message.status === 'pending') return { text: '正在生成图片', hasAnimation: true };
}
```

**改后**：
```typescript
if (genType === 'image') {
  const n = Number(genParams.num_images) || 1;
  const text = n > 1
    ? `好的，我来为你生成 ${n} 张图片`
    : '好的，我来为你生成图片';
  return { text, hasAnimation: false };  // 始终显示，无动画
}
```

引导文字始终存在，不随状态变化。图片占位符自身的脉冲动画已足够表达"生成中"。

#### 7.2.2 透传 onRegenerateSingle

```tsx
// MessageItem → MessageMedia → AiImageGrid → GridCell
<MessageMedia
  onRegenerateSingle={handleRegenerateSingle}
  ...
/>
```

`handleRegenerateSingle` 绑定在 MessageItem 层，因为这里能拿到关联的用户消息：

```typescript
const handleRegenerateSingle = useCallback((imageIndex: number) => {
  const userMsg = getPreviousUserMessage(messages, messageIndex);
  if (userMsg) {
    onRegenerateSingle?.(message, userMsg, imageIndex);
  }
}, [message, messages, messageIndex, onRegenerateSingle]);
```

### 7.3 ImagePart 类型扩展

```typescript
export interface ImagePart {
  type: 'image';
  url: string | null;   // 修改：允许 null（进行中/失败）
  width?: number;
  height?: number;
  alt?: string;
  failed?: boolean;     // 新增：标记失败
  error?: string;       // 新增：失败原因
}
```

消除现有代码中的 `as unknown as` 类型断言。

---

## 8. Props 传递链路

```
ChatView
  └─ onRegenerateSingle={handleRegenerateSingle}    // useRegenerateHandlers 提供
      ↓
MessageList
  └─ onRegenerateSingle={onRegenerateSingle}
      ↓
MessageItem
  └─ handleRegenerateSingle(imageIndex)              // 绑定 message + userMessage
      ↓
MessageMedia
  └─ onRegenerateSingle={onRegenerateSingle}
      ↓
AiImageGrid
  └─ onRegenerateSingle={onRegenerateSingle}
      ↓
GridCell
  └─ onClick → onRegenerateSingle(index)
```

---

## 9. 状态流转

### 9.1 单图重新生成期间的消息状态

| 阶段 | message.status | content[targetIndex] | 其他 content | UI 表现 |
|------|---------------|---------------------|-------------|---------|
| 点击重新生成 | completed | `{type:'image', url:null}` | 不变 | 目标格脉冲，其他格正常 |
| image_partial_update | completed | `{type:'image', url:'...'}` | 不变 | 目标格渐显图片 |
| message_done | completed | 同上 | 不变 | 完成 |
| 单图失败 | completed | `{type:'image', url:null, failed:true}` | 不变 | 目标格灰色裂开 |

**关键**：message.status 始终保持 `completed`（因为其他图片仍然可用）。
仅当全部图片都失败时，status 才变为 `failed`。

### 9.2 isGenerating 不受影响

当前 isGenerating 判断：`message.status === 'pending' && !hasImage`。
单图重新生成时 status='completed'，所以 isGenerating=false，不影响其他图片的交互。

---

## 10. 开发任务拆分

### 阶段 1：后端（API + 任务创建）

- [ ] **1.1** MessageOperation 枚举新增 `REGENERATE_SINGLE`
- [ ] **1.2** `message.py` 路由层新增 `REGENERATE_SINGLE` 分支
- [ ] **1.3** `message_generation_helpers.py` 新增 `handle_regenerate_single_operation()`
  - 验证消息存在 + status=completed
  - 验证 image_index 合法
  - 更新 content[index] 为 null 占位
  - 返回 (message_id, message)
- [ ] **1.4** `image_handler.py` 支持指定 image_index
  - 检测 `params.image_index` → 单图模式
  - num_images=1，使用目标 index 而非循环 index
  - 保存 `operation: "regenerate_single"` 到 `request_params`

### 阶段 2：后端（任务完成处理）

- [ ] **2.1** `batch_completion_service.py` 新增 `_finalize_single_image()`
  - 读取现有 content → 合并替换 → UPDATE
- [ ] **2.2** 修改 `handle_image_complete()` / `handle_image_failure()` 判断分发逻辑
  - 单图重新生成 → `_finalize_single_image()`
  - 其他 → `_finalize_batch()`（不变）

### 阶段 3：前端（类型 + 发送）

- [ ] **3.1** ImagePart 类型扩展（url 允许 null，新增 failed/error）
- [ ] **3.2** `messageSender.ts` 支持 `regenerate_single` 操作
  - 乐观更新：content[index] → null
  - 复用原消息 ID
  - 不创建占位符消息
- [ ] **3.3** `useRegenerateHandlers.ts` 新增 `handleRegenerateSingle()`

### 阶段 4：前端（UI 组件）

- [ ] **4.1** GridCell 失败 UI 改造
  - 移除 AlertTriangle + 文字
  - 替换为 ImageOff 图标（统一视觉）
- [ ] **4.2** GridCell 悬停操作栏
  - 已完成图片：RefreshCw + Download
  - 失败/超时占位符：RefreshCw
  - URL 加载失败：RefreshCw（重试加载）
- [ ] **4.3** GridCell 超时兜底（2 分钟计时器）
- [ ] **4.4** URL 加载失败视觉统一（灰色 + ImageOff）
- [ ] **4.5** MessageItem bubbleTextInfo 改为引导文字
- [ ] **4.6** Props 链路：MessageItem → MessageMedia → AiImageGrid → GridCell

### 阶段 5：集成验证

- [ ] **5.1** 单图重新生成 E2E 测试
- [ ] **5.2** 多图同时重新生成测试
- [ ] **5.3** 超时兜底测试
- [ ] **5.4** URL 加载失败测试
- [ ] **5.5** 更新文档

### 依赖关系

```
阶段1 → 阶段2 → 阶段3 → 阶段4 → 阶段5
       （后端先行，前端依赖后端 API）
```

---

## 11. 依赖变更

无需新增依赖。`ImageOff`、`RefreshCw` 已包含在项目现有的 `lucide-react` 中。

---

## 12. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| `_finalize_single_image` 读写 content 非原子 | 中 | 单图重新生成创建独立 batch_id，不与其他操作共享；消息级更新无并发（同一消息同一时刻只有一个 finalize） |
| 超时兜底误判（网络慢但未真正失败） | 低 | 若超时后 WS 补到成功消息，imageUrl 变非 null → 自动恢复显示图片 |
| `_finalize_batch` 多次调用（现有问题） | 低 | 现有 upsert 幂等保护已足够；可追加消息状态检查作为防御 |
| ImagePart 类型变更影响现有代码 | 低 | `url: string \| null` 向后兼容；`failed?` / `error?` 为可选字段 |
| Props 链路过深 (5层) | 低 | 可考虑 Context 优化，但当前层数可控，暂不过度设计 |

---

## 13. 文档更新清单

- [ ] FUNCTION_INDEX.md（新增 handleRegenerateSingle、_finalize_single_image）
- [ ] TECH_多图生成功能方案.md（Phase 5 标记为已实现）
- [ ] CURRENT_ISSUES.md（标记完成）
