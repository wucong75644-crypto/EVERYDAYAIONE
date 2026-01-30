# 实施计划：任务持久化和跨会话恢复

> **关联技术方案**：`TECH_TASK_PERSISTENCE.md`
> **创建时间**：2026-01-30
> **预计工时**：9小时

---

## 实施概览

### 核心改动
- **数据库**：扩展tasks表，添加6个字段
- **后端**：8个文件修改/新增
- **前端**：8个文件修改/新增
- **测试**：5个测试场景

### 关键特性
✅ 任务持久化到数据库
✅ 页面刷新/登录自动恢复
✅ 用户离线时后台继续轮询
✅ 多标签页协调避免重复
✅ 任务超时自动清理

---

## Phase 1: 数据库Schema扩展 (30分钟)

### 1.1 创建迁移脚本

**文件**：`/docs/database/migrations/010_extend_tasks_for_persistence.sql`

```sql
-- 010_extend_tasks_for_persistence.sql
-- 扩展任务表以支持任务持久化和恢复
-- 创建日期: 2026-01-30

-- 扩展字段
ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS external_task_id VARCHAR(100),
  ADD COLUMN IF NOT EXISTS request_params JSONB,
  ADD COLUMN IF NOT EXISTS result JSONB,
  ADD COLUMN IF NOT EXISTS fail_code VARCHAR(50),
  ADD COLUMN IF NOT EXISTS placeholder_message_id UUID,
  ADD COLUMN IF NOT EXISTS last_polled_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS client_context JSONB,
  ADD COLUMN IF NOT EXISTS kie_url_expires_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1,
  ADD COLUMN IF NOT EXISTS oss_retry_count INTEGER DEFAULT 0;

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_tasks_external_id ON tasks(external_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status_type ON tasks(status, type);
CREATE INDEX IF NOT EXISTS idx_tasks_user_pending ON tasks(user_id, status)
  WHERE status IN ('pending', 'running');

-- 添加注释
COMMENT ON COLUMN tasks.external_task_id IS 'KIE API返回的task_id';
COMMENT ON COLUMN tasks.request_params IS '生成请求参数 (prompt, model, size等)';
COMMENT ON COLUMN tasks.result IS '任务结果 (image_urls, video_url等)';
COMMENT ON COLUMN tasks.placeholder_message_id IS '前端占位符消息ID,用于更新UI';
COMMENT ON COLUMN tasks.last_polled_at IS '最后一次轮询时间';
COMMENT ON COLUMN tasks.fail_code IS 'KIE返回的失败错误码';
COMMENT ON COLUMN tasks.client_context IS '客户端设备信息 (device, browser, tab_id等)';
COMMENT ON COLUMN tasks.kie_url_expires_at IS 'KIE原始URL过期时间';
COMMENT ON COLUMN tasks.version IS '版本号，用于乐观锁防止并发更新冲突';
COMMENT ON COLUMN tasks.oss_retry_count IS 'OSS上传重试次数';
```

### 1.2 创建回滚脚本

**文件**：`/docs/database/migrations/rollback/010_rollback_extend_tasks.sql`

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

### 1.3 执行迁移

```bash
# 1. 在开发环境执行
psql -U postgres -d everydayai_dev < docs/database/migrations/010_extend_tasks_for_persistence.sql

# 2. 验证表结构
psql -U postgres -d everydayai_dev -c "\d+ tasks"

# 3. 验证索引
psql -U postgres -d everydayai_dev -c "SELECT * FROM pg_indexes WHERE tablename = 'tasks';"

# 4. 测试回滚
psql -U postgres -d everydayai_dev < docs/database/migrations/rollback/010_rollback_extend_tasks.sql
```

**验证清单**：
- [ ] tasks表包含6个新字段
- [ ] 3个新索引创建成功
- [ ] 字段注释正确
- [ ] 回滚脚本测试通过

---

## Phase 2: 后端核心逻辑 (2.5小时)

### 2.1 扩展BaseGenerationService

**文件**：`/backend/services/base_generation_service.py`

**修改位置**：在类`BaseGenerationService`末尾添加两个方法

```python
async def _save_task_to_db(
    self,
    user_id: str,
    conversation_id: Optional[str],
    task_id: str,
    task_type: str,
    request_params: Dict[str, Any],
    credits_locked: int,
    placeholder_message_id: Optional[str] = None,
) -> str:
    """
    保存任务到数据库

    Args:
        user_id: 用户ID
        conversation_id: 对话ID (可选)
        task_id: KIE返回的external_task_id
        task_type: 任务类型 ('image' | 'video')
        request_params: 生成请求参数
        credits_locked: 预扣积分
        placeholder_message_id: 前端占位符消息ID

    Returns:
        数据库任务ID (UUID)
    """
    from datetime import datetime

    response = self.db.table("tasks").insert({
        "user_id": user_id,
        "conversation_id": conversation_id,
        "external_task_id": task_id,
        "type": task_type,
        "status": "pending",
        "request_params": request_params,
        "credits_locked": credits_locked,
        "placeholder_message_id": placeholder_message_id,
        "started_at": datetime.utcnow().isoformat(),
    }).execute()

    db_task_id = response.data[0]["id"]
    logger.info(
        f"Task saved to DB: db_id={db_task_id}, external_id={task_id}, "
        f"type={task_type}, user_id={user_id}"
    )

    return db_task_id


async def _update_task_status(
    self,
    task_id: str,
    status: str,
    result: Optional[Dict] = None,
    fail_code: Optional[str] = None,
    fail_msg: Optional[str] = None,
) -> None:
    """
    更新任务状态到数据库

    Args:
        task_id: KIE返回的external_task_id
        status: KIE任务状态 ('pending' | 'processing' | 'success' | 'failed')
        result: 任务完成结果 (仅成功时)
        fail_code: 失败错误码 (仅失败时)
        fail_msg: 失败详细信息 (仅失败时)
    """
    from datetime import datetime

    # 映射KIE状态到数据库状态
    status_mapping = {
        "pending": "pending",
        "processing": "running",
        "success": "completed",
        "failed": "failed",
    }

    db_status = status_mapping.get(status, "pending")

    update_data = {
        "status": db_status,
        "last_polled_at": datetime.utcnow().isoformat(),
    }

    # 任务完成
    if db_status == "completed" and result:
        update_data["result"] = result
        update_data["completed_at"] = datetime.utcnow().isoformat()
        update_data["credits_used"] = result.get("credits_consumed", 0)

    # 任务失败
    if db_status == "failed":
        update_data["fail_code"] = fail_code
        update_data["error_message"] = fail_msg
        update_data["completed_at"] = datetime.utcnow().isoformat()

    try:
        self.db.table("tasks").update(update_data).eq(
            "external_task_id", task_id
        ).execute()

        logger.debug(f"Task status updated: task_id={task_id}, status={db_status}")
    except Exception as e:
        logger.error(f"Failed to update task status: task_id={task_id}, error={e}")
```

