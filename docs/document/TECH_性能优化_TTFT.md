## 技术设计：聊天响应性能优化（并行化 + 延迟加载 + DB 查询合并）

### 1. 现有代码分析

**已阅读文件**：

| 文件 | 关键理解 |
|------|---------|
| `backend/services/handlers/chat_context_mixin.py` (389行) | `_build_llm_messages` 在第54/74/79行三个完全独立的 await 顺序执行：记忆(100-2000ms) → 摘要(10-50ms) → 历史(20-80ms)。三者仅依赖 `user_id`/`conversation_id`/`text_content`，无交叉数据依赖 |
| `backend/services/handlers/chat_handler.py` (400行) | `_stream_generate:206-210` 调用 `_build_llm_messages`；第261行 `await _save_accumulated_content` 同步阻塞流式循环；第256行每个 chunk 携带完整 `accumulated` 文本(O(n²)流量) |
| `backend/services/handlers/mixins/task_mixin.py` (157行) | `_complete_task:50` 重新 SELECT task，但 `_handle_complete_common:219` 已通过 `_get_task_context` 查过同一行 |
| `backend/services/handlers/mixins/message_mixin.py` (280行) | `_handle_complete_common:219` 调 `_get_task_context` (SELECT *)，然后 `_complete_task:265` 再次 SELECT 同行 |
| `backend/services/conversation_service.py` (320行) | `get_conversation:97` 用 `select("*")` 含 `context_summary`，但 `_format_conversation:308-319` 丢弃了它。Phase 2 `_get_context_summary:249-254` 对同一行再查一次 |
| `frontend/src/contexts/wsMessageHandlers.ts` (443行) | `message_chunk` handler 第360-361行：所有 chunk（含首字节）统一 50ms 延迟渲染 |
| `backend/services/task_limit_service.py` (186行) | 第56/69行两个独立 Redis GET 串行执行 |

**可复用模块**：
- `asyncio.gather` — Python 标准库，无需引入新依赖
- `flushChunkBuffer` — 前端已有的批量 flush 函数，只需改调用时机

**设计约束**：
- 必须向后兼容，所有新参数均为 Optional，默认走原有路径
- `_build_llm_messages` 消息组装顺序（system prompt 排列）不可改变
- WebSocket 协议不可破坏性变更（`accumulated` 字段本就是 optional）

**连锁修改清单**：

| 改动点 | 影响文件 | 必须同步修改 |
|--------|---------|------------|
| `_build_llm_messages` 签名增加 `prefetched_summary` | `chat_handler.py:206-210` | 传入新参数 |
| `_get_context_summary` 增加 `prefetched` 参数 | `chat_context_mixin.py` 内部调用 | 无外部调用方 |
| `_format_conversation` 增加 `context_summary` 字段 | `conversation_service.py` 3处调用方 | 新增字段，向后兼容 |
| `message.py` 注入 `_prefetched_summary` 到 params | `chat_handler.py:92` `_params` 传递链 | 已有 `_router_*` 同模式 |
| `_complete_task`/`_fail_task` 增加 `task` 参数 | `message_mixin.py:265` | 传入已有 task 数据 |
| `build_message_chunk` 不再传 `accumulated` | `chat_handler.py:256` | 同时检查 `_stream_direct_reply:127` |
| 前端 `message_chunk` handler 修改 flush 逻辑 | `wsMessageHandlers.ts:360-362` | 自包含，无外部影响 |

---

### 2. 边界与极限情况

