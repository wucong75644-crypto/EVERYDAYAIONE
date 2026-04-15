# TECH：AI 主动沟通与打断机制

> **版本**：V1.0 | **日期**：2026-04-15 | **状态**：方案评审

## 一、问题定义

当前 AI 系统缺少"和用户沟通"的能力：
- AI 收到模糊指令直接执行，不会问用户补充关键参数
- ERP Agent 遇到缺少必要参数时，自己编造默认值
- 用户无法在 AI 执行过程中打断或改变指令

**目标**：参考 Claude Code 的交互模式，让 AI 在信息不足时主动追问，并支持用户随时打断。

## 二、架构现状分析

### 2.1 当前消息流

```
用户发消息 → HTTP POST /messages/generate
  → ChatHandler._stream_generate()
    → while not _budget.stop_reason:
        → adapter.stream_chat()         # LLM 流式生成
        → 检测 tool_calls
        → _execute_tool_calls()         # 执行工具（含 ERP Agent 子循环）
        → 工具结果塞回 messages
        → 继续循环
    → on_complete() 持久化到 DB
  → WS 推送 message_done
```

### 2.2 七层断裂点

| 层 | 问题 | 文件 |
|----|------|------|
| 路由层 | 意图集无 `need_more_info` | `intent_router.py` |
| 提示词层 | 主聊天 system prompt 无追问引导 | `chat_context_mixin.py` |
| 工具层 | 主聊天工具集无 `ask_user` | `chat_tools.py` |
| 参数验证层 | 缺参数 → "请补齐后重试" → AI 自己编 | `tool_args_validator.py` |
| 工具循环层 | ask_user 是退出信号，调了就结束 | `tool_loop_executor.py:144` |
| WS 层 | 无追问消息类型 | `websocket_types.py` |
| 前端层 | 不区分"追问"和"回答" | `message.ts` |

### 2.3 已有基础设施（可复用）

| 组件 | 位置 | 复用方式 |
|------|------|---------|
| `ask_user` 工具定义 | `phase_tools.py:16-38` | 扩展到主聊天工具集 |
| `tool_confirm_request` WS 类型 | `websocket_types.py:38` | 参考其模式新增 ask_user 类型 |
| `wait_for_confirm` / `resolve_confirm` | `websocket_manager.py:300-345` | 复用同样的 Future 等待模式 |
| `ToolLoopExecutor.exit_signals` | `loop_types.py:48` | 主聊天循环中也用退出信号 |
| `context_compressor` | `context_compressor.py` | 恢复时压缩 frozen_messages |
| `tool_result_envelope` | `tool_result_envelope.py` | 恢复时截断大工具结果 |

## 三、Claude Code 参考架构

### 3.1 Claude 的三种消息路由

| 路由 | 触发条件 | 行为 |
|------|---------|------|
| **Question 工具** | AI 调用 AskUserQuestion | 工具循环阻塞等回答，结果作为 tool_result 返回 |
| **Steer** | 用户在 AI 执行中发消息 | 当前工具完成后跳过剩余工具，注入用户消息，开始新 turn |
| **FollowUp** | 用户在 AI 停止后追加 | 等待当前 turn 完全结束后注入，开始新 turn |

### 3.2 Claude 的上下文管理

- **内存层**：messages[] 原封不动保留（不删不改）
- **送给 LLM 前**：三层处理
  - `transformContext()` — 扩展钩子可修改
  - 自动压缩 — 上下文 > (窗口 - 16K) 时，旧消息压缩成结构化摘要，保留最近 20K token
  - `convertToLlm()` — 类型转换 + 过滤
- **工具结果截断**：> 50KB 或 > 2000 行 → 截断

### 3.3 我们的适配

Claude 是本地 CLI，可以内存阻塞。我们是 Web 服务，用 **DB 持久化 + WS 异步等待** 模拟：

