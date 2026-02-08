# 技术设计：Webhook 回调 + 轮询兜底（多 Provider 兼容）

## 1. 技术栈

- 后端：Python 3.x + FastAPI
- 复用组件：`BaseHandler.on_complete/on_error`、`WebSocketManager`、适配器体系
- 无需新增依赖

## 2. 核心设计原则

### 2.1 统一处理路径

**问题**：当前轮询的 `save_completed_message()` 和 Handler 的 `on_complete()` 是两套完全不同的逻辑：

| | save_completed_message（轮询） | handler.on_complete |
|--|-------------------------------|---------------------|
| 消息操作 | INSERT 新消息 | UPDATE 占位符 |
| 消息格式 | `image_url: "..."` 旧字段 | `content: [{type:image}]` 新格式 |
| 积分确认 | 不确认（credits_used=0） | 调用 `_confirm_deduct()` |

**方案**：Webhook 和轮询兜底**统一调用 `handler.on_complete()`**，删掉 `save_completed_message()`。

### 2.2 多 Provider 兼容原则

**现状**：适配器层已有多 Provider 架构（`ModelProvider` 枚举 + 工厂模式），回调层需同步对齐。

**原则**：
1. **统一结果格式**：所有 Provider 的回调统一转换为 `ImageGenerateResult` / `VideoGenerateResult`
2. **Provider 隔离**：每个 Provider 的回调解析逻辑封装在自己的适配器中
3. **路由分发**：Webhook 路由根据 Provider 分发到对应的解析器
4. **TaskCompletionService 无感知**：统一服务只接收标准结果，不关心来源

### 2.3 数据流

```
                ┌─────────────────────────────────────────────┐
                │       任意 AI Provider（KIE / Google / ...） │
                └──────────┬──────────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              │                         │
    ① 主路径（毫秒级）          ② 兜底路径（2分钟）
    Webhook POST 回调          BackgroundTaskWorker 轮询
              │                         │
    Provider 回调解析器          adapter.query_task()
    parse_callback()                    │
              │                         │
              └────────────┬────────────┘
                           │
              ImageGenerateResult / VideoGenerateResult
                    （统一结果格式）
                           │
                    ③ 统一入口
              TaskCompletionService
              .process_result()
                           │
              ┌────────────┴────────────┐
              │                         │
         成功 → handler.on_complete()  失败 → handler.on_error()
              │                         │
              ├─ 确认积分               ├─ 退回积分
              ├─ OSS 上传               ├─ 更新消息为失败
              ├─ UPDATE 占位符消息       └─ WebSocket 推送
              └─ WebSocket 推送
```

## 3. 目录结构

### 新增文件
- `backend/api/routes/webhook.py`：多 Provider 回调接收路由
- `backend/services/task_completion_service.py`：统一任务完成处理服务

### 修改文件
- `backend/core/config.py`：添加 `callback_base_url` 配置
- `backend/services/adapters/base.py`：`BaseImageAdapter` / `BaseVideoAdapter` 新增 `parse_callback()` 抽象方法
- `backend/services/adapters/kie/image_adapter.py`：实现 `parse_callback()`
- `backend/services/adapters/kie/video_adapter.py`：实现 `parse_callback()`
- `backend/services/handlers/base.py`：添加 `_build_callback_url()`
- `backend/services/handlers/image_handler.py`：`start()` 传递 `callback_url`
- `backend/services/handlers/video_handler.py`：`start()` 传递 `callback_url`
- `backend/services/background_task_worker.py`：降级为兜底 + 调用统一入口
- `backend/main.py`：注册 webhook 路由
- `.env.example`：添加 `CALLBACK_BASE_URL`

## 4. API 设计

### POST /api/webhook/{provider}

> Provider 任务完成回调端点（无需用户鉴权，支持多 Provider）

路由示例：
- `POST /api/webhook/kie` — KIE 平台回调
- `POST /api/webhook/google` — Google 平台回调（未来）
- `POST /api/webhook/openai` — OpenAI 平台回调（未来）

#### KIE 请求格式

```json
{
  "taskId": "kie-task-id-xxx",
  "state": "success",
  "resultJson": "{\"resultUrls\": [\"https://cdn.kie.ai/temp/xxx.png\"]}",
  "failCode": null,
  "failMsg": null,
  "costTime": 12345
}
```