| 场景 | 处理策略 | 涉及模块 |
|-----|---------|---------|
| `asyncio.gather` 其中一个任务异常 | `return_exceptions=True` + 逐个 isinstance 检查，异常结果降级为 None/[] | `chat_context_mixin.py` |
| 记忆检索超时（Mem0 45s timeout） | 不影响 summary 和 history（已并行），`_build_memory_prompt` 内部已有 try/except 返回 None | `chat_context_mixin.py` |
| `_save_accumulated_content` fire-and-forget 时进程崩溃 | 最多丢失最后一批（<20 chunks）的中间态，`on_complete` 的 `upsert_assistant_message` 以完整内容覆盖 | `chat_handler.py` |
| fire-and-forget 任务大量堆积 | `_save_accumulated_content` 本身很轻（单行 UPDATE），不会堆积；且已有 try/except 不传播异常 | `chat_handler.py` |
| `prefetched_summary` 在 Phase 1 和 Phase 2 之间数据过时 | 两阶段间隔仅几毫秒，实际不存在不一致；且 `prefetched=None` 时自动降级到 DB 查询 | `chat_context_mixin.py` |
| 移除 `accumulated` 后，前端依赖 `accumulated` 的地方 | 已确认：`message_chunk` handler 不读 `msg.accumulated`；`subscribed` handler 的 `accumulated` 来自 `build_subscribed`（不同消息类型），不受影响 | `wsMessageHandlers.ts` |
| 首字节立即 flush 导致多消息并发时互相干扰 | `flushChunkBuffer` 用 `buffer.forEach` 处理所有消息，`buffer.clear()` 清空所有——与现有行为一致，只是时机提前 | `wsMessageHandlers.ts` |
| `_complete_task` 接收过时的 task 数据 | task 数据从 `_get_task_context` 获取到 `_complete_task` 调用之间是同步执行链，无并发窗口 | `task_mixin.py` |

---

### 3. 技术栈

- 后端：Python 3.x + FastAPI + asyncio
- 前端：React + TypeScript
- 数据库：Supabase (PostgreSQL) + Redis
- 无需新增依赖

---

### 4. 目录结构

#### 修改文件

| 文件 | 改动内容 |
|------|---------|
| `backend/services/handlers/chat_context_mixin.py` | `_build_llm_messages` 三路并行 + `_get_context_summary` 增加 prefetched |
| `backend/services/handlers/chat_handler.py` | 传递 prefetched_summary + accumulated 移除 + fire-and-forget |
| `backend/services/handlers/mixins/task_mixin.py` | `_complete_task`/`_fail_task` 增加可选 task 参数 |
| `backend/services/handlers/mixins/message_mixin.py` | `_handle_complete_common` 传递 task 到 `_complete_task` |
| `backend/services/conversation_service.py` | `_format_conversation` 增加 `context_summary` |
| `backend/api/routes/message.py` | 注入 `_prefetched_summary` |
| `backend/services/task_limit_service.py` | Redis 两个 GET 合并 pipeline |
| `frontend/src/contexts/wsMessageHandlers.ts` | 首字节立即渲染 + 16ms 批量窗口 |

#### 无新增文件

---

### 5. 数据库设计

无数据库变更。

---

### 6. API设计

无 API 变更（纯内部优化，不影响前后端接口协议）。

---

### 7. 前端状态管理

无 Store 结构变更。仅修改 `wsMessageHandlers.ts` 中 `message_chunk` 的 flush 时机。

---

### 8. 开发任务拆分

#### 阶段1：高收益低风险（P0 + P1）

- [ ] 任务1.1：`chat_context_mixin.py` — `_build_llm_messages` 三路 `asyncio.gather` 并行化（~15行改动）
  - 增加 `import asyncio`
  - 提取三个 await 到 `asyncio.gather(return_exceptions=True)`
  - 逐个解包结果，异常降级为 None/[]
  - 保持原有 `messages.insert` 组装顺序不变
- [ ] 任务1.2：`chat_handler.py:261` — `_save_accumulated_content` 改为 fire-and-forget（1行）
  - `await self._save_accumulated_content(...)` → `asyncio.create_task(self._save_accumulated_content(...))`
- [ ] 任务1.3：`chat_handler.py:256` — 移除 `accumulated=accumulated_text`（1行）
  - 同时检查 `_stream_direct_reply:127`，单次发送保留或一并移除
