# TECH：用户中断与恢复机制（按停止按钮的全链路设计）

> **版本**：V1.0 | **日期**：2026-06-05 | **状态**：方案确认（用户已拍板"丢弃模式 + 大厂格式"）
>
> **作用边界**：本文档覆盖"用户主动按停止按钮"的中断、落锚、恢复全链路。
> - 区别于 [TECH_Agent停止策略产品化.md](TECH_Agent停止策略产品化.md)（Agent 自主停止：连续失败 / 预算耗尽 / wrap_up）
> - 区别于 [TECH_AI主动沟通与打断机制.md](TECH_AI主动沟通与打断机制.md)（AI 主动追问 ask_user / 用户发新消息 Steer / FollowUp）
> - 三者互不冲突，共享同一套 `ExecutionBudget` / `frozen_messages` / WS 基础设施

---

## 一、问题定义

### 1.1 当前线上现象

用户在前端点击"停止"按钮后：

1. **代码并未真停**：后台 LLM stream 跑完整轮、当前工具继续执行
2. **工具鬼显**：旧 task 被取消后，已在跑的工具完成时仍向同一 task_id 推 WS 事件，前端 UI 显示已"停止"却又弹出工具卡片
3. **承接不上**：停止后用户发新消息或点"继续"，可能因 `messages.content` 残留不完整 `tool_step` 导致：
   - LLM 看到的历史里 partial 内容混乱
   - 极端情况下 OpenAI/Anthropic API 报 `400 invalid_request_error: tool_use ids were found without tool_result blocks`，会话永久腐化

### 1.2 根因汇总（基于代码定位）