**测试代码**：
```python
# tests/test_base_generation_service.py
async def test_save_task_to_db():
    service = BaseGenerationService(db)

    task_id = await service._save_task_to_db(
        user_id="uuid",
        conversation_id="conv-uuid",
        task_id="img_abc123",
        task_type="image",
        request_params={"prompt": "cat", "model": "google/nano-banana"},
        credits_locked=10,
    )

    assert task_id is not None

    # 验证数据库
    task = db.table("tasks").select("*").eq("id", task_id).single().execute()
    assert task.data["external_task_id"] == "img_abc123"
    assert task.data["status"] == "pending"
```

### 2.2 修改ImageService

**文件**：`/backend/services/image_service.py`

**修改1**：在`generate_image`方法签名中添加`conversation_id`参数

```python
# 原来：第33行
async def generate_image(
    self,
    user_id: str,
    prompt: str,
    model: str = "google/nano-banana",
    size: str = "1:1",
    output_format: str = "png",
    resolution: Optional[str] = None,
    wait_for_result: bool = True,
) -> Dict[str, Any]:

# 修改为：
async def generate_image(
    self,
    user_id: str,
    prompt: str,
    model: str = "google/nano-banana",
    size: str = "1:1",
    output_format: str = "png",
    resolution: Optional[str] = None,
    wait_for_result: bool = True,
    conversation_id: Optional[str] = None,  # ← 新增参数
) -> Dict[str, Any]:
```

**修改2**：在调用KIE后保存任务（约第90-100行之间）

```python
# 在 result = await adapter.generate(...) 之后添加

task_id = result.get("task_id")

# 保存任务到数据库
if task_id and conversation_id:
    await self._save_task_to_db(
        user_id=user_id,
        conversation_id=conversation_id,
        task_id=task_id,
        task_type="image",
        request_params={
            "prompt": prompt,
            "model": model,
            "size": size,
            "output_format": output_format,
            "resolution": resolution,
        },
        credits_locked=estimated_credits,
    )

# 5. 如果生成完成,将图片上传到 OSS (现有逻辑)
if wait_for_result and result.get("status") == "success":
    result = await self._upload_images_to_oss(result, user_id)

    # 更新任务状态为完成
    if task_id:
        await self._update_task_status(
            task_id=task_id,
            status="success",
            result=result,
        )
```

**修改3**：在`query_task`方法中更新状态（约第120行）

```python
async def query_task(
    self,
    task_id: str,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    # 1. 查询KIE任务状态 (现有逻辑)
    async with KieClient(self.settings.kie_api_key) as client:
        adapter = KieImageAdapter(client, "google/nano-banana")
        result = await adapter.query_task(task_id)

    # 2. 更新数据库任务状态 ← 新增
    await self._update_task_status(
        task_id=task_id,
        status=result.get("status"),
        result=result if result.get("status") == "success" else None,
        fail_code=result.get("fail_code"),
        fail_msg=result.get("fail_msg"),
    )

    # 3. 如果图片生成完成且提供了 user_id,上传到 OSS (现有逻辑)
    if result.get("status") == "success" and user_id:
        try:
            result = await self._upload_images_to_oss(result, user_id)

            # OSS上传完成后再次更新result ← 新增
            await self._update_task_status(
                task_id=task_id,
                status="success",
                result=result,
            )
        except Exception as e:
            logger.warning(f"Failed to upload images to OSS: task_id={task_id}, error={e}")

    return result
```

### 2.3 修改VideoService

**文件**：`/backend/services/video_service.py`

**修改1**：在`text_to_video`方法签名中添加`conversation_id`参数（约第31行）

```python
# 原来：
async def text_to_video(
    self,
    user_id: str,
    prompt: str,
    model: str = "sora-2-text-to-video",
    n_frames: str = "10s",
    aspect_ratio: str = "16:9",
    remove_watermark: bool = True,
    wait_for_result: bool = False,
) -> Dict[str, Any]:

# 修改为：
async def text_to_video(
    self,
    user_id: str,
    prompt: str,
    model: str = "sora-2-text-to-video",
    n_frames: str = "10s",
    aspect_ratio: str = "16:9",
    remove_watermark: bool = True,
    wait_for_result: bool = False,
    conversation_id: Optional[str] = None,  # ← 新增参数
) -> Dict[str, Any]:
```