- [ ] 任务1.4：`wsMessageHandlers.ts:339-362` — 首字节立即渲染（~10行）
  - 判断 `!bufferData`（首字节）→ 立即 `flushChunkBuffer(deps)`
  - 后续 chunk → 16ms 批量窗口（替代 50ms）

#### 阶段2：中等复杂度（P2）

- [ ] 任务2.1：`conversation_service.py:308-319` — `_format_conversation` 增加 `context_summary` 字段
- [ ] 任务2.2：`message.py:292` — 将 `conversation.get("context_summary")` 注入 `body.params["_prefetched_summary"]`
- [ ] 任务2.3：`chat_handler.py:206-210` — `_stream_generate` 从 `_params` 取出 `prefetched_summary` 并传给 `_build_llm_messages`
- [ ] 任务2.4：`chat_context_mixin.py` — `_build_llm_messages` 签名增加 `prefetched_summary` 参数，传给 `_get_context_summary`
- [ ] 任务2.5：`chat_context_mixin.py:239` — `_get_context_summary` 增加 `prefetched` 参数，有值时跳过 DB
- [ ] 任务2.6：`task_mixin.py:37,101` — `_complete_task`/`_fail_task` 增加可选 `task: Optional[Dict]` 参数
- [ ] 任务2.7：`message_mixin.py:265` — `_handle_complete_common` 传递 `task` 给 `_complete_task`

#### 阶段3：可选低收益（P3）

- [ ] 任务3.1：`task_limit_service.py:56,69` — 两个 Redis GET 合并为 pipeline 读取

---

### 9. 依赖变更

无需新增依赖。所有改动使用 Python 标准库 `asyncio` 和现有前端 API。

---

### 10. 风险评估

| 风险 | 严重度 | 缓解措施 |
|-----|--------|---------|
| `asyncio.gather` 异常传播 | 中 | `return_exceptions=True` + isinstance 检查，三个函数已有内部 try/except |
| fire-and-forget 写入丢失 | 低 | `on_complete` 最终以完整内容覆盖，中间态丢失无影响 |
| 移除 `accumulated` 影响前端 | 低 | 已确认 `message_chunk` handler 不读该字段；`subscribed` 消息不受影响 |
| 首字节立即 flush 多次 setState | 极低 | 每消息仅多 1 次 setState，影响可忽略 |
| `prefetched_summary` 跨阶段过时 | 极低 | 两阶段间隔 <100ms，且有 `None` 降级路径 |

---

### 11. 文档更新清单

- [ ] FUNCTION_INDEX.md — 更新 `_build_llm_messages`、`_complete_task`、`_get_context_summary` 签名变更
- [ ] docs/document/TECH_性能优化_TTFT.md — 保存本设计文档

---

### 12. 设计自检

- [x] 连锁修改已全部纳入任务拆分（7个连锁点 → 对应任务2.1-2.7）
- [x] 7类边界场景均有处理策略
- [x] 所有修改文件均 ≤500行
- [x] 无新增依赖
- [x] 向后兼容（所有新参数 Optional，默认降级到原路径）

---

### 预期效果

| 优化 | TTFT 改善 | 其他收益 |
|------|----------|---------|
| 三路并行 (O2-A) | 50–130ms | — |
| 首字节立即渲染 (O3-B) | 50ms 感知 | — |
| fire-and-forget 写入 (O3-A) | — | 流畅度：100 chunk 减少 50–250ms 停顿 |
| 移除 accumulated (O4-D) | — | 网络流量 -40~60% |
| summary 复用 (O4-A) | 10–30ms | 省 1 次 DB |
| task 复用 (O4-C) | 10–30ms | 省 1 次 DB |
| Redis pipeline (O2-B) | 1–30ms | — |
| **总计** | **~120–400ms** | 网络+流畅度显著提升 |