| Claude（本地进程） | 我们（Web 服务） |
|-------------------|-----------------|
| Question 工具阻塞在内存 | ask_user → 状态存 DB → WS 推问题 → 用户回复 → 从 DB 恢复 |
| messages 在内存不动 | messages 序列化到 DB（frozen_messages），反序列化后内容一致 |
| Steer 队列（内存） | WS 接收新消息 → 检测 pending → 跳过剩余工具 |
| 上下文自动压缩 | 复用现有 `context_compressor` + `tool_result_envelope` |

## 四、详细设计

### 4.1 数据模型

#### 4.1.1 `pending_interaction` 表

```sql
-- 迁移文件: backend/migrations/078_pending_interaction.sql

CREATE TABLE IF NOT EXISTS pending_interaction (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    org_id UUID,

    -- 冻结的 messages 数组（完整工具循环上下文）
    frozen_messages JSONB NOT NULL,

    -- ask_user 的问题内容（前端展示 + 恢复时校验用）
    question TEXT NOT NULL,

    -- 来源标记
    source VARCHAR(50) NOT NULL DEFAULT 'chat',  -- 'chat' | 'erp_agent'

    -- ask_user 工具的 tool_call_id（恢复时用于构造 tool_result）
    tool_call_id VARCHAR(100) NOT NULL,

    -- 冻结时的工具循环状态快照
    loop_snapshot JSONB NOT NULL DEFAULT '{}',
    -- loop_snapshot 结构:
    -- {
    --   "turn": 3,                    -- 当前轮次
    --   "tools_called": ["local_global_stats", "ask_user"],
    --   "accumulated_text": "...",     -- 已累积的文本
    --   "content_blocks": [...],       -- 已收割的内容块
    --   "tool_context": {...},         -- ToolLoopContext 状态
    --   "model_id": "gemini-3-pro",
    --   "budget_snapshot": {           -- 预算消耗快照
    --     "turns_used": 3,
    --     "tokens_used": 12500
    --   }
    -- }

    -- 状态
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    -- 'pending'  = 等待用户回复
    -- 'resumed'  = 已恢复（用户回复了）
    -- 'expired'  = 已过期（超时/用户换话题）
    -- 'cancelled' = 用户主动取消

    created_at TIMESTAMPTZ DEFAULT NOW(),
    expired_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '24 hours'
);

-- 一个对话同时只有一条 pending
CREATE UNIQUE INDEX idx_pending_conv_active
    ON pending_interaction(conversation_id) WHERE status = 'pending';

-- 过期清理索引
CREATE INDEX idx_pending_expired
    ON pending_interaction(expired_at) WHERE status = 'pending';
```

#### 4.1.2 新增 WS 消息类型

```python
# websocket_types.py 新增

class WSMessageType(str, Enum):
    # ... 现有类型 ...

    # === AI 主动沟通 ===
    ASK_USER_REQUEST = "ask_user_request"      # 后端 → 前端：AI 追问
    ASK_USER_RESPONSE = "ask_user_response"    # 前端 → 后端：用户回答
    ASK_USER_DISMISS = "ask_user_dismiss"      # 前端 → 后端：用户忽略/跳过

    # === 用户打断 ===
    USER_STEER = "user_steer"                  # 前端 → 后端：打断当前执行
```

#### 4.1.3 WS 消息 Payload

```python
# ask_user_request payload（后端 → 前端）
{
    "interaction_id": "uuid",           # pending_interaction.id
    "question": "检测到异常订单，需要排除刷单吗？",
    "source": "erp_agent",              # 来源标识
    "options": [                        # 可选：快捷选项
        "排除刷单",
        "排除所有异常",
        "不排除，显示全部"
    ],
    "timeout": 86400,                   # 过期秒数
}

# ask_user_response payload（前端 → 后端）
{
    "interaction_id": "uuid",
    "answer": "排除刷单"
}

# user_steer payload（前端 → 后端）
{
    "conversation_id": "uuid",
    "task_id": "uuid",
    "message": "算了，帮我查库存"       # 新指令
}
```

### 4.2 后端核心流程

