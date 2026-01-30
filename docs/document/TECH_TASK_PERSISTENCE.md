# 技术方案：任务持久化和跨会话恢复

## 背景

当前问题：
1. 用户刷新页面后，正在生成的图片/视频任务轮询丢失
2. 用户退出重新登录后，无法恢复未完成的任务
3. 浏览器崩溃后，任务生成中断，结果无法显示
4. 任务信息未持久化，无法追踪任务状态

目标：实现任务在页面刷新、账号退出重新登录、浏览器崩溃等场景下的持久化和自动恢复。

## 系统需求

### 功能需求
- **FR1**: 任务数据持久化到数据库（task_id、请求参数、状态、结果）
- **FR2**: 页面刷新后自动恢复进行中的任务轮询
- **FR3**: 退出重新登录后恢复所有未完成任务
- **FR4**: 用户离线时后台继续轮询KIE，任务完成后自动保存结果
- **FR5**: 多标签页打开时避免重复轮询同一任务
- **FR6**: 任务超时后自动标记为失败

### 非功能需求
- **NFR1**: 数据库查询响应时间 < 50ms
- **NFR2**: 前端恢复20个任务时页面响应时间 < 2秒
- **NFR3**: 后台轮询间隔30秒，避免频繁数据库访问
- **NFR4**: 支持水平扩展（无单点故障）
- **NFR5**: 向后兼容，不影响现有功能

---

## 架构设计

### 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│ 前端（React + Zustand）                                          │
│ ┌───────────────┐  ┌──────────────────┐  ┌──────────────────┐ │
│ │ Chat.tsx      │  │ useTaskStore     │  │ taskCoordinator  │ │
│ │ 恢复入口      │→ │ 轮询管理         │→ │ 多标签页协调     │ │
│ └───────────────┘  └──────────────────┘  └──────────────────┘ │
│          ↓                  ↓                       ↓           │
│ ┌───────────────────────────────────────────────────────────┐  │
│ │ taskRestoration.ts - 任务恢复工具                         │  │
│ │ - fetchPendingTasks() - 获取进行中任务                    │  │
│ │ - restoreTaskPolling() - 恢复单个任务                     │  │
│ │ - restoreAllPendingTasks() - 批量恢复                     │  │
│ └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                               │ HTTP API
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│ 后端（FastAPI + Supabase + asyncio）                            │
│ ┌───────────────────────────────────────────────────────────┐  │
│ │ API Layer                                                  │  │
│ │ /images/generate (POST) - 生成图片，保存任务             │  │
│ │ /images/tasks/:id (GET) - 查询任务，更新状态             │  │
│ │ /tasks/pending (GET) - 获取用户进行中任务                │  │
│ └─────────────────────────────┬─────────────────────────────┘  │
│                               ↓                                 │
│ ┌───────────────────────────────────────────────────────────┐  │
│ │ Service Layer                                              │  │
│ │ BaseGenerationService                                      │  │
│ │ ├─ _save_task_to_db() - 保存任务到数据库                 │  │
│ │ └─ _update_task_status() - 更新任务状态                   │  │
│ └─────────────────────────────┬─────────────────────────────┘  │
│                               ↓                                 │
│ ┌───────────────────────────────────────────────────────────┐  │
│ │ BackgroundTaskWorker（后台轮询服务）                      │  │
│ │ ├─ poll_pending_tasks() - 每30秒扫描pending/running任务   │  │
│ │ ├─ query_kie_and_update() - 调用KIE查询并更新状态        │  │
│ │ ├─ save_completed_message() - 完成后自动创建消息         │  │
│ │ └─ cleanup_stale_tasks() - 清理超时任务                   │  │
│ └───────────────────────────────────────────────────────────┘  │
│                               ↓                                 │
│ ┌───────────────────────────────────────────────────────────┐  │
│ │ Supabase PostgreSQL                                        │  │
│ │ tasks 表（扩展）:                                          │  │
│ │ ├─ external_task_id (KIE task_id)                         │  │
│ │ ├─ request_params (JSONB) - 请求参数                      │  │
│ │ ├─ result (JSONB) - 任务结果                              │  │
│ │ ├─ placeholder_message_id - 前端占位符ID                  │  │
│ │ └─ last_polled_at - 最后轮询时间                          │  │
│ └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ↓
                        KIE External API
                        任务生成和查询
```

### 数据库Schema扩展

扩展现有`tasks`表（006_add_tasks_table.sql基础上）：

```sql
-- 010_extend_tasks_for_persistence.sql

ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS external_task_id VARCHAR(100),
  ADD COLUMN IF NOT EXISTS request_params JSONB,
  ADD COLUMN IF NOT EXISTS result JSONB,
  ADD COLUMN IF NOT EXISTS fail_code VARCHAR(50),
  ADD COLUMN IF NOT EXISTS placeholder_message_id UUID,
  ADD COLUMN IF NOT EXISTS last_polled_at TIMESTAMPTZ;