**修改2**：在`_generate_with_credits`方法中添加保存逻辑（约第137行）

```python
# 在 result = await adapter.generate(...) 之后添加

task_id = result.get("task_id")

# 保存任务到数据库
if task_id and conversation_id:
    await self._save_task_to_db(
        user_id=user_id,
        conversation_id=conversation_id,
        task_id=task_id,
        task_type="video",
        request_params={
            "model": model,
            "n_frames": n_frames,
            **generate_kwargs,
        },
        credits_locked=estimated_credits,
    )
```

**修改3**：在`query_task`方法中更新状态（类似ImageService）

### 2.4 修改API Schema

**文件1**：`/backend/schemas/image.py`

```python
# 在 GenerateImageRequest 类中添加字段（约第30行）
class GenerateImageRequest(BaseModel):
    prompt: str
    model: ImageModel = ImageModel.NANO_BANANA
    size: AspectRatio = AspectRatio.SQUARE
    output_format: ImageOutputFormat = ImageOutputFormat.PNG
    resolution: Optional[ImageResolution] = None
    wait_for_result: bool = True
    conversation_id: Optional[str] = None  # ← 新增字段
```

**文件2**：`/backend/schemas/video.py`

```python
# 在 GenerateTextToVideoRequest 类中添加字段
class GenerateTextToVideoRequest(BaseModel):
    prompt: str
    model: VideoModel = VideoModel.SORA_2_TEXT_TO_VIDEO
    n_frames: VideoFrames = VideoFrames.FRAMES_10
    aspect_ratio: VideoAspectRatio = VideoAspectRatio.LANDSCAPE
    remove_watermark: bool = True
    wait_for_result: bool = False
    conversation_id: Optional[str] = None  # ← 新增字段
```

### 2.5 修改路由传递参数

**文件1**：`/backend/api/routes/image.py`

```python
# 在 generate_image 路由中传递参数（约第50行）
@router.post("/generate", response_model=GenerateImageResponse)
async def generate_image(
    request: Request,
    body: GenerateImageRequest,
    current_user: CurrentUser,
    service: ImageService = Depends(get_image_service),
):
    result = await service.generate_image(
        user_id=current_user["id"],
        prompt=body.prompt,
        model=body.model.value,
        size=body.size.value,
        output_format=body.output_format.value,
        resolution=body.resolution.value if body.resolution else None,
        wait_for_result=body.wait_for_result,
        conversation_id=body.conversation_id,  # ← 传递参数
    )
    # ...
```

**文件2**：`/backend/api/routes/video.py`（类似修改）

### 2.6 创建任务管理API

**文件**：`/backend/api/routes/task.py`（新建）

```python
"""
任务管理路由

提供任务查询、恢复等接口
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any

from api.deps import CurrentUser, Database

router = APIRouter(prefix="/tasks", tags=["任务管理"])


@router.get("/pending", summary="获取用户进行中任务")
async def get_pending_tasks(
    current_user: CurrentUser,
    db: Database,
) -> Dict[str, Any]:
    """
    获取当前用户所有进行中的任务

    用于页面刷新/登录后恢复轮询。
    返回所有status为'pending'或'running'的任务。
    """
    response = db.table("tasks").select(
        "id, external_task_id, conversation_id, type, status, "
        "request_params, credits_locked, placeholder_message_id, "
        "started_at, last_polled_at"
    ).eq("user_id", current_user["id"]).in_(
        "status", ["pending", "running"]
    ).order("started_at", desc=False).execute()

    return {
        "tasks": response.data,
        "count": len(response.data),
    }


@router.post("/{external_task_id}/fail", summary="手动标记任务失败")
async def mark_task_failed(
    external_task_id: str,
    current_user: CurrentUser,
    db: Database,
    reason: str = "用户取消或超时",
) -> Dict[str, Any]:
    """
    手动标记任务为失败状态

    用于前端超时或用户主动取消任务。
    """
    from datetime import datetime

    # 验证任务属于当前用户
    task = db.table("tasks").select("id").eq(
        "external_task_id", external_task_id
    ).eq("user_id", current_user["id"]).single().execute()

    if not task.data:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 更新状态
    db.table("tasks").update({
        "status": "failed",
        "error_message": reason,
        "completed_at": datetime.utcnow().isoformat(),
    }).eq("external_task_id", external_task_id).execute()

    return {"success": True, "message": "任务已标记为失败"}
```

### 2.7 注册路由

**文件**：`/backend/api/routes/__init__.py`

```python
# 添加导入
from api.routes import task

# 在路由注册部分添加
def register_routes(app: FastAPI):
    # ... 现有路由 ...
    app.include_router(task.router)  # ← 新增
```

---

## Phase 3: 后台轮询服务 (2小时)

### 3.1 创建后台任务处理器（包含抖动和重试）

**文件**：`/backend/services/background_task_worker.py`（新建）

