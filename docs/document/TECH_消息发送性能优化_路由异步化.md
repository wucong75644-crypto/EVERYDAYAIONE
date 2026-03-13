## 技术设计：消息发送性能优化 — 路由异步化 + 四方向并行

### 1. 现有代码分析

**已阅读文件**：

| 文件 | 关键理解 |
|------|---------|
| `backend/api/routes/message.py` (448行) | `generate_message` HTTP handler，`_resolve_generation_type` 在 L220 同步执行 Agent Loop，**阻塞整个 HTTP 响应** |
| `backend/services/agent_loop.py` (601行) | ReAct 循环引擎，`_execute_loop` 中 `_build_system_prompt`(L106) 和 `_get_recent_history`(L123) **串行**执行，然后才调大脑 |
| `backend/services/agent_context.py` (214行) | Agent 上下文构建 Mixin，`_build_system_prompt` 调 knowledge_service.search_relevant |
| `backend/services/intent_router.py` (578行) | 旧路由降级路径，`_enhance_with_knowledge`(L213) 也调 knowledge_service |
| `backend/services/handlers/chat_handler.py` (401行) | `start()` 保存任务+启动异步流，`_stream_generate` 在 L207 调 `_build_llm_messages` 含记忆检索 |
| `backend/services/handlers/chat_context_mixin.py` (418行) | `_build_llm_messages` L56 已用 asyncio.gather 并行获取记忆/摘要/历史（上次优化已完成） |
| `backend/services/memory_service.py` (438行) | `get_relevant_memories` 含 Mem0 向量搜索 + 千问 LLM 精排，耗时 100-3000ms |
| `frontend/src/services/messageSender.ts` (458行) | Phase 1 乐观更新 → Phase 1.5 WS 预订阅 → Phase 2 HTTP POST → Phase 3-5 响应处理 |
| `backend/services/handlers/base.py` (469行) | Handler 基类，定义 start/on_complete/on_error 抽象接口 |
| `backend/api/routes/message_generation_helpers.py` (396行) | `start_generation_task` 调 handler.start()，`create_user_message` 创建用户消息 |
| `backend/schemas/websocket.py` | WS 消息类型定义，已有 `AGENT_STEP` 用于工具执行进度通知 |
| `frontend/src/contexts/wsMessageHandlers.ts` | `agent_step` handler 已展示工具执行提示（如"正在搜索…"） |

**可复用模块**：
- `asyncio.gather` — 并行化
- `agent_step` WS 事件 — Agent Loop 进度已有前端渲染
- `_prefetched_summary` 模式 — 预取结果通过 params 传递给下游，`chat_context_mixin.py` 已实现此模式
- 前端"旋转圆点 → 类型占位符"变形机制 — `processApiResponse` 已有此逻辑

**设计约束**：
- 仅影响 smart mode（model="auto"），非 smart mode 流程不变
- retry / regenerate / regenerate_single 操作不走 Agent Loop，不受影响
- 向后兼容：所有新参数 Optional，新 WS 事件可选
- Agent Loop 工具执行的 `agent_step` WS 事件需要 task_id 关联（当前用 conversation_id）

**连锁修改清单**：

| 改动点 | 影响文件 | 必须同步修改 |
|--------|---------|------------|
| `message.py` smart mode 跳过 `_resolve_generation_type` | `message.py` L220-283 | 路由相关参数注入逻辑移至 ChatHandler |
| ChatHandler.start() 新增 smart mode 路由分支 | `chat_handler.py` L43-101 | 新增 `_route_and_stream` 方法 |
| `_stream_generate` 接受 `prefetched_memory` | `chat_handler.py` L158-173 | `_build_llm_messages` 签名变更 |
| `_build_llm_messages` 增加 `prefetched_memory` 参数 | `chat_context_mixin.py` L22-31 | 跳过 `_build_memory_prompt` 当有预取值 |
| Agent Loop `_execute_loop` 并行化 | `agent_loop.py` L106-123 | 无外部调用方影响 |
| Agent Loop `_notify_progress` 需要 task_id | `agent_loop.py` L461-477 | 新增 task_id 参数传递 |
| 新增 `routing_complete` WS 事件 | `schemas/websocket.py` + `wsMessageHandlers.ts` | 前端新增 handler |
| 路由结果注入逻辑从 message.py 迁移 | `chat_handler.py` 新增 `_apply_routing_result` | 复用 message.py L224-283 逻辑 |

