# 阶段2：工具编排 — Agent Loop 技术规划 (V2)

## Context

**现状**：智能模式 = 单步路由。`IntentRouter.route()` 千问 FC → **1个** tool_call → **1个** Handler → **1个** 任务。

**目标**：大脑可以**多轮调用多个工具**，自主完成复杂任务。最终让她可以像人一样自主用工具实现任何操作。

**典型场景**：
1. "帮我生成5张不同角度的猫" → 大脑生成5个不同提示词 → 批量创建5个图片任务
2. "用刚才的图片生成新图" → 查对话历史找到图片URL → 调用图片编辑
3. "搜索猫的品种然后画最受欢迎的" → 搜索(同步) → 用结果生成提示词 → 生图(异步)
4. "帮我做一个产品海报" → 大脑发现信息不足 → **主动追问**：产品名称？尺寸？风格偏好？
5. "帮我查一下库存" → 大脑判断超出当前能力 → **主动说明**：目前还不支持ERP查询，后续会接入

---

## 行业调研总结

### 大厂方案对比

| 维度 | OpenAI Agents SDK | Claude Agent SDK | LangGraph | Google ADK | Dify |
|------|------------------|------------------|-----------|------------|------|
| **循环** | `Runner.run()` + final_output检测 | `stop_reason` 检测 (end_turn vs tool_use) | 条件边路由到 END | SequentialAgent/LoopAgent | DAG引擎 + Agent Node |
| **终止** | 产出 final_output 或 max_turns | 无 tool_calls 或 max_turns/max_budget_usd | 条件边 → END 或 recursion_limit | max_iterations | max_iterations(1-50) |
| **错误处理** | `is_error: True` 回传LLM自适应 | `is_error: True` + Claude自行调整策略 | ToolMessage 带错误 + LLM自适应 | 事件驱动错误传播 | 重试+降级 |
| **成本控制** | max_turns | max_turns + **max_budget_usd** + effort级别 | recursion_limit | max_iterations | max_iterations |
| **可观测** | 内置Tracing(21+集成) | 消息流类型 + Hooks系统 | LangSmith | Session事件历史 | 节点级日志 |

### 关键行业共识（我们方案必须采纳）

1. **所有框架收敛到同一核心循环**：调LLM → 检查是否完成 → 执行工具 → 结果回传 → 重复
2. **工具错误必须回传LLM**：`is_error: True` 标记，让大脑自行推理失败原因并调整策略，比硬编码 try-catch 更鲁棒
3. **工具设计 > 工具数量**（Anthropic经验）：少量精心设计的工具 > 大量细粒度工具。描述要像给新员工解释一样详细
4. **安全护栏用代码实现，不靠提示词**：步数限制 + token预算 + 循环检测 + schema验证
5. **可观测性**：每个步骤结构化日志（输入/输出/耗时），用于调试和回放

### 我们方案的补充项

| 补充项 | 来源 | 说明 |
|--------|------|------|
| **工具错误回传大脑** | OpenAI/Anthropic/LangGraph 通用 | 工具执行失败时，将错误作为 tool_result 回传，让大脑自适应 |
| **循环检测** | Anthropic 生产经验 | 检测连续相同工具调用，防止死循环 |
| **Schema 验证** | Anthropic "Writing Tools for Agents" | 执行前验证参数格式，拒绝幻觉工具调用 |
| **结构化步骤日志** | OpenTelemetry GenAI 规范 | 每步记录：tool_name/args/result/duration/tokens |
| **token 预算追踪** | Claude Agent SDK best practice | 追踪每轮 token 消耗，接近预算时优雅终止 |
| **优雅终止** | Anthropic "Effective Harnesses" | 超限时保存已有进度，返回部分结果，而非直接报错 |

---

## 架构设计

### 核心循环（对齐行业标准）