```python
"""
后台任务轮询服务

即使用户离线也继续轮询KIE，任务完成后自动保存结果
"""

import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

from loguru import logger
from supabase import Client

from core.config import Settings, get_settings
from services.adapters.kie.client import KieClient
from services.adapters.kie.image_adapter import KieImageAdapter
from services.adapters.kie.video_adapter import KieVideoAdapter


class BackgroundTaskWorker:
    """后台任务轮询器（带执行锁防止重叠）"""

    def __init__(self, db: Client):
        self.db = db
        self.settings: Settings = get_settings()
        self.is_running = False
        self._poll_lock = asyncio.Lock()  # ← 轮询锁（单进程）

    async def start(self):
        """启动后台工作器"""
        self.is_running = True
        logger.info("BackgroundTaskWorker started")

        while self.is_running:
            try:
                # ⭐ 检查锁，防止轮询重叠
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

    async def stop(self):
        """停止后台工作器"""
        self.is_running = False
        logger.info("BackgroundTaskWorker stopped")

    async def poll_pending_tasks(self):
        """轮询所有pending/running任务（带随机抖动）"""
        # 查询所有进行中的任务
        response = self.db.table("tasks").select("*").in_(
            "status", ["pending", "running"]
        ).execute()

        if not response.data:
            return

        logger.debug(f"Polling {len(response.data)} tasks")

        # ⭐ 随机打散任务（防止惊群效应）
        tasks_shuffled = random.sample(response.data, len(response.data))

        # 动态调整并发数（根据KIE QPS限制）
        kie_qps_limit = getattr(self.settings, 'kie_qps_limit', 50)
        semaphore = asyncio.Semaphore(kie_qps_limit)

        async def process_task_with_jitter(task: dict, index: int):
            # ⭐ 在30秒窗口内均匀分布（随机抖动）
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

    async def query_kie_and_update(self, task: dict):
        """查询KIE并更新任务状态"""
        external_task_id = task["external_task_id"]
        task_type = task["type"]

        try:
            async with KieClient(self.settings.kie_api_key) as client:
                if task_type == "image":
                    adapter = KieImageAdapter(client, "google/nano-banana")
                else:
                    adapter = KieVideoAdapter(client, "sora-2-text-to-video")

                result = await adapter.query_task(external_task_id)

        except Exception as e:
            logger.error(f"KIE query failed: {external_task_id}, error={e}")
            # 更新 last_polled_at，但不标记为失败（等待下次重试）
            self.db.table("tasks").update({
                "last_polled_at": datetime.now(timezone.utc).isoformat(),
            }).eq("external_task_id", external_task_id).execute()
            return

        # 映射KIE状态
        kie_status = result.get("status")
        status_mapping = {
            "waiting": "running",
            "success": "completed",
            "fail": "failed",
        }
        db_status = status_mapping.get(kie_status, "pending")

        update_data = {
            "status": db_status,
            "last_polled_at": datetime.now(timezone.utc).isoformat(),
        }

        # 任务完成
        if db_status == "completed":
            update_data["result"] = result
            update_data["completed_at"] = datetime.now(timezone.utc).isoformat()
            update_data["credits_used"] = result.get("credits_consumed", 0)

            # 自动创建消息
            if task["conversation_id"]:
                await self.save_completed_message(task, result)

        # 任务失败
        elif db_status == "failed":
            update_data["fail_code"] = result.get("fail_code")
            update_data["error_message"] = result.get("fail_msg", "任务失败")
            update_data["completed_at"] = datetime.now(timezone.utc).isoformat()

        # 更新数据库
        self.db.table("tasks").update(update_data).eq(
            "external_task_id", external_task_id
        ).execute()

        logger.info(
            f"Task updated: {external_task_id}, status={db_status}, "
            f"type={task_type}"
        )

    async def save_completed_message(self, task: dict, result: dict):
        """任务完成后自动创建消息"""
        try:
            task_type = task["type"]
            conversation_id = task["conversation_id"]

            message_data = {
                "content": "生成完成",
                "role": "assistant",
                "credits_cost": task["credits_locked"],
                "generation_params": task["request_params"],
            }

            if task_type == "image":
                message_data["image_url"] = result.get("image_urls", [None])[0]
            else:
                message_data["video_url"] = result.get("video_url")

            # 创建消息
            self.db.table("messages").insert(
                {
                    "conversation_id": conversation_id,
                    **message_data,
                }
            ).execute()

            # 标记conversation为未读
            self.db.table("conversations").update({
                "unread": True,
            }).eq("id", conversation_id).execute()

            logger.info(
                f"Message created for task: {task['external_task_id']}, "
                f"conversation={conversation_id}"
            )

        except Exception as e:
            logger.error(
                f"Failed to save completed message: {task['external_task_id']}, "
                f"error={e}"
            )

    async def cleanup_stale_tasks(self):
        """清理超时任务"""
        now = datetime.now(timezone.utc)

        # 查询所有pending/running任务
        response = self.db.table("tasks").select("*").in_(
            "status", ["pending", "running"]
        ).execute()

        cleaned_count = 0

        for task in response.data:
            started_at = datetime.fromisoformat(
                task["started_at"].replace("Z", "+00:00")
            )
            max_duration_minutes = 10 if task["type"] == "image" else 30

            # 检查是否超时
            if (now - started_at).total_seconds() > max_duration_minutes * 60:
                # 标记为失败
                self.db.table("tasks").update({
                    "status": "failed",
                    "error_message": f"任务超时 (超过{max_duration_minutes}分钟)",
                    "completed_at": now.isoformat(),
                }).eq("id", task["id"]).execute()

                cleaned_count += 1
                logger.warning(
                    f"Task timeout: id={task['id']}, "
                    f"external_id={task.get('external_task_id')}, "
                    f"type={task['type']}"
                )

        if cleaned_count > 0:
            logger.info(f"Cleaned {cleaned_count} stale tasks")
```

### 3.2 集成到FastAPI

**文件**：`/backend/main.py`