#### 4.2.1 ask_user 冻结流程（主聊天工具循环）

```
chat_handler._stream_generate() 工具循环中
  → AI 调用 ask_user(message="需要排除刷单吗？")
  → _execute_tool_calls() 检测到 ask_user
  → 调用 _freeze_for_ask_user():
      ① 序列化当前 messages[] → frozen_messages
      ② 快照循环状态 → loop_snapshot
      ③ INSERT INTO pending_interaction
      ④ WS 推送 ask_user_request 给前端
      ⑤ 把 ask_user 的文本作为当前轮的 accumulated_text
      ⑥ 设置 _ask_user_frozen = True → break 出工具循环
  → on_complete() 持久化（assistant 消息 = ask_user 的问题文本）
```

**关键**：ask_user 的问题文本会作为 assistant 消息保存到 DB，前端正常显示为 AI 的回复。

#### 4.2.2 用户回复恢复流程

```
用户回复 → HTTP POST /messages/generate
  → ChatHandler.start() / generate()
  → _check_pending_interaction(conversation_id):
      ① SELECT * FROM pending_interaction
         WHERE conversation_id = ? AND status = 'pending'
      ② 如果存在：
         a. 反序列化 frozen_messages → messages[]
         b. 构造 tool_result 消息：
            {"role": "tool",
             "tool_call_id": pending.tool_call_id,
             "content": f"用户回答: {user_answer}"}
         c. messages.append(tool_result)
         d. 恢复 loop_snapshot（turn/tools_called/content_blocks/...）
         e. UPDATE status = 'resumed'
         f. 继续工具循环（从冻结点恢复）
      ③ 如果不存在：正常流程
```

**上下文恢复后的压缩**：
```python
# 恢复后，走现有压缩管线
from services.handlers.context_compressor import (
    compact_stale_tool_results,
    enforce_tool_budget,
    enforce_history_budget_sync,
)
compact_stale_tool_results(messages, settings.context_tool_keep_turns)
enforce_tool_budget(messages, settings.context_tool_token_budget)
enforce_history_budget_sync(messages, settings.context_history_token_budget)
```

#### 4.2.3 ERP Agent 内的 ask_user

ERP Agent 作为主聊天的子循环，ask_user 需要**冒泡**到主循环：

```
主聊天工具循环
  → AI 调用 erp_agent(query="查上周销售额")
  → ERPAgent.execute():
      → ERP 工具循环
      → AI 调用 ask_user(message="需要排除刷单吗？")
      → exit_via_ask_user = True
      → 返回 ERPAgentResult(text="需要排除刷单吗？", status="ask_user")
  → tool_executor._erp_agent() 检测到 status="ask_user"
  → 返回特殊结果给主循环
  → 主循环检测到 erp_agent 返回 ask_user
  → 触发 _freeze_for_ask_user()（冻结的是主循环的 messages）
```

**ERPAgentResult 新增字段**：
```python
@dataclass
class ERPAgentResult:
    text: str
    status: str           # "success" | "partial" | "error" | "ask_user"  ← 新增
    # ...
    ask_user_question: str | None = None  # ask_user 时的原始问题
```

#### 4.2.4 用户换话题的处理

```python
async def _check_pending_interaction(self, conversation_id, user_content):
    """检查是否有待恢复的 pending interaction"""
    pending = self.db.table("pending_interaction") \
        .select("*") \
        .eq("conversation_id", conversation_id) \
        .eq("status", "pending") \
        .maybe_single() \
        .execute()

    if not pending.data:
        return None  # 无 pending，正常流程

    # 一律恢复上下文，让 AI 自己判断是回答追问还是新话题
    # （参考 Claude：不做意图判断，统一恢复）
    return pending.data
```

**不做意图分类**：恢复 frozen_messages + 追加用户回答，AI 自己判断语义。如果用户确实换了话题，AI 看到完整上下文后会自然切换。之前查过的数据也不浪费。

### 4.3 打断机制（Steer）

#### 4.3.1 打断触发场景