```python
# 与 OpenAI/Anthropic/LangGraph 完全对齐的核心循环
async def run(content) -> AgentResult:
    messages = [system_prompt, user_message]
    accumulated_context = []
    pending_async = []

    for turn in range(max_turns):
        # 1. 调用大脑
        response = await call_brain(messages)

        # 2. 无 tool_calls → 大脑直接文字回复 → 结束
        if not response.tool_calls:
            return build_chat_result(response.content, accumulated_context)

        # 3. 处理每个 tool_call
        tool_results = []
        for tc in response.tool_calls:
            if tc.name == "ask_user":
                # 大脑主动追问/说明 → 走 ChatHandler 文字回复
                # 场景：信息不足需追问 / 超出能力范围需说明
                return build_ask_user_result(tc, accumulated_context, pending_async)
            if tc.name in TERMINAL_TOOLS:
                return build_terminal_result(tc, accumulated_context, pending_async)
            elif tc.name in SYNC_TOOLS:
                try:
                    result = await executor.execute(tc.name, tc.arguments)
                    tool_results.append({"id": tc.id, "content": result})
                except Exception as e:
                    tool_results.append({"id": tc.id, "content": str(e), "is_error": True})
            elif tc.name in ASYNC_TOOLS:
                pending_async.append(tc)

        # 4. 纯异步(无同步结果) → 不需要迭代
        if not tool_results:
            return build_async_result(pending_async, accumulated_context)

        # 5. 回传同步结果给大脑（标准 tool_result 格式）
        messages.append({"role": "assistant", "tool_calls": response.tool_calls})
        for tr in tool_results:
            messages.append({"role": "tool", "tool_call_id": tr["id"], "content": tr["content"]})
        accumulated_context.extend(tool_results)

    # 6. 超出轮次 → 优雅终止（保存已有进度）
    return build_graceful_timeout(pending_async, accumulated_context)
```

### 工具体系

| 类别 | 工具名 | 描述 | 复用代码 |
|------|--------|------|----------|
| **同步** | `web_search` | 搜索互联网 | `IntentRouter.execute_search()` |
| **同步** | `get_conversation_context` | 获取近期对话+图片URL | `MessageService.get_messages()` |
| **同步** | `search_knowledge` | 查询知识库 | `knowledge_service.search_relevant()` |
| **异步** | `generate_image` | 生成单张图片 | 同现有 ROUTER_TOOLS |
| **异步** | `generate_video` | 生成视频 | 同现有 ROUTER_TOOLS |
| **异步** | `batch_generate_image` | 多提示词批量生图(2-8张) | **新增** |
| **终端** | `text_chat` | 文字回复(委托ChatHandler) | 同现有 ROUTER_TOOLS |
| **终端** | `ask_user` | 主动追问/说明(信息不足/超出能力) | **新增** |
| **终端** | `finish` | 仅异步任务,无需文字 | **新增** |

### 安全护栏（代码级，不靠提示词）

```python
class AgentGuardrails:
    """安全护栏 — 用代码实现，不依赖提示词"""

    # 1. 步数限制
    max_turns: int = 3                    # 最大循环轮数

    # 2. Token 预算追踪
    max_total_tokens: int = 3000          # 每次 Agent 运行的总 token 上限
    tokens_used: int = 0                  # 累积消耗

    # 3. 循环检测
    recent_calls: List[str] = []          # 最近的工具调用序列
    def detect_loop(self, tool_name: str, args_hash: str) -> bool:
        """检测连续相同调用（如连续3次相同 web_search）"""
        key = f"{tool_name}:{args_hash}"
        self.recent_calls.append(key)
        if len(self.recent_calls) >= 3 and len(set(self.recent_calls[-3:])) == 1:
            return True  # 死循环
        return False

    # 4. Schema 验证（防止幻觉工具调用）
    def validate_tool_call(self, tool_name: str, arguments: Dict) -> bool:
        """在执行前验证工具名和参数格式"""
        if tool_name not in ALL_TOOLS:
            return False  # 幻觉工具名
        schema = TOOL_SCHEMAS.get(tool_name)
        return validate_against_schema(arguments, schema)

    # 5. 优雅终止
    def should_abort(self) -> bool:
        return self.tokens_used >= self.max_total_tokens
```