```python
# 在文件顶部添加导入
from services.background_task_worker import BackgroundTaskWorker
import asyncio

# 在 create_app() 函数中添加
@app.on_event("startup")
async def startup_event():
    """应用启动时执行"""
    # 获取数据库实例
    from api.deps import get_db
    db = next(get_db())

    # 启动后台工作器
    worker = BackgroundTaskWorker(db)
    asyncio.create_task(worker.start())

    logger.info("Application started, background worker running")
```

---

## Phase 4: 前端恢复机制 (2小时)

### 4.1 创建任务恢复工具

**文件**：`/frontend/src/utils/taskRestoration.ts`（新建）

```typescript
/**
 * 任务恢复工具
 */

import { useTaskStore } from '../stores/useTaskStore';
import { useChatStore } from '../stores/useChatStore';
import { useConversationRuntimeStore } from '../stores/useConversationRuntimeStore';
import { queryTaskStatus as getImageTaskStatus } from '../services/image';
import { queryVideoTaskStatus as getVideoTaskStatus } from '../services/video';
import { createMessage } from '../services/message';
import toast from 'react-hot-toast';

interface PendingTask {
  id: string;
  external_task_id: string;
  conversation_id: string;
  type: 'image' | 'video';
  status: string;
  request_params: any;
  credits_locked: number;
  placeholder_message_id: string | null;
  started_at: string;
  last_polled_at: string | null;
}

export async function fetchPendingTasks(): Promise<PendingTask[]> {
  try {
    const response = await fetch('/api/tasks/pending');
    if (!response.ok) throw new Error('获取任务失败');
    const data = await response.json();
    return data.tasks || [];
  } catch (error) {
    console.error('获取进行中任务失败:', error);
    return [];
  }
}

export function restoreTaskPolling(task: PendingTask, conversationTitle: string) {
  const { startMediaTask, startPolling, completeMediaTask, failMediaTask } =
    useTaskStore.getState();
  const { replaceMediaPlaceholder } = useConversationRuntimeStore.getState();
  const { addMessageToCache } = useChatStore.getState();

  const maxDuration = task.type === 'image' ? 10 * 60 * 1000 : 30 * 60 * 1000;
  const elapsed = Date.now() - new Date(task.started_at).getTime();

  if (elapsed > maxDuration) {
    console.warn(`任务 ${task.external_task_id} 已超时,跳过恢复`);
    markTaskAsFailed(task.external_task_id, '任务超时');
    return;
  }

  const placeholderId = task.placeholder_message_id ||
    `restored-${task.external_task_id}`;

  startMediaTask({
    taskId: task.external_task_id,
    conversationId: task.conversation_id,
    conversationTitle,
    type: task.type,
    placeholderId,
  });

  const pollFn = task.type === 'image'
    ? getImageTaskStatus
    : getVideoTaskStatus;

  const pollInterval = task.type === 'image' ? 2000 : 5000;
  const remainingTime = maxDuration - elapsed;

  startPolling(
    task.external_task_id,
    async () => {
      const result = await pollFn(task.external_task_id);
      if (result.status === 'success') return { done: true, result };
      if (result.status === 'failed') return { done: true, error: new Error(result.fail_msg || '任务失败') };
      return { done: false };
    },
    {
      onSuccess: async (result: any) => {
        try {
          const mediaUrl = task.type === 'image' ? result.image_urls[0] : result.video_url;
          const successContent = task.type === 'image' ? '图片已生成完成' : '视频生成完成';

          const savedMessage = await createMessage(task.conversation_id, {
            content: successContent,
            role: 'assistant',
            image_url: task.type === 'image' ? mediaUrl : undefined,
            video_url: task.type === 'video' ? mediaUrl : undefined,
            credits_cost: task.credits_locked,
            generation_params: task.request_params,
          });

          replaceMediaPlaceholder(task.conversation_id, placeholderId, savedMessage);
          addMessageToCache(task.conversation_id, {
            id: savedMessage.id,
            role: 'assistant',
            content: savedMessage.content,
            imageUrl: savedMessage.image_url,
            videoUrl: savedMessage.video_url,
            createdAt: savedMessage.created_at,
          });

          completeMediaTask(task.external_task_id);
          toast.success(`${task.type === 'image' ? '图片' : '视频'}生成完成`);
        } catch (error) {
          console.error('保存任务结果失败:', error);
          failMediaTask(task.external_task_id);
        }
      },
      onError: async (error: Error) => {
        console.error('任务恢复失败:', error);
        failMediaTask(task.external_task_id);
        await markTaskAsFailed(task.external_task_id, error.message);
      },
    },
    { interval: pollInterval, maxDuration: remainingTime }
  );
}

async function markTaskAsFailed(externalTaskId: string, reason: string) {
  try {
    await fetch(`/api/tasks/${externalTaskId}/fail`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    });
  } catch (error) {
    console.error('标记任务失败:', error);
  }
}

export async function restoreAllPendingTasks() {
  const tasks = await fetchPendingTasks();

  if (tasks.length === 0) {
    console.log('没有需要恢复的任务');
    return;
  }

  console.log(`开始恢复 ${tasks.length} 个任务`);

  const conversationTitles = new Map<string, string>();
  const { cachedConversations } = useChatStore.getState();

  for (const conv of cachedConversations) {
    conversationTitles.set(conv.id, conv.title);
  }

  for (const [index, task] of tasks.entries()) {
    setTimeout(() => {
      const title = conversationTitles.get(task.conversation_id) || '未知对话';
      restoreTaskPolling(task, title);
    }, index * 200);
  }

  toast.success(`正在恢复 ${tasks.length} 个任务`);
}
```

### 4.2 创建任务协调器

**文件**：`/frontend/src/utils/taskCoordinator.ts`（新建）

