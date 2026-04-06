# 技术设计：Redis Streams WS 可靠投递

## 1. 现有代码分析

### 已阅读文件及关键理解

| 文件 | 关键理解 |
|------|---------|
| `backend/services/websocket_manager.py` | 全局单例 `ws_manager`，每个 Worker 各一份。`send_to_task_or_user()` 本地投递 + Redis Pub/Sub 跨 Worker。**致命问题：Pub/Sub 是 fire-and-forget，客户端离线时消息永久丢失** |
| `backend/services/websocket_redis.py` | `RedisPubSubMixin`，`_publish()` 发到 `ws:broadcast` channel，`_deliver_from_redis()` 按 target_type 投递。`source == worker_id` 跳过自己，**单 Worker 场景下如果本地也找不到连接 → 消息丢失** |
| `backend/api/routes/ws.py` | WS 端点，`subscribe` 时查 `accumulated_content` 补发 + `_check_and_send_completed_task` 检查已完成任务。**但只补发"最终结果"，不补发中间流式 chunk** |
| `backend/services/handlers/chat_handler.py` | `_stream_generate()` 流式产出 chunk，每个 chunk 调用 `send_to_task_or_user(task_id, user_id, msg)`。每 20 chunk 存 `accumulated_content` 到 DB |
| `backend/services/handlers/chat_tool_mixin.py` | tool_call/tool_result 通知用 `send_to_task_or_user` ✅ |
| `backend/services/erp_agent.py:441` | `_notify_progress()` 用 `send_to_task_subscribers` ❌ 缺少 user fallback，跨 Worker 投递失败 |
| `backend/services/handlers/mixins/message_mixin.py` | `_push_ws_message()` → `send_to_task_or_user`，`_handle_complete_common` 构建 `message_done` 并推送 |
| `frontend/src/hooks/useWebSocket.ts` | `subscribeTask` 只在 WS OPEN 时发送，**WS 断线时订阅静默丢弃，重连后不重新订阅** |
| `frontend/src/contexts/wsMessageHandlers.ts` | chunk 缓冲 16ms flush，`message_done` 先 flush 再处理 |
| `frontend/src/stores/slices/streamingSlice.ts` | `appendStreamingContent` 更新 optimisticMessages Map |
| `frontend/src/services/messageSender.ts` | Phase 1.5 提前订阅 `subscribeTask(clientTaskId, conversationId)` |
| `backend/services/wecom/wecom_message_service.py` | **企微完全独立**：用 `generate_complete()`（非流式）+ `ws_client.send_stream_chunk()`（企微协议）。不走 `WebSocketManager`。只在最后通知 Web 端 `send_to_user(conversation_updated)` |
| `backend/wecom_ws_runner.py` | 独立进程，管理企微 WS 长连接，与 Web 4 Worker 无关 |

### 可复用模块
- `core/redis.py` 的 `RedisClient` — 已有 Redis 连接池，直接复用
- `ws.py` 的 `_check_and_send_completed_task` — 已有"任务完成补发"逻辑，Stream 方案将覆盖它
- `chat_handler.py` 的 `_save_accumulated_content` — Stream 替代后此逻辑可简化

### 设计约束
- 企微链路完全独立，**本次不改动企微任何代码**
- 前端 WS 协议保持兼容，消息格式不变
- 4 Worker 继续保留，不降性能
- Redis Stream key 必须设 TTL，防止内存泄漏

### 连锁修改清单

| 改动点 | 影响文件 | 必须同步修改 |
|-------|---------|------------|
| 新增 `stream_publish()` 替代 `send_to_task_or_user` 的任务消息 | `chat_handler.py`, `chat_tool_mixin.py`, `message_mixin.py`, `erp_agent.py`, `batch_completion_service.py`, `base.py:385` | 所有调用处改为 `stream_publish` |
| WS subscribe 改为从 Stream 读取 | `ws.py` | `_handle_message` 的 subscribe 分支重写 |
| 前端重连带 `last_stream_id` | `useWebSocket.ts`, `WebSocketContext.tsx` | `subscribeTask` 增加参数，重连逻辑补全 |
| Stream 清理 | `message_mixin.py` 的 `_handle_complete_common` | 任务完成后设置 Stream EXPIRE |
| `send_to_task_subscribers` 调用修正 | `erp_agent.py:441` | 改为 `stream_publish` |

---