### 可观测性（结构化步骤日志）

```python
# 每个工具调用记录结构化日志（对齐 OpenTelemetry GenAI 规范）
logger.info(
    "agent_step",
    extra={
        "conversation_id": conversation_id,
        "turn": turn,
        "tool_name": tc.name,
        "tool_args": tc.arguments,        # 输入
        "tool_result_len": len(result),   # 输出长度
        "is_error": False,
        "duration_ms": elapsed_ms,
        "tokens_this_turn": usage.total_tokens,
        "tokens_cumulative": guardrails.tokens_used,
    }
)
```

### 工具错误回传大脑（行业最佳实践）

```python
# 改前（我们之前的方案）：工具出错直接 catch，返回兜底
except Exception as e:
    logger.error(f"Tool failed: {e}")
    return fallback_result()

# 改后（对齐 OpenAI/Anthropic/LangGraph 通用模式）：
# 错误作为 tool_result 回传大脑，让大脑自适应
except Exception as e:
    tool_results.append({
        "tool_call_id": tc.id,
        "content": f"工具执行失败: {str(e)}",
        "is_error": True,
    })
    # 大脑看到错误后可以：换个工具、换个参数、或放弃
```

**为什么更好**：大脑能推理失败原因。比如 web_search 超时，大脑可以决定换个查询词重试，或直接用已有知识回答。比硬编码 fallback 链灵活得多。

---

## 核心数据结构

```python
@dataclass
class AgentResult:
    """Agent Loop 执行结果"""
    generation_type: GenerationType
    model: str
    system_prompt: Optional[str] = None
    search_context: Optional[str] = None    # 同步工具累积的上下文
    tool_params: Dict[str, Any] = field(default_factory=dict)
    batch_prompts: Optional[List[Dict]] = None
    direct_reply: Optional[str] = None      # ask_user: 大脑主动输出的文字（追问/说明）
    turns_used: int = 1
    total_tokens: int = 0                   # 总 token 消耗
    routed_by: str = "agent_loop"

@dataclass
class PendingAsyncTool:
    """待分发的异步工具调用"""
    tool_name: str
    arguments: Dict[str, Any]
```

---

## 文件改动清单

### 新建文件 (3个)

#### 1. `backend/config/agent_tools.py` (~140行)

工具定义 + Schema 验证 + 元数据。

```python
# 核心导出
AGENT_TOOLS: List[Dict]         # 千问 FC 工具定义 (9个工具)
AGENT_SYSTEM_PROMPT: str        # Agent 系统提示词
SYNC_TOOLS: Set[str]
ASYNC_TOOLS: Set[str]
TERMINAL_TOOLS: Set[str]
TOOL_SCHEMAS: Dict[str, Dict]   # 参数 Schema（用于验证）

def build_agent_tools() -> List[Dict]:
    """从 smart_models.json 动态构建"""
    # generate_image/video/text_chat: 复用 _get_model_enum/_get_model_desc
    # 新增: web_search, get_conversation_context, search_knowledge
    # 新增: batch_generate_image, ask_user, finish

def build_agent_system_prompt() -> str:
    """Agent 系统提示词（比路由器更详细，指导多步思考）"""
    # 包含：角色定义、工具使用指南、链式思考示例、约束条件
```

**`ask_user` 工具定义**：

```python
{
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": "当你无法完成任务时使用此工具。两种场景：\n"
            "1. 信息不足需要追问：用户请求不够具体，需要补充关键信息才能继续\n"
            "2. 超出能力范围：当前系统不支持该功能，需要诚实说明\n"
            "使用此工具后会直接回复用户，不再继续执行其他工具。",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "要回复给用户的文字内容（追问问题或能力说明）"
                },
                "reason": {
                    "type": "string",
                    "enum": ["need_info", "out_of_scope"],
                    "description": "need_info=信息不足需追问, out_of_scope=超出当前能力"
                }
            },
            "required": ["message", "reason"]
        }
    }
}
```

**系统提示词中 ask_user 的使用指引**：