```typescript
/**
 * 任务协调器 - 防止多个标签页同时轮询同一任务
 */

class TaskCoordinator {
  private channel: BroadcastChannel;
  private activeTasks = new Set<string>();
  private tabId: string;

  constructor() {
    this.tabId = this.getOrCreateTabId();
    this.channel = new BroadcastChannel('task-polling-coordinator');

    this.channel.onmessage = (event) => {
      if (event.data.type === 'task-started') {
        this.activeTasks.add(event.data.taskId);
      } else if (event.data.type === 'task-completed') {
        this.activeTasks.delete(event.data.taskId);
      }
    };

    setInterval(() => this.cleanupExpiredLocks(), 30000);
  }

  canStartPolling(taskId: string): boolean {
    const lockKey = `task-lock-${taskId}`;
    const lock = localStorage.getItem(lockKey);

    if (lock) {
      try {
        const lockData = JSON.parse(lock);
        const lockAge = Date.now() - lockData.timestamp;

        if (lockAge < 30000) {
          if (lockData.tabId === this.tabId) return true;
          return false;
        }
      } catch (e) {
        console.warn('解析任务锁失败:', e);
      }
    }

    localStorage.setItem(lockKey, JSON.stringify({
      timestamp: Date.now(),
      tabId: this.tabId,
    }));

    this.channel.postMessage({ type: 'task-started', taskId, tabId: this.tabId });
    this.activeTasks.add(taskId);

    return true;
  }

  releasePolling(taskId: string) {
    localStorage.removeItem(`task-lock-${taskId}`);
    this.activeTasks.delete(taskId);
    this.channel.postMessage({ type: 'task-completed', taskId, tabId: this.tabId });
  }

  renewLock(taskId: string) {
    const lockKey = `task-lock-${taskId}`;
    const lock = localStorage.getItem(lockKey);

    if (lock) {
      try {
        const lockData = JSON.parse(lock);
        if (lockData.tabId === this.tabId) {
          localStorage.setItem(lockKey, JSON.stringify({
            timestamp: Date.now(),
            tabId: this.tabId,
          }));
        }
      } catch (e) {
        console.warn('更新任务锁失败:', e);
      }
    }
  }

  private cleanupExpiredLocks() {
    const now = Date.now();

    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith('task-lock-')) {
        const lock = localStorage.getItem(key);
        if (lock) {
          try {
            const lockData = JSON.parse(lock);
            const lockAge = now - lockData.timestamp;

            if (lockAge > 60000) {
              localStorage.removeItem(key);
            }
          } catch (e) {
            localStorage.removeItem(key);
          }
        }
      }
    }
  }

  private getOrCreateTabId(): string {
    let tabId = sessionStorage.getItem('tab-id');
    if (!tabId) {
      tabId = `tab-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
      sessionStorage.setItem('tab-id', tabId);
    }
    return tabId;
  }

  cleanup() {
    for (const taskId of this.activeTasks) {
      this.releasePolling(taskId);
    }
    this.channel.close();
  }
}

export const taskCoordinator = new TaskCoordinator();

window.addEventListener('beforeunload', () => {
  taskCoordinator.cleanup();
});
```

### 4.3 修改useTaskStore集成协调器

**文件**：`/frontend/src/stores/useTaskStore.ts`

在文件顶部添加导入：
```typescript
import { taskCoordinator } from '../utils/taskCoordinator';
```

修改`startPolling`方法（约第273行）：
```typescript
startPolling: (taskId, pollFn, callbacks, options = {}) => {
  const { interval = 2000, maxDuration } = options;

  // ⭐ 检查是否可以开始轮询
  if (!taskCoordinator.canStartPolling(taskId)) {
    console.log(`任务 ${taskId} 已在其他标签页轮询中,跳过`);
    return;
  }

  const startTime = Date.now();
  let consecutiveFailures = 0;
  const MAX_CONSECUTIVE_FAILURES = 5;

  // ... 现有轮询逻辑 ...

  // 封装回调，确保释放锁
  const wrappedOnSuccess = async (result: unknown) => {
    taskCoordinator.releasePolling(taskId);  // ⭐ 释放锁
    await callbacks.onSuccess(result);
  };

  const wrappedOnError = async (error: Error) => {
    taskCoordinator.releasePolling(taskId);  // ⭐ 释放锁
    await callbacks.onError(error);
  };

  // ... 使用 wrappedOnSuccess 和 wrappedOnError ...

  // 在轮询执行函数中添加锁更新
  if (Date.now() - startTime > 15000) {
    taskCoordinator.renewLock(taskId);  // ⭐ 更新锁
  }
},

stopPolling: (taskId: string) => {
  const state = get();
  const config = state.pollingConfigs.get(taskId);
  if (config) {
    clearInterval(config.intervalId);
    taskCoordinator.releasePolling(taskId);  // ⭐ 释放锁
    set((state) => {
      const newConfigs = new Map(state.pollingConfigs);
      newConfigs.delete(taskId);
      return { pollingConfigs: newConfigs };
    });
  }
},
```

### 4.4 修改Chat.tsx添加恢复逻辑（优化时机）

**文件**：`/frontend/src/pages/Chat.tsx`

在文件顶部添加导入（约第23行）：
```typescript
import { restoreAllPendingTasks } from '../utils/taskRestoration';
```

**方案1：挂载在对话列表加载完成后（推荐）**
```typescript
// 假设有一个加载对话列表的函数
const [conversationsLoaded, setConversationsLoaded] = useState(false);