| 参数 | 类型 | 必填 | 说明 |
|-----|------|------|------|
| taskId | string | 是 | KIE 任务 ID（= tasks.external_task_id） |
| state | string | 是 | `success` / `fail` |
| resultJson | string | 否 | JSON 字符串，含 `resultUrls` 数组 |
| failCode | string | 否 | 失败错误码 |
| failMsg | string | 否 | 失败原因 |
| costTime | int | 否 | 耗时（毫秒） |

#### 统一响应

| 状态码 | 说明 |
|--------|------|
| 200 | 处理成功（含幂等重复） |
| 400 | 参数缺失 |
| 404 | 任务不存在 |
| 500 | 内部错误（Provider 会重试） |

**设计说明**：
- 即使任务已完成也返回 200（幂等），避免 Provider 无限重试
- 500 时 Provider 会重试，所以内部异常要区分可重试/不可重试
- 每个 Provider 的 payload 格式不同，由各自适配器的 `parse_callback()` 负责解析

## 5. 核心模块设计

### 5.1 适配器层扩展：`parse_callback()`

在 `BaseImageAdapter` / `BaseVideoAdapter` 中新增回调解析抽象方法：

```python
# backend/services/adapters/base.py

class BaseImageAdapter(ABC):
    # ... 现有方法 ...

    @classmethod
    @abstractmethod
    def parse_callback(cls, payload: Dict[str, Any]) -> ImageGenerateResult:
        """
        解析 Provider 回调 payload 为统一结果格式

        每个 Provider 实现自己的解析逻辑：
        - KIE: taskId + state + resultJson
        - Google: 待定
        - OpenAI: 待定

        Args:
            payload: Provider 发送的原始回调数据

        Returns:
            ImageGenerateResult: 统一结果格式

        Raises:
            ValueError: payload 格式无效
        """
        pass

    @classmethod
    @abstractmethod
    def extract_task_id(cls, payload: Dict[str, Any]) -> str:
        """
        从回调 payload 中提取任务 ID

        用于在解析前快速定位任务记录

        Args:
            payload: Provider 发送的原始回调数据

        Returns:
            external_task_id
        """
        pass


class BaseVideoAdapter(ABC):
    # ... 现有方法 ...

    @classmethod
    @abstractmethod
    def parse_callback(cls, payload: Dict[str, Any]) -> VideoGenerateResult:
        """解析 Provider 回调 payload 为统一结果格式"""
        pass

    @classmethod
    @abstractmethod
    def extract_task_id(cls, payload: Dict[str, Any]) -> str:
        """从回调 payload 中提取任务 ID"""
        pass
```

#### KIE 适配器实现

```python
# backend/services/adapters/kie/image_adapter.py

class KieImageAdapter(BaseImageAdapter):
    # ... 现有方法 ...

    @classmethod
    def extract_task_id(cls, payload: Dict[str, Any]) -> str:
        task_id = payload.get("taskId")
        if not task_id:
            raise ValueError("Missing taskId in KIE callback")
        return task_id

    @classmethod
    def parse_callback(cls, payload: Dict[str, Any]) -> ImageGenerateResult:
        """解析 KIE 回调格式"""
        task_id = cls.extract_task_id(payload)
        state = payload.get("state")

        if state == "success":
            # 解析 resultJson
            result_json = payload.get("resultJson", "{}")
            if isinstance(result_json, str):
                import json
                result_data = json.loads(result_json)
            else:
                result_data = result_json

            image_urls = result_data.get("resultUrls", [])

            return ImageGenerateResult(
                task_id=task_id,
                status=TaskStatus.SUCCESS,
                image_urls=image_urls,
                cost_time_ms=payload.get("costTime"),
            )
        else:
            return ImageGenerateResult(
                task_id=task_id,
                status=TaskStatus.FAILED,
                fail_code=payload.get("failCode", "UNKNOWN"),
                fail_msg=payload.get("failMsg", "任务失败"),
                cost_time_ms=payload.get("costTime"),
            )
```

### 5.2 Webhook 路由：Provider 分发