---

### 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| Agent Loop 超时/异常 | 已有降级链：Agent Loop → IntentRouter → 关键词兜底。异步化后降级链不变，只是从 HTTP 路径移到 async 路径 | agent_loop.py, intent_router.py |
| 记忆预取超时（Mem0 + LLM 精排） | `asyncio.gather(return_exceptions=True)` + isinstance 检查，超时返回 None，LLM 消息组装跳过记忆注入 | chat_handler.py, chat_context_mixin.py |
| Agent Loop 路由到 image/video（非 chat 类型） | 发送 `routing_complete` WS 事件 → 更新 task 记录和 message 记录 → 委派给对应 Handler | chat_handler.py |
| 用户刷新页面时路由还在进行中 | task 记录已保存（status=running），刷新后 taskRestoration 机制从 DB 恢复 | 前端 taskRestoration |
| Agent Loop 内 `_build_system_prompt` 失败 | `asyncio.gather(return_exceptions=True)`，知识库搜索失败时用基础 prompt，不影响主循环 | agent_context.py |
| 并发发送多条消息 | 每条消息独立 task_id，Agent Loop 实例独立，无共享状态 | message.py, chat_handler.py |
| memory prefetch 与 Agent Loop 竞争 DB 连接 | Supabase client 支持并发，无连接池瓶颈。Mem0 用独立连接 | memory_service.py |
| smart mode 下 `_needs_routing` 参数意外传给非 ChatHandler | 只在 ChatHandler._stream_generate 中检查，其他 Handler 忽略未知 params | chat_handler.py |

---

### 3. 技术栈

- 后端：Python 3.x + FastAPI + asyncio
- 前端：React + TypeScript + Zustand
- 数据库：Supabase (PostgreSQL)
- 无需新增依赖

---

### 4. 目录结构

#### 修改文件

| 文件 | 改动内容 |
|------|---------|
| `backend/api/routes/message.py` | smart mode 跳过 Agent Loop，注入 `_needs_routing` 标记 |
| `backend/services/handlers/chat_handler.py` | 新增 `_route_and_stream()`，smart mode 路由+记忆并行 |
| `backend/services/handlers/chat_context_mixin.py` | `_build_llm_messages` 增加 `prefetched_memory` 参数 |
| `backend/services/agent_loop.py` | `_execute_loop` 内部并行化 + 支持传入 task_id |
| `backend/schemas/websocket.py` | 新增 `ROUTING_COMPLETE` 事件类型 + `build_routing_complete` |
| `frontend/src/contexts/wsMessageHandlers.ts` | 新增 `routing_complete` handler |
| `frontend/src/hooks/useWebSocket.ts` | WSMessageType 新增 `routing_complete` |

#### 无新增文件

---

### 5. 数据库设计

无数据库变更。

---

### 6. API 设计

#### HTTP 接口：无变更

`POST /conversations/{id}/messages/generate` 请求/响应格式不变。

Smart mode 下 `generation_type` 返回 `"chat"`（provisional），实际类型通过 WS `routing_complete` 事件通知。

#### 新增 WS 事件：`routing_complete`

```json
{
  "type": "routing_complete",
  "task_id": "xxx",
  "conversation_id": "xxx",
  "timestamp": 1234567890,
  "payload": {
    "generation_type": "image",
    "model": "flux-kontext-pro",
    "generation_params": {
      "type": "image",
      "model": "flux-kontext-pro",
      "aspect_ratio": "1:1",
      "num_images": 1
    }
  }
}
```

触发条件：仅当 smart mode 路由结果为 image/video 时发送（chat 类型无需此事件，直接进入流式输出）。

---

### 7. 前端状态管理

无 Store 结构变更。

`routing_complete` handler 复用已有的 `completeStreamingWithMessage` + `setIsSending` 逻辑（与 `processApiResponse` 中 image/video 分支一致）。

---

### 8. 核心架构变更

#### 当前流程（smart mode）