## 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| WS 断线期间有新 chunk | Stream 持久化，重连后 XRANGE 补发 | ws.py consumer |
| WS 断线 → 任务已完成 → WS 重连 | subscribe 时 XRANGE 返回所有消息含 message_done | ws.py subscribe |
| Stream key 已过期（任务完成>10分钟后重连） | fallback 到 `_check_and_send_completed_task`（从 DB 读取最终消息） | ws.py subscribe |
| 同一用户多 Tab 同时订阅 | 每个 WS 连接独立 consumer，各自 XREAD | ws.py consumer |
| Redis 不可用 | 降级为直接 Pub/Sub 投递（现有逻辑） | stream_service.py |
| Stream 消息积压（极慢网络） | MAXLEN 1000 cap，防止 OOM | stream_service.py |
| 任务被取消 | 写入 cancel 事件到 Stream → consumer 检测后关闭 | ws.py consumer |
| 200 人同时在线 | 200 连接 × 平均 5 个活跃 Stream = 1000 XREAD，Redis 轻松支撑 | 无需特殊处理 |
| Worker 重启 | Stream 数据在 Redis 不丢失，新 Worker 的 consumer 继续读取 | 自动恢复 |
| 前端页面刷新 | 新 WS 连接 + taskRestoration Phase 2 订阅 → 带 last_stream_id="0" 全量补发 | taskRestoration.ts |

---

## 3. 技术栈
- 前端：React + TypeScript + Zustand（不变）
- 后端：Python 3.11 + FastAPI + uvicorn 4 workers（不变）
- 消息总线：**Redis Streams**（替代 Redis Pub/Sub 做任务消息投递）
- 数据库：Supabase PostgreSQL（不变）
- 企微：不变

---

## 4. 架构设计

### 4.1 核心流程变更

```
=== 改前 ===
chat_handler → ws_manager.send_to_task_or_user() → 本地找人 → 找不到就丢
                                                  → Redis Pub/Sub → 其他 Worker → 找不到也丢

=== 改后 ===
chat_handler → task_stream.publish(task_id, user_id, message)
                  ↓
              Redis XADD stream:task:{task_id}  ← 持久化！
                  ↓
WS handler   → asyncio.Task: XREAD BLOCK stream:task:{task_id}
                  ↓
              ws.send_json(message) → 前端
```

### 4.2 不变的部分
- **企微链路**：完全不动。`wecom_message_service.py` 用 `generate_complete()` + `ws_client.send_stream_chunk()`，与本方案无关
- **用户级通知**（credits_changed, memory_extracted, conversation_updated）：继续用 `ws_manager.send_to_user()` + Redis Pub/Sub，这些是 fire-and-forget 场景，丢了无影响
- **前端消息格式**：WS 推送给前端的 JSON 格式完全不变，前端渲染逻辑零改动
- **streamingSlice / useUnifiedMessages / MessageItem**：全部不变

---

## 5. 目录结构

### 新增文件
| 文件 | 职责 |
|------|------|
| `backend/services/task_stream.py` | Redis Streams 读写封装（publish / consume / replay / cleanup） |

### 修改文件
| 文件 | 改动内容 |
|------|---------|
| `backend/services/handlers/chat_handler.py` | `send_to_task_or_user` → `task_stream.publish` |
| `backend/services/handlers/chat_tool_mixin.py` | 同上 |
| `backend/services/handlers/mixins/message_mixin.py` | 同上 + 完成后设 Stream EXPIRE |
| `backend/services/handlers/base.py` | 第385行 retry 消息改用 `task_stream.publish` |
| `backend/services/erp_agent.py` | `send_to_task_subscribers` → `task_stream.publish` |
| `backend/services/batch_completion_service.py` | `send_to_task_or_user` → `task_stream.publish` |
| `backend/api/routes/ws.py` | subscribe 启动 Stream consumer task |
| `frontend/src/hooks/useWebSocket.ts` | subscribeTask 增加 lastStreamId 参数 |
| `frontend/src/contexts/WebSocketContext.tsx` | WS 重连后重新订阅活跃任务 |

---

## 6. 后端核心设计：`task_stream.py`

### 6.1 接口定义