```python
# backend/api/routes/webhook.py

from fastapi import APIRouter, Request
from services.adapters.base import ModelProvider
from services.task_completion_service import TaskCompletionService

router = APIRouter(prefix="/api/webhook", tags=["webhook"])

# Provider → 适配器类映射（回调解析用）
CALLBACK_PARSERS = {
    ModelProvider.KIE: {
        "image": KieImageAdapter,
        "video": KieVideoAdapter,
    },
    # 未来扩展：
    # ModelProvider.GOOGLE: {
    #     "image": GoogleImageAdapter,
    #     "video": GoogleVideoAdapter,
    # },
}

@router.post("/{provider}")
async def handle_webhook(provider: str, request: Request):
    """
    统一 webhook 入口，根据 provider 分发到对应解析器

    流程：
    1. 验证 provider 有效性
    2. 从 payload 提取 task_id
    3. 查询任务记录获取 task_type（image/video）
    4. 调用对应适配器的 parse_callback()
    5. 传递统一结果给 TaskCompletionService
    """
    # 1. 验证 provider
    try:
        model_provider = ModelProvider(provider)
    except ValueError:
        return {"error": f"Unknown provider: {provider}"}, 400

    parsers = CALLBACK_PARSERS.get(model_provider)
    if not parsers:
        return {"error": f"No callback parser for: {provider}"}, 400

    # 2. 解析 payload
    payload = await request.json()

    # 3. 提取 task_id（使用任意一个 parser，extract_task_id 是 Provider 级别通用的）
    first_parser = next(iter(parsers.values()))
    try:
        task_id = first_parser.extract_task_id(payload)
    except ValueError as e:
        return {"error": str(e)}, 400

    # 4. 查询任务获取类型
    service = TaskCompletionService(db)
    task = service.get_task(task_id)
    if not task:
        return {"error": "Task not found"}, 404

    task_type = task["type"]  # "image" / "video"

    # 5. 使用对应类型的解析器
    parser_class = parsers.get(task_type)
    if not parser_class:
        return {"error": f"No parser for type: {task_type}"}, 400

    result = parser_class.parse_callback(payload)

    # 6. 调用统一处理
    await service.process_result(task_id, result)

    return {"status": "ok"}
```

### 5.3 统一完成处理服务（新增）

`backend/services/task_completion_service.py`

```python
from services.adapters.base import (
    ImageGenerateResult,
    VideoGenerateResult,
    TaskStatus,
)
from typing import Union

TaskResult = Union[ImageGenerateResult, VideoGenerateResult]

class TaskCompletionService:
    """
    统一任务完成处理入口

    接收标准 ImageGenerateResult / VideoGenerateResult，
    不关心结果来自 Webhook 还是轮询、来自哪个 Provider。

    保证：
    1. 幂等性：已完成的任务不重复处理
    2. 格式一致：统一走 handler.on_complete/on_error
    3. OSS 上传：在调用 handler 前完成
    """

    def __init__(self, db: Client):
        self.db = db

    async def process_result(self, task_id: str, result: TaskResult) -> bool:
        """
        统一处理入口

        Args:
            task_id: external_task_id
            result: 统一结果（ImageGenerateResult 或 VideoGenerateResult）

        Returns:
            True = 已处理（含幂等跳过），False = 处理失败
        """
        # 1. 查询任务（幂等检查）
        task = self.get_task(task_id)
        if not task:
            return False

        if task["status"] in ("completed", "failed"):
            logger.info(f"Task already {task['status']} | task_id={task_id}")
            return True  # 幂等

        # 2. 根据结果状态分发
        if result.status == TaskStatus.SUCCESS:
            return await self._handle_success(task, result)
        elif result.status == TaskStatus.FAILED:
            return await self._handle_failure(task, result)
        else:
            # pending/processing 状态忽略
            return True

    async def _handle_success(self, task: dict, result: TaskResult) -> bool:
        """处理成功结果"""
        task_id = task["external_task_id"]
        task_type = task["type"]
        user_id = task["user_id"]

        # 1. OSS 上传（临时 URL → 持久化）
        oss_urls = await self._upload_result_to_oss(result, user_id, task_type)

        # 2. 转换为 ContentPart 数组
        content_parts = self._build_content_parts(oss_urls, task_type)

        # 3. 创建 Handler 并调用 on_complete
        handler = self._create_handler(task_type)
        await handler.on_complete(
            task_id=task_id,
            result=content_parts,
        )

        return True

    async def _handle_failure(self, task: dict, result: TaskResult) -> bool:
        """处理失败结果"""
        task_id = task["external_task_id"]
        task_type = task["type"]

        handler = self._create_handler(task_type)
        await handler.on_error(
            task_id=task_id,
            error_code=result.fail_code or "UNKNOWN",
            error_message=result.fail_msg or "任务失败",
        )

        return True

    def _create_handler(self, task_type: str) -> BaseHandler:
        """根据任务类型创建 Handler"""
        if task_type == "image":
            return ImageHandler(self.db)
        elif task_type == "video":
            return VideoHandler(self.db)
        else:
            raise ValueError(f"Unknown task type: {task_type}")
```