-- 索引优化
CREATE INDEX IF NOT EXISTS idx_tasks_external_id ON tasks(external_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status_type ON tasks(status, type);
CREATE INDEX IF NOT EXISTS idx_tasks_user_pending ON tasks(user_id, status)
  WHERE status IN ('pending', 'running');
```

**字段说明**：
| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| external_task_id | VARCHAR(100) | KIE API返回的task_id | "img_abc123xyz" |
| request_params | JSONB | 生成请求参数 | `{"prompt": "...", "model": "google/nano-banana"}` |
| result | JSONB | 任务完成结果 | `{"image_urls": ["https://..."]}` |
| fail_code | VARCHAR(50) | KIE失败错误码 | "INSUFFICIENT_BALANCE" |
| placeholder_message_id | UUID | 前端占位符消息ID | UUID |
| last_polled_at | TIMESTAMPTZ | 最后轮询时间 | "2026-01-30 10:00:00+00" |

---

## 核心流程设计

### 流程1: 任务生成和保存

```
用户发起图片生成
    ↓
InputArea.handleSubmit()
    ↓
API: POST /images/generate
    {
      prompt: "猫咪",
      model: "google/nano-banana",
      conversation_id: "uuid",  // ← 新增参数
      wait_for_result: false
    }
    ↓
ImageService.generate_image()
    ├─ 1. 检查积分充足
    ├─ 2. 扣除预估积分
    ├─ 3. 调用 KIE 创建任务 → 返回 task_id
    ├─ 4. ⭐ 保存任务到数据库
    │      INSERT INTO tasks (
    │          user_id, conversation_id, external_task_id,
    │          type, status, request_params, credits_locked
    │      ) VALUES (...)
    └─ 5. 返回 { task_id, status: 'pending' }
    ↓
前端接收 task_id
    ├─ 创建占位符消息
    ├─ taskStore.startMediaTask()
    ├─ taskStore.startPolling() ← 开始轮询
    └─ 解锁输入框（并发支持）
```

### 流程2: 后台轮询（用户在线/离线均执行）

```
BackgroundTaskWorker 启动（main.py中注册）
    ↓
每30秒执行一次
    ↓
SELECT * FROM tasks WHERE status IN ('pending', 'running')
    ↓
对每个任务：
    ├─ 调用 KIE API: GET /jobs/recordInfo?taskId={external_task_id}
    ├─ 解析响应状态：
    │   ├─ waiting → UPDATE status='running'
    │   ├─ success →
    │   │   ├─ 上传结果到OSS
    │   │   ├─ UPDATE status='completed', result={...}
    │   │   ├─ ⭐ 自动创建消息到messages表
    │   │   └─ 标记conversation为未读
    │   └─ fail → UPDATE status='failed', fail_code=...
    └─ UPDATE last_polled_at=NOW()
```

**关键特性**：
- 无论用户在线/离线，任务都会继续轮询
- 完成后自动保存到数据库，用户下次登录直接看到结果
- 使用`asyncio`实现，无需Celery，简化部署

### 流程3: 页面刷新/登录恢复

```
Chat.tsx 页面加载
    ↓
useEffect(() => {
  if (user) {
    setTimeout(() => restoreAllPendingTasks(), 1000)
  }
}, [user])
    ↓
API: GET /tasks/pending
    ↓
返回：[
  {
    external_task_id: "img_123",
    conversation_id: "uuid",
    type: "image",
    request_params: {...},
    started_at: "2026-01-30T10:00:00Z",
    ...
  },
  ...
]
    ↓
对每个任务（延迟200ms启动，避免并发过高）：
    ├─ 检查是否超时
    │   ├─ 图片: started_at + 10分钟 < NOW() → 跳过，标记失败
    │   └─ 视频: started_at + 30分钟 < NOW() → 跳过，标记失败
    ├─ ⭐ taskCoordinator.canStartPolling(taskId)
    │   └─ 检查localStorage锁，避免多标签页重复轮询
    ├─ taskStore.startMediaTask()
    ├─ taskStore.startPolling(taskId, pollFn, callbacks)
    │   └─ 每2秒（图片）/5秒（视频）轮询一次
    └─ 完成后：
        ├─ 保存消息到数据库
        ├─ replaceMediaPlaceholder()
        └─ completeMediaTask()
```

### 流程4: 多标签页协调

```
taskCoordinator（基于localStorage + BroadcastChannel）
    ↓
canStartPolling(taskId):
    ├─ 读取 localStorage["task-lock-{taskId}"]
    ├─ 检查锁：
    │   ├─ 不存在 → 获取锁，返回 true
    │   ├─ 存在但超时（30秒） → 获取锁，返回 true
    │   └─ 存在且有效 → 返回 false（其他标签页正在轮询）
    └─ 设置锁：
        localStorage["task-lock-{taskId}"] = JSON.stringify({
          timestamp: Date.now(),
          tabId: "tab-abc123"
        })
    ↓
轮询过程中（每15秒）：
    └─ renewLock(taskId) - 更新时间戳，防止锁过期
    ↓
轮询完成：
    ├─ releasePolling(taskId)
    └─ localStorage.removeItem("task-lock-{taskId}")
    ↓
BroadcastChannel 通知：
    └─ postMessage({ type: 'task-completed', taskId })
        → 其他标签页收到通知，更新UI
```

---

## 边界情况处理

### 1. 任务超时处理

**场景**：任务生成时间过长或KIE服务异常

**后端清理**（BackgroundTaskWorker）：
```python
async def cleanup_stale_tasks(self):
    """每5分钟执行一次"""
    now = datetime.utcnow()

    # 查询所有pending/running任务
    tasks = await self.db.table("tasks").select("*").in_(
        "status", ["pending", "running"]
    ).execute()

    for task in tasks.data:
        started_at = datetime.fromisoformat(task["started_at"])
        max_duration = 10 * 60 if task["type"] == "image" else 30 * 60

        if (now - started_at).total_seconds() > max_duration:
            # 标记为失败
            await self.db.table("tasks").update({
                "status": "failed",
                "error_message": f"任务超时（超过{max_duration//60}分钟）",
                "completed_at": now.isoformat(),
            }).eq("id", task["id"]).execute()

            logger.warning(f"Task timeout: {task['external_task_id']}")
```

**前端恢复跳过**：
```typescript
const maxDuration = task.type === 'image' ? 10 * 60 * 1000 : 30 * 60 * 1000;
const elapsed = Date.now() - new Date(task.started_at).getTime();

if (elapsed > maxDuration) {
  console.warn(`任务 ${task.external_task_id} 已超时，跳过恢复`);
  await markTaskAsFailed(task.external_task_id, '任务超时');
  return;
}
```

**风险**：
- ⚠️ 用户可能损失积分（已扣除但任务失败）
- **缓解**：在credits_history中记录，管理员可手动退还

### 2. 数据库迁移失败

**场景**：ALTER TABLE失败或索引创建失败

**预防措施**：
```sql
-- 使用 IF NOT EXISTS 避免重复执行错误
ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS external_task_id VARCHAR(100);

-- 索引创建失败不影响功能
CREATE INDEX IF NOT EXISTS idx_tasks_external_id ON tasks(external_task_id);
```

**回滚脚本**：
```sql
-- rollback/010_rollback_extend_tasks.sql
ALTER TABLE tasks
  DROP COLUMN IF EXISTS external_task_id,
  DROP COLUMN IF EXISTS request_params,
  DROP COLUMN IF EXISTS result,
  DROP COLUMN IF EXISTS fail_code,
  DROP COLUMN IF EXISTS placeholder_message_id,
  DROP COLUMN IF EXISTS last_polled_at;

DROP INDEX IF EXISTS idx_tasks_external_id;
DROP INDEX IF EXISTS idx_tasks_status_type;
DROP INDEX IF EXISTS idx_tasks_user_pending;
```

**测试步骤**：
1. 在staging数据库执行迁移
2. 验证表结构：`\d+ tasks`
3. 验证索引：`SELECT * FROM pg_indexes WHERE tablename = 'tasks'`
4. 执行回滚脚本
5. 验证回滚成功

### 3. 前端保存消息失败 + OSS上传失败重试

**场景1**：轮询成功，但保存消息API失败（网络错误、服务器错误）

**后端保护**：
```python
# 在 query_task 中已保存 result 到 tasks 表
await self._update_task_status(
    task_id=task_id,
    status="success",
    result=result,  # ← 已保存
)
```

**前端重试**：
```typescript
// taskRestoration.ts
onSuccess: async (result: any) => {
  try {
    const savedMessage = await createMessage(conversationId, {...});
    replaceMediaPlaceholder(conversationId, placeholderId, savedMessage);
  } catch (error) {
    console.error('保存消息失败:', error);

    // ⭐ 标记任务为"需要重试"
    localStorage.setItem(
      `task-retry-${task.external_task_id}`,
      JSON.stringify({ conversationId, result })
    );

    toast.error('消息保存失败，请刷新页面重试');
  }
}
```

**场景2**：用户下次登录后发现任务完成但消息未保存

**手动重试API**：
```python
@router.post("/{external_task_id}/retry-save")
async def retry_save_task_result(
    external_task_id: str,
    current_user: CurrentUser,
    db: Database,
):
    """从tasks表读取result，重新创建消息"""
    task = db.table("tasks").select("*").eq(
        "external_task_id", external_task_id
    ).eq("user_id", current_user["id"]).single().execute()

    if task.data["status"] != "completed":
        raise HTTPException(400, "任务未完成")

    # 创建消息
    message = await message_service.create_message(
        conversation_id=task.data["conversation_id"],
        content="任务生成完成",
        role="assistant",
        image_url=task.data["result"].get("image_urls", [None])[0],
        video_url=task.data["result"].get("video_url"),
    )

    return {"success": True, "message_id": message["id"]}
```

---

**场景3**：KIE成功但OSS上传失败

**问题**：
```python
# ❌ 错误的流程
result = await kie_client.query_task(task_id)
if result.status == "success":
    # OSS上传失败 → 整个任务标记失败？
    result = await oss_service.upload(result.image_urls)
    await update_task_status("completed", result)
```

**解决方案：结果抓取与资源持久化解耦**
```python
# ✅ 正确的流程
class BackgroundTaskWorker:
    async def query_kie_and_update(self, task: dict):
        # 1. 查询KIE状态
        result = await kie_client.query_task(task["external_task_id"])

        if result.status == "success":
            # 2. 先保存KIE返回的原始URL
            await self.db.table("tasks").update({
                "status": "completed",
                "result": {
                    "kie_urls": result.image_urls,  # KIE原始URL
                    "oss_urls": None,  # OSS URL待上传
                    "oss_upload_status": "pending",
                },
                "completed_at": datetime.utcnow().isoformat(),
            }).eq("external_task_id", task["external_task_id"]).execute()

            # 3. 异步上传OSS（失败不影响任务状态）
            try:
                oss_urls = await self.upload_to_oss(result.image_urls, task["user_id"])

                # 更新OSS URL
                await self.db.table("tasks").update({
                    "result": {
                        "kie_urls": result.image_urls,
                        "oss_urls": oss_urls,  # ← OSS URL
                        "oss_upload_status": "success",
                    },
                }).eq("external_task_id", task["external_task_id"]).execute()

            except Exception as e:
                logger.error(f"OSS upload failed: {task['external_task_id']}, error={e}")

                # ⭐ 标记为待重试，不影响任务状态
                await self.db.table("tasks").update({
                    "result": {
                        "kie_urls": result.image_urls,
                        "oss_urls": None,
                        "oss_upload_status": "failed",
                        "oss_error": str(e),
                    },
                }).eq("external_task_id", task["external_task_id"]).execute()

                # 加入重试队列
                await self.add_to_retry_queue(task["external_task_id"])

    async def retry_failed_oss_uploads(self):
        """定期重试失败的OSS上传（每5分钟）"""
        tasks = await self.db.table("tasks").select("*").eq(
            "status", "completed"
        ).filter(
            "result->oss_upload_status", "eq", "failed"
        ).execute()

        for task in tasks.data:
            try:
                kie_urls = task["result"]["kie_urls"]
                oss_urls = await self.upload_to_oss(kie_urls, task["user_id"])

                # 更新成功
                await self.db.table("tasks").update({
                    "result": {
                        **task["result"],
                        "oss_urls": oss_urls,
                        "oss_upload_status": "success",
                    },
                }).eq("id", task["id"]).execute()

                logger.info(f"OSS upload retry success: {task['external_task_id']}")

            except Exception as e:
                logger.warning(f"OSS upload retry failed: {task['external_task_id']}, error={e}")
```

**效果**：
- KIE成功 → 任务标记为completed（用户可见）
- OSS失败 → 不影响任务状态，后台自动重试
- 前端优先显示OSS URL，降级显示KIE URL

---

**优化：KIE链接失效预防**

**问题**：KIE返回的原始URL通常有较短的有效期（10分钟），如果OSS上传重试多次，可能导致原始链接失效。

**解决方案**：
```python
async def query_kie_and_update(self, task: dict):
    result = await kie_client.query_task(task["external_task_id"])

    if result.status == "success":
        # 1. 记录URL过期时间
        kie_url_expires_at = datetime.utcnow() + timedelta(minutes=10)

        # 2. 先保存KIE URL
        await self.db.table("tasks").update({
            "status": "completed",
            "result": {
                "kie_urls": result.image_urls,
                "kie_url_expires_at": kie_url_expires_at.isoformat(),
                "oss_urls": None,
                "oss_upload_status": "pending",
            },
            "completed_at": datetime.utcnow().isoformat(),
        }).eq("external_task_id", task["external_task_id"]).execute()

        # 3. ⭐ 最高优先级上传OSS（不等待后续任务）
        try:
            # 立即上传，不加入队列
            oss_urls = await self.upload_to_oss_urgent(
                result.image_urls,
                task["user_id"],
                timeout=30  # 30秒超时
            )

            # 更新OSS URL
            await self.db.table("tasks").update({
                "result": {
                    "kie_urls": result.image_urls,
                    "kie_url_expires_at": kie_url_expires_at.isoformat(),
                    "oss_urls": oss_urls,
                    "oss_upload_status": "success",
                },
            }).eq("external_task_id", task["external_task_id"]).execute()

        except Exception as e:
            logger.error(f"OSS urgent upload failed: {task['external_task_id']}, error={e}")

            # 检查重试次数
            retry_count = task.get("oss_retry_count", 0)
            if retry_count >= 3:
                # ⭐ 触发紧急告警
                await self.send_alert(
                    level="CRITICAL",
                    message=f"OSS upload failed after 3 retries: {task['external_task_id']}",
                    task_id=task["external_task_id"],
                )

            # 标记为待重试
            await self.db.table("tasks").update({
                "result": {
                    "kie_urls": result.image_urls,
                    "kie_url_expires_at": kie_url_expires_at.isoformat(),
                    "oss_urls": None,
                    "oss_upload_status": "failed",
                    "oss_error": str(e),
                },
                "oss_retry_count": retry_count + 1,
            }).eq("external_task_id", task["external_task_id"]).execute()

async def upload_to_oss_urgent(self, urls: list, user_id: str, timeout: int = 30):
    """紧急上传OSS（最高优先级）"""
    # 使用更短的超时时间，避免阻塞后续任务
    async with asyncio.timeout(timeout):
        return await self.oss_service.upload_batch(urls, user_id)

async def retry_failed_oss_uploads(self):
    """重试失败的OSS上传（检查URL是否过期）"""
    now = datetime.utcnow()

    tasks = await self.db.table("tasks").select("*").eq(
        "status", "completed"
    ).filter(
        "result->oss_upload_status", "eq", "failed"
    ).execute()

    for task in tasks.data:
        try:
            # ⭐ 检查KIE URL是否过期
            expires_at_str = task["result"].get("kie_url_expires_at")
            if expires_at_str:
                expires_at = datetime.fromisoformat(expires_at_str)
                if now > expires_at:
                    logger.warning(
                        f"KIE URL expired for task: {task['external_task_id']}, "
                        f"skipping OSS retry"
                    )
                    # 标记为永久失败
                    await self.db.table("tasks").update({
                        "result": {
                            **task["result"],
                            "oss_upload_status": "expired",
                        },
                    }).eq("id", task["id"]).execute()
                    continue

            # URL未过期，重试上传
            kie_urls = task["result"]["kie_urls"]
            oss_urls = await self.upload_to_oss(kie_urls, task["user_id"])

            # 更新成功
            await self.db.table("tasks").update({
                "result": {
                    **task["result"],
                    "oss_urls": oss_urls,
                    "oss_upload_status": "success",
                },
                "oss_retry_count": 0,
            }).eq("id", task["id"]).execute()

            logger.info(f"OSS upload retry success: {task['external_task_id']}")

        except Exception as e:
            logger.warning(f"OSS upload retry failed: {task['external_task_id']}, error={e}")
```

**告警集成**：
```python
async def send_alert(self, level: str, message: str, task_id: str):
    """发送告警（邮件/钉钉/Slack等）"""
    # 实现告警通知逻辑
    logger.critical(f"ALERT [{level}]: {message}, task_id={task_id}")
    # TODO: 集成告警系统
```

### 4. 多标签页竞态条件

**场景1**：两个标签页几乎同时恢复任务

**localStorage锁机制**：
```typescript
// taskCoordinator.ts
canStartPolling(taskId: string): boolean {
  const lockKey = `task-lock-${taskId}`;
  const lock = localStorage.getItem(lockKey);

  if (lock) {
    const lockData = JSON.parse(lock);
    const lockAge = Date.now() - lockData.timestamp;

    // 锁未过期且不是自己的锁
    if (lockAge < 30000 && lockData.tabId !== this.tabId) {
      return false; // ← 阻止重复轮询
    }
  }

  // 获取锁（原子操作，但可能有极小概率竞态）
  localStorage.setItem(lockKey, JSON.stringify({
    timestamp: Date.now(),
    tabId: this.tabId,
  }));

  return true;
}
```

**极端情况**：两个标签页同时通过检查并获取锁

**后果**：两个标签页都开始轮询（浪费网络请求）

**影响评估**：
- 概率极低（<0.1%）
- 后果轻微（多几个请求，不会破坏数据）
- 自动修复（30秒后只有一个标签页的锁有效）

**进一步优化（可选）**：
```typescript
// 使用 storage 事件监听锁冲突
window.addEventListener('storage', (e) => {
  if (e.key?.startsWith('task-lock-')) {
    const taskId = e.key.replace('task-lock-', '');
    const lock = JSON.parse(e.newValue || '{}');

    // 如果其他标签页抢到了锁，立即停止轮询
    if (lock.tabId !== this.tabId && this.activeTasks.has(taskId)) {
      console.log(`检测到标签页 ${lock.tabId} 获取锁，停止轮询 ${taskId}`);
      stopPolling(taskId);
    }
  }
});
```

**场景2**：用户快速切换标签页

**BroadcastChannel同步**：
```typescript
this.channel.onmessage = (event) => {
  if (event.data.type === 'task-completed') {
    // 其他标签页完成了任务，更新UI
    const { taskId, result } = event.data;
    completeMediaTask(taskId);
    toast.success('任务已在其他标签页完成');
  }
};
```

### 5. 后台轮询性能 + 防止惊群效应

**场景**：100个用户各有5个任务 = 500个并发任务

**优化1：批量查询**
```python
# ❌ 错误：循环查询（500次数据库请求）
for task in tasks:
    kie_result = await kie_client.query_task(task["external_task_id"])

# ✅ 正确：批量查询（1次数据库查询 + 500次KIE请求）
tasks = await self.db.table("tasks").select("*").in_(
    "status", ["pending", "running"]
).execute()

# KIE请求并发执行（限制并发数）
async with asyncio.Semaphore(10):  # 最多10个并发
    await asyncio.gather(*[
        self.query_and_update_task(task)
        for task in tasks.data
    ])
```

**优化2：避免频繁轮询**
```python
# 只轮询"最近更新"的任务
tasks = await self.db.table("tasks").select("*").in_(
    "status", ["pending", "running"]
).gte(
    "last_polled_at",
    (datetime.utcnow() - timedelta(minutes=5)).isoformat()  # 5分钟内轮询过的
).or_(
    "last_polled_at.is.null"  # 或从未轮询过的
).execute()
```

**优化3：指数退避**
```python
# 任务轮询次数越多，间隔越长
async def get_poll_interval(task: dict) -> int:
    """动态计算轮询间隔"""
    poll_count = task.get("poll_count", 0)

    if poll_count < 10:
        return 30  # 前10次：30秒
    elif poll_count < 30:
        return 60  # 10-30次：1分钟
    else:
        return 120  # 30次以上：2分钟
```

**优化4：随机抖动防止惊群效应**

**问题**：如果1000个任务同时到期，会在同一秒发起1000个KIE请求。

**解决方案**：
```python
import random

async def poll_pending_tasks(self):
    """轮询所有pending/running任务"""
    response = self.db.table("tasks").select("*").in_(
        "status", ["pending", "running"]
    ).execute()

    if not response.data:
        return

    logger.debug(f"Polling {len(response.data)} tasks")

    # ⭐ 随机打散任务（Jitter）
    tasks_shuffled = random.sample(response.data, len(response.data))

    # 动态调整并发数（根据KIE QPS限制）
    kie_qps_limit = self.settings.kie_qps_limit or 50  # 默认50 QPS
    semaphore = asyncio.Semaphore(kie_qps_limit)

    async def process_task_with_jitter(task: dict, index: int):
        # ⭐ 在30秒窗口内均匀分布
        jitter_delay = (index / len(tasks_shuffled)) * 30.0
        await asyncio.sleep(jitter_delay)

        async with semaphore:
            try:
                await self.query_kie_and_update(task)
            except Exception as e:
                logger.error(
                    f"Failed to process task: {task.get('external_task_id')}, "
                    f"error={e}"
                )

    await asyncio.gather(*[
        process_task_with_jitter(task, i)
        for i, task in enumerate(tasks_shuffled)
    ])

    logger.info(f"Polled {len(response.data)} tasks in 30s window")
```

**效果**：
- 1000个任务分散在30秒窗口内
- 平均QPS: 1000 / 30 = 33.3（平滑）
- 峰值QPS: ~50（受Semaphore限制）

**优化5：防止轮询重叠（执行锁）**

**问题**：如果上一次轮询还没结束（处理1000个任务需要1分钟），30秒后下一次轮询又开始，会造成负载翻倍。

**解决方案1：单进程锁（asyncio.Lock）**
```python
class BackgroundTaskWorker:
    def __init__(self, db: Client):
        self.db = db
        self.settings = get_settings()
        self.is_running = False
        self._poll_lock = asyncio.Lock()  # ← 轮询锁

    async def start(self):
        self.is_running = True
        logger.info("BackgroundTaskWorker started")

        while self.is_running:
            try:
                # ⭐ 尝试获取锁，如果上一次轮询未结束则跳过
                if self._poll_lock.locked():
                    logger.warning("Previous polling not finished, skipping this round")
                    await asyncio.sleep(30)
                    continue

                async with self._poll_lock:
                    # 1. 轮询进行中的任务
                    await self.poll_pending_tasks()

                    # 2. 清理超时任务
                    await self.cleanup_stale_tasks()

                    # 3. 重试失败的OSS上传
                    await self.retry_failed_oss_uploads()

            except Exception as e:
                logger.error(f"BackgroundTaskWorker error: {e}")

            # 等待30秒后继续
            await asyncio.sleep(30)
```

**解决方案2：分布式锁（Redis）**
```python
import aioredis
from contextlib import asynccontextmanager

class BackgroundTaskWorker:
    def __init__(self, db: Client):
        self.db = db
        self.settings = get_settings()
        self.is_running = False
        self.redis = None

    async def start(self):
        # 连接Redis
        self.redis = await aioredis.from_url(
            f"redis://{self.settings.redis_host}:{self.settings.redis_port}"
        )

        self.is_running = True
        logger.info("BackgroundTaskWorker started")

        while self.is_running:
            try:
                # ⭐ 使用Redis分布式锁（支持多进程/多服务器）
                async with self.acquire_redis_lock("task_polling_lock", timeout=600):
                    await self.poll_pending_tasks()
                    await self.cleanup_stale_tasks()
                    await self.retry_failed_oss_uploads()

            except LockAcquireError:
                logger.warning("Another worker is polling, skipping this round")
            except Exception as e:
                logger.error(f"BackgroundTaskWorker error: {e}")

            await asyncio.sleep(30)

    @asynccontextmanager
    async def acquire_redis_lock(self, lock_key: str, timeout: int = 600):
        """
        获取Redis分布式锁

        Args:
            lock_key: 锁的键
            timeout: 锁的过期时间（秒）

        Raises:
            LockAcquireError: 锁已被占用
        """
        lock_value = f"{socket.gethostname()}-{os.getpid()}-{time.time()}"

        # 尝试获取锁（SET NX EX）
        acquired = await self.redis.set(
            lock_key,
            lock_value,
            ex=timeout,
            nx=True  # Only set if key doesn't exist
        )

        if not acquired:
            # 检查锁的剩余时间
            ttl = await self.redis.ttl(lock_key)
            raise LockAcquireError(f"Lock already held, TTL: {ttl}s")

        try:
            logger.info(f"Acquired lock: {lock_key}, value: {lock_value}")
            yield
        finally:
            # 释放锁（只删除自己的锁）
            current_value = await self.redis.get(lock_key)
            if current_value == lock_value:
                await self.redis.delete(lock_key)
                logger.info(f"Released lock: {lock_key}")
            else:
                logger.warning(f"Lock {lock_key} was already released or taken over")


class LockAcquireError(Exception):
    """锁获取失败"""
    pass
```

**方案对比**：

| 特性 | asyncio.Lock | Redis分布式锁 |
|------|-------------|--------------|
| 适用场景 | 单进程 | 多进程/多服务器 |
| 实现复杂度 | 简单 | 中等 |
| 依赖 | 无 | Redis |
| 容错性 | 进程崩溃锁丢失 | 锁自动过期（TTL） |
| **推荐** | ✅ 单服务器部署 | ✅ 分布式部署 |

**性能指标**：
- 500个任务，30秒轮询间隔，随机抖动，执行锁保护
- 平均每秒16.7个KIE请求（平滑分布）
- 数据库查询：1次/30秒（可忽略）
- 轮询重叠：0次（锁保护）

### 6. 用户离线时任务完成 + 跨设备恢复

**场景1**：用户发起视频生成后退出登录，5分钟后任务完成

**后台处理**：
```python
# BackgroundTaskWorker.poll_pending_tasks()
if result.status == "success":
    # 1. 更新tasks表
    await self._update_task_status(...)

    # 2. ⭐ 自动创建消息（用户离线也执行）
    if task["conversation_id"]:
        await self.message_service.create_message(
            conversation_id=task["conversation_id"],
            content="视频生成完成",
            role="assistant",
            video_url=result.video_url,
            credits_cost=task["credits_locked"],
        )

        # 3. 标记conversation为未读
        await self.conversation_service.mark_unread(
            task["conversation_id"]
        )
```

**用户登录后**：
```typescript
// 加载对话消息时，已经包含了完成的消息
const messages = await getMessages(conversationId);
// ✅ 包含后台创建的消息
```

**UI提示**：
```typescript
// useChatStore.ts
const unreadCount = conversations.filter(c => c.unread).length;

// Sidebar.tsx
{conversation.unread && (
  <Badge className="bg-primary text-white">新消息</Badge>
)}
```

---

**场景2**：跨设备/会话恢复 - placeholder_message_id 不存在

**问题**：用户在A浏览器发起任务，在B浏览器登录，B没有对应的占位符消息。

**解决方案**：
```typescript
// taskRestoration.ts
export function restoreTaskPolling(task: PendingTask, conversationTitle: string) {
  const { replaceMediaPlaceholder } = useConversationRuntimeStore.getState();

  // ⭐ 检查占位符是否存在
  const placeholderId = task.placeholder_message_id ||
    `restored-${task.external_task_id}`;

  const existingMessages = useConversationRuntimeStore.getState()
    .optimisticMessages.filter(m => m.id === placeholderId);

  // 如果占位符不存在（跨设备场景），动态创建
  if (existingMessages.length === 0 && task.placeholder_message_id) {
    useConversationRuntimeStore.getState().addOptimisticMessage({
      id: placeholderId,
      role: 'assistant',
      content: '正在恢复任务...',
      isStreaming: true,
      conversationId: task.conversation_id,
      createdAt: new Date(task.started_at).toISOString(),
    });
  }

  // 恢复轮询...
  startPolling(task.external_task_id, pollFn, {
    onSuccess: async (result: any) => {
      // 1. 检查消息是否已存在（后台可能已创建）
      const existingMessage = await checkMessageExists(
        task.conversation_id,
        task.external_task_id
      );

      if (existingMessage) {
        // 后台已创建，直接更新UI
        replaceMediaPlaceholder(task.conversation_id, placeholderId, existingMessage);
      } else {
        // 前端创建消息
        const savedMessage = await createMessage(task.conversation_id, {...});
        replaceMediaPlaceholder(task.conversation_id, placeholderId, savedMessage);
      }
    },
  });
}

async function checkMessageExists(conversationId: string, taskId: string) {
  // 通过 generation_params 中的 task_id 查找
  const messages = await getMessages(conversationId);
  return messages.find(m =>
    m.generation_params?.task_id === taskId
  );
}
```

**数据库改进**：
```python
# 后台创建消息时，记录task_id到generation_params
await self.message_service.create_message(
    conversation_id=task["conversation_id"],
    content="视频生成完成",
    role="assistant",
    video_url=result.video_url,
    generation_params={
        **task["request_params"],
        "task_id": task["external_task_id"],  # ← 关联task_id
    },
)

### 7. KIE服务异常

**场景1**：KIE API返回500错误

**后端容错**：
```python
try:
    result = await kie_client.query_task(external_task_id)
except Exception as e:
    logger.error(f"KIE query failed: {external_task_id}, error={e}")
    # ⭐ 不标记为失败，下次轮询继续尝试
    await self.db.table("tasks").update({
        "last_polled_at": datetime.utcnow().isoformat(),
    }).eq("external_task_id", external_task_id).execute()
    return
```

**场景2**：KIE API长时间不响应

**超时设置**：
```python
# backend/services/adapters/kie/client.py
TASK_QUERY_TIMEOUT = 10.0  # 10秒超时

async def query_task(self, task_id: str):
    async with asyncio.timeout(TASK_QUERY_TIMEOUT):
        response = await self.client.get(f"/jobs/recordInfo?taskId={task_id}")
```

**场景3**：KIE返回"任务不存在"

**处理**：
```python
if result.error_code == "TASK_NOT_FOUND":
    # 标记为失败（可能KIE任务已过期）
    await self._update_task_status(
        task_id=task_id,
        status="failed",
        fail_code="TASK_NOT_FOUND",
        fail_msg="任务在KIE系统中不存在或已过期",
    )
```

### 8. 数据库连接失败

**场景**：Supabase临时不可用

**后端容错**：
```python
try:
    tasks = await self.db.table("tasks").select("*").execute()
except Exception as e:
    logger.error(f"Database query failed: {e}")
    # ⭐ 等待下一轮轮询（30秒后）
    return
```

**影响**：
- 后台轮询暂停1次（30秒）
- 前端请求返回503错误
- 用户体验：轻微延迟，不影响核心功能

### 9. 前端恢复大量任务

**场景**：用户有50个未完成任务

**批量恢复优化**：
```typescript
export async function restoreAllPendingTasks() {
  const tasks = await fetchPendingTasks();

  if (tasks.length === 0) return;

  console.log(`开始恢复 ${tasks.length} 个任务`);

  // ⭐ 限制并发恢复数量
  const BATCH_SIZE = 5;
  for (let i = 0; i < tasks.length; i += BATCH_SIZE) {
    const batch = tasks.slice(i, i + BATCH_SIZE);

    // 并发恢复5个
    await Promise.all(
      batch.map((task, index) =>
        new Promise(resolve =>
          setTimeout(() => {
            restoreTaskPolling(task, getConversationTitle(task.conversation_id));
            resolve(undefined);
          }, index * 200)  // 批内延迟200ms
        )
      )
    );

    // 批之间延迟1秒
    if (i + BATCH_SIZE < tasks.length) {
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }

  toast.success(`正在恢复 ${tasks.length} 个任务`);
}
```

**用户体验**：
- 50个任务分10批，每批5个
- 每批内延迟200ms，批间延迟1秒
- 总耗时：10秒（可接受）

### 10. 积分扣除但任务失败

**场景**：任务失败但积分已扣除

**解决方案1：后台自动退还**
```python
# BackgroundTaskWorker
if task["status"] == "failed" and not task.get("credits_refunded"):
    # 退还积分
    await self.credit_service.refund_credits(
        user_id=task["user_id"],
        credits=task["credits_locked"],
        description=f"任务失败退还: {task['external_task_id']}",
    )

    # 标记已退还
    await self.db.table("tasks").update({
        "credits_refunded": True,
    }).eq("id", task["id"]).execute()
```

**解决方案2：管理员手动退还**
```python
# 超级管理员功能
@router.post("/admin/tasks/{task_id}/refund")
async def refund_task_credits(
    task_id: str,
    current_user: CurrentUser,
    admin_service: AdminService,
):
    """管理员手动退还任务积分"""
    if current_user["role"] != "admin":
        raise HTTPException(403, "需要管理员权限")

    await admin_service.refund_task_credits(task_id)
    return {"success": True}
```

**credits_history审计**：
```sql
-- 查询所有任务失败但未退还的记录
SELECT t.id, t.user_id, t.credits_locked, t.error_message
FROM tasks t
LEFT JOIN credits_history ch ON ch.description LIKE '%' || t.external_task_id || '%'
WHERE t.status = 'failed'
  AND t.credits_locked > 0
  AND ch.id IS NULL;
```

---

## 性能指标

### 数据库查询性能

**目标**：< 50ms

**测试SQL**：
```sql
-- 测试1：获取用户进行中任务（前端恢复）
EXPLAIN ANALYZE
SELECT * FROM tasks
WHERE user_id = 'uuid'
  AND status IN ('pending', 'running')
ORDER BY started_at ASC;

-- 预期：Index Scan on idx_tasks_user_pending (cost=0.15..8.17)

-- 测试2：后台扫描所有进行中任务
EXPLAIN ANALYZE
SELECT * FROM tasks
WHERE status IN ('pending', 'running')
  AND (last_polled_at IS NULL OR last_polled_at < NOW() - INTERVAL '5 minutes');

-- 预期：Index Scan on idx_tasks_status_type (cost=0.15..25.50)
```

**索引覆盖率**：
- `idx_tasks_user_pending`: 用于前端恢复查询
- `idx_tasks_status_type`: 用于后台轮询查询
- `idx_tasks_external_id`: 用于通过KIE task_id查询

### 前端恢复性能

**目标**：20个任务 < 2秒

**测试步骤**：
1. 创建20个pending任务
2. 刷新页面
3. Chrome DevTools Performance记录
4. 验证`restoreAllPendingTasks`总耗时

**优化手段**：
- 批量查询（1次API请求）
- 并发恢复（每批5个）
- 延迟启动（200ms间隔）

### 后台轮询性能

**目标**：500个任务 < 30秒

**性能分析**：
```
数据库查询: 1次 × 50ms = 50ms
KIE并发请求: 500个任务 / 10并发 × 2秒 = 100秒
数据库更新: 500次 × 10ms = 5秒
总计: ~105秒
```

**优化后**：
```
数据库查询: 1次 × 50ms = 50ms
KIE并发请求: 500个任务 / 50并发 × 2秒 = 20秒
批量更新: 10批 × 100ms = 1秒
总计: ~21秒 ✅
```

---

## 数据一致性保证

### 1. 任务状态转换

**有效状态机**：
```
pending → running → completed
pending → running → failed
pending → failed (timeout)
```

**无效转换（防护）**：
```python
VALID_TRANSITIONS = {
    "pending": ["running", "failed"],
    "running": ["completed", "failed"],
    "completed": [],  # 终态
    "failed": [],     # 终态
}

def validate_status_transition(old_status: str, new_status: str) -> bool:
    if old_status not in VALID_TRANSITIONS:
        return False
    if new_status not in VALID_TRANSITIONS[old_status]:
        logger.warning(f"Invalid transition: {old_status} → {new_status}")
        return False
    return True
```

### 2. 幂等性保证

**场景**：后台轮询和前端轮询同时更新同一任务

**保护**：
```python
# 使用 last_polled_at 作为乐观锁
await self.db.table("tasks").update({
    "status": "completed",
    "result": result,
    "completed_at": now.isoformat(),
    "last_polled_at": now.isoformat(),
}).eq("external_task_id", task_id).eq(
    "status", "running"  # ← 只更新running状态的任务
).execute()
```

**后果**：
- 第一个请求成功更新
- 第二个请求匹配0行（因为状态已变为completed）
- 数据一致性保证 ✅

### 3. 事务保护（重要操作）

**场景**：创建消息 + 更新任务状态

```python
async def complete_task_and_save_message(
    task_id: str,
    conversation_id: str,
    message_data: dict,
):
    """原子操作：完成任务 + 保存消息"""
    try:
        # Supabase不支持事务，使用补偿机制

        # 1. 先保存消息
        message = await self.message_service.create_message(
            conversation_id, **message_data
        )

        # 2. 更新任务状态（保存message_id）
        await self.db.table("tasks").update({
            "status": "completed",
            "result": message_data,
            "completed_message_id": message["id"],  # ← 关联消息
            "completed_at": datetime.utcnow().isoformat(),
        }).eq("external_task_id", task_id).execute()

        return message

    except Exception as e:
        logger.error(f"Failed to complete task: {task_id}, error={e}")
        # 回滚：删除消息（如果已创建）
        if message:
            await self.message_service.delete_message(message["id"])
        raise
```

---

## 向后兼容性

### 现有功能不受影响

**验证清单**：
- ✅ 现有API参数向后兼容（`conversation_id`为可选参数）
- ✅ 前端不传`conversation_id`时功能正常
- ✅ 旧版本前端可以正常使用（不恢复任务，但生成功能正常）

**测试**：
```bash
# 不传 conversation_id 的请求
curl -X POST /api/images/generate \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"prompt": "cat", "wait_for_result": false}'

# 预期：正常返回 task_id，但不保存到数据库
```

### 渐进式迁移

**阶段1**：后端修改 + 数据库扩展
- 修改后端保存任务逻辑
- 不影响前端现有功能

**阶段2**：前端修改（传入conversation_id）
- 逐步修改前端传入`conversation_id`
- 新请求开始持久化

**阶段3**：前端恢复机制
- 添加任务恢复功能
- 用户体验提升

---

## 安全性考虑

### 1. RLS (Row Level Security)

**tasks表策略**：
```sql
-- 用户只能查看自己的任务
CREATE POLICY "Users can view own tasks" ON tasks FOR SELECT
    USING (auth.uid() = user_id);

-- 用户只能更新自己的任务
CREATE POLICY "Users can update own tasks" ON tasks FOR UPDATE
    USING (auth.uid() = user_id);

-- 服务角色可以管理所有任务（后台轮询）
CREATE POLICY "Service role can manage all tasks" ON tasks FOR ALL
    USING (auth.role() = 'service_role');
```

### 2. API权限验证

```python
@router.get("/tasks/pending")
async def get_pending_tasks(
    current_user: CurrentUser,  # ← JWT验证
    db: Database,
):
    """只返回当前用户的任务"""
    response = db.table("tasks").select("*").eq(
        "user_id", current_user["id"]  # ← 用户隔离
    ).in_("status", ["pending", "running"]).execute()

    return {"tasks": response.data}
```

### 3. 敏感信息保护

**request_params过滤**：
```python
# 不保存敏感信息到数据库
safe_params = {
    k: v for k, v in request_params.items()
    if k not in ["api_key", "access_token", "password"]
}
```

---

## 监控和告警

### 1. 关键指标

**任务成功率**：
```sql
SELECT
  type,
  COUNT(*) FILTER (WHERE status = 'completed') * 100.0 / COUNT(*) as success_rate
FROM tasks
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY type;

-- 预期：> 95%
```

**任务平均耗时**：
```sql
SELECT
  type,
  AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) as avg_duration_seconds
FROM tasks
WHERE status = 'completed'
  AND created_at > NOW() - INTERVAL '24 hours'
GROUP BY type;

-- 预期：图片 < 60秒，视频 < 300秒
```

**后台轮询延迟**：
```sql
SELECT
  COUNT(*),
  AVG(EXTRACT(EPOCH FROM (NOW() - last_polled_at))) as avg_delay_seconds
FROM tasks
WHERE status IN ('pending', 'running');

-- 预期：< 60秒
```

### 2. 告警规则

**任务失败率过高**：
```python
failure_rate = failed_count / total_count
if failure_rate > 0.1:  # 超过10%
    alert("任务失败率过高", f"24小时内失败率: {failure_rate:.2%}")
```

**后台轮询停止**：
```python
max_delay = max([
    (datetime.utcnow() - task["last_polled_at"]).total_seconds()
    for task in pending_tasks
])

if max_delay > 300:  # 超过5分钟未轮询
    alert("后台轮询异常", f"最长未轮询时间: {max_delay}秒")
```

---

## 创建时间
2026-01-30

## 文档维护
- 实施后需要更新：
  - `/docs/API_REFERENCE.md` - 添加新API文档
  - `/docs/PROJECT_OVERVIEW.md` - 更新tasks表结构
  - `/docs/FUNCTION_INDEX.md` - 添加新增函数
  - `/docs/CURRENT_ISSUES.md` - 标记问题已解决