```
AI 正在执行工具循环（流式输出中 / 工具执行中）
用户发了新消息（不是点"停止"，是直接输入新消息）
  → 前端检测：当前对话有 streaming 消息
  → WS 发送 user_steer { conversation_id, task_id, message }
```

#### 4.3.2 后端打断处理

**方案：复用 tool_confirm 的 asyncio.Event 模式**

```python
# websocket_manager.py 新增

class WebSocketManager:
    def __init__(self):
        # ... 现有 ...
        self._steer_signals: Dict[str, asyncio.Event] = {}
        self._steer_messages: Dict[str, str] = {}

    def register_steer_listener(self, task_id: str) -> None:
        """注册打断监听（工具循环开始时调用）"""
        self._steer_signals[task_id] = asyncio.Event()

    def check_steer(self, task_id: str) -> str | None:
        """非阻塞检查是否有打断信号（每个工具执行完后调用）"""
        event = self._steer_signals.get(task_id)
        if event and event.is_set():
            msg = self._steer_messages.pop(task_id, None)
            del self._steer_signals[task_id]
            return msg
        return None

    def resolve_steer(self, task_id: str, message: str) -> None:
        """前端打断消息到达时调用"""
        self._steer_messages[task_id] = message
        event = self._steer_signals.get(task_id)
        if event:
            event.set()

    def unregister_steer_listener(self, task_id: str) -> None:
        """清理（工具循环结束时调用）"""
        self._steer_signals.pop(task_id, None)
        self._steer_messages.pop(task_id, None)
```

#### 4.3.3 工具循环中的打断检查点

**主聊天工具循环** (`chat_handler.py`)：

```python
# 在每个工具执行完后检查打断
for tc, result_text, is_error in tool_results:
    messages.append({"role": "tool", ...})

    # ── 打断检查点 ──
    steer_msg = ws_manager.check_steer(task_id)
    if steer_msg:
        # 跳过剩余工具，注入用户新消息
        logger.info(f"Steer detected | task={task_id} | msg={steer_msg[:50]}")
        messages.append({"role": "user", "content": steer_msg})
        # 继续工具循环（AI 看到新消息后自行决策）
        break
```

**子 Agent 工具循环** (`tool_loop_executor.py`)：

```python
# _execute_tools() 中每个工具执行完后检查
for tc in completed:
    # ... 执行工具 ...

    # ── 打断检查点（通过 hook_ctx 传入 task_id）──
    from services.websocket_manager import ws_manager
    steer_msg = ws_manager.check_steer(hook_ctx.task_id) if hook_ctx.task_id else None
    if steer_msg:
        # 跳过剩余工具
        for remaining_tc in completed[completed.index(tc) + 1:]:
            messages.append({
                "role": "tool",
                "tool_call_id": remaining_tc["id"],
                "content": "⚠ 用户发送了新消息，跳过此工具调用。",
            })
        # 注入用户消息
        messages.append({"role": "user", "content": steer_msg})
        break
```

#### 4.3.4 前端打断交互

```
用户在 AI 执行中输入新消息 → 发送 user_steer WS 消息
  → 停止当前消息的流式渲染
  → 将新消息作为 user message 乐观显示
  → 等待后端返回新的 message_start（继续流式）
```

### 4.4 提示词改造

#### 4.4.1 主聊天 System Prompt 追问引导

```python
# chat_context_mixin.py _build_llm_messages() 中注入

ASK_USER_GUIDANCE = (
    "## 主动沟通规则\n"
    "当以下情况出现时，必须调用 ask_user 工具向用户确认，禁止猜测或使用默认值：\n"
    "1. 查询缺少关键参数（时间范围、具体店铺、商品等）且有多种合理默认值\n"
    "2. 工具返回多条相似结果无法区分（需要用户明确选择）\n"
    "3. 操作有风险或不可逆（删除、修改等需要用户确认）\n"
    "4. 用户需求有歧义，不同理解会导致完全不同的结果\n\n"
    "调用 ask_user 时：\n"
    "- 列出你已知的信息，说明缺什么\n"
    "- 给出 2-3 个选项引导用户选择\n"
    "- 用简洁的语言，不要长篇大论\n"
)
```

