# 多图生成功能 - 技术实施方案

## Context

当前系统每次图片生成请求只生成 1 张图片。用户希望像豆包/Midjourney 一样，支持一次生成多张变体图片（1/2/3/4 张），以 2×2 网格等布局展示，并支持单张图片悬浮重新生成。

KIE API 不支持单次请求生成多图（Nano Banana 系列），需要发起 N 个独立 `createTask` 调用。KIE 支持 20 req/10s 提交频率和 100+ 并发任务，4 张并行无压力。

---

## 已确认需求

| 项目 | 决定 |
|------|------|
| 数量选项 | 1 / 2 / 3 / 4 张 |
| 模型范围 | 所有图片模型（含 edit） |
| 生成方式 | 并行发起 N 个 API 请求 |
| 展示布局 | 1 张: 当前单图 / 2 张: 横排 / 3 张: 横排 / 4 张: 2×2 网格 |
| 部分失败 | 成功的正常显示，失败的灰色占位符 |
| 积分策略 | 积分不够的数量选项禁用（灰色不可点） |
| 单图重新生成 | 悬浮图片底部显示重新生成按钮，只替换这一张 |
| 全部重新生成 | 底部操作栏按钮，按原始数量重新生成全部 |
| 交互方式 | 纯展示 + 点击放大 |

---

## 核心架构决策

### N tasks → 1 message 模型（统一路径）

```
用户发送 num_images=N（1/2/3/4，默认 1）
    ↓
Backend 创建 1 个 assistant message (pending)
    ↓
Backend 发起 N 个 KIE createTask 调用（N=1 也走同一逻辑）
    ↓
创建 N 条 task 记录（共享同一 placeholder_message_id + batch_id）
    ↓
每个 task 独立完成/失败（webhook/polling）
    ↓
每完成 1 个 → push image_partial_update → 前端显示该图
    ↓
全部终态 → _finalize_batch → upsert 完整 message → push message_done
```

**统一路径**：单图（num_images=1）当作 batch_size=1 的批次处理，不做 if/else 分流。
- 一套代码维护，不存在"单图走旧逻辑、多图走新逻辑"的分支
- num_images=1 时 batch 只有 1 个 task，`_finalize_batch` 立即完成，效果等同于原来
- `image_partial_update` 和 `message_done` 几乎同时发出，前端无感知差异

**为什么 N tasks 而不是 1 task N API calls**：
- 每个 task 有独立的 `credit_transaction_id`，支持单独退款
- 每个 task 有独立的 `external_task_id`，webhook/polling 自然适配
- 部分失败处理天然支持

---

## Phase 1: 数据库迁移

### 新增字段（tasks 表）

```sql
-- 多图批次支持
ALTER TABLE tasks ADD COLUMN image_index INTEGER DEFAULT NULL;
ALTER TABLE tasks ADD COLUMN batch_id TEXT DEFAULT NULL;
ALTER TABLE tasks ADD COLUMN result_data JSONB DEFAULT NULL;

-- 索引：按 batch_id 查询批次内所有 task
CREATE INDEX idx_tasks_batch_id ON tasks(batch_id) WHERE batch_id IS NOT NULL;
```

| 字段 | 用途 |
|------|------|
| `image_index` | 图片在网格中的位置 (0,1,2,3)。单图时 = 0，多图时 = 0~3。NULL = 非图片任务（chat/video） |
| `batch_id` | UUID，同一批次的所有 task 共享。单图也有 batch_id（batch_size=1）。NULL = 非图片任务 |
| `result_data` | 存储单个 task 的生成结果（ImagePart dict），用于批次最终合并 |

### messages.generation_params 扩展（无需迁移）

JSONB 字段，新增 `num_images` 键：
```json
{"type": "image", "model": "nano-banana-pro", "num_images": 4, "aspect_ratio": "1:1"}
```

**文件**: 新建 `backend/migrations/add_multi_image_support.sql`

---

## Phase 2: Backend 核心改造

### 2.1 ImageHandler.start() 统一路径

**文件**: `backend/services/handlers/image_handler.py`

改造 `start()` 方法 — **不区分单图/多图**，统一走批次逻辑：