```
## 何时使用 ask_user
- 用户请求模糊（如"帮我做个海报"但没说产品/尺寸/风格）→ ask_user(reason="need_info")
- 用户请求超出当前工具能力（如查库存、发邮件）→ ask_user(reason="out_of_scope")
- 你不确定用户想要什么类型的输出（图片vs视频vs文字）→ ask_user(reason="need_info")

## 何时不要使用 ask_user
- 你有足够信息完成任务 → 直接用对应工具
- 用户请求清晰但你不确定最佳参数 → 用合理默认值，不要追问
- 只是缺少非核心细节（如图片尺寸未指定）→ 用默认值，不要追问
```

**工具描述原则**（Anthropic "Writing Tools for Agents"）：
- 像给新员工解释一样写描述
- 定义术语、解释资源关系、给出使用示例
- 返回有用的信号（name而非uuid）

#### 2. `backend/services/tool_executor.py` (~160行)

同步工具执行 + 错误处理。

```python
class ToolExecutor:
    def __init__(self, db, user_id, conversation_id): ...

    async def execute(self, tool_name: str, arguments: Dict) -> str:
        """执行同步工具，返回结果文本

        异常不在此处 catch — 调用方统一处理并回传大脑
        """
        handler = self._handlers.get(tool_name)
        if not handler:
            raise ValueError(f"Unknown sync tool: {tool_name}")
        return await handler(arguments)

    async def _web_search(self, args) -> str:
        """复用 IntentRouter.execute_search()"""

    async def _get_conversation_context(self, args) -> str:
        """查询近期对话，提取图片URL和关键消息
        复用 MessageService.get_messages()
        格式化为大脑可读文本"""

    async def _search_knowledge(self, args) -> str:
        """复用 knowledge_service.search_relevant()"""
```

#### 3. `backend/services/agent_loop.py` (~250行)

核心编排引擎 + 护栏 + 可观测。

```python
class AgentLoop:
    """
    多步工具编排引擎 (ReAct 模式)

    对齐行业标准：OpenAI Agents SDK / Claude Agent SDK / LangGraph
    核心循环：调LLM → 检查是否完成 → 执行工具 → 结果回传 → 重复
    """

    def __init__(self, db, user_id, conversation_id):
        self.executor = ToolExecutor(db, user_id, conversation_id)
        self.guardrails = AgentGuardrails()  # 安全护栏
        self._messages = []
        self._client = None

    async def run(self, content: List[ContentPart]) -> AgentResult:
        """执行 Agent Loop"""
        # 见上方核心循环伪代码

    async def _call_brain(self) -> Dict:
        """调用千问 FC（DashScope OpenAI 兼容 API）"""
        # model: settings.agent_loop_model
        # tools: AGENT_TOOLS
        # temperature: 0.1, max_tokens: 500

    def _extract_tool_calls(self, response: Dict) -> List[Dict]:
        """解析所有 tool_calls（不再只取第一个）
        + Schema 验证 + 幻觉检测"""

    async def _build_system_prompt(self, content) -> str:
        """Agent 系统提示词 + 图片上下文 + 知识库经验注入
        复用 IntentRouter._enhance_with_knowledge() 模式"""

    def _build_terminal_result(self, tc, context, async_tools) -> AgentResult:
        """终端工具 → 构建最终结果"""

    def _build_ask_user_result(self, tc, context, async_tools) -> AgentResult:
        """ask_user → 大脑主动回复用户（追问/说明）
        内部映射为 text_chat，direct_reply 存放大脑的回复文字
        如果之前有 pending_async（如先搜索再追问），保留异步任务"""

    def _build_async_result(self, async_tools, context) -> AgentResult:
        """纯异步工具 → 从第一个异步工具推断 generation_type"""

    def _build_graceful_timeout(self, async_tools, context) -> AgentResult:
        """超出轮次 → 优雅终止，保存已有进度"""

    async def _notify_progress(self, turn, tool_name, status):
        """WebSocket 推送 agent_step 事件"""

    async def close(self): ...
```

### 修改文件 (5个)