```
HTTP POST /generate
  ├─ task_limit_check              [10-50ms]
  ├─ ★ _resolve_generation_type    [2,000-10,000ms 阻塞]
  │   ├─ _build_system_prompt      [串行]
  │   ├─ _get_recent_history       [串行]
  │   └─ _call_brain × N轮        [串行]
  ├─ resolve_auto_model            [<1ms]
  ├─ get_conversation              [10-50ms]
  ├─ create_user_message           [10-50ms]
  ├─ handle_regenerate_or_send     [0-50ms]
  ├─ handler.start → _save_task    [10-50ms]
  └─ return HTTP response
      └─ async _stream_generate
          ├─ _build_llm_messages   [100-3000ms, 含记忆检索]
          └─ adapter.stream_chat   [首 token 500-2000ms]
```

**首 token 总耗时：3-16 秒**

#### 新流程（smart mode）

```
HTTP POST /generate
  ├─ task_limit_check              [10-50ms]
  ├─ ✗ 跳过 Agent Loop
  ├─ get_conversation              [10-50ms]
  ├─ create_user_message           [10-50ms]
  ├─ handle_regenerate_or_send     [0ms, chat 不入库]
  ├─ handler.start → _save_task    [10-50ms]
  └─ return HTTP response          [~100ms 总计]
      └─ async _route_and_stream
          ├─ ★ asyncio.gather:                    [并行，耗时=max(两者)]
          │   ├─ Agent Loop                       [1.5-8s]
          │   │   ├─ _build_system_prompt ┐
          │   │   ├─ _get_recent_history  ┘ 并行  [方向1]
          │   │   └─ _call_brain × N轮
          │   └─ Memory prefetch                  [100-3000ms, 方向4]
          ├─ 路由结果处理:
          │   ├─ chat → 继续 _stream_generate     [记忆已就绪]
          │   └─ image/video → routing_complete WS + 委派
          └─ adapter.stream_chat                  [首 token 500-2000ms]
```

**首 token 总耗时：2-10 秒**（HTTP 返回 ~100ms，用户立即看到"思考中"）

---

### 9. 开发任务拆分

#### 阶段1：方向1 — Agent Loop 内部并行化（低风险）

- [ ] 任务1.1：`agent_loop.py:_execute_loop` — `_build_system_prompt` 和 `_get_recent_history` 改为 `asyncio.gather` 并行（~10行改动）
  - `return_exceptions=True` + isinstance 检查
  - system_prompt 失败降级为 `AGENT_SYSTEM_PROMPT`
  - history 失败降级为 None

#### 阶段2：方向3 — 路由异步化（核心，涉及多文件）

- [ ] 任务2.1：`message.py` — smart mode 跳过 `_resolve_generation_type`
  - 条件：`body.model == SMART_MODEL_ID and body.operation == MessageOperation.SEND`
  - 使用 provisional `gen_type = GenerationType.CHAT`
  - 将路由相关信息打包到 `body.params["_needs_routing"] = True`
  - 路由注入逻辑（L224-283）提取为独立函数 `_apply_routing_to_params()`，供 ChatHandler 复用

- [ ] 任务2.2：`chat_handler.py` — 新增 `_route_and_stream()` 方法
  - 在 `start()` 中：当 `params.get("_needs_routing")` 时，启动 `_route_and_stream` 替代 `_stream_generate`
  - `_route_and_stream` 内部：
    1. 运行 Agent Loop（传入 task_id 用于 agent_step 通知）
    2. 处理路由结果（resolve model、注入 system_prompt 等）
    3. chat 类型：调用 `_stream_generate`（传入预取的记忆）
    4. image/video 类型：调用 `_reroute_to_media()`

- [ ] 任务2.3：`chat_handler.py` — 新增 `_reroute_to_media()` 方法
  - 更新 task 记录（type, model_id, status）
  - 更新 assistant message 为媒体占位符
  - 发送 `routing_complete` WS 事件
  - 获取对应 Handler 并调用其生成逻辑

- [ ] 任务2.4：`schemas/websocket.py` — 新增 `ROUTING_COMPLETE` 事件
  - 新增枚举值 `ROUTING_COMPLETE = "routing_complete"`
  - 新增 `build_routing_complete()` 构建函数

- [ ] 任务2.5：`frontend/wsMessageHandlers.ts` — 新增 `routing_complete` handler
  - 接收 `generation_type`、`model`、`generation_params`
  - image/video：调用 `completeStreamingWithMessage` 变形占位符
  - chat：仅更新 `generation_params`（传入 model 信息）

- [ ] 任务2.6：`frontend/useWebSocket.ts` — WSMessageType 新增 `routing_complete`

#### 阶段3：方向4 — 记忆检索与 Agent Loop 并行