```python
async def start(self, message_id, conversation_id, user_id, content, params, metadata) -> str:
    num_images = max(1, min(4, params.get("num_images", 1)))

    # 1. 计算总积分并一次性校验余额
    cost_result = calculate_image_cost(model_name=model_id, image_count=num_images, resolution=resolution)
    total_credits = cost_result["user_credits"]
    per_image_credits = total_credits // num_images
    self._check_balance(user_id, total_credits)

    # 2. 统一批次逻辑（单图 = batch_size=1）
    batch_id = str(uuid.uuid4())
    tasks_created = []
    adapter = create_image_adapter(model_id)

    try:
        for i in range(num_images):
            transaction_id = self._lock_credits(...)
            try:
                if i > 0:
                    await asyncio.sleep(0.3)  # 300ms 间隔，尊重 KIE 频率限制
                result = await adapter.generate(**generate_kwargs)
                external_task_id = result.task_id
            except Exception as e:
                self._refund_credits(transaction_id)
                continue  # 跳过失败的，继续下一张

            self._save_task(
                ...,
                image_index=i,
                batch_id=batch_id,
            )
            tasks_created.append(external_task_id)
    finally:
        await adapter.close()

    if not tasks_created:
        raise Exception("所有图片生成请求均失败")

    return metadata.client_task_id or tasks_created[0]
```

**关键点**：num_images=1 时也生成 batch_id，也设 image_index=0。后续 TaskCompletionService 无需判断是否为批次。

### 2.2 _build_task_data() 扩展

**文件**: `backend/services/handlers/base.py`

`_build_task_data()` 新增 `image_index` 和 `batch_id` 可选参数：

```python
def _build_task_data(self, ..., image_index=None, batch_id=None) -> Dict:
    task_data = { ... }
    if image_index is not None:
        task_data["image_index"] = image_index
    if batch_id:
        task_data["batch_id"] = batch_id
    return task_data
```

### 2.3 _save_task() 传递新参数

**文件**: `backend/services/handlers/image_handler.py`

`_save_task()` 签名新增 `image_index` 和 `batch_id`，转发给 `_build_task_data()`。

### 2.4 TaskCompletionService 统一批次处理

**文件**: `backend/services/task_completion_service.py`

图片任务的 `_handle_success()` 统一走批次路径（不分流）：

```python
async def _handle_success(self, task, result):
    # ... 现有 OSS 上传逻辑不变 ...
    content_parts = self._build_content_parts(oss_urls, task_type, task)

    if task_type == "image" and task.get("batch_id"):
        # 图片任务：统一走批次处理（含 num_images=1）
        return await self._handle_batch_image_complete(task, content_parts)
    else:
        # video / chat：现有逻辑不变
        handler = self._create_handler(task_type)
        await handler.on_complete(task_id=external_task_id, result=content_parts)
        return True
```

**注意**：分流依据是 `task_type == "image"`，不是 batch_size。所有图片 task 都有 batch_id（包括单图），所以图片统一走新路径。video/chat 不受影响。

新增 `_handle_batch_image_complete()`：
1. 确认该 task 的积分（复用 ImageHandler._handle_credits_on_complete）
2. 将 `result_data` 存入 task 行
3. 更新 task 状态为 completed
4. 查询同 batch_id 所有 tasks 获取进度
5. Push `image_partial_update` WebSocket 事件
6. 如果所有 task 都是终态 → 调用 `_finalize_batch()`

新增 `_finalize_batch()`：
1. 按 image_index 排序所有 batch tasks
2. 构建完整 content 数组（成功的用 result_data，失败的标记 `failed: true`）
3. 汇总 credits
4. Upsert message（status=completed，content=完整数组）
5. Push `message_done` WebSocket 事件

`_handle_failure()` 同理：
- 图片任务（有 batch_id）：退积分 → 标记 task failed → 检查是否全部终态 → 可能 finalize
- 其他任务：现有逻辑不变

### 2.5 新增 WebSocket 事件

**文件**: `backend/schemas/websocket.py`

新增 `image_partial_update` 事件构建函数：