useEffect(() => {
  const loadConversations = async () => {
    if (!user) return;

    try {
      await getConversationList();
      setConversationsLoaded(true);  // ← 标记加载完成
    } catch (error) {
      console.error('加载对话列表失败:', error);
    }
  };

  loadConversations();
}, [user]);

// ⭐ 恢复任务（在对话列表加载完成后）
useEffect(() => {
  if (!conversationsLoaded) return;

  const restoreTasks = async () => {
    try {
      await restoreAllPendingTasks();
    } catch (error) {
      console.error('恢复任务失败:', error);
    }
  };

  restoreTasks();
}, [conversationsLoaded]);
```

**方案2：挂载在useChatStore初始化完成后**
```typescript
// useChatStore.ts 中添加标志
interface ChatState {
  initialized: boolean;
  // ...
}

// 在 useChatStore 中
useEffect(() => {
  const init = async () => {
    await loadConversations();
    set({ initialized: true });
  };
  init();
}, []);

// Chat.tsx 中
const { initialized } = useChatStore();

useEffect(() => {
  if (initialized && user) {
    restoreAllPendingTasks();
  }
}, [initialized, user]);
```

**方案3：显示恢复UI**
```typescript
const [isRestoringTasks, setIsRestoringTasks] = useState(false);

useEffect(() => {
  if (!conversationsLoaded || !user) return;

  const restoreTasks = async () => {
    setIsRestoringTasks(true);  // ← 显示UI

    try {
      await restoreAllPendingTasks();
    } catch (error) {
      console.error('恢复任务失败:', error);
      toast.error('恢复任务失败，请刷新页面重试');
    } finally {
      setIsRestoringTasks(false);
    }
  };

  restoreTasks();
}, [conversationsLoaded, user]);