#### 4. `backend/api/routes/message.py`

**改动**：`_resolve_generation_type()` 改为调用 AgentLoop

```python
# 改后 (替换 L63-93):
async def _resolve_generation_type(body, user_id, conversation_id, db):
    from services.intent_router import SMART_MODEL_ID
    from services.agent_loop import AgentLoop

    # 非智能模式 / retry / regenerate_single → 不走 Agent Loop
    if body.model != SMART_MODEL_ID and body.generation_type:
        return body.generation_type, None
    if body.operation in (MessageOperation.RETRY, MessageOperation.REGENERATE_SINGLE):
        return infer_generation_type(body.content), None

    # Agent Loop（开关控制，可回退到 IntentRouter）
    from core.config import get_settings
    if not get_settings().agent_loop_enabled:
        # 降级到旧路由（保持完全向后兼容）
        return await _legacy_resolve(body, user_id, conversation_id)

    agent = AgentLoop(db, user_id, conversation_id)
    try:
        result = await agent.run(body.content)
        return result.generation_type, result
    except Exception as e:
        logger.warning(f"Agent loop failed, keyword fallback | error={e}")
        return infer_generation_type(body.content), None
    finally:
        await agent.close()
```

**消除 web_search 硬编码特殊处理**：L79-87 的 if 分支删除。web_search 现在是 Agent Loop 的同步工具，结果通过 `AgentResult.search_context` 传递。

**参数注入**：统一处理 AgentResult（含 batch_prompts、search_context、direct_reply）。

**ask_user 处理**：当 `result.direct_reply` 有值时，将其注入 params 作为 `_direct_reply`，ChatHandler 检测到该字段后直接输出文字（跳过 LLM 调用），本质上是大脑的追问/说明直接推送给用户。

**`ask_user` 在 message.py 中的完整流程**：

```python
# AgentResult.direct_reply 有值时：
if result and result.direct_reply:
    body.params["_direct_reply"] = result.direct_reply
    # generation_type 已经是 CHAT（ask_user 映射为 text_chat）
    # ChatHandler._stream_generate() 检测到 _direct_reply 后：
    #   直接将文字推送给前端（跳过 LLM 调用），节省成本和时间
    #   用户可以正常回复，下一轮大脑继续处理
```

#### 5. `backend/services/handlers/image_handler.py`

**改动**：`start()` 支持 `_batch_prompts` 参数

```python
# L76-81 改后:
batch_prompts = params.get("_batch_prompts")
if batch_prompts:
    num_images = min(len(batch_prompts), 8)  # Agent Loop 场景上限8
else:
    num_images = max(1, min(4, int(params.get("num_images", 1))))

# L123 循环内改后:
for i in range(num_images):
    if batch_prompts and i < len(batch_prompts):
        item = batch_prompts[i]
        task_kwargs = {**generate_kwargs,
            "prompt": item["prompt"],
            "size": item.get("aspect_ratio", aspect_ratio),
        }
        task_prompt = item["prompt"]
    else:
        task_kwargs = generate_kwargs
        task_prompt = prompt
    # ...调用 _create_single_task
```

#### 6. `backend/core/config.py`

```python
# Agent Loop 配置
agent_loop_enabled: bool = True
agent_loop_max_turns: int = 3
agent_loop_max_tokens: int = 3000          # 总 token 预算
agent_loop_model: str = "qwen3.5-plus"
agent_loop_fallback_model: str = "qwen3.5-flash"
agent_loop_timeout: float = 5.0
```

#### 7. `backend/schemas/websocket.py`

新增 `AGENT_STEP` 事件类型 + `build_agent_step()` 构造函数。

#### 8. `backend/services/intent_router.py`

**不改代码**。IntentRouter 保留用于：
- `route_retry()` — retry 专用路由（每个 task 独立 retry）
- `resolve_auto_model()` — 模型校验+兜底
- `execute_search()` — 被 ToolExecutor 内部复用
- `route()` — 当 `agent_loop_enabled=False` 时作为降级路径

---