```python
def build_image_partial_update(
    task_id, conversation_id, message_id,
    image_index, content_part, completed_count, total_count, error=None
) -> Dict:
    return {
        "type": "image_partial_update",
        "task_id": task_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "payload": {
            "image_index": image_index,
            "content_part": content_part,  # {type, url, width, height} 或 None
            "completed_count": completed_count,
            "total_count": total_count,
            "error": error,  # 失败时的错误信息
        },
        "timestamp": int(time.time() * 1000),
    }
```

### 2.6 generation_params 保存 num_images

**文件**: `backend/api/routes/message_generation_helpers.py`

在创建 `generation_params` 时，合并 params 中的 `num_images`：

```python
generation_params = {"type": gen_type.value}
if gen_type == GenerationType.IMAGE and params:
    for key in ["num_images", "aspect_ratio", "resolution", "output_format"]:
        if key in params:
            generation_params[key] = params[key]
```

### 2.7 单图重新生成 (regenerate_single)

**文件**: `backend/schemas/message.py` — 新增 `REGENERATE_SINGLE` 操作类型

**文件**: `backend/api/routes/message.py` / `message_generation_helpers.py`

处理 `operation="regenerate_single"` + `image_index` 参数：
1. 获取原始 message 的 prompt 和 generation_params
2. 创建 1 个新 task（指定 image_index, batch_id 复用原 batch 或新建）
3. 该 task 完成后，替换 message content 中对应 index 的图片
4. Push `image_partial_update` + `message_done`

---

## Phase 3: Frontend 设置与发送

### 3.1 settingsStorage 新增 numImages

**文件**: `frontend/src/utils/settingsStorage.ts`

```typescript
image: {
  aspectRatio: AspectRatio;
  resolution: ImageResolution;
  outputFormat: ImageOutputFormat;
  numImages: 1 | 2 | 3 | 4;  // 新增
}
// 默认值: numImages: 1
```

### 3.2 models.ts 新增类型

**文件**: `frontend/src/constants/models.ts`

```typescript
export type ImageCount = 1 | 2 | 3 | 4;
export const IMAGE_COUNTS: { value: ImageCount; label: string }[] = [
  { value: 1, label: '1张' },
  { value: 2, label: '2张' },
  { value: 3, label: '3张' },
  { value: 4, label: '4张' },
];
```

### 3.3 AdvancedSettingsMenu 新增数量选择器

**文件**: `frontend/src/components/chat/AdvancedSettingsMenu.tsx`

在图片设置区域（比例选择之后、费用显示之前）新增"生成数量"选择器：
- 4 个按钮（1/2/3/4），每个显示总积分
- 积分不够的按钮 `disabled` + 灰色样式
- 新增 props: `numImages`, `onNumImagesChange`, `userCredits`（从 useAuthStore 获取）

费用显示区域更新为 `perImageCredits × numImages`。

### 3.4 useSettingsManager 传递 numImages

**文件**: `frontend/src/hooks/useSettingsManager.ts`

新增 `numImages` 的 get/set。

### 3.5 useMediaMessageHandler 传参

**文件**: `frontend/src/hooks/handlers/useMediaMessageHandler.ts`

```typescript
if (type === 'image') {
  mediaParams.num_images = numImages ?? 1;  // 新增
}
```

### 3.6 messageSender 保留 num_images

**文件**: `frontend/src/services/messageSender.ts`

`extractGenerationParams()` 中新增：
```typescript
if (gp.num_images) params.num_images = gp.num_images;
```
确保全部重新生成时保留原始数量。

---

## Phase 4: Frontend 多图展示

### 4.1 新建 AiImageGrid 组件

**新文件**: `frontend/src/components/chat/AiImageGrid.tsx`

核心组件，负责多图网格展示：

```tsx
// Grid layout via CSS Grid
// data-count="1" → 无grid
// data-count="2" → grid-cols-2
// data-count="3" → grid-cols-3
// data-count="4" → grid-cols-2 (2×2)

// 每个 cell 复用 AiGeneratedImage 逻辑 + 悬浮重新生成按钮
// 失败的 cell 显示灰色占位 + 错误文字
```

**子组件 AiGridCell**:
- 正常图片: 复用 AiGeneratedImage 的加载/淡入/下载逻辑
- 失败占位: 灰色背景 + 错误图标 + "生成失败" 文字
- 悬浮层: `opacity-0 group-hover:opacity-100` 显示重新生成按钮