#### 4.4.2 ERP Agent 路由提示词补充

```python
# erp_tools.py ERP_ROUTING_PROMPT 中追加

ERP_ASK_USER_SCENARIOS = (
    "\n## 主动追问场景\n"
    "以下场景必须用 ask_user 追问用户，禁止自行决定：\n"
    "- 用户查「销量/销售额/付款订单」等统计需求，未指定是否排除异常订单\n"
    "  → 先查 tag_list 获取异常标签分布，告知用户异常订单情况，询问是否排除\n"
    "- 用户查商品但关键词匹配到多个 SKU → 列出候选让用户选择\n"
    "- 用户要执行写操作（取消订单、修改价格等）→ 确认操作对象和影响范围\n"
    "- 时间范围模糊（"最近"/"上次"）→ 给出几个选项让用户确认\n"
)
```

#### 4.4.3 参数验证引导改造

```python
# tool_args_validator.py 修改

# 现有：
error_msg = "参数校验失败 — 缺少必填参数:\n... \n请补齐后重试。"

# 改为：
error_msg = (
    "参数校验失败 — 缺少必填参数:\n"
    + "\n".join(param_hints)
    + "\n\n请调用 ask_user 向用户确认缺失的参数，"
    "禁止自行猜测参数值。"
)
```

### 4.5 前端改造

#### 4.5.1 消息类型扩展

```typescript
// message.ts 新增字段
interface Message {
    // ... 现有字段 ...
    interaction_type?: 'response' | 'question';  // 新增
    interaction_id?: string;                       // pending_interaction.id
    interaction_options?: string[];                 // 快捷选项
}
```

#### 4.5.2 追问消息渲染

```
MessageItem 组件扩展：
  if (message.interaction_type === 'question') {
    → 显示问题文本（普通 AI 消息样式）
    → 底部显示快捷选项按钮（类似 SuggestionChips）
    → 输入框显示"回答 AI 的问题..."占位符
  }
```

#### 4.5.3 打断交互

```
InputArea 组件扩展：
  if (isStreaming && userInput) {
    → 发送按钮变为"发送并打断"（橙色）
    → 点击：发送 user_steer WS 消息 + 停止当前流式
  }
```

## 五、Phase 分解

### Phase 0：数据层（0.5 天）

| 文件 | 改动 |
|------|------|
| `backend/migrations/078_pending_interaction.sql` | 新建表 + 索引 |
| `backend/schemas/websocket_types.py` | 新增 4 个 WS 消息类型 |
| `backend/schemas/websocket_builders.py` | 新增 `build_ask_user_request` 构建函数 |

### Phase 1：主聊天 ask_user 能力（1.5 天）

| 文件 | 改动 |
|------|------|
| `backend/config/chat_tools.py` | `get_core_tools()` 加入 `ask_user` 工具 |
| `backend/services/handlers/chat_context_mixin.py` | `_build_llm_messages()` 注入 ASK_USER_GUIDANCE |
| `backend/services/handlers/chat_tool_mixin.py` | `_execute_tool_calls()` 检测 ask_user → 调用冻结流程 |
| `backend/services/handlers/chat_handler.py` | 新增 `_freeze_for_ask_user()` / 修改工具循环 break 逻辑 |
| `backend/config/erp_tools.py` | ERP_ROUTING_PROMPT 追加追问场景 |
| `backend/services/agent/tool_args_validator.py` | 缺参错误信息改为引导调 ask_user |

### Phase 2：恢复流程（1.5 天）

| 文件 | 改动 |
|------|------|
| `backend/services/handlers/chat_handler.py` | `_stream_generate()` 入口加 `_check_pending_interaction()` |
| `backend/services/handlers/chat_handler.py` | 新增 `_resume_from_pending()` — 反序列化 + 构造 tool_result + 恢复循环 |
| `backend/services/handlers/chat_handler.py` | 新增 `_stream_generate_resumed()` — 恢复后的工具循环（复用现有循环体） |