## 面向未来的扩展点

### 阶段3：ERP + 企业微信（新增工具即可，无需改引擎）

```python
# 只需在 agent_tools.py 注册新工具：
SYNC_TOOLS.add("query_erp")           # 查询快麦 ERP 数据
SYNC_TOOLS.add("send_wechat_notify")  # 发送企业微信通知

# 在 tool_executor.py 添加执行逻辑：
async def _query_erp(self, args) -> str:
    """调用快麦 ERP API"""
async def _send_wechat_notify(self, args) -> str:
    """调用企业微信 API"""
```

Agent Loop 引擎完全不需要改动。**新能力 = 注册新工具**。

### 阶段4：自主造工具（需要沙箱）

```python
# Agent 自己写代码并注册为工具：
ASYNC_TOOLS.add("execute_code")  # 沙箱执行 Agent 生成的代码
# 需要：Docker 沙箱、代码审查、资源限制
```

### 多 Agent 协作（远期）

参考 OpenAI Handoff 模式：当前 Agent 发现任务超出能力 → 转交给专业 Agent（如数据分析 Agent、报表 Agent）。核心循环不变，只需在 AGENT_TOOLS 中添加 `handoff_to_xxx` 工具。

---

## 任务拆分

### Phase A：核心引擎（后端，~550行新代码）

| # | 任务 | 文件 | 行数 |
|---|------|------|------|
| A1 | 新建 agent_tools.py — 9个工具定义 + Schema + 系统提示词 | `config/agent_tools.py` | ~170 |
| A2 | 新建 tool_executor.py — 3个同步工具执行 | `services/tool_executor.py` | ~160 |
| A3 | 新建 agent_loop.py — ReAct循环 + 护栏 + 可观测 | `services/agent_loop.py` | ~250 |
| A4 | 修改 config.py — 新配置项 | `core/config.py` | ~6 |
| A5 | 修改 websocket.py — agent_step 事件 | `schemas/websocket.py` | ~20 |
| A6 | 修改 message.py — 接入 AgentLoop + 消除 web_search 硬编码 | `api/routes/message.py` | ~40改 |

### Phase B：新工具（后端，~50行改动）

| # | 任务 | 文件 |
|---|------|------|
| B1 | ImageHandler 支持 _batch_prompts | `handlers/image_handler.py` |

### Phase C：前端（~30行）

| # | 任务 | 文件 |
|---|------|------|
| C1 | WebSocket 处理 agent_step 事件 | WS handler |
| C2 | 临时状态 UI（搜索中/查询中） | 消息组件 |

---

## 验证方案

| # | 测试场景 | 预期 | 验证点 |
|---|---------|------|--------|
| 1 | "你好" | 1轮,行为不变 | 日志 turns_used=1 |
| 2 | "画一只猫" | 1轮,行为不变 | 日志 turns_used=1 |
| 3 | "今天有什么新闻" | web_search→text_chat | 日志 turns_used=2 |
| 4 | "画5张不同角度的猫" | batch_generate_image→5个任务 | 前端5张不同图 |
| 5 | "用刚才的图片重新画" | get_context→generate_image | 引用正确URL |
| 6 | "搜索猫品种然后画一只" | web_search→generate_image | 2轮完成 |
| 7 | agent_loop_enabled=False | 退回 IntentRouter | 行为与改动前一致 |
| 8 | 同步工具执行失败 | 错误回传大脑,大脑自适应 | 不报500 |
| 9 | 连续3次相同调用 | 循环检测触发,优雅终止 | 日志 abort_reason |
| 10 | token超预算 | 优雅终止,返回已有结果 | 日志 total_tokens |
| 11 | "帮我做个海报" (无具体信息) | ask_user追问产品名/尺寸/风格 | direct_reply有值,reason=need_info |
| 12 | "帮我查一下库存" (超出能力) | ask_user说明暂不支持 | direct_reply有值,reason=out_of_scope |
| 13 | "搜索XX然后帮我做个海报" | web_search→ask_user追问 | 搜索结果保留+追问文字 |