### 4.2 MessageMedia 分流

**文件**: `frontend/src/components/chat/MessageMedia.tsx`

```typescript
// AI 图片分支:
const numImages = genParams?.num_images || 1;
if (numImages > 1) {
  return <AiImageGrid ... />;
} else {
  return <AiGeneratedImage ... />;  // 现有逻辑
}
```

新增 props: `numImages`, `failedIndexes`, `onRegenerateSingle`。

### 4.3 WebSocket 处理 image_partial_update

**文件**: `frontend/src/contexts/wsMessageHandlers.ts`

新增 handler：

```typescript
image_partial_update: (msg) => {
  const { message_id } = msg;
  const { image_index, content_part, completed_count, total_count, error } = msg.payload;

  const store = deps.getStore();
  const existing = store.getMessage(message_id);
  if (!existing) return;

  // 克隆 content 数组，在对应 index 插入/替换
  const content = [...(existing.content || [])];
  while (content.length <= image_index) {
    content.push({ type: 'image', url: null });
  }

  if (error) {
    content[image_index] = { type: 'image', url: null, failed: true, error };
  } else if (content_part) {
    content[image_index] = content_part;
  }

  store.updateMessage(message_id, { content });
}
```

### 4.4 MessageItem 适配

**文件**: `frontend/src/components/chat/MessageItem.tsx`

- 从 `generation_params.num_images` 获取预期图片数
- 生成中显示 N 个占位符（传给 AiImageGrid）
- 进度文案: "正在生成图片 (2/4)..."

### 4.5 多图占位符

**文件**: `frontend/src/components/chat/MediaPlaceholder.tsx`

复用现有组件，AiImageGrid 为每个 pending cell 独立渲染 MediaPlaceholder。

---

## Phase 5: 重新生成

### 5.1 单图重新生成（悬浮按钮）

**文件**: `frontend/src/hooks/useRegenerateHandlers.ts`

新增 `handleRegenerateSingle(targetMessage, imageIndex)`:
- operation: `'regenerate_single'`
- params 带 `image_index`
- 前端立即将该 index 图片替换为占位符
- 不创建新的用户消息

### 5.2 全部重新生成（操作栏按钮）

现有 `handleRegenerate()` 无需改动 — `extractGenerationParams()` 已经会提取 `num_images`，传给后端后自然走多图生成流程。

---

## 受影响文件清单

| 文件 | 类型 | 改动 |
|------|------|------|
| `backend/migrations/add_multi_image_support.sql` | 新建 | tasks 表新增 3 列 + 索引 |
| `backend/services/handlers/image_handler.py` | 修改 | start() 支持 N 张并行生成 |
| `backend/services/handlers/base.py` | 修改 | _build_task_data() 新增参数 |
| `backend/services/task_completion_service.py` | 修改 | 批次完成逻辑 + _finalize_batch |
| `backend/schemas/websocket.py` | 修改 | 新增 image_partial_update 事件 |
| `backend/schemas/message.py` | 修改 | 新增 REGENERATE_SINGLE 操作 |
| `backend/api/routes/message_generation_helpers.py` | 修改 | generation_params 保存 num_images |
| `backend/api/routes/message.py` | 修改 | 处理 regenerate_single |
| `frontend/src/utils/settingsStorage.ts` | 修改 | 新增 numImages |
| `frontend/src/constants/models.ts` | 修改 | 新增 ImageCount 类型 |
| `frontend/src/components/chat/AdvancedSettingsMenu.tsx` | 修改 | 生成数量选择器 |
| `frontend/src/components/chat/AiImageGrid.tsx` | 新建 | 多图网格组件 |
| `frontend/src/components/chat/MessageMedia.tsx` | 修改 | 分流单图/多图 |
| `frontend/src/components/chat/MessageItem.tsx` | 修改 | 多图占位符 + 进度 |
| `frontend/src/contexts/wsMessageHandlers.ts` | 修改 | image_partial_update handler |
| `frontend/src/hooks/handlers/useMediaMessageHandler.ts` | 修改 | 传递 num_images |
| `frontend/src/hooks/useSettingsManager.ts` | 修改 | numImages get/set |
| `frontend/src/hooks/useRegenerateHandlers.ts` | 修改 | handleRegenerateSingle |
| `frontend/src/services/messageSender.ts` | 修改 | extractGenerationParams 提取 num_images |