// 在 JSX 中显示恢复UI
{isRestoringTasks && (
  <div className="fixed top-20 left-1/2 -translate-x-1/2 z-50">
    <div className="bg-white dark:bg-gray-800 shadow-lg rounded-lg px-4 py-2 flex items-center gap-2">
      <div className="animate-spin h-4 w-4 border-2 border-primary border-t-transparent rounded-full" />
      <span className="text-sm text-gray-700 dark:text-gray-300">
        正在恢复之前的任务...
      </span>
    </div>
  </div>
)}
```

### 4.5 修改前端API调用

**文件1**：`/frontend/src/services/image.ts`

```typescript
export async function generateImage(params: {
  prompt: string;
  model: ImageModel;
  size: AspectRatio;
  output_format: ImageOutputFormat;
  resolution?: ImageResolution;
  wait_for_result?: boolean;
  conversation_id?: string;  // ⭐ 新增参数
}): Promise<GenerateImageResponse> {
  const response = await fetch('/api/images/generate', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getToken()}`,
    },
    body: JSON.stringify(params),
  });
  // ...
}
```

**文件2**：`/frontend/src/hooks/handlers/useImageMessageHandler.ts`

在调用`generateImage`时传入`conversationId`（约第120行）：
```typescript
const response = await generateImage({
  prompt: messageContent,
  model: selectedModel.id as ImageModel,
  size: aspectRatio,
  output_format: outputFormat,
  resolution: selectedModel.supportsResolution ? resolution : undefined,
  wait_for_result: false,
  conversation_id: currentConversationId,  // ⭐ 传入
});
```

**文件3**：`/frontend/src/services/video.ts`（类似修改）
**文件4**：`/frontend/src/hooks/handlers/useVideoMessageHandler.ts`（类似修改）

---

## Phase 5: 测试与验证 (1.5小时)

### 5.1 单元测试

**测试1：数据库操作**
```bash
# 测试保存任务
cd backend
python3 -m pytest tests/test_base_generation_service.py::test_save_task_to_db -v

# 测试更新状态
python3 -m pytest tests/test_base_generation_service.py::test_update_task_status -v
```

**测试2：任务协调器**
```bash
cd frontend
npm run test -- taskCoordinator.test.ts
```

### 5.2 集成测试

**测试1：生成API → 保存任务**
```bash
curl -X POST http://localhost:8000/api/images/generate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "一只可爱的猫咪",
    "model": "google/nano-banana",
    "conversation_id": "uuid",
    "wait_for_result": false
  }'

# 验证tasks表有记录
psql -d everydayai_dev -c "SELECT * FROM tasks WHERE conversation_id='uuid' ORDER BY created_at DESC LIMIT 1;"
```

**测试2：查询API → 更新状态**
```bash
curl -X GET "http://localhost:8000/api/images/tasks/img_abc123?user_id=uuid" \
  -H "Authorization: Bearer $TOKEN"

# 验证last_polled_at已更新
psql -d everydayai_dev -c "SELECT external_task_id, status, last_polled_at FROM tasks WHERE external_task_id='img_abc123';"
```

**测试3：获取进行中任务**
```bash
curl -X GET http://localhost:8000/api/tasks/pending \
  -H "Authorization: Bearer $TOKEN"

# 预期返回JSON数组
```

### 5.3 端到端测试

**场景1：页面刷新**
1. 登录系统
2. 发起图片生成（观察task_id）
3. 页面显示占位符，开始轮询
4. **按F5刷新页面**
5. 验证：
   - [ ] 页面重新加载后显示toast "正在恢复X个任务"
   - [ ] 占位符消息重新出现
   - [ ] 轮询继续（Network标签可见请求）
   - [ ] 图片生成完成后正常显示

**场景2：退出重新登录**
1. 登录系统
2. 发起视频生成
3. 立即退出登录
4. 等待2分钟
5. 重新登录
6. 验证：
   - [ ] 登录后显示toast "正在恢复X个任务"
   - [ ] 如果视频已完成（后台轮询），直接显示结果
   - [ ] 如果视频未完成，恢复轮询，完成后显示

**场景3：多标签页**
1. 登录系统（标签页A）
2. 发起图片生成
3. 复制URL，在新标签页B打开
4. 验证（Chrome DevTools Network标签）：
   - [ ] 只有一个标签页在轮询（另一个显示"已在其他标签页轮询中"）
   - [ ] localStorage中存在`task-lock-{taskId}`锁
   - [ ] 任务完成后两个标签页都显示结果

**场景4：任务超时**
1. 修改超时时间为1分钟（测试用）：
   ```typescript
   // taskRestoration.ts
   const maxDuration = 60 * 1000; // 1分钟
   ```
2. 发起任务
3. 等待1分钟以上
4. 刷新页面
5. 验证：
   - [ ] 控制台显示"任务已超时，跳过恢复"
   - [ ] 数据库tasks表status变为'failed'
   - [ ] error_message为"任务超时"

**场景5：用户离线时任务完成**
1. 登录系统
2. 发起视频生成
3. 退出登录（或关闭浏览器）
4. 等待5分钟（假设视频生成完成）
5. 重新登录
6. 打开对话
7. 验证：
   - [ ] 消息已自动保存（messages表有记录）
   - [ ] 视频URL正确显示
   - [ ] 对话标记为未读（unread=true）

### 5.4 性能验证

**测试1：数据库查询性能**
```sql
EXPLAIN ANALYZE
SELECT * FROM tasks
WHERE user_id = 'uuid'
  AND status IN ('pending', 'running')
ORDER BY started_at ASC;

-- 预期：执行时间 < 50ms
```

**测试2：前端恢复20个任务**
```javascript
// Chrome DevTools Performance
// 1. 创建20个pending任务
// 2. 刷新页面
// 3. 开始Performance录制
// 4. 等待恢复完成
// 5. 停止录制
// 6. 查看 restoreAllPendingTasks 总耗时

// 预期：< 2秒
```

**测试3：后台轮询性能**
```python
# 创建100个pending任务
# 观察后台日志：Polling {count} tasks
# 记录处理时间

# 预期：100个任务 < 30秒
```

---

## 实施顺序

**严格按照以下顺序执行**：

1. ✅ **Phase 1** - 数据库Schema扩展（30分钟）
   - 创建迁移文件
   - 执行迁移
   - 验证表结构

2. ✅ **Phase 2** - 后端核心逻辑（2.5小时）
   - 扩展BaseGenerationService
   - 修改ImageService和VideoService
   - 修改API Schema和路由
   - 创建任务管理API

3. ✅ **Phase 3** - 后台轮询服务（2小时）
   - 创建BackgroundTaskWorker
   - 集成到FastAPI

4. ✅ **Phase 4** - 前端恢复机制（2小时）
   - 创建任务恢复工具
   - 创建任务协调器
   - 修改useTaskStore
   - 修改Chat.tsx
   - 修改API调用

5. ✅ **Phase 5** - 测试与验证（1.5小时）
   - 单元测试
   - 集成测试
   - E2E测试
   - 性能测试

---

## 部署检查清单

### 开发环境
- [ ] 数据库迁移执行成功
- [ ] 后端启动无错误
- [ ] 前端编译无警告
- [ ] 所有单元测试通过
- [ ] 所有集成测试通过
- [ ] 所有E2E测试通过

### Staging环境
- [ ] 数据库迁移执行成功
- [ ] 后台工作器正常启动
- [ ] 冒烟测试通过（5个E2E场景）
- [ ] 性能指标达标

### 生产环境
- [ ] 数据库备份完成
- [ ] 迁移脚本就绪（含回滚）
- [ ] 监控告警配置完成
- [ ] 灰度发布计划确认

---

## 回滚计划

如果生产环境出现问题，执行以下回滚步骤：

```bash
# 1. 停止后台工作器（重启应用，注释掉startup_event）

# 2. 回滚数据库
psql -d everydayai_prod < docs/database/migrations/rollback/010_rollback_extend_tasks.sql

# 3. 回滚代码（git revert或重新部署）
git revert <commit-hash>
git push origin main

# 4. 验证核心功能正常
curl -X POST .../api/images/generate ...
```

---

## 文档更新清单

实施完成后必须更新以下文档：

- [ ] `/docs/API_REFERENCE.md` - 添加 `/tasks/pending` API文档
- [ ] `/docs/PROJECT_OVERVIEW.md` - 更新tasks表结构说明
- [ ] `/docs/FUNCTION_INDEX.md` - 添加新增函数索引
- [ ] `/docs/CURRENT_ISSUES.md` - 标记"任务刷新丢失"问题已解决
- [ ] `/docs/document/TECH_TASK_PERSISTENCE.md` - 技术方案存档
- [ ] `/docs/document/IMPL_TASK_PERSISTENCE.md` - 实施计划存档（本文件）

---

## 监控指标

部署后需要监控以下指标：

| 指标 | 目标 | 告警阈值 |
|------|------|----------|
| 任务成功率 | > 95% | < 90% |
| 任务平均耗时（图片） | < 60秒 | > 120秒 |
| 任务平均耗时（视频） | < 300秒 | > 600秒 |
| 后台轮询延迟 | < 60秒 | > 300秒 |
| 数据库查询时间 | < 50ms | > 100ms |
| 前端恢复时间（20任务） | < 2秒 | > 5秒 |

---

## 创建时间
2026-01-30

## 最后更新
2026-01-30