- [ ] 任务3.1：`chat_handler.py:_route_and_stream` — 记忆检索并行化
  - 将记忆预取 `_build_memory_prompt(user_id, text_content)` 与 Agent Loop 放入同一个 `asyncio.gather`
  - 记忆结果通过参数传递给 `_stream_generate`

- [ ] 任务3.2：`chat_context_mixin.py:_build_llm_messages` — 增加 `prefetched_memory` 参数
  - 有预取值时：直接使用，跳过 `_build_memory_prompt` 调用
  - 无预取值时：保持原有行为（向后兼容）

#### 阶段4：方向2 — DB 写入并行化（低风险，小收益）

- [ ] 任务4.1：`message.py` — 并行化 `create_user_message` 和 `get_conversation`
  - 两者独立无依赖，可用 `asyncio.gather` 并行
  - 注意：`handle_regenerate_or_send_operation` 依赖 `gen_type`，在新流程中 gen_type 是 provisional 的 chat，不影响

#### 阶段5：Agent Loop task_id 关联

- [ ] 任务5.1：`agent_loop.py` — `_notify_progress` 支持 task_id
  - `run()` 接受可选 `task_id` 参数
  - `_notify_progress` 通过 task_id 发送 `agent_step`（当前用 conversation_id 广播）
  - 前端 `agent_step` handler 已能处理，无需修改

#### 阶段6：测试更新

- [ ] 任务6.1：`test_agent_loop.py` — 补充并行化相关测试
- [ ] 任务6.2：`test_chat_handler_stream.py` — 补充 `_route_and_stream` 测试（smart mode 路由 + 记忆并行）
- [ ] 任务6.3：`test_message_generation_helpers.py` — 验证 smart mode 跳过路由后的 HTTP 响应

---

### 10. 依赖变更

无需新增依赖。所有改动使用 Python 标准库 `asyncio` 和现有前端 API。

---

### 11. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| Agent Loop 在 async 路径中失败 | 中 | 保留完整降级链（Agent Loop → IntentRouter → 关键词兜底），失败时 fallback 到默认模型聊天 |
| image/video 重路由时数据不一致 | 中 | 事务性更新：先更新 DB，再发 WS 事件，失败时走 on_error 路径 |
| 记忆预取结果被丢弃（非 chat 路由） | 低 | 仅浪费一次 Mem0 搜索 + 可能一次 qwen-turbo 调用，无副作用 |
| smart mode 下 HTTP 返回 provisional `chat` 类型 | 低 | 前端已有"旋转圆点"通用占位符，收到 routing_complete 后变形；chat 类型无需变形 |
| agent_step WS 事件在 task 创建前发送 | 低 | 前端 WS 预订阅机制已处理此场景，agent_step 基于 conversation_id 广播 |
| 非 smart mode 被意外走入新路径 | 极低 | `_needs_routing` 标记仅在 `body.model == SMART_MODEL_ID` 时设置 |

---

### 12. 文档更新清单

- [ ] FUNCTION_INDEX.md — 更新 `_route_and_stream`、`_reroute_to_media`、`build_routing_complete` 等新增函数
- [ ] docs/document/TECH_消息发送性能优化_路由异步化.md — 本文档

---

### 13. 设计自检

- [x] 连锁修改已全部纳入任务拆分（8 个连锁点 → 对应 6 个阶段 13 个任务）
- [x] 7 类边界场景均有处理策略
- [x] 所有修改文件预估 ≤500 行
- [x] 无新增依赖
- [x] 向后兼容（仅影响 smart mode，非 smart mode 和 retry/regenerate 完全不变）
- [x] 非 chat 路由（image/video）有完整的重路由机制

---

### 预期效果

| 优化方向 | 收益 | 复杂度 |
|---------|------|--------|
| 方向1：Agent Loop 内部并行 | TTFT -100~300ms | 低（~10行） |
| 方向2：DB 写入并行 | HTTP 响应 -30~100ms | 低（~5行） |
| 方向3：路由异步化 | 用户体感从"干等"变"思考中"，HTTP 100ms 内返回 | 中高（核心改动） |
| 方向4：记忆与路由并行 | 记忆 1-3s 被隐藏到 Agent Loop 耗时中 | 低（~15行） |
| **综合** | **首 token 2-10s（原 3-16s），体感大幅提升** | — |