### 5.4 幂等性保证

```
process_result(task_id, result):
    │
    ├─ 查询 tasks 表 WHERE external_task_id = task_id
    │
    ├─ status = completed → 日志 + return True（已处理）
    ├─ status = failed    → 日志 + return True（已处理）
    │
    ├─ status = pending/running → 正常处理
    │   ├─ result.status = SUCCESS:
    │   │   ├─ OSS 上传
    │   │   ├─ 转换 ContentPart
    │   │   ├─ handler.on_complete()
    │   │   └─ handler 内部标记 completed
    │   │
    │   └─ result.status = FAILED:
    │       ├─ handler.on_error()
    │       └─ handler 内部标记 failed + 退回积分
    │
    └─ 异常时不改状态，让轮询兜底重试
```

### 5.5 Handler 改造

#### base.py 新增方法

```python
def _build_callback_url(self, provider: ModelProvider) -> Optional[str]:
    """
    构建回调 URL，未配置则返回 None

    URL 格式：{base_url}/api/webhook/{provider}
    不同 Provider 走不同的回调路由
    """
    base_url = get_settings().callback_base_url
    if not base_url:
        return None
    return f"{base_url}/api/webhook/{provider.value}"
```

#### image_handler.start() 改造

```python
# 当前
result = await adapter.generate(
    prompt=prompt,
    wait_for_result=False,
)

# 改为
result = await adapter.generate(
    prompt=prompt,
    callback_url=self._build_callback_url(adapter.provider),  # Provider 感知
    wait_for_result=False,
)
```

#### video_handler.start() 同理

```python
result = await adapter.generate(
    prompt=prompt,
    image_urls=[image_url] if image_url else None,
    callback_url=self._build_callback_url(adapter.provider),  # Provider 感知
    wait_for_result=False,
)
```

### 5.6 轮询降级改造

`background_task_worker.py` 改造：

```
当前（每 30 秒）:
    poll_pending_tasks()     → 查询 KIE → save_completed_message()
    cleanup_stale_tasks()    → 超时清理

改为（每 2 分钟）:
    poll_pending_tasks()     → adapter.query_task() → TaskCompletionService.process_result()
    cleanup_stale_tasks()    → 超时清理（不变）
```

```python
# 改造后的 query_and_update（伪码）
async def query_and_update(self, task: dict):
    task_type = task["type"]
    model_id = task.get("model_id")

    # 1. 使用工厂创建适配器（自动路由到正确 Provider）
    adapter = create_image_adapter(model_id) if task_type == "image" \
              else create_video_adapter(model_id)

    # 2. 查询任务状态（返回统一结果格式）
    result = await adapter.query_task(task["external_task_id"])

    # 3. 如果已完成/失败，交给统一处理服务
    if result.status in (TaskStatus.SUCCESS, TaskStatus.FAILED):
        service = TaskCompletionService(self.db)
        await service.process_result(task["external_task_id"], result)
```

变更点：
1. **间隔**：30秒 → 120秒
2. **完成处理**：`save_completed_message()` → `TaskCompletionService.process_result()`
3. **删除** `save_completed_message()` 和 `_notify_task_status()`
4. **使用 model_id** 创建适配器（而非硬编码 `google/nano-banana`）

## 6. 多 Provider 扩展示例

### 6.1 新增 Google Provider 回调（未来）

只需 3 步：

**Step 1**：实现 `parse_callback()`