```python
# === 生产者（chat_handler 等调用） ===

async def publish(task_id: str, user_id: str, message: Dict[str, Any]) -> str:
    """
    写入一条消息到 Redis Stream
    
    key: stream:task:{task_id}
    返回: stream entry ID (如 "1712438000000-0")
    
    降级: Redis 不可用时 fallback 到 ws_manager.send_to_task_or_user
    """

async def set_stream_expire(task_id: str, ttl_seconds: int = 600) -> None:
    """任务完成后设置 Stream 过期时间（默认10分钟）"""

# === 消费者（ws.py subscribe 时启动） ===

async def consume(
    task_id: str,
    user_id: str,
    conn_id: str,
    last_stream_id: str = "0",
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    从 Stream 读取消息的异步生成器
    
    1. 先 XRANGE 补发 last_stream_id 之后的历史消息
    2. 然后 XREAD BLOCK 实时监听新消息
    3. 遇到 message_done / message_error 时自动结束
    
    用户鉴权: 每条消息的 user_id 必须匹配
    """
```

### 6.2 Redis Stream 数据结构

```
Key: stream:task:{task_id}
MAXLEN: 1000（防止极端情况 OOM）
TTL: 任务完成后 600 秒自动删除

每条 Entry:
{
    "user_id": "7be35735-...",           # 鉴权用
    "data": "{\"type\":\"message_chunk\",\"task_id\":\"...\",\"payload\":{\"chunk\":\"你好\"},...}"
}
```

### 6.3 降级策略

```python
async def publish(task_id, user_id, message):
    try:
        client = await RedisClient.get_client()
        stream_key = f"stream:task:{task_id}"
        await client.xadd(stream_key, {
            "user_id": user_id,
            "data": json.dumps(message, ensure_ascii=False),
        }, maxlen=1000)
    except Exception as e:
        logger.warning(f"Stream publish failed, fallback to PubSub | error={e}")
        # 降级：走原来的 Pub/Sub 路径（有总比没有好）
        await ws_manager.send_to_task_or_user(task_id, user_id, message)
```

---

## 7. WS 端点改造：`ws.py`

### 7.1 Subscribe 新逻辑

```python
# 伪代码，说明核心逻辑

async def _handle_subscribe(conn_id, user_id, task_id, last_stream_id="0"):
    # 1. 注册订阅（保留，用于 unsubscribe 时清理）
    await ws_manager.subscribe_task(conn_id, task_id)
    
    # 2. 启动 Stream consumer 协程
    consumer_task = asyncio.create_task(
        _stream_consumer(conn_id, user_id, task_id, last_stream_id)
    )
    # 保存 task ref，断开时 cancel
    _consumer_tasks[conn_id][task_id] = consumer_task

async def _stream_consumer(conn_id, user_id, task_id, last_stream_id):
    """从 Redis Stream 读取并推送到 WS 客户端"""
    try:
        async for message in task_stream.consume(task_id, user_id, conn_id, last_stream_id):
            # message 已经是完整的 WS 消息 JSON
            # 附加 stream_id 供前端追踪
            await ws_manager.send_to_connection(conn_id, message)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"Stream consumer error | task={task_id} | conn={conn_id} | error={e}")
    
    # Stream 读完（遇到 done/error）或连接断开
    # fallback: 如果 Stream 不存在（已过期），查数据库
    await _check_and_send_completed_task(conn_id, task_id, user_id)
```

### 7.2 断开连接时清理

```python
# ws.py finally 块中
finally:
    # 取消所有 consumer tasks
    for task_id, consumer in _consumer_tasks.get(conn_id, {}).items():
        consumer.cancel()
    _consumer_tasks.pop(conn_id, None)
    
    heartbeat_task.cancel()
    await ws_manager.disconnect(conn_id)
```

---

## 8. 前端改造

### 8.1 `useWebSocket.ts` — subscribeTask 增加 lastStreamId

```typescript
// 改前
const subscribeTask = useCallback((taskId: string, lastIndex: number = -1) => {
    wsRef.current.send(JSON.stringify({
        type: 'subscribe',
        payload: { task_id: taskId, last_index: lastIndex },
    }))
}, []);

// 改后
const subscribeTask = useCallback((taskId: string, lastStreamId: string = "0") => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({
            type: 'subscribe',
            payload: { task_id: taskId, last_stream_id: lastStreamId },
        }))
    } else {
        // 🔥 WS 未连接时入队列，连接后自动重发
        pendingSubscriptionsRef.current.set(taskId, lastStreamId);
    }
}, []);
```