---

## 边界场景处理

| 场景 | 处理方式 |
|------|---------|
| 4 张全部 API 调用失败 | start() 中 `tasks_created` 为空 → 抛异常 → 前端显示错误 |
| 2 成功 2 失败 | 成功的确认积分 + 显示图片，失败的退积分 + 灰色占位符 |
| 页面刷新时部分完成 | 已完成的 task 的 result_data 在 DB 中，刷新后从 message content 恢复 |
| Webhook + Polling 并发 | 已有 version 字段乐观锁，每个 task 独立锁定 |
| 积分恰好够 3 张但选了 4 | 前端禁用 4 张按钮；后端也有 _check_balance 兜底 |
| KIE 429 频率限制 | 300ms 间隔 + tenacity 重试已有覆盖 |
| 单图重新生成期间其他图正在生成 | 各 task 独立处理，互不影响 |

---

## 验证步骤

### 后端验证
1. 发送 `num_images=1` 请求 → 验证 DB 中创建 1 条 task（有 batch_id + image_index=0）+ 1 条 message → `image_partial_update` + `message_done` 正常
2. 发送 `num_images=4` 请求 → 验证 DB 中创建 4 条 task（同 batch_id）+ 1 条 message
3. Mock 4 个 KIE 回调 → 验证逐个 `image_partial_update` 事件 + 最终 `message_done`
4. Mock 2 成功 2 失败 → 验证积分退款 + 部分内容

### 前端验证
1. 打开高级设置 → 验证 1/2/3/4 按钮显示正确积分
2. 积分为 10、单价 5 → 验证 3/4 按钮灰色禁用
3. 生成 4 张 → 验证 2×2 网格 + 逐张显示
4. 悬浮单张图片 → 验证重新生成按钮出现
5. 点击单图重新生成 → 验证仅该位置变为占位符并替换
6. 点击全部重新生成 → 验证 4 张全部重新生成
7. 页面刷新 → 验证已完成的多图消息正确展示

### 兼容性验证
1. 历史单图消息 → 渲染不受影响
2. `generation_params` 无 `num_images` 的消息 → 默认按 1 处理

---

## 风险审计报告

### 一、代码规则违规（必须修复）

#### R1. `task_completion_service.py` 超 500 行限制（严重）

**现状**：475 行。新增 `_handle_batch_image_complete()` + `_finalize_batch()` + `_handle_batch_failure()` 约 +120 行 → **~595 行，超限**。

**修复方案**：提取批次逻辑到独立模块 `backend/services/batch_completion_service.py`。
- `BatchCompletionService` 类：`handle_image_complete()`, `handle_image_failure()`, `_finalize_batch()`
- `TaskCompletionService._handle_success/failure()` 仅做分流：图片 → BatchCompletionService，其他 → 原逻辑
- 两个文件各 ~350 行，符合规则

**受影响文件清单更新**：新增 `backend/services/batch_completion_service.py`（新建）

#### R2. `start()` 循环嵌套深度达 4 层（边界）

```
async def start():           # L1
  for i in range(num_images): # L2
    try:                       # L3 (lock_credits)
      try:                     # L4 (adapter.generate)
```

**修复方案**：提取内层逻辑为 `_create_single_task()` 辅助方法：
```python
async def _create_single_task(self, adapter, index, ...) -> Optional[str]:
    """创建单个图片生成任务，返回 external_task_id 或 None"""
```
`start()` 循环体缩减为 1 层调用，总嵌套 = 2 层。

---

### 二、现有 Bug / 必须同步修复

#### R3. `getImageUrls()` 不过滤 null URL（严重）

**位置**：`frontend/src/utils/messageUtils.ts:38-44`

```ts
return message.content
  .filter((p): p is ImagePart => p.type === 'image')
  .map((p) => p.url);  // ⚠️ url 可能是 null
```

**影响**：`image_partial_update` handler 在填充空槽位时设置 `{ type: 'image', url: null }`。`getImageUrls()` 会返回 `[null, "https://..."]`，导致 `imageUrls.length > 0` 为 true 但渲染传入 null URL → 图片组件异常。