| # | 根因 | 位置 | 后果 |
|---|---|---|---|
| 1 | cancel_event 只在两个轮询点检查 | [chat_handler.py:298](backend/services/handlers/chat_handler.py#L298) / [:722](backend/services/handlers/chat_handler.py#L722) | LLM stream 与工具调用都不真停 |
| 2 | 取消后旧 task 仍推 WS | [chat_handler.py:608](backend/services/handlers/chat_handler.py#L608) 等多处 | 工具卡片"鬼显" |
| 3 | 取消不清理 content/blocks | [api/routes/task.py:207-209](backend/api/routes/task.py#L207-L209) | `messages.content` 与 `tasks.accumulated_blocks` 状态撕裂 |
| 4 | 无 resume 概念 | [useRegenerateHandlers.ts:26-65](frontend/src/hooks/useRegenerateHandlers.ts#L26-L65) | "继续任务" = 发新 task，沿用旧 message_id |
| 5 | asyncio.Event 仅本地进程 | [websocket_manager.py:413](backend/services/websocket_manager.py#L413) | 多 worker 部署跨进程信号丢失 |
| 6 | history_loader 无 orphan 兜底 | [chat_context/history_loader.py](backend/services/handlers/chat_context/history_loader.py) | 协议碎片可能导致 LLM API 400 |

---

## 二、业界一手证据汇总

### 2.1 真停边界（业界共识：没有任何厂能瞬间真停工具调用）

| 厂 | 证据 | 行为 |
|---|---|---|
| Claude Code | [issue #17466](https://github.com/anthropics/claude-code/issues/17466) / [#29351](https://github.com/anthropics/claude-code/issues/29351) | ESC/Ctrl+C 在工具激活时**无法可靠中断** |
| Claude Code | [issue #3003](https://github.com/anthropics/claude-code/issues/3003) | 工具执行中断后会话**永久腐化** |
| Vercel AI SDK | [docs/stopping-streams](https://ai-sdk.dev/docs/advanced/stopping-streams) | 客户端 `stop()` **只关 HTTP 连接**，不取消服务端工作 |

**统一边界**：
- LLM stream（HTTP/SSE）：✅ 可真停（abort signal + cancel scope）
- 工具调用（单次 RPC）：❌ 不能砍，让它跑完但**结果丢弃不写历史**
- 下一轮 LLM：✅ 可真停（abort 后不开始）

### 2.2 中断字符串与渲染格式（4 家一致：纯文本 + XML tag，零 JSON）

**LiteLLM 工业级 orphan 补对**（[docs/completion/message_sanitization](https://docs.litellm.ai/docs/completion/message_sanitization)）：
```json
{
  "role": "tool",
  "tool_call_id": "[original_call_id]",
  "content": "[System: Tool execution skipped/interrupted by user. No result provided for tool '[tool_name]'.]"
}
```

**Claude Code 实际字符串**（[issue #7673](https://github.com/anthropics/claude-code/issues/7673)）：
```
"[Request interrupted by user for tool use]"
```

**Cline 生产代码** [`apps/vscode/src/core/prompts/responses.ts:231-258`](https://github.com/cline/cline/blob/main/apps/vscode/src/core/prompts/responses.ts) — `taskResumption` 函数：
```
[TASK RESUMPTION] This task was interrupted {agoText}. It may or may not be
complete, so please reassess the task context. Be aware that the project state
may have changed since then. The current working directory is now '{cwd}'.
If the task has not been completed, retry the last step before interruption
and proceed with completing the task.

Note: If you previously attempted a tool use that the user did not provide a
result for, you should assume the tool use was not successful and assess
whether you should retry.
```

**Cline 用户反馈格式**（同文件 [`buildUserFeedbackContent.ts`](https://github.com/cline/cline/blob/main/apps/vscode/src/core/task/utils/buildUserFeedbackContent.ts)）：
```
<feedback>
{用户输入}
</feedback>
```

**LangGraph Interrupt 极简结构**（[docs](https://docs.langchain.com/oss/python/langgraph/interrupts)）：只有 `id` + `value` 两个字段。

### 2.3 业界共识结论

| 共识 | 证据来源 |
|---|---|
| 喂 LLM 用**纯文本 + XML tag**，不用 JSON schema | Cline / Claude Code / LiteLLM 一致 |
| **不喂工具参数详情**（已在 `tool_calls` 里） | Cline / LiteLLM |
| **不喂 partial 长度等 metadata**（已在历史里） | 全部 |
| 时间用**相对自然语言**（"5 minutes ago"），不用 ISO | Cline |
| 核心指令只两条："被打断"+"未配对工具按未成功处理" | Cline / LiteLLM |
| **内部存储可结构化，喂 LLM 必须扁平化** | LangGraph payload 自由，各家渲染时扁平化 |
| **「继续」不是 resume API**，就是发隐式 user 消息 | Cline / 其他工业实践 |

---

## 三、核心抽象与产品策略

### 3.1 一句话定义

> **停止 = 砍 LLM stream + orphan 补对 + 落锚标记；恢复 = 用户在 INTERRUPTED 输入任意文字（无按钮分支），LLM 看 `[任务恢复]` 前缀 + 用户文本自主决策续接 or 换方向。**

### 3.2 产品策略（丢弃模式，用户已确认）

| 维度 | 选择 |
|---|---|
| **工具未完成结果** | **丢弃**（后台跑完不写历史），与 Cursor / Claude Code / Vercel 一致 |
| **partial 内容** | **原样保留**（不总结），加结构化 `interrupt_marker` 标记 |
| **恢复机制** | 用户在 INTERRUPTED 输入任意文字即可，UI 无「继续 / 新任务」按钮区分。LLM 根据历史 + 用户文本自主决策续接 or 换方向 |
| **协议合法性** | orphan tool_call 必须立即补对 synthetic `tool_result`（hard requirement） |

### 3.3 三层架构

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: 真停层（cancel signal 传播）                   │
│  - asyncio.Event 触发 → 全链路 abort                     │
│  - LLM stream task .cancel() 立即抛 CancelledError        │
│  - 工具调用：不传 cancel，让其跑完但结果丢弃               │
│  - 跨 worker：Redis pub/sub                              │
├─────────────────────────────────────────────────────────┤
│  Layer 2: 落锚层（落锚原子操作）                          │
│  - partial_text + partial_thinking 原样保留              │
│  - orphan tool_call 立即补 synthetic tool_result          │
│  - 末尾追加 interrupt_marker block（3 字段轻量结构化）     │
│  - messages.content + tasks.accumulated_blocks 同步写入   │
├─────────────────────────────────────────────────────────┤
│  Layer 3: 恢复层（喂 LLM 的扁平化渲染）                    │
│  - history_loader 渲染 [TASK RESUMPTION] 文本前缀          │
│  - orphan 兜底自动补对（防御未来 / 存量脏数据）             │
│  - WS 闸门：cancelled_tasks 集合，已取消任务 drop 所有推送   │
└─────────────────────────────────────────────────────────┘
```

---

## 四、详细设计

### 4.1 数据结构

#### 4.1.1 `interrupt_marker` block（仅数据层，前端不渲染独立卡片）

**极简 3 字段**（参考 LangGraph 极简哲学）：
```json
{
  "type": "interrupt_marker",
  "interrupted_at": "2026-06-05T14:30:00+08:00",
  "reason": "user_cancel"
}
```

- `type`：固定为 `"interrupt_marker"`，**仅 `history_loader` 检测使用**
- `interrupted_at`：ISO 时间戳（`history_loader` 渲染 `[任务恢复]` 前缀时算 agoText）
- `reason`：`"user_cancel"` / `"system_timeout"` / `"network_error"`（v1 只支持 `user_cancel`，其他为预留）

**写入位置**：作为 `_content_blocks` 列表的最后一项追加。

**重要**：此 block 是**数据层标记**，不渲染为独立 UI 卡片。前端中断视觉信号通过工具卡片自带的 `cancelled` 状态 + 纯文本场景的 partial 末尾 8px 灰字承载（详见 §15.5）。

#### 4.1.2 orphan tool_result content（synthetic 补对）

```python
INTERRUPTED_TOOL_RESULT = (
    "[系统: 用户在工具 '{tool_name}' 执行完成前中断了对话。"
    "该工具结果未知，请视为未成功。]"
)
```

**注入位置**：
1. **取消瞬间落锚时**：扫描内存 `messages` 数组，未配对的 `assistant.tool_calls[*].id` 追加一条 `role=tool` 配对
2. **history_loader 兜底时**：从 DB 重建后再扫一次，防御性补对

#### 4.1.3 TASK RESUMPTION 渲染模板

```python
TASK_RESUMPTION_TEMPLATE = (
    "[任务恢复] 此任务在 {ago_text} 被用户中断。可能未完成，请重新评估任务上下文。\n\n"
    "注意：如果之前调用的工具没有收到结果，请假设该工具未成功执行；"
    "根据当前需要判断是否重试。"
)

USER_MESSAGE_WRAPPER = (
    "<user_message>\n{user_input}\n</user_message>"
)
```

**渲染时机**：history_loader 加载到带 `interrupt_marker` 的消息时，**在该消息之后、下一条 user 消息之前**注入 TASK RESUMPTION 前缀；下一条 user 消息用 `<user_message>` 包裹。

**`ago_text` 算法**（后端 `utils/time_context.py` 新增）：
```python
def format_relative_time(dt: datetime) -> str:
    delta = (now() - dt).total_seconds()
    if delta < 60: return f"约 {int(delta)} 秒前"
    if delta < 3600: return f"约 {int(delta / 60)} 分钟前"
    if delta < 86400: return f"约 {int(delta / 3600)} 小时前"
    return f"约 {int(delta / 86400)} 天前"
```

### 4.2 落锚原子操作（取消瞬间的核心动作）

伪代码（在 `chat_handler.py` 接收到 cancel_event 时执行）：

```python
async def _persist_interrupt_anchor(
    task_id: str,
    conversation_id: str,
    message_id: str,
    messages: list[dict],       # 内存 LLM messages
    content_blocks: list[dict], # 内存 _content_blocks
) -> None:
    """落锚：partial 保留 + orphan 补对 + interrupt_marker。原子写两表。"""

    # ── Step 1: orphan tool_call 补对（内存 messages） ──
    orphan_ids = _find_orphan_tool_calls(messages)
    for tc_id, tool_name in orphan_ids:
        messages.append({
            "role": "tool",
            "tool_call_id": tc_id,
            "content": INTERRUPTED_TOOL_RESULT.format(tool_name=tool_name),
        })

    # ── Step 2: 追加 interrupt_marker block ──
    content_blocks.append({
        "type": "interrupt_marker",
        "interrupted_at": now_iso(),
        "reason": "user_cancel",
    })

    # ── Step 3: 原子写入 DB（事务） ──
    async with db.transaction():
        db.table("messages").update({
            "content": content_blocks,
            "status": "interrupted",  # 新增 status 值
        }).eq("id", message_id).execute()

        db.table("tasks").update({
            "accumulated_blocks": content_blocks,
            "status": "cancelled",
        }).eq("external_task_id", task_id).execute()
```

**`_find_orphan_tool_calls` 实现**：

```python
def _find_orphan_tool_calls(messages: list[dict]) -> list[tuple[str, str]]:
    """扫描 messages，返回所有未配对的 (tool_call_id, tool_name)。"""
    tool_call_ids: dict[str, str] = {}  # id → tool_name
    seen_results: set[str] = set()

    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                tool_call_ids[tc["id"]] = tc["function"]["name"]
        elif msg.get("role") == "tool":
            seen_results.add(msg.get("tool_call_id"))

    return [(tid, tname) for tid, tname in tool_call_ids.items() if tid not in seen_results]
```

### 4.3 history_loader orphan 兜底（Phase 0 核心，独立可发）

在 [`history_loader.py`](backend/services/handlers/chat_context/history_loader.py) 的 `context.reverse()` 之后追加：

```python
# ── 防御性 orphan 补对：处理历史脏数据 / 边界情况 ──
context = _fix_orphan_tool_calls(context)
```

新增 `_fix_orphan_tool_calls`：

```python
def _fix_orphan_tool_calls(messages: list[dict]) -> list[dict]:
    """扫描历史 messages，自动补对未配对的 tool_call。

    顺序遍历，遇到 assistant.tool_calls 后必须紧跟对应 role=tool。
    若下一条不是配对 tool，立即插入 synthetic tool_result。

    设计原则（参考 LiteLLM）：
    - 不删除孤儿 tool_call（保留语义"我尝试过 X"）
    - 用 synthetic tool_result 补对，让 LLM 看到"未成功"明确信号
    """
    fixed: list[dict] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        fixed.append(msg)

        if msg.get("role") != "assistant":
            i += 1
            continue

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            i += 1
            continue

        expected_ids = [tc["id"] for tc in tool_calls]
        tool_names = {tc["id"]: tc["function"]["name"] for tc in tool_calls}

        # 收集紧随其后的 role=tool 消息
        j = i + 1
        seen: set[str] = set()
        while j < len(messages) and messages[j].get("role") == "tool":
            tc_id = messages[j].get("tool_call_id")
            if tc_id in tool_names:
                fixed.append(messages[j])
                seen.add(tc_id)
            j += 1

        # 补对缺失的 tool_call
        for tc_id in expected_ids:
            if tc_id not in seen:
                fixed.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": INTERRUPTED_TOOL_RESULT.format(
                        tool_name=tool_names[tc_id]
                    ),
                })

        i = j

    return fixed
```

### 4.4 TASK RESUMPTION 文本注入

在 [`history_loader.py`](backend/services/handlers/chat_context/history_loader.py) 的 `_fix_orphan_tool_calls` 之后追加：

```python
# ── interrupt_marker 渲染：注入 TASK RESUMPTION 前缀 ──
context = _inject_task_resumption(context, conversation_id)
```

新增 `_inject_task_resumption`：

```python
def _inject_task_resumption(messages: list[dict], conversation_id: str) -> list[dict]:
    """识别 interrupt_marker，在其后注入 [任务恢复] 文本前缀。

    interrupt_marker 在 content_extractors 中被跳过，但此处需要识别原始 content 里的标记。
    实现方式：扫描每条 assistant 消息的 raw content，发现 interrupt_marker 时记录其时间，
    在该消息后的第一条 user 消息之前插入 TASK RESUMPTION system 消息。
    """
    # 实现细节见 Phase 4 阶段
    ...
```

> **注：** Phase 0 只做 4.3 的 orphan 兜底，4.4 在 Phase 4 完成。

### 4.5 WS 闸门（Phase 1）

[`websocket_manager.py`](backend/services/websocket_manager.py) 加入 `cancelled_tasks` 集合：

```python
class WebSocketManager:
    def __init__(self):
        # ... 现有字段 ...
        self._cancelled_tasks: set[str] = set()
        self._cancelled_tasks_lock = asyncio.Lock()

    def mark_cancelled(self, task_id: str) -> None:
        """标记任务已取消，后续推送全部 drop。"""
        self._cancelled_tasks.add(task_id)
        # TTL 清理：30 分钟后自动移除
        asyncio.create_task(self._auto_cleanup_cancelled(task_id, ttl=1800))

    async def send_to_task_or_user(self, task_id, user_id, message):
        # 闸门：已取消任务，drop 所有后续推送
        if task_id in self._cancelled_tasks:
            return  # silent drop
        # ... 原有推送逻辑 ...
```

`mark_cancelled` 调用时机：[`api/routes/task.py`](backend/api/routes/task.py) 取消路径 + `chat_handler.py` 检测到 cancel_event 时。

### 4.6 真停 LLM stream（Phase 2）

[`chat_generate_mixin.py`](backend/services/handlers/chat_generate_mixin.py) 的 `_stream_generate` 内：

```python
async def _stream_generate(self, task_id, messages, stream_kwargs, ...):
    cancel_event = ws_manager.get_cancel_event(task_id)

    stream_task = asyncio.create_task(self._do_stream(messages, stream_kwargs))
    cancel_task = asyncio.create_task(cancel_event.wait())

    done, pending = await asyncio.wait(
        {stream_task, cancel_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    if cancel_task in done:
        # 用户取消 → 立即砍 stream
        stream_task.cancel()
        try:
            await stream_task
        except asyncio.CancelledError:
            pass
        raise UserCancelledError(task_id)

    # 正常完成
    return await stream_task
```

**关键：** `stream_task.cancel()` 会让 httpx/openai-python 的 SSE 迭代器抛 `CancelledError`，HTTP 连接立即关闭。

### 4.7 跨 worker 取消（Phase 5）

新增 Redis pub/sub：

```python
# services/websocket_manager.py

CANCEL_CHANNEL = "task:cancel"

async def cancel_task(self, task_id: str):
    """本地 + 跨进程 cancel 双轨。"""
    # 本地 asyncio.Event
    if task_id in self._cancel_events:
        self._cancel_events[task_id].set()
        return True

    # 跨进程：发 Redis pub/sub
    await redis.publish(CANCEL_CHANNEL, task_id)
    return False  # 本进程没找到，已转发

async def _subscribe_cancel_channel(self):
    """订阅 cancel 频道，触发本地 Event。"""
    pubsub = redis.pubsub()
    await pubsub.subscribe(CANCEL_CHANNEL)
    async for msg in pubsub.listen():
        if msg["type"] != "message":
            continue
        task_id = msg["data"].decode()
        if task_id in self._cancel_events:
            self._cancel_events[task_id].set()
            self.mark_cancelled(task_id)
```

---

## 五、文件改动清单

### 5.1 新增文件

| 文件 | 职责 | 预估行数 |
|------|------|---------|
| `backend/services/handlers/interrupt_anchor.py` | 落锚常量 + orphan 检测 + TASK RESUMPTION 渲染 | ~120 |
| `frontend/src/components/chat/MessageContent.tsx`（或对应渲染组件） | `cancelled` 状态渲染 + partial 末尾 8px 灰字提示 | ~30 |

### 5.2 修改文件

| 文件 | 改动点 | Phase | 预估改动量 |
|------|--------|-------|-----------|
| `backend/services/handlers/chat_context/history_loader.py` | 加 `_fix_orphan_tool_calls` + `_inject_task_resumption` | 0 / 4 | ~80 |
| `backend/services/websocket_manager.py` | `cancelled_tasks` 集合 + 闸门 + Redis pub/sub | 1 / 5 | ~80 |
| `backend/services/handlers/chat_generate_mixin.py` | LLM stream cancel | 2 | ~30 |
| `backend/services/handlers/chat_handler.py` | 接收 cancel_event 后落锚原子操作 + 工具结果丢弃 | 2 / 3 | ~60 |
| `backend/services/handlers/chat_context/content_extractors.py` | `interrupt_marker` 跳过（不喂 LLM） | 3 | ~10 |
| `backend/api/routes/task.py` | 取消路径改走落锚（不再粗暴改 status） | 3 | ~30 |
| `backend/utils/time_context.py` | 新增 `format_relative_time` | 4 | ~15 |
| `backend/config/system_prompts.py`（或对应位置） | 系统提示词加"中断标记理解"段 | 4 | ~10 |
| `frontend/src/components/chat/input/InputArea.tsx` | 停止流程对齐 | 5 | ~20 |
| `frontend/src/hooks/useRegenerateHandlers.ts` | 删除中断场景的「继续」分支（INTERRUPTED 走标准发消息链路） | 5 | ~10 |
| `frontend/src/contexts/wsMessageHandlers.ts` | 接收 `interrupt_marker` 事件 | 5 | ~15 |

### 5.3 不改动的文件

- `backend/services/agent/stop_policy.py`（如果已存在）— Agent 自主停止链路独立
- `backend/services/agent/loop_hooks.py` — Hook 链路不变
- `backend/migrations/` — `messages` 表已有 `status` 字段，新增 `"interrupted"` 值不需要 schema 迁移

---

## 六、Phase 计划（6 个独立 Phase，每个可独立验证回滚）

| Phase | 内容 | 工作量 | 价值 | 依赖 |
|-------|------|--------|------|------|
| **0** | `history_loader` orphan 自动补对兜底 | 0.5d | 防御性，防止协议碎片导致 400 腐化 | 无 |
| **1** | WS 闸门 `cancelled_tasks`（**复合 key (org_id, task_id)**） | 0.5d | 根治"工具鬼显" + 多租户隔离 | 无 |
| **2** | 真停 LLM stream（abort 传播）+ `cancel.latency` metric 埋点 | 1d | "点停止真停"用户感知最强 | 无 |
| **3** | 落锚原子操作 + interrupt_marker 写入 + `cancel.events` metric + `credit_lock.release()` | 2d | 协议合法 + 双轨同步 + 计费正确 | Phase 2 |
| **4** | TASK RESUMPTION 渲染 + 系统提示词 + **跨 worker 信号（按 org 分片）** + `cancel.continued_5m` metric | 1d | LLM 理解中断 + 多 worker 支持 | Phase 3 |
| **5** | 前端：**四态机（IDLE/WORKING/STOPPING/INTERRUPTED）** + 工具卡片 `cancelled` 状态 + partial 末尾 8px 灰字 | 1.5d | 用户能用 + STOPPING 中间态 | Phase 4 |

**总计：~6.5 天**（前端简化，去掉「继续 / 新任务」按钮分支）。Phase 0/1/2 可并行先发，Phase 3-5 串行依赖。

**灰度节奏**：每个 Phase 上线按 Ring 0 → 1 → 2 → 3 推进（详见 §十六）。

### Phase 0 独立性说明

Phase 0 是**纯防御性兜底**：
- 即使其他 Phase 都不做，单独上 Phase 0 也能立即防止现存 / 未来的协议碎片造成会话腐化
- 改动范围极小（1 文件 ~80 行 + 新文件 ~60 行常量与工具函数）
- 不改主链路、不依赖 cancel 信号、不影响 WS
- 零回滚成本：直接 revert 即可

---

## 七、边界场景清单（9 项）

| # | 场景 | 处理策略 |
|---|---|---|
| 1 | 多工具并发，停止时部分完成 | 所有未配对 tool_call 全部 orphan 补对（统一不区分） |
| 2 | 工具后台卡住超时 | 后端独立 60s 硬超时 + 资源回收（与中断无关，由 Agent 停止策略层处理） |
| 3 | 用户停止后秒发新消息 | cancel_event 必须先 ack 落锚完成，否则两个 turn 并发污染 `messages` |
| 4 | 跨 worker 取消 | Redis pub/sub（Phase 5） |
| 5 | 表单等待中停止 | FormBlock 已 break，cancel 路径需兼容（落锚跳过 form 写入） |
| 6 | 企微链路停止 | 企微无停止按钮，但同走 `chat_handler`，验证不破坏现有 Steer 链路 |
| 7 | 刷新页面时正好被停 | `accumulated_blocks` 已落锚，前端 UI 应一致显示中断状态（工具 cancelled 标签 + 灰字提示） |
| 8 | WebSocket 断线 ≠ 用户停止 | 网络抖动不触发取消，区分主动 cancel vs 被动断开 |
| 9 | partial text 为空 | `interrupt_marker` 仍写，前端显示"用户中断（无内容）" |

---

## 八、测试用例清单

### 8.1 Phase 0 测试（orphan 兜底）

| 用例 | 输入 | 预期 |
|------|------|------|
| 正常配对消息 | `[assistant(tc_A), tool(tc_A), assistant(text)]` | 无变化 |
| 单个 orphan | `[assistant(tc_A), assistant(text)]` | 插入 `tool(tc_A, "[系统:...]")` |
| 多工具并发部分缺失 | `[assistant(tc_A, tc_B), tool(tc_A)]` | 补 `tool(tc_B, "[系统:...]")` |
| 全部缺失 | `[assistant(tc_A, tc_B)]` | 补 `tool(tc_A)` + `tool(tc_B)` |
| tc_id 顺序错乱 | `[assistant(tc_A, tc_B), tool(tc_B), tool(tc_A)]` | 无变化（都有配对） |
| 空 messages | `[]` | `[]` |
| 无 tool_calls 的 assistant | `[assistant(text)]` | 无变化 |

### 8.2 Phase 1 测试（WS 闸门）

| 用例 | 预期 |
|------|------|
| `mark_cancelled` 后 send_to_task 立即 drop | 不抛错，silent drop |
| TTL 30 分钟后 cancelled_tasks 自动清理 | 集合中移除该 task_id |
| 并发 mark_cancelled + send | 线程安全（锁保护） |

### 8.3 Phase 2 测试（真停 LLM stream）

| 用例 | 预期 |
|------|------|
| stream 中触发 cancel_event | stream_task 抛 `CancelledError`，<100ms 内退出 |
| stream 已完成后触发 cancel | 正常返回，cancel 无副作用 |
| 多 cancel 信号 | 幂等，只 cancel 一次 |

### 8.4 Phase 3 测试（落锚原子操作）

| 用例 | 预期 |
|------|------|
| 中途取消，messages 表 + tasks 表同步写入 | 事务内一致 |
| 取消后立即发新消息 | LLM API 无 400（orphan 已补对） |
| 取消后 partial_text 保留 | DB content 末尾有 partial + interrupt_marker |
| 取消后 thinking 保留 | DB content 末尾有 thinking + interrupt_marker |

### 8.5 集成测试

| 用例 | 预期 |
|------|------|
| 取消后用户在 INTERRUPTED 输入"继续上一步"（或任意文字） | LLM 看到 `[任务恢复]` + 已完成工具结果 + orphan `[系统:...]`，自然续上（不依赖特殊按钮） |
| 取消后用户输入任意文字 | history_loader 注入 `[任务恢复]` 前缀，LLM 看到中断标记 + 新 user 消息，自主判断续接 or 换方向 |
| 取消后刷新页面 | 前端中断信号正确显示（工具 cancelled 状态 + 灰字提示），partial 内容可见 |
| 多 worker 跨进程取消 | Redis pub/sub 触发本地 Event，cancel 生效 |
| 企微链路 | 现有 Steer 不受影响 |

---

## 九、风险与回滚

### 9.1 风险

1. **核心循环改动 = 主链路风险**：Phase 2 / 3 上线前必须 staging 跑全量回归
2. **cancel_event 时序**：取消瞬间到落锚完成之间有微秒级窗口，可能漏配对——通过 `_find_orphan_tool_calls` 在落锚时强制扫描兜底
3. **Redis pub/sub 延迟**：跨 worker 取消可能有 100-500ms 延迟，但工具反正不真停，不是关键路径
4. **STOPPING 期间输入框误触**：用户可能在落锚未完成时尝试输入——前端 STOPPING 态输入框 `disabled={true}`，避免并发污染
5. **WS 闸门 TTL 不当**：30 分钟过短可能导致跨页面重连收不到 drop，过长占内存——按 30 分钟 + 进程内存监控
6. **历史脏数据**：Phase 0 兜底覆盖了存量脏数据，但要监控 `_fix_orphan_tool_calls` 命中率（Phase 0 上线后日志监控 7 天）

### 9.2 回滚预案

| Phase | 回滚方式 |
|-------|---------|
| 0 | revert 单 commit（独立兜底） |
| 1 | revert 单 commit（移除 cancelled_tasks 集合） |
| 2 | feature flag `CANCEL_V2_STREAM_ABORT` 关闭 |
| 3 | feature flag `CANCEL_V2_ANCHOR` 关闭，回退到旧 `task.py` 取消逻辑 |
| 4 | feature flag `CANCEL_V2_RESUMPTION` 关闭，TASK RESUMPTION 不注入 |
| 5 | 前端 commit 单独 revert |

**feature flag 命名约定**：`CANCEL_V2_*`，默认 `False`，逐 Phase 灰度开启。

---

## 十、与现有文档的关系

| 现有文档 | 关系 |
|---------|------|
| [TECH_Agent停止策略产品化.md](TECH_Agent停止策略产品化.md) | **正交**：Agent 自主停止（连续失败/预算耗尽）vs 用户主动停止。共享 `ExecutionBudget` 但独立决策路径 |
| [TECH_AI主动沟通与打断机制.md](TECH_AI主动沟通与打断机制.md) | **正交**：ask_user / Steer / FollowUp 都是用户主动**发新消息**触发；本方案是用户**按按钮**触发。共享 WS 基础设施 |
| [TECH_Agent通信协议结构化.md](TECH_Agent通信协议结构化.md) | **互补**：`interrupt_marker` block 是新增的结构化协议块，符合现有协议规范 |
| [TECH_上下文工程重构.md](TECH_上下文工程重构.md) | **依赖**：复用 `history_loader` + `content_extractors` 链路，兜底逻辑加在尾部 |

---

## 十二、可观测性指标（基于 OpenTelemetry GenAI 规范扩展）

### 12.1 业界基线

- [Datadog LLM Observability Metrics](https://docs.datadoghq.com/llm_observability/monitoring/metrics/) 默认指标系列（`ml_obs.span.*`）只覆盖 span / token / cost，**没有 cancel / abort 专属指标**
- [OpenTelemetry GenAI Spec](https://opentelemetry.io/docs/specs/semconv/gen-ai/) 的 `gen_ai.response.finish_reasons` 当前只规定 `stop` / `tool_calls`，未规定 `cancel`
- **业界共识**：取消相关指标没有现成标准，必须 custom metric

### 12.2 自定义指标清单（4 个 + finish_reasons 扩展）

```python
# backend/services/observability/cancel_metrics.py（新增）

gen_ai.cancel.events            # Counter   每次取消事件
gen_ai.cancel.latency           # Histogram 点击→真停延迟 (ms)
gen_ai.cancel.orphan_fixed      # Counter   history_loader 兜底命中次数
gen_ai.cancel.continued_5m      # Counter   取消后 5 分钟内点继续

# OpenTelemetry finish_reasons 扩展枚举
finish_reason ∈ {stop, tool_calls, user_cancel, system_timeout, network_error}
```

### 12.3 必带 Tag

| Tag | 用途 |
|---|---|
| `org_id` | 多租户切分 |
| `phase` | 哪个 Phase 链路触发（v1/v2 区分） |
| `had_partial` | 是否有 partial 内容（bool） |
| `tools_in_flight` | 取消时正在跑的工具数（0/1/2+） |
| `cancel_source` | `frontend_button` / `esc_key` / `api` |

### 12.4 告警规则

| 告警 | 触发 | 严重性 |
|---|---|---|
| 取消延迟 p95 > 500ms | 5 分钟窗口 | P2 |
| `orphan_fixed` 命中率突增 > 10% | 1 小时窗口 | P1（说明落锚链路有 bug） |
| 单 org 1h 取消 > 50 次 | 滑动窗口 | P2（防恶意刷成本） |
| WS 闸门 drop > 100/min | 持续 5 分钟 | P2（说明取消信号路由异常） |

---

## 十三、多租户隔离

### 13.1 业界证据

- [Redis 官方多租户最佳实践](https://redis.io/blog/data-isolation-multi-tenant-saas/)：key 前缀 + ACL Channels
- 应用层 + 服务器层双重防护（"developers can make mistakes"）

### 13.2 决策

**1. Redis pub/sub channel 按 org 分片**：

```python
# 旧：CANCEL_CHANNEL = "task:cancel"
# 新：每 org 独立 channel
def cancel_channel(org_id: str) -> str:
    return f"task:cancel:{org_id or 'default'}"
```

**2. `cancelled_tasks` 集合用复合 key**：

```python
self._cancelled_tasks: set[tuple[str, str]] = set()  # (org_id, task_id)

def is_cancelled(self, org_id: str, task_id: str) -> bool:
    return (org_id or "", task_id) in self._cancelled_tasks
```

**3. WS 闸门检查时校验 `org_id`**：与 OrgScopedDB 三层防线对齐

### 13.3 跨 worker 取消的隔离

```
Worker A 收到取消请求 (org_a, task_x)
  → 本地 Event 找不到
  → publish "task:cancel:org_a" 频道
  → Worker B 订阅了所有 "task:cancel:*"
  → 收到消息后**二次校验** task 归属
  → 触发本地 cancel
```

### 13.4 不上 Redis ACL Channels 的理由

- 项目当前 Redis 部署未启用 ACL
- key 前缀方案在 task_id UUID 全局唯一前提下已足够
- ACL Channels 留作 v2 升级项

---

## 十四、计费策略（取消时的成本归属）

### 14.1 业界共识（一手）

[Vercel AI Gateway Stripe Billing](https://vercel.com/docs/ai-gateway/ecosystem/stripe-billing)：

> "For supported providers (OpenAI, Anthropic, Azure), aborting the connection **immediately stops model processing AND billing**"
> "Stripe billing only fires on **successful responses**"

业界一致：**取消后不计费已生成的 partial token，由平台吸收**。

### 14.2 决策矩阵

| 资源 | 取消后处理 | 谁吃 |
|---|---|---|
| LLM 已生成的 partial tokens | 不计入用户消耗 | 平台 |
| 后台工具未返回（ERP API） | 跑完丢弃 | 平台（API 配额消耗已发生） |
| 后台工具已返回（落锚瞬间已完成） | 写入历史 = 用户能继续用 | 用户 |
| `credit_lock` 预留积分 | **release 而非 deduct** | 用户不损失 |
| `tool_audit_log` | 仍记录（合规） | 平台数据 |

### 14.3 实现位置

```python
# chat_handler.py 落锚路径
async def _on_user_cancel(self, ctx):
    # ... 落锚 ...
    if self._credit_lock_active:
        await self._credit_lock.release(reason="user_cancelled")
        # 不调 self._credit_lock.deduct(...)

    await tool_audit.log(
        org_id=ctx.org_id,
        action="user_cancel",
        partial_tokens=ctx.tokens_so_far,
        tools_in_flight=len(ctx.pending_tool_calls),
    )
```

### 14.4 防恶意刷成本

| 阈值 | 处理 |
|---|---|
| 单 org 1 小时 cancel > 50 次 | P2 告警 + Slack 通知运营 |
| 单 org 24 小时累计 > 200 次 | 自动限流（cancel API 降级，要求等待 task 完成） |

阈值后续根据真实数据调整。

---

## 十五、UX 状态机（四态完整定义）

### 15.1 业界反面教材

[Claude Code Issue #50665](https://github.com/anthropics/claude-code/issues/50665) — 必读警示：

> "UI shows working when Claude is idle... no way to know if Claude is working, stuck, or crashed"
> "Stop button non-functional during execution"

**根因**：Claude Code 缺失 **STOPPING 中间态**——按下停止后没有视觉反馈，用户以为没生效。

### 15.2 四态机定义

| 状态 | 触发 | 视觉 | 输入框 | 操作按钮 | 典型时长 |
|---|---|---|---|---|---|
| **IDLE** | 无任务 | 静态输入框 | ✅ 可输入 | 「发送」 | — |
| **WORKING** | task running | 动画 + streaming dots | ⚠️ 只读 | 「停止」红色 | 长 |
| **STOPPING** | 收到 cancel 请求，落锚未完成 | 灰色 spinner "正在停止…" | ❌ 禁用 | 按钮置灰 | 100-500ms |
| **INTERRUPTED** | 落锚完成，`messages.status='interrupted'` | partial + 工具 cancelled 标签 + 末尾 8px 灰字 | ✅ **可输入** | **「发送」（与 IDLE 一致）** | — |

### 15.3 状态转移

```
       发任意消息
IDLE ─────────► WORKING
                  │ 点停止
                  ▼
              STOPPING ──落锚完成──► INTERRUPTED
                                          │
                                  用户发任意消息
                                  （文本=用户唯一表达入口）
                                          ▼
                                       WORKING
```

### 15.4 关键约束

1. **STOPPING 不可跳过**：按下停止后 UI 必须**立即**切 STOPPING，不能等后端 ack
2. **STOPPING 期间输入框禁用**：避免落锚未完成时的并发污染
3. **INTERRUPTED 视觉极简**：不渲染独立"已中断"卡片。工具卡片自带"已中断"标签 + 纯文本场景在 partial 末尾加 8px 灰字"停止于 X 前"——仅此两处
4. **INTERRUPTED 的输入区域与 IDLE 完全一致**：占位符文案、按钮样式、键盘快捷键全部沿用 IDLE 状态，**不**加"输入任意文字"之类的特殊提示
5. **不区分「继续 / 新任务」**：用户在 INTERRUPTED 输入任何文字即新一轮 user message；LLM 看历史里的 `interrupt_marker` + [任务恢复] 前缀 + 用户文本，自主理解"是续接还是换方向"
6. **`[任务恢复]` 前缀注入条件**：history_loader 检测到上一条 assistant 消息含 `interrupt_marker` 就**无条件**注入，不依赖用户输入文本

### 15.5 中断的视觉信号（极简，无独立卡片）

**核心原则**：不渲染独立"已中断"卡片。中断状态通过两处轻量信号传达：

**信号 1：工具卡片自带"已中断"标签**（覆盖 99% 场景）

```
┌────────────────────────────────┐
│ 🟡 erp_query    已中断          │
│ ─────────────────────────────  │
│ 参数：{ "platform": "tb", ... } │
│ ⏸ 用户中断了执行（结果不可用）   │
└────────────────────────────────┘
```

**信号 2：partial 文本末尾极轻量灰字提示**（纯文本场景，参考 Cursor / Claude Code CLI）

```
🤖 助手:
   昨天的销售数据显示，整体销售环比增长 12%，主要驱动因素是
   
   停止于 1 分钟前      ← 8px 中性灰 (#9ca3af)，左对齐，不带背景
```

**视觉规范**：
- 字号：8-10px（项目最小可读字号）
- 颜色：`var(--color-neutral-500)`（暗色主题 `--color-neutral-400`）
- 位置：partial 文本结束后另起一行，左对齐
- 时间格式：相对时间（"刚刚" / "X 分钟前" / "X 小时前" / "X 天前"）
- **不画卡片边框、不画背景色、不加图标**——极简即止

**数据层 vs 视觉层分离**：
- `interrupt_marker` block 仍存在 `messages.content` 数据层，供 `history_loader` 检测后注入 `[任务恢复]` 前缀
- 前端**不渲染**此 block 为独立卡片
- 前端通过 `messages.status === 'interrupted'` 判断是否在 partial 末尾添加灰字提示

### 15.6 文案统一表

| 场景 | 统一文案 |
|---|---|
| 用户主动按按钮 | **停止** |
| 系统层动作（asyncio） | cancel |
| 数据库 `messages.status` 值 | `interrupted` |
| 后台 `tasks.status` 值 | `cancelled` |
| UI 卡片标题 | **已中断** |
| LLM 提示词 | "任务恢复" / "用户中断" |

### 15.7 视觉规范

- WORKING 主色：跟随主题（Linear 黑 / Claude 暖 / Classic 蓝）
- STOPPING 主色：中性灰 `var(--color-neutral-500)`
- 工具 `cancelled` 状态：弱化背景 + 左侧 2px 黄色细边 + ⏸ 暂停符号（不用 ❌ 减少负面联想）
- 纯文本场景灰字提示：`var(--color-neutral-500)` 8-10px，左对齐无背景

---

## 十六、灰度策略（按 org 分阶段）

### 16.1 业界证据

业界通用 canary：**5% → 10% → 25% → 100%**（[LaunchDarkly](https://launchdarkly.com/docs/guides/infrastructure/deployment-strategies) / [ConfigCat](https://configcat.com/blog/how-to-implement-a-canary-release-with-feature-flags/)）。

### 16.2 按 org 灰度（不是 user）

**理由**：
- 项目主要营收单位是 org（企业付费）
- 单个 org 内体验一致避免"同事看到不同 UI"的混乱
- 按 org 灰度更容易回滚（关一家比关一批用户简单）

### 16.3 四阶段 Ring 部署

| 阶段 | 范围 | 持续 | 准入指标 | 准出指标 |
|---|---|---|---|---|
| **Ring 0** Dogfood | 内部测试 org（蓝创） | 3 天 | 单测 + 回归全过 | 0 P0/P1 + 用户反馈无负面 |
| **Ring 1** Beta | 10 家友好 org | 7 天 | Ring 0 准出 | `cancel.latency.p95` < 500ms + `orphan_fixed` < 5% |
| **Ring 2** Gradual | 25% org（按 `org_id` hash） | 7 天 | Ring 1 准出 | 各项指标符合预期 + 无回归 |
| **Ring 3** Full | 100% | — | Ring 2 准出 | — |

### 16.4 feature flag 矩阵

加入 `core/config.py`：

```python
# Phase 灰度开关（每 Phase 独立控制）
cancel_v2_phase0_orphan_fix: bool = True   # 防御性，默认开启
cancel_v2_phase1_ws_gate: bool = False
cancel_v2_phase2_real_stop: bool = False
cancel_v2_phase3_anchor: bool = False
cancel_v2_phase4_resumption: bool = False
cancel_v2_phase5_frontend: bool = False

# org 白名单（按阶段控制范围）
cancel_v2_enabled_org_ids: list[str] = []   # 空 = 全量
cancel_v2_dogfood_org_ids: list[str] = []   # Ring 0 名单
```

### 16.5 灰度判定函数

```python
# core/feature_flags.py（新增）

def cancel_v2_enabled_for(phase: int, org_id: str | None) -> bool:
    flag = getattr(settings, f"cancel_v2_phase{phase}_*")
    if not flag:
        return False
    if not settings.cancel_v2_enabled_org_ids:
        return True
    return org_id in settings.cancel_v2_enabled_org_ids
```

### 16.6 回滚预案

每个 Phase 独立 commit + 独立 feature flag：

1. **快速回滚**：关 feature flag（生效 < 1 分钟）
2. **代码回滚**：revert 单 commit（5-10 分钟）
3. **数据回滚**：`messages.status='interrupted'` 改回 `'completed'`（仅 Phase 3 需要）

---

## 十七、数据一致性（双轨持久化）

### 17.1 业界证据

- [Vercel AI SDK Resume Streams](https://ai-sdk.dev/docs/ai-sdk-ui/chatbot-resume-streams) 用 **Redis cache + DB source of truth** 双层
- LangChain checkpoint 用单事务保证一致性

### 17.2 三个数据源

| 数据源 | 用途 | 角色 |
|---|---|---|
| `messages.content` | DB 持久化 + LLM 历史重建 | **Single Source of Truth** |
| `tasks.accumulated_blocks` | 前端 UI 展示 + 刷新恢复 | 冗余 cache（最终一致） |
| 内存 `messages` 数组 | 当轮 LLM 调用 | 进程内临时态 |

### 17.3 落锚顺序（先主后副）

```python
async def _persist_anchor():
    # Step 1: 先写 messages 表（落锚成功标志）
    await db.table("messages").update({
        "content": content_blocks,
        "status": "interrupted",
    }).eq("id", message_id).execute()

    # Step 2: 再写 tasks 表（冗余）
    try:
        await db.table("tasks").update({
            "accumulated_blocks": content_blocks,
            "status": "cancelled",
        }).eq("external_task_id", task_id).execute()
    except Exception as e:
        # 副表失败不致命，下次刷新会自愈
        logger.warning(f"tasks 表落锚失败，依赖重建机制 | {e}")
```

### 17.4 崩溃恢复（自愈机制）

Worker 启动时 reconcile：

```python
async def reconcile_interrupted_messages():
    """扫描 status='interrupted' 但 accumulated_blocks 不一致的 task"""
    interrupted_msgs = await db.table("messages").select(
        "id, content, conversation_id"
    ).eq("status", "interrupted").execute()

    for msg in interrupted_msgs.data:
        task = await db.table("tasks").select(
            "external_task_id, accumulated_blocks"
        ).eq("assistant_message_id", msg["id"]).execute()

        if not task.data:
            continue
        task = task.data[0]
        if task["accumulated_blocks"] != msg["content"]:
            # 不一致 → 以 messages 为准重建 tasks
            await db.table("tasks").update({
                "accumulated_blocks": msg["content"],
                "status": "cancelled",
            }).eq("external_task_id", task["external_task_id"]).execute()
```

### 17.5 不上 DB transaction 的理由

- Supabase 跨表 transaction 需要 RPC 包装，复杂度增加
- "先主后副 + 重建机制" 在实践中性能更好（写 messages 立即返回）
- 极端崩溃场景由 reconcile 兜底，最终一致即可

### 17.6 内存与 DB 双重保险

```
内存补对 (find_orphan_tool_calls) → 序列化到 DB
                                          ↓
                          history_loader 读出
                                          ↓
                          fix_orphan_tool_calls 兜底（Phase 0）
```

双重保险：即使内存补对漏了，DB 读出时还会补一次。

---

## 十八、Sources（一手证据）

### 18.1 功能实现层

- [Cline `responses.ts:231-258` — taskResumption 生产代码](https://github.com/cline/cline/blob/main/apps/vscode/src/core/prompts/responses.ts)
- [Cline `buildUserFeedbackContent.ts` — `<feedback>` 标签](https://github.com/cline/cline/blob/main/apps/vscode/src/core/task/utils/buildUserFeedbackContent.ts)
- [LiteLLM Message Sanitization — orphan tool_call 补全](https://docs.litellm.ai/docs/completion/message_sanitization)
- [Claude Code #7673 — Tool cancellation 实际字符串](https://github.com/anthropics/claude-code/issues/7673)
- [Claude Code #3003 — Interrupting during tool calls corrupts conversation](https://github.com/anthropics/claude-code/issues/3003)
- [LangGraph Interrupts — `id` + `value` 极简结构](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [Vercel AI SDK — Stopping Streams 工业建议](https://ai-sdk.dev/docs/advanced/stopping-streams)

### 18.2 非功能维度层

- [Datadog LLM Observability Metrics](https://docs.datadoghq.com/llm_observability/monitoring/metrics/)
- [OpenTelemetry GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [Redis Multi-tenant Data Isolation](https://redis.io/blog/data-isolation-multi-tenant-saas/)
- [Vercel AI Gateway Billing Policy](https://vercel.com/docs/ai-gateway/ecosystem/stripe-billing)
- [Vercel AI SDK Resume Streams](https://ai-sdk.dev/docs/ai-sdk-ui/chatbot-resume-streams)
- [Claude Code Stop Button Bug #50665](https://github.com/anthropics/claude-code/issues/50665)
- [LaunchDarkly Deployment Strategies](https://launchdarkly.com/docs/guides/infrastructure/deployment-strategies)
- [ConfigCat Canary Release Guide](https://configcat.com/blog/how-to-implement-a-canary-release-with-feature-flags/)