```python
# backend/services/adapters/google/image_adapter.py

class GoogleImageAdapter(BaseImageAdapter):
    @classmethod
    def extract_task_id(cls, payload: Dict[str, Any]) -> str:
        return payload.get("operationId", "")

    @classmethod
    def parse_callback(cls, payload: Dict[str, Any]) -> ImageGenerateResult:
        """解析 Google 回调格式"""
        task_id = cls.extract_task_id(payload)
        done = payload.get("done", False)

        if done and "response" in payload:
            images = payload["response"].get("images", [])
            urls = [img["uri"] for img in images]
            return ImageGenerateResult(
                task_id=task_id,
                status=TaskStatus.SUCCESS,
                image_urls=urls,
            )
        elif done and "error" in payload:
            error = payload["error"]
            return ImageGenerateResult(
                task_id=task_id,
                status=TaskStatus.FAILED,
                fail_code=str(error.get("code")),
                fail_msg=error.get("message"),
            )
        else:
            return ImageGenerateResult(
                task_id=task_id,
                status=TaskStatus.PROCESSING,
            )
```

**Step 2**：注册到 `CALLBACK_PARSERS`

```python
# backend/api/routes/webhook.py

CALLBACK_PARSERS = {
    ModelProvider.KIE: { ... },
    ModelProvider.GOOGLE: {
        "image": GoogleImageAdapter,
        "video": GoogleVideoAdapter,  # 如果有
    },
}
```

**Step 3**：配置回调 URL（自动生效）

Handler 的 `_build_callback_url(adapter.provider)` 会自动生成：
- KIE 模型 → `{base}/api/webhook/kie`
- Google 模型 → `{base}/api/webhook/google`

**不需要改动的部分**：
- TaskCompletionService（只接收统一结果）
- Handler.on_complete / on_error（不关心来源）
- WebSocket 推送逻辑
- 前端代码

### 6.2 架构层级图

```
┌─────────────────────────────────────────────────────┐
│                  Webhook 路由层                       │
│  /api/webhook/{provider}                            │
│  职责：接收请求 → 路由到 Provider 解析器              │
└───────────────────────┬─────────────────────────────┘
                        │
┌───────────────────────┴─────────────────────────────┐
│              Provider 回调解析层                      │
│  KieImageAdapter.parse_callback()                   │
│  KieVideoAdapter.parse_callback()                   │
│  GoogleImageAdapter.parse_callback()  (未来)         │
│  职责：Provider 原始 payload → 统一 Result 格式       │
└───────────────────────┬─────────────────────────────┘
                        │
            ImageGenerateResult / VideoGenerateResult
                        │
┌───────────────────────┴─────────────────────────────┐
│           TaskCompletionService（统一处理层）          │
│  process_result(task_id, result)                    │
│  职责：幂等检查 → OSS 上传 → Handler 调用            │
│  ⚠️ 不关心 Provider、不关心来源（Webhook/轮询）        │
└───────────────────────┬─────────────────────────────┘
                        │
┌───────────────────────┴─────────────────────────────┐
│              Handler 层（现有，不改动）                │
│  ImageHandler.on_complete() / on_error()            │
│  VideoHandler.on_complete() / on_error()            │
│  职责：更新消息 → 确认/退回积分 → WebSocket 推送       │
└─────────────────────────────────────────────────────┘
```

## 7. 边缘情况处理

### 7.1 回调先于前端 WebSocket 到达

**场景**：Provider 秒级完成，回调到达时前端还没建立 WebSocket 连接

**现有保障**（不需要改动）：
- `send_to_user()` 发给用户所有连接，无连接时静默丢弃
- 前端刷新后 `initializeTaskRestoration()` 从 API 获取任务状态
- `_check_and_send_completed_task()` 在 WebSocket 订阅时补发

**结论**：已有兜底，无需额外处理

### 7.2 回调重复发送

**场景**：Provider 重试机制发送多次回调

**保障**：
```
TaskCompletionService.process_result():
    task = get_task(task_id)
    if task.status in ('completed', 'failed'):
        return True  # 幂等返回
```

**结论**：统一入口天然幂等

### 7.3 回调和轮询同时处理

**场景**：Webhook 回调到达的同时，轮询也发现任务完成

**保障**：
- 两者都经过 `TaskCompletionService`
- 第一个进入的正常处理
- 第二个发现 status 已 completed，直接返回

**结论**：幂等保证

### 7.4 Provider 永不回调

**场景**：回调 URL 不可达、Provider 故障