**修复**：
```ts
.filter((p): p is ImagePart => p.type === 'image' && !!p.url)
```

#### R4. `extractGenerationParams()` 缺少 `num_images`（严重）

**位置**：`frontend/src/services/messageSender.ts:417-437`

**影响**：全部重新生成（regenerate）时从原消息提取参数，但 `num_images` 不在白名单中 → 重新生成丢失数量，退化为 1 张。

**修复**：在图片参数区块添加：
```ts
if (gp.num_images) params.num_images = gp.num_images;
```

---

### 三、功能安全评估

#### R5. 任务恢复（Task Restoration）— 安全，需小改

**位置**：`frontend/src/utils/taskRestoration.ts`

**分析**：
- `restoreMediaTask()` 会被调用 N 次（N 个 batch tasks 共享同一 `placeholder_message_id`）
- `addMessage()` 内部有 ID 去重 → **不会创建重复占位符** ✓
- `markForceRefresh()` 多次调用幂等 ✓
- `subscribeRestoredTasks()` 对每个 task 用 `client_task_id` 订阅 → 批次中 N 个 task 会各自被订阅 ✓

**需要修改**：占位符的 `generation_params`（第 150-154 行）当前只设 `type` + `model`，需要从 `task.request_params` 中提取 `num_images`：
```ts
generation_params: {
  type: task.type,
  model: task.request_params?.model,
  num_images: task.request_params?.num_images,  // 新增
},
```
**如果不修复**：刷新后多图消息的占位符不知道要显示几个格子，退化为单图占位符。

#### R6. 积分锁定循环安全 — 安全

**分析**：
- `_lock_credits()` 使用乐观锁（`credit_mixin.py:62-130`），1 次重试
- 循环中按序调用（i=0 → lock → API → save, i=1 → lock → API → save）
- 如果 lock 成功但 API 失败 → 立即 `_refund_credits()` → `continue`
- 如果 lock 失败（余额不足） → 该次循环异常 → 已创建的 task 正常运行
- **部分成功场景安全**：每个 task 独立 `transaction_id`，不存在级联退款问题 ✓

#### R7. WebSocket 事件推送路由 — 需注意

**问题**：`image_partial_update` 由 TaskCompletionService 发出时，用哪个 task_id 推送？

- 前端用 `client_task_id` 订阅（1 个订阅）
- 但批次有 N 个 task，每个有不同的 `external_task_id`
- `_handle_complete_common()` 中查询 `client_task_id = task.get("client_task_id")`

**方案**：BatchCompletionService 推送时统一用 `client_task_id`（所有 batch task 共享同一个）。如果 `client_task_id` 为空则 fallback 到 `send_to_user(user_id, msg)`。

#### R8. `_finalize_batch()` 并发保护 — 需处理

**场景**：2 个 task 几乎同时完成 → 都检测到"全部终态" → 都调用 `_finalize_batch()`。

**方案**：`_finalize_batch()` 内部使用 `messages` 表的 upsert（ON CONFLICT id）天然幂等。额外用 batch_id + 乐观锁（类似 tasks.version 模式）确保只有一个进程执行最终汇总。

实现：在第一个进入 finalize 的进程中，先尝试 update message status 为 completing（中间态），成功才继续，失败说明已有其他进程在处理。

#### R9. 单图重新生成 — 需处理旧任务

**场景**：用户对 index=2 点击重新生成，但 index=2 的旧 task 仍在 running。

**方案**：
1. 前端立即将 index=2 替换为占位符（视觉反馈）
2. 后端 `regenerate_single` 创建新 task（新 batch_id 或标记替代关系）
3. 旧 task 完成时检查是否已被替代 → 如果已替代则跳过更新 message content
4. 旧 task 的积分已锁定，完成时正常确认/退回（不影响新 task 积分）

**简化方案**：由于旧 task 完成是毫秒级操作，且 `_finalize_batch()` 取最新 result_data → 新 task 覆盖旧 task 的 result_data 即可，最终 finalize 用新结果。

---

### 四、前端渲染安全

#### R10. Zustand `updateMessage()` 高频调用 — 安全