### 8.2 `useWebSocket.ts` — WS 重连后重新订阅

```typescript
// ws.onopen 中追加：
ws.onopen = () => {
    // ... 现有逻辑 ...
    
    // 🔥 重连后重发 pending 订阅
    pendingSubscriptionsRef.current.forEach((lastStreamId, taskId) => {
        wsRef.current.send(JSON.stringify({
            type: 'subscribe',
            payload: { task_id: taskId, last_stream_id: lastStreamId },
        }));
    });
    pendingSubscriptionsRef.current.clear();
};
```

### 8.3 `WebSocketContext.tsx` — 追踪活跃订阅的 lastStreamId

```typescript
// wsMessageHandlers 中，每收到一条消息就更新 lastStreamId
// 用于断线重连时从正确位置继续

if (msg.stream_id) {
    lastStreamIdRef.current.set(msg.task_id, msg.stream_id);
}
```

### 8.4 `wsMessageHandlers.ts` — 零改动
消息格式不变，处理逻辑不变。只是消息多了一个 `stream_id` 字段（可选），不影响现有处理。

### 8.5 `messageSender.ts` — 零改动
`subscribeTask` 接口签名变了（number → string），但调用处传 `"0"` 或不传（默认值）即可。

---

## 9. 开发任务拆分

### 阶段1：后端 Stream 基础设施（核心）
- [ ] **任务1.1**：新建 `backend/services/task_stream.py`（publish / consume / set_expire）
- [ ] **任务1.2**：`chat_handler.py` 改用 `task_stream.publish`（替换 6 处 `send_to_task_or_user`）
- [ ] **任务1.3**：`chat_tool_mixin.py` 改用 `task_stream.publish`（替换 3 处）
- [ ] **任务1.4**：`message_mixin.py` 改用 `task_stream.publish` + 完成后 `set_stream_expire`
- [ ] **任务1.5**：`erp_agent.py:441` 改用 `task_stream.publish`（修复原有 bug）
- [ ] **任务1.6**：`base.py:385` + `batch_completion_service.py` 改用 `task_stream.publish`

### 阶段2：WS 端点改造
- [ ] **任务2.1**：`ws.py` subscribe 启动 Stream consumer task
- [ ] **任务2.2**：`ws.py` 断开连接时 cancel consumer tasks
- [ ] **任务2.3**：`ws.py` Stream 不存在时 fallback 到 DB 查询（兼容已过期 Stream）

### 阶段3：前端重连补发
- [ ] **任务3.1**：`useWebSocket.ts` — subscribeTask 增加 lastStreamId + pending 队列
- [ ] **任务3.2**：`useWebSocket.ts` — onopen 重发 pending 订阅
- [ ] **任务3.3**：`WebSocketContext.tsx` — 追踪 lastStreamId，重连时传入
- [ ] **任务3.4**：`taskRestoration.ts` — Phase 2 订阅时传 `last_stream_id="0"`

### 阶段4：测试
- [ ] **任务4.1**：`task_stream.py` 单元测试
- [ ] **任务4.2**：`ws.py` Stream consumer 集成测试
- [ ] **任务4.3**：端到端测试（4 Worker 场景）

---

## 10. 依赖变更

无需新增依赖。`redis` 包已有，Redis Streams API（`xadd`, `xread`, `xrange`）是原生支持。

---

## 11. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| Redis Stream 内存增长 | 中 | MAXLEN 1000 + TTL 600s 双重保护 |
| Redis 不可用 | 低 | fallback 到原 Pub/Sub 路径（`publish` 中 try-except） |
| 前端兼容性 | 低 | 消息格式不变，只多一个可选 `stream_id` 字段 |
| 企微被误改 | 低 | 企微完全独立链路，不动任何企微代码 |
| 多 Tab 消息重复 | 低 | 每个 Tab 独立 consumer，各自独立序列，不影响 |
| XREAD BLOCK 导致连接占用 | 低 | block 超时 5s 循环重试，不会永久阻塞 |

---

## 12. 设计自检

- [x] 连锁修改已全部纳入任务拆分（6 处后端调用 + 2 处前端 + WS 端点）
- [x] 10 类边界场景均有处理策略
- [x] `task_stream.py` 预估 ~150 行
- [x] 无新增依赖
- [x] 企微链路零改动
- [x] Redis 不可用时有降级方案
- [x] 4 Worker 无需改动