### Phase 3：ERP Agent ask_user 冒泡（1 天）

| 文件 | 改动 |
|------|------|
| `backend/services/agent/erp_agent_types.py` | `ERPAgentResult` 新增 `ask_user_question` 字段 |
| `backend/services/agent/erp_agent.py` | `execute()` 检测 `exit_via_ask_user` → 设置 status="ask_user" |
| `backend/services/agent/tool_executor.py` | `_erp_agent()` 检测 status="ask_user" → 返回特殊标记 |
| `backend/services/handlers/chat_tool_mixin.py` | 检测 erp_agent 返回 ask_user → 触发主循环冻结 |

### Phase 4：打断机制 Steer（1.5 天）

| 文件 | 改动 |
|------|------|
| `backend/services/websocket_manager.py` | 新增 `register/check/resolve/unregister_steer` |
| `backend/services/handlers/chat_handler.py` | 工具循环中每个工具执行后加打断检查点 |
| `backend/services/agent/tool_loop_executor.py` | `_execute_tools()` 中加打断检查点 |
| `backend/api/websocket_handler.py` | 处理 `user_steer` 消息 → 调用 `resolve_steer` |
| `backend/services/handlers/chat_handler.py` | `_stream_generate()` 入口注册 steer 监听，finally 中清理 |

### Phase 5：前端改造（1.5 天）

| 文件 | 改动 |
|------|------|
| `frontend/src/types/message.ts` | Message 新增 `interaction_type` / `interaction_id` / `interaction_options` |
| `frontend/src/contexts/wsMessageHandlers.ts` | 新增 `ask_user_request` 处理器 |
| `frontend/src/components/chat/message/MessageItem.tsx` | 追问消息渲染 + 快捷选项 |
| `frontend/src/components/chat/input/InputArea.tsx` | 打断交互（流式中发新消息 → user_steer） |
| `frontend/src/services/messageSender.ts` | 检测 pending → 发送 ask_user_response 而非普通 message |
| `frontend/src/hooks/useWebSocket.ts` | 处理新 WS 消息类型 |

### Phase 6：过期清理 + 测试（1 天）

| 文件 | 改动 |
|------|------|
| `backend/services/scheduled/` | 定时清理过期 pending（24h） |
| `backend/tests/test_pending_interaction.py` | 冻结/恢复/过期/换话题 单元测试 |
| `backend/tests/test_steer.py` | 打断机制单元测试 |
| `frontend/src/components/chat/message/__tests__/` | 追问消息渲染测试 |

## 六、风险与约束

| 风险 | 影响 | 缓解 |
|------|------|------|
| frozen_messages 体积过大 | DB 存储压力 | JSONB 自带压缩 + 24h 过期清理 |
| 恢复时模型不同 | 上下文格式不兼容 | loop_snapshot 记录 model_id，恢复时用相同模型 |
| 并发：用户同时发多条消息 | 多个 pending 冲突 | UNIQUE 索引保证一个对话只有一条 pending |
| 打断时机：LLM 流式中 | 无法中断 LLM 调用 | 打断只在工具执行间检查，不中断 LLM 流式 |
| 企微链路 | 企微 WS 消息格式不同 | Phase 1-4 先做 Web 端，企微后续适配 |

## 七、验收标准

1. **主动追问**：用户说"查销售额"（不给时间范围），AI 必须追问
2. **上下文保持**：追问 → 用户回答后，AI 能引用之前已查到的数据继续
3. **换话题**：追问等待中用户说"查库存"，AI 正常处理新话题
4. **打断**：AI 执行工具时用户发新消息，AI 停止当前任务处理新指令
5. **过期清理**：24h 未回复的 pending 自动清除
6. **前端体验**：追问消息有快捷选项，输入框有状态提示