**分析**：4 张图片几乎同时完成 → 4 次 `image_partial_update` → 4 次 `updateMessage()`。Zustand 使用 immutable update pattern，每次创建新引用，React 会合并渲染。

**结论**：安全，无性能问题 ✓

#### R11. `ImagePreviewModal` 多图兼容 — 安全

**分析**：`MessageArea.tsx:197` 使用 `getImageUrls()` 收集所有图片 URL 用于 lightbox。修复 R3 后，null URL 被过滤，lightbox 只显示已加载的图片。

#### R12. 历史消息兼容 — 安全

**分析**：
- 历史消息 `generation_params` 无 `num_images` 字段
- 前端用 `genParams?.num_images || 1` 默认为 1
- 后端 `params.get("num_images", 1)` 默认为 1
- **不影响现有功能** ✓

---

### 五、后端路径绕过安全

#### R13. `_handle_complete_common()` 被绕过（关键）

**问题**：原来图片完成走 `ImageHandler.on_complete()` → `_handle_complete_common()`，这个方法包含：
1. 幂等性检查
2. 积分确认
3. Content 转换
4. Message upsert
5. WebSocket 推送（`message_done`）
6. Task 状态更新
7. 对话预览更新

新方案中，图片走 `BatchCompletionService._handle_batch_image_complete()` → 直接处理。**必须确保新路径复用/覆盖以上全部 7 个步骤**。

**修复方案**：
- 步骤 1（幂等）：TaskCompletionService 已有乐观锁 ✓
- 步骤 2（积分）：复用 `ImageHandler._handle_credits_on_complete()` ✓
- 步骤 3（转换）：TaskCompletionService 已在 `_build_content_parts()` 中处理 ✓
- 步骤 4（Message upsert）：`_finalize_batch()` 中执行 ✓
- 步骤 5（WebSocket）：`_finalize_batch()` 推送 `message_done` ✓
- 步骤 6（Task 状态）：每个 task 单独更新 ✓
- 步骤 7（对话预览）：`_finalize_batch()` 中更新 ✓

逐项覆盖，无遗漏。

---

### 六、修复优先级总结

| ID | 风险 | 严重度 | 修复阶段 |
|----|------|--------|---------|
| R1 | task_completion_service.py 超 500 行 | 🔴 严重 | Phase 2（提取 BatchCompletionService） |
| R2 | start() 嵌套 4 层 | 🟡 中等 | Phase 2（提取 _create_single_task） |
| R3 | getImageUrls() null URL | 🔴 严重 | Phase 4（与 MessageMedia 同步改） |
| R4 | extractGenerationParams 缺 num_images | 🔴 严重 | Phase 3（与 messageSender 同步改） |
| R5 | 任务恢复缺 num_images | 🟡 中等 | Phase 4（taskRestoration 小改） |
| R7 | WS 推送路由 | 🟡 中等 | Phase 2（BatchCompletionService 内处理） |
| R8 | finalize 并发 | 🟡 中等 | Phase 2（乐观锁 / upsert 幂等） |
| R9 | 单图重新生成旧任务 | 🟢 低 | Phase 5（result_data 覆盖策略） |
| R13 | 完成路径 7 步覆盖 | 🔴 严重 | Phase 2（逐项验证清单） |

---

### 七、增量测试计划（每 Phase 必测）

| Phase | 完成后测试内容 |
|-------|--------------|
| Phase 1 | SQL 迁移执行成功，现有查询不受影响，`tasks` 表 CRUD 正常 |
| Phase 2 | `num_images=1` 走统一路径 → 结果与现有单图完全一致（回归测试）；`num_images=4` → DB 创建 4 条 task + WebSocket 事件正确 |
| Phase 3 | 高级设置显示数量选择器 → 参数正确传递到后端 → `generation_params` 含 `num_images` |
| Phase 4 | 多图网格正确渲染 → `image_partial_update` 逐张显示 → 页面刷新后多图恢复 → 历史单图消息不受影响 |
| Phase 5 | 单图重新生成 → 仅替换目标位置 → 全部重新生成保留数量 |

**每个 Phase 的回归检查**：
- 现有单图生成端到端正常
- 积分扣除/退回正确
- WebSocket 推送无遗漏
- 页面刷新后状态恢复