**保障**：
- 轮询兜底每 2 分钟检查
- `cleanup_stale_tasks()` 超时清理（image 30分钟 / video 120分钟）

**结论**：轮询兜底

### 7.5 回调时对话已删除

**场景**：用户删除对话后 Provider 才完成

**保障**：
- `handler.on_complete()` → `_update_message()` 失败
- 捕获异常，标记任务为 failed
- 退回积分

**需要新增**：on_complete 中增加 conversation 存在性检查

### 7.6 回调数据缺少关键字段

**场景**：resultJson 为空、resultUrls 为空数组

**保障**：
```
adapter.parse_callback(payload):
    → 缺 taskId → raise ValueError → webhook 返回 400

TaskCompletionService:
    → result.image_urls 为空 → handler.on_error(task_id, "NO_RESULT", "生成结果为空")
```

### 7.7 服务重启错过回调

**场景**：回调到达时服务正在重启

**保障**：
- Provider 回调收到非 200 会重试
- 即使重试也失败，轮询兜底会在 2 分钟内发现

**结论**：双重保障

### 7.8 callback_base_url 未配置

**场景**：开发环境或未配置公网域名

**保障**：
```python
callback_url = self._build_callback_url(adapter.provider)  # 返回 None
# adapter.generate(callback_url=None) → Provider 不回调
# 退回纯轮询模式（2 分钟间隔）
```

**结论**：优雅降级

### 7.9 未知 Provider 发送回调

**场景**：收到不在 `CALLBACK_PARSERS` 中的 Provider 回调

**保障**：
```python
@router.post("/{provider}")
async def handle_webhook(provider: str, ...):
    if provider not in CALLBACK_PARSERS:
        return {"error": f"Unknown provider"}, 400
```

**结论**：路由层拒绝

## 8. 配置变更

```python
# core/config.py 新增
callback_base_url: Optional[str] = None
```

```bash
# .env.example 新增
# Webhook 回调地址（需公网可访问，未配置则退回轮询模式）
# 所有 Provider 共用同一个 base URL，通过路径区分
CALLBACK_BASE_URL=
```

## 9. 开发任务拆分

### 阶段 1：适配器层扩展 + 统一处理服务

- [ ] 1.1 `config.py` 添加 `callback_base_url`
- [ ] 1.2 `base.py` 新增 `parse_callback()` + `extract_task_id()` 抽象方法
- [ ] 1.3 KIE `image_adapter.py` 实现 `parse_callback()`
- [ ] 1.4 KIE `video_adapter.py` 实现 `parse_callback()`
- [ ] 1.5 新建 `task_completion_service.py`（统一入口 + 幂等 + OSS 上传）

### 阶段 2：Webhook 路由 + Handler 改造

- [ ] 2.1 新建 `webhook.py` 路由（多 Provider 分发）
- [ ] 2.2 `main.py` 注册路由
- [ ] 2.3 `base.py` 添加 `_build_callback_url(provider)`
- [ ] 2.4 `image_handler.start()` 传递 `callback_url`
- [ ] 2.5 `video_handler.start()` 传递 `callback_url`

### 阶段 3：轮询降级

- [ ] 3.1 `background_task_worker.py` 改为调用 `TaskCompletionService.process_result()`
- [ ] 3.2 使用 `model_id` 创建适配器（替换硬编码）
- [ ] 3.3 删除 `save_completed_message()` 和 `_notify_task_status()`
- [ ] 3.4 轮询间隔改为 120 秒

### 阶段 4：验证 + 文档

- [ ] 4.1 端到端测试
- [ ] 4.2 更新 FUNCTION_INDEX.md、PROJECT_OVERVIEW.md

## 10. 依赖变更

无需新增依赖。

## 11. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| callback_base_url 不可达 | 高 | 未配置时自动退回轮询 |
| 删除 save_completed_message 引入回归 | 中 | 统一入口覆盖所有场景 + 完整测试 |
| Provider 回调格式与预期不符 | 中 | parse_callback 容错解析 + 日志 |
| 并发竞态（回调+轮询） | 低 | TaskCompletionService 幂等检查 |
| 新 Provider 接入遗漏 parse_callback | 低 | 抽象方法强制实现（编译期检查） |

## 12. 文档更新清单

- [ ] FUNCTION_INDEX.md
- [ ] PROJECT_OVERVIEW.md
