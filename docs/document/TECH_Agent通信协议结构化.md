# 技术设计：主 Agent ↔ 子 Agent 标准通信协议

> 日期：2026-04-20 | 等级：A级 | 状态：待实施
> 前置依赖：无（与 Agent 架构细节对齐计划独立并行）

---

## 1. 背景与动机

### 1.1 当前问题

```
用户 ←→ 主 Agent ──自然语言 str──→ 子 Agent ──纯文本 str──→ 主 Agent
```

**职责不清**：主 Agent 已经和用户聊清楚了意图，却把原始自然语言丢给子 Agent，让子 Agent 再猜一遍。子 Agent 执行完后返回纯文本，主 Agent 又要从文本里猜结果状态和文件引用。

**核心矛盾**：两端都是非结构化字符串，没有通信协议。

### 1.2 正确的职责分工

```
用户 ←→ 主 Agent（打磨需求、整理意图）
              ↓ 结构化输入（整理好的任务描述）
         子 Agent（理解任务、自主执行、内部编排）
              ↓ 结构化输出（标准格式返回）
         主 Agent（拿到结果、组织回复）
```

- **主 Agent**：负责和用户沟通，理解意图，整理成结构化任务交给子 Agent
- **子 Agent**：有自己的大脑，拿到清晰的任务描述后自主工作（可以多轮调用工具、自主编排），按标准格式返回结果
- **通信协议**：定义输入输出的标准格式，任何子 Agent 接入都遵循同一套协议

### 1.3 为什么需要子 Agent（而不是直接工具函数）

工具多到一定程度需要封装成子 Agent。子 Agent 本质是多 Agent 协作——有自己的 LLM、能自主决策、能内部编排多个工具。

| 场景 | 工具函数 | 子 Agent |
|------|---------|---------|
| 单步查询 | ✅ 够用 | 过重 |
| 多工具编排 | ❌ 不行 | ✅ 自主选择工具和参数 |
| 结果分析 | ❌ 不行 | ✅ 有推理能力 |
| 异常诊断 | ❌ 不行 | ✅ 能判断和建议 |

ERPAgent 内部有 17+ 工具（4 个域 × 多个 action），且具备参数校验、域路由、编码补全等能力，未来还要加分析能力。适合作为子 Agent 存在。

### 1.4 当前代码现状

**已有基础设施（可复用）：**

| 基础设施 | 位置 | 状态 |
|---------|------|------|
| `ToolOutput` 数据结构（summary/file_ref/columns/data） | tool_output.py:116-142 | ✅ 可复用 |
| `FileRef` 数据结构（path/filename/format/rows） | tool_output.py:74-109 | ✅ 可复用 |
| `ChatMessage.content: Union[str, List[ChatContentPart]]` | kie/models.py:109 | ✅ 已支持 list |
| `_sanitize_params` 参数校验 | plan_builder.py:108-166 | ✅ 可复用 |
| `_fill_platform` / `_fill_codes` 意图补全 | erp_agent.py / plan_builder.py | ✅ 可复用 |
| `DepartmentAgent.execute(params=dict)` | department_agent.py:565-664 | ✅ 已支持结构化参数 |
| DashScope API 对 `content: list[dict]` 的支持 | 已验证 | ✅ 可用 |

**需要改造的传输层：**

| 位置 | 当前 | 问题 |
|------|------|------|
| chat_handler.py:574-580 | `content: str` | 只传纯文本 |
| chat_generate_mixin.py:136-147 | `content: str` | 企微路径同样只传纯文本 |
| chat_tool_mixin.py | 返回 `(tc, str, bool)` | AgentResult 被拍扁为 str |
| kie/chat_adapter.py:168-184 | 只提取 role+content | 丢失 tool_call_id |
| tool_result_envelope.py:wrap_erp_agent_result() | 包装为纯文本 | 应保留结构化 |
| tool_loop_context.py:update_from_result | 期望 str 参数 | 字符串操作，传 AgentResult 会崩 |
| context_compressor.py:532 | `content[:300]` 切片 | list[dict] 会 TypeError |

**已验证安全（不需要改造）：**

| 位置 | 原因 |
|------|------|
| tool_output.py:to_message_content() | 保持返回 str，ERP Agent 内部循环不受影响 |
| dashscope/chat_adapter.py | 天然兼容 list[dict]（json.dumps 自动序列化） |
| messages 表（JSONB） | 天然支持 str 和 list[dict]，round-trip 安全 |
| AgentResult.summary 长度 | DepartmentAgent 产出的 summary 一般 300-500 字符，不需要截断 |

---

## 2. 协议定义

### 2.1 输入协议（主 Agent → 子 Agent）

主 Agent 的职责是和用户沟通、理解需求、解析上下文，然后把整理好的清晰任务交给子 Agent。子 Agent 有自己的大脑，拿到任务后自主决定怎么执行（调哪个部门、用什么参数、走什么模式）。

**核心原则：主 Agent 负责"要什么"，子 Agent 负责"怎么做"。**

#### 2.1.1 输入 Schema（通用，适用于所有子 Agent）

```python
{
    "type": "function",
    "function": {
        "name": "erp_agent",
        "description": "...(由 build_tool_description() 从 capability_manifest 自动生成)",
        "parameters": {
            "type": "object",
            "required": ["task"],
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "整理好的查询任务描述。主 Agent 负责：\n"
                        "1. 解析指代词（'那个'→具体对象，'刚才的'→具体内容）\n"
                        "2. 补全对话上下文（追问时补全前文信息）\n"
                        "3. 保留用户原始意图（不要替子 Agent 做技术决策）\n\n"
                        "示例：\n"
                        "· 用户说'退了多少'，上文聊的是HZ001 → task='HZ001商品昨天的退货情况'\n"
                        "· 用户说'导出来看看'，上文查了淘宝订单 → task='导出昨天淘宝订单明细'\n"
                        "· 用户说'按店铺看看' → task='昨天退货按店铺统计'"
                    ),
                },
                "conversation_context": {
                    "type": "string",
                    "description": (
                        "对话背景补充（可选）。当任务涉及追问或多轮对话时，"
                        "提供前文关键信息帮助子 Agent 理解完整语境。\n"
                        "示例：'用户之前查了本月淘宝订单汇总，现在想看明细'"
                    ),
                },
            }
        }
    }
}
```

**设计要点：**
- `task`（必填）：主 Agent 整理好的清晰任务描述。解析了指代词、补全了上下文，但不做技术决策（不指定 doc_type/domain/mode 等）
- `conversation_context`（可选）：对话背景补充，帮助子 Agent 理解多轮对话的语境
- **不暴露子 Agent 内部参数**（doc_type/platform/group_by 等）：这些是子 Agent 自己的决策，由它内部的 LLM 或规则引擎判断
- **未来接入新子 Agent 时，输入格式统一**：都是 `task` + `conversation_context`，不同子 Agent 只是 description 不同（由各自的 capability_manifest 生成）

#### 2.1.2 主 Agent 整理任务的示例

```
场景 1：简单查询
  用户："昨天淘宝退货多少"
  → task: "昨天淘宝退货多少"（原样传递，已经很清晰）

场景 2：指代词解析
  用户上文聊了 HZ001 商品
  用户："那个退了多少"
  → task: "HZ001 商品昨天的退货情况"
  → conversation_context: "用户之前在查 HZ001 商品的库存"

场景 3：追问补全
  上一轮 erp_agent 返回了淘宝订单汇总
  用户："导出来看看"
  → task: "导出昨天淘宝订单明细"
  → conversation_context: "上一轮查了昨天淘宝订单汇总，用户现在要导出明细"

场景 4：复杂需求
  用户："各平台退货率，帮我分析一下哪个平台退货最多"
  → task: "各平台退货率，分析哪个平台退货最多"（保留分析需求，由子 Agent 判断能不能做）
```

#### 2.1.3 子 Agent 如何消费输入

```python
# erp_agent.py — 内部决策完全自主
async def execute(self, task: str, conversation_context: str = "") -> AgentResult:
    """
    执行 ERP 查询任务。

    Args:
        task: 主 Agent 整理好的清晰任务描述
        conversation_context: 对话背景补充（可选）
    """
    # 合并为完整查询上下文
    full_query = task
    if conversation_context:
        full_query = f"{task}\n（背景：{conversation_context}）"

    # 子 Agent 自主决策：用 LLM 提取参数、路由到对应部门
    domain, params, degraded = await self._extract_params(full_query)

    # 后续逻辑不变：校验 → 补全 → 路由 → 执行
    ...
```

### 2.2 输出协议（子 Agent → 主 Agent）

子 Agent 不管内部怎么工作（用了几次 LLM、调了几个工具），最终必须按标准格式返回。主 Agent 拿到结构化结果就能精确知道状态、数据、文件。

#### 2.2.1 AgentResult 标准结构

```python
@dataclass
class AgentResult:
    """子 Agent 标准返回格式 — 所有子 Agent 必须遵循"""

    # ── 必填 ──
    status: str           # "success" | "partial" | "error" | "timeout" | "ask_user"
    summary: str          # 人类可读的结果摘要（给主 Agent 看）

    # ── 数据（按场景填充）──
    file_ref: FileRef | None = None    # 文件引用（导出/大数据场景）
    data: list[dict] | None = None     # 内联数据（少量数据直接返回）
    columns: list[ColumnMeta] | None = None  # 列定义

    # ── 前端展示通道 ──
    collected_files: list[dict] | None = None
    # 文件卡片信息（供前端 content_block_add 展示）
    # 每项: {"url": str, "name": str, "mime_type": str, "size": int}
    # 与 file_ref 的区别：file_ref 给 LLM 看路径/行数，collected_files 给前端展示卡片

    # ── 元信息 ──
    agent_name: str = ""               # 哪个子 Agent 产出的
    tokens_used: int = 0               # 消耗的 tokens
    confidence: float = 1.0            # 结果置信度（降级时 0.6）
    error_message: str = ""            # status=error 时填写
    ask_user_question: str = ""        # status=ask_user 时填写
    insights: list[str] | None = None  # 子 Agent 的分析洞察（可选）
    follow_up: list[str] | None = None # 建议的后续操作（可选）
    metadata: dict = field(default_factory=dict)  # 扩展字段
```

#### 2.2.2 转为 message content 的标准格式

```python
def to_message_content(self) -> list[dict]:
    """AgentResult → 结构化 content block（传给主 Agent LLM）"""
    blocks = []

    # 文本摘要（始终有）
    blocks.append({"type": "text", "text": self.summary})

    # 文件引用（有数据文件时）
    if self.file_ref:
        blocks.append({
            "type": "file_ref",
            "file_ref": {
                "path": self.file_ref.path,
                "filename": self.file_ref.filename,
                "format": self.file_ref.format,
                "rows": self.file_ref.row_count,
                "size_kb": self.file_ref.size_bytes // 1024,
            }
        })

    # 内联数据（少量数据时）
    if self.data and not self.file_ref:
        blocks.append({
            "type": "data",
            "data": {
                "rows": len(self.data),
                "columns": [c.name for c in (self.columns or [])],
                "records": self.data[:20],  # 最多 20 行预览
            }
        })

    # 分析洞察（子 Agent 有分析能力时）
    if self.insights:
        blocks.append({
            "type": "insights",
            "insights": self.insights,
        })

    return blocks
```

#### 2.2.3 主 Agent 消费输出的场景

**场景 A：统计查询**
```python
# 子 Agent 返回
AgentResult(
    status="success",
    summary="昨天淘宝刷单订单共 23 笔，金额合计 ¥0",
    data=[{"shop": "旗舰店", "count": 15}, {"shop": "专营店", "count": 8}],
)
# → 主 Agent 直接给用户展示摘要
```

**场景 B：大数据导出**
```python
# 子 Agent 返回
AgentResult(
    status="success",
    summary="共 945 条订单已导出",
    file_ref=FileRef(path="staging/trade_xxx.parquet", rows=945, format="parquet"),
)
# → 主 Agent 看到 file_ref，知道调 code_execute 读文件加工
# → 主 Agent 从 file_ref.path 拿到准确路径，不会幻觉
```

**场景 C：信息不足**
```python
# 子 Agent 返回
AgentResult(
    status="ask_user",
    summary="需要确认查询范围",
    ask_user_question="请问要查哪个平台的订单？淘宝/拼多多/抖音？",
)
# → 主 Agent 冒泡追问给用户
```

**场景 D：带分析的结果（未来能力）**
```python
# 子 Agent 返回
AgentResult(
    status="success",
    summary="本月退货率 15%，环比上升 8%",
    file_ref=FileRef(...),
    insights=["商品 HZ001 退货率 30%，远超均值", "退货原因集中在'尺码不合'"],
    follow_up=["需要看 HZ001 的退货原因分布吗？"],
)
```

---

## 3. 传输层改造

### 3.1 完整数据流（改后）

```
tool_executor._erp_agent()
  → 返回 AgentResult（不再 wrap 成 str）

ChatToolMixin._execute_tool_calls()     ← 问题1修复：中间层不再拍扁
  → 返回 List[(tc, result: AgentResult | str, is_error)]
  → AgentResult 时：
    ① collected_files → _pending_file_parts（前端文件卡片通道）
    ② ask_user 冒泡处理
    ③ result 原样传递（不转 str）

chat_handler / chat_generate_mixin
  → isinstance(result, AgentResult) ?
    → content = result.to_message_content()  # list[dict]
  → else:
    → content = result_text  # str（旧路径兼容）
  → messages.append({role: "tool", tool_call_id, content})

adapter 层
  → DashScope: 直接透传 list[dict]
  → KIE: file_ref/data/insights block → 转文本描述
```

### 3.2 ChatToolMixin._execute_tool_calls() 改造

当前返回 `List[(tc, result_text: str, is_error)]`，AgentResult 在这一层被拍扁为 str。改为支持传递 AgentResult。

```python
# 改前：返回 (tc, str, bool)
result_text = await executor.execute(tool_name, arguments)
# ... wrap / truncate ...
results.append((tc, result_text, is_error))

# 改后：返回 (tc, AgentResult | str, bool)
result = await executor.execute(tool_name, arguments)
if isinstance(result, AgentResult):
    # ① 前端文件卡片通道（保留现有机制）
    if result.collected_files:
        from schemas.message import FilePart
        for f in result.collected_files:
            self._pending_file_parts.append(FilePart(
                url=f["url"], name=f["name"],
                mime_type=f["mime_type"], size=f["size"],
            ))
    # ② ask_user 冒泡
    if result.status == "ask_user" and result.ask_user_question:
        self._ask_user_pending = {
            "message": result.ask_user_question,
            "reason": "need_info",
            "tool_call_id": tc["id"],
            "source": result.agent_name,
        }
    # ③ 展示文本（供 content_block_add 推送）
    self._erp_display_text = result.summary
    self._erp_display_files = result.collected_files or []
    # ④ token 统计
    self._erp_agent_tokens = getattr(self, "_erp_agent_tokens", 0)
    self._erp_agent_tokens += result.tokens_used
    # ⑤ 原样传递，不转 str
    results.append((tc, result, False))
else:
    results.append((tc, result, is_error))
```

### 3.3 chat_handler.py / chat_generate_mixin.py — tool result 注入

```python
# 改后 (chat_handler.py:574-580 + chat_generate_mixin.py:136-147)
for tc, result, is_error in tool_results:
    if isinstance(result, AgentResult):
        content = result.to_message_content()  # → list[dict] 给 LLM
        # tool_context 期望 str，传 summary 而非 AgentResult
        tool_context.update_from_result(tc["name"], result.summary, is_error)
    else:
        content = result  # str（旧路径、非 Agent 工具）
        tool_context.update_from_result(tc["name"], result, is_error)

    messages.append({
        "role": "tool",
        "tool_call_id": tc["id"],
        "content": content,  # str | list[dict]
    })
```

**注意**：chat_generate_mixin.py（企微非流式路径）同步修改，逻辑一致。

### 3.4 context_compressor 兼容 list content

`context_compressor.py` 多处假设 tool message 的 content 是 str（切片、字符串判断），list[dict] 会 TypeError。加一个工具函数统一提取文本：

```python
# context_compressor.py 新增
def _extract_text(content: str | list | Any) -> str:
    """从 message content 提取纯文本（兼容 str 和 list[dict] 两种格式）"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content)
```

修改 3 处调用点：
- `_is_archived()` :177 — `content = _extract_text(msg.get("content", ""))`
- `_build_loop_summary_input()` :532 — `text = _extract_text(content)[:300]`
- `estimate_tokens()` :36 已有 list 判断，但建议也统一用 `_extract_text()`

### 3.5 ToolOutput.to_message_content() — 不改

**ToolOutput.to_message_content() 保持返回 str 不变。** 理由：
- ToolOutput 被 ToolLoopExecutor 在 ERP Agent **内部循环**调用
- ERP Agent 内部用的 LLM（agent_loop_model）不一定支持 list content
- AgentResult.to_message_content() 单独实现返回 list[dict]，两个类各自负责自己的序列化

```
ToolOutput.to_message_content()  → str（[DATA_REF] 标记，内部循环用）  不改
AgentResult.to_message_content() → list[dict]（结构化 block，主 Agent 用）  新增
```

### 3.5 KIE adapter — format_messages_from_history

```python
# 改后 (kie/chat_adapter.py:168-184)
for msg in history:
    role = MessageRole(msg["role"])
    content = msg.get("content", "")
    tool_call_id = msg.get("tool_call_id")  # 新增：保留关联

    if isinstance(content, list):
        # 结构化 content block → 转为 KIE API 格式
        parts = []
        for block in content:
            if block.get("type") == "text":
                parts.append(ChatContentPart(type="text", text=block["text"]))
            elif block.get("type") == "file_ref":
                ref = block["file_ref"]
                parts.append(ChatContentPart(
                    type="text",
                    text=f"[文件: {ref['path']} | {ref['rows']}行 | {ref['format']}]",
                ))
            elif block.get("type") == "data":
                d = block["data"]
                parts.append(ChatContentPart(
                    type="text",
                    text=f"[数据: {d['rows']}行 | 列: {', '.join(d['columns'])}]",
                ))
            elif block.get("type") == "insights":
                parts.append(ChatContentPart(
                    type="text",
                    text="分析洞察：\n" + "\n".join(f"· {i}" for i in block["insights"]),
                ))
        messages.append(ChatMessage(role=role, content=parts))
    elif attachments:
        messages.append(self.format_multimodal_message(role, content, media_urls))
    else:
        messages.append(self.format_text_message(role, content))
```

### 3.6 DashScope adapter — 天然兼容

DashScope 走 OpenAI 兼容协议，直接透传 dict messages，content 支持 list[dict]。
**已验证可用，无需改造。**

### 3.7 ChatContentPart 扩展

```python
# kie/models.py
class ChatContentPart(BaseModel):
    type: str  # "text" | "image_url" | "file_ref" | "data" | "insights"
    text: Optional[str] = None
    image_url: Optional[Dict[str, str]] = None
    file_ref: Optional[Dict[str, Any]] = None   # 新增
    data: Optional[Dict[str, Any]] = None        # 新增
    insights: Optional[List[str]] = None          # 新增
```

### 3.8 tool_result_envelope — 不再包装 AgentResult

`wrap_erp_agent_result()` 只处理 str（旧路径兼容）。AgentResult 不经过 envelope，直接传递。

```python
# tool_executor._erp_agent() 改后不再调 wrap_erp_agent_result
# AgentResult 的 summary 已经是精简版，不需要截断包装
return result  # AgentResult 原样返回
```

---

## 4. ERPAgent 改造（第一个接入者）

### 4.1 输入改造

```python
# 改前
erp_agent(query="昨天淘宝刷单多少")

# 改后
erp_agent(
    task="昨天淘宝刷单多少",  # 主 Agent 整理好的清晰任务
    conversation_context="用户在分析各平台刷单情况",  # 对话背景（可选）
)
```

主 Agent 只负责把用户需求整理清楚（解析指代词、补全上下文），不做技术决策（不指定 doc_type/platform 等）。ERPAgent 内部自主判断用哪个部门、什么参数。

### 4.2 执行流程

```
改前：
  主 Agent → erp_agent(query=自然语言)
    → _llm_extract() 调 LLM 提取参数
    → _sanitize_params → DepartmentAgent 执行
    → ERPAgentResult → wrap_erp_agent_result() → 纯文本 str

改后：
  主 Agent → erp_agent(task=整理好的任务, conversation_context=对话背景)
    → _llm_extract() 调 LLM 提取参数（子 Agent 自主决策，不变）
    → _sanitize_params → DepartmentAgent 执行
    → AgentResult → to_message_content() → list[dict]（结构化输出）
```

输入侧的变化：`query` → `task` + `conversation_context`，让子 Agent 拿到更清晰的任务描述。
输出侧的变化：纯文本 → 结构化 AgentResult，主 Agent 精确知道状态和数据。

### 4.3 返回改造

ERPAgent 当前返回 `ERPAgentResult`（自定义 dataclass），改为返回标准 `AgentResult`。

```python
# 改后 (erp_agent.py:_build_result 完整版)
def _build_result(self, result, query, domain, degraded, params=None) -> AgentResult:
    from services.agent.tool_output import OutputStatus, OutputFormat

    summary = result.summary or ""
    collected_files = []

    # ① SessionFileRegistry 注册（保留，沙盒 read_file 依赖）
    if result.file_ref:
        from services.agent.session_file_registry import SessionFileRegistry
        registry = SessionFileRegistry()
        registry.register(domain, "execute", result.file_ref)
        collected_files.append({
            "url": result.file_ref.path,
            "name": result.file_ref.filename,
            "mime_type": result.file_ref.mime_type or "application/octet-stream",
            "size": result.file_ref.size_bytes,
        })
        # ② staging 延迟清理（保留）
        asyncio.create_task(self._cleanup_staging_delayed())

    # ③ 经验记录（保留）
    if result.status == OutputStatus.ERROR:
        asyncio.create_task(self._experience.record(
            "failure", query, [domain], f"单域失败：{summary[:200]}",
        ))
    else:
        detail = self._build_experience_detail(domain, params)
        asyncio.create_task(self._experience.record(
            "routing", query, [domain], detail, confidence=0.6,
        ))

    # ④ 构建 AgentResult（完整字段）
    status = "error" if result.status == OutputStatus.ERROR else "success"
    return AgentResult(
        status=status,
        summary=summary,
        file_ref=result.file_ref,                    # 直接传递，不转文本
        data=result.data if result.format == OutputFormat.TABLE else None,
        columns=result.columns,
        collected_files=collected_files or None,      # 前端文件卡片
        agent_name="erp_agent",
        tokens_used=self._tokens_used,
        confidence=0.6 if degraded else 1.0,          # 降级标记
        error_message=summary if status == "error" else "",
    )
```

### 4.4 tool_executor 适配

tool_executor._erp_agent() 改为返回 AgentResult，**不再做 wrap/文件提取/ask_user 冒泡**。这些职责上移到 ChatToolMixin（§3.2）统一处理。

```python
# 改前 (tool_executor.py:_erp_agent)
query = args.get("query", "").strip()
result = await agent.execute(query)
# ... 50+ 行：文件提取、ask_user 冒泡、display_text、wrap ...
return wrap_erp_agent_result(result.text)  # → str

# 改后（精简：只负责创建 Agent + 调用 + 返回）
task = args.get("task") or args.get("query", "")  # 向后兼容旧 query
task = task.strip()
if not task:
    return "请输入 ERP 相关问题"

conversation_context = args.get("conversation_context", "")
result = await agent.execute(task, conversation_context=conversation_context)
return result  # → AgentResult（文件/ask_user/display 由 ChatToolMixin 统一处理）
```

**职责重新分配：**

| 职责 | 改前（在 tool_executor） | 改后 |
|------|----------------------|------|
| 创建 ERPAgent + 调用 | tool_executor | tool_executor（不变） |
| collected_files → _pending_file_parts | tool_executor:207-216 | ChatToolMixin（§3.2） |
| ask_user 冒泡 | tool_executor:220-232 | ChatToolMixin（§3.2） |
| display_text / display_files | tool_executor:229-243 | ChatToolMixin（§3.2） |
| token 统计 | tool_executor:202-203 | ChatToolMixin（§3.2） |
| wrap_erp_agent_result | tool_executor:242-243 | **删除**（AgentResult 不需要 wrap） |

### 4.5 ERPAgent 内部能力（全部保留，不受协议影响）

| 能力 | 说明 |
|------|------|
| LLM 参数提取（_llm_extract） | 子 Agent 自主理解 task，提取 domain/params |
| 三级降级链 | LLM → 关键词匹配 → abort |
| 域路由（doc_type→domain） | 确定性映射 |
| 参数校验（_sanitize_params） | enum/格式校验 |
| L2 平台补全（_fill_platform） | 从 task 文本检测中文平台名 |
| L2 编码 DB 验证（_fill_codes） | product_code/order_no 存在性验证 |
| 经验记录 | 成功/失败路径自动记录 |
| 超时控制 | asyncio.wait_for |

**这些全是子 Agent 内部逻辑，通信协议不涉及、不干预。**

---

## 5. 协议扩展性

### 5.1 未来接入新子 Agent

任何新子 Agent 只需遵循两条规则：
1. 实现 `get_capability_manifest()` + `build_tool_description()` — 能力描述层
2. 接收 `task(str)` + `conversation_context(str)`，返回 `AgentResult` — 通信协议层

```python
class FinanceAgent:
    @staticmethod
    def get_capability_manifest() -> dict:
        return {"summary": "财务报表分析", "use_when": [...], ...}

    @staticmethod
    def build_tool_description() -> str:
        # 从 manifest 格式化为 5 段式文本
        ...

    async def execute(self, task: str, conversation_context: str = "") -> AgentResult:
        # 内部自主工作...
        return AgentResult(status="success", summary="...", ...)

class AnalystAgent:
    async def execute(self, task: str, conversation_context: str = "") -> AgentResult:
        # 内部多轮推理...
        return AgentResult(status="success", summary="...", insights=[...])
```

### 5.2 两层配合

```
能力描述层（Agent Card）        通信协议层（本文档）
  get_capability_manifest()       输入：task + conversation_context
  build_tool_description()        输出：AgentResult
  ↓                               ↓
  主 Agent 知道该调谁             主 Agent 知道怎么传、怎么收
```

每个子 Agent 的内部实现完全独立，只要对外遵循这两层协议即可。

---

## 6. 改动范围

### 新增
| 文件 | 说明 |
|------|------|
| `services/agent/agent_result.py` | AgentResult 标准结构 + to_message_content() |

### 修改
| 文件 | 方向 | 说明 |
|------|------|------|
| `config/chat_tools.py` | 输入 | erp_agent schema: query → task + conversation_context |
| `services/agent/erp_agent.py` | 输入+输出 | execute() 接收 task+conversation_context，返回 AgentResult |
| `services/agent/tool_executor.py` | 输入 | 精简：只创建 Agent + 调用 + 返回 AgentResult |
| `services/handlers/chat_tool_mixin.py` | 输出 | AgentResult 处理：文件通道 + ask_user 冒泡 + 原样传递 |
| `services/handlers/chat_handler.py:574-580` | 输出 | content 支持 str \| list[dict] + tool_context 兼容 |
| `services/handlers/chat_generate_mixin.py:136-147` | 输出 | 企微路径同步 |
| `services/handlers/context_compressor.py` | 兼容 | 新增 `_extract_text()` 兼容 list[dict] content |
| `services/adapters/kie/chat_adapter.py:168-184` | 输出 | format_messages 处理 list content + tool_call_id |
| `services/adapters/kie/models.py` | 输出 | ChatContentPart 扩展 file_ref/data/insights |

### 不改
| 文件 | 原因 |
|------|------|
| `services/agent/tool_output.py` | ToolOutput.to_message_content() 保持返回 str，内部循环不受影响 |
| `services/agent/tool_loop_executor.py` | 只处理 ToolOutput（内部循环），不涉及 AgentResult |

### 可删除（协议稳定后）
| 文件/代码 | 说明 |
|----------|------|
| `erp_agent_types.py` 的 `ERPAgentResult` | 被 AgentResult 替代 |
| `tool_executor.py` 的文件提取/ask_user/display/wrap 逻辑 | 上移到 ChatToolMixin |
| `tool_result_envelope.py` 的 `wrap_erp_agent_result()` | AgentResult 不需要纯文本包装 |
| `tool_output.py` 的 `[DATA_REF]` 标记生成 | 未来被结构化 block 替代（Phase 6） |

---

## 7. 风险

| 风险 | 严重度 | 缓解措施 |
|------|--------|---------|
| KIE API 对 tool role list content 兼容性 | 中 | adapter 内部转文本描述（已有降级方案） |
| 主 Agent 整理 task 不够清晰 | 低 | task description 含示例引导，子 Agent 仍有 LLM 兜底理解 |
| AgentResult 替换 ERPAgentResult 的兼容性 | 中 | 渐进式：先新增 AgentResult，再逐步替换 |
| 企微路径遗漏 | 低 | chat_generate_mixin 与 chat_handler 同步改 |
| 过渡期两种输出格式并存 | 低 | str 和 list[dict] 并存，adapter 层兼容处理 |

---

## 8. 任务拆分

### Phase 1：协议定义 + AgentResult
- [ ] 1.1 新建 `services/agent/agent_result.py`，定义 AgentResult（含 confidence、collected_files）
- [ ] 1.2 实现 `to_message_content()` → list[dict] 输出
- [ ] 1.3 单元测试：各场景（success/error/timeout/ask_user/file_ref/data/insights）

### Phase 2：输入改造（主 Agent → erp_agent）
- [ ] 2.1 `chat_tools.py` erp_agent schema: query → task + conversation_context
- [ ] 2.2 `tool_executor.py` 精简：只创建 Agent + 传 task/conversation_context + 返回 AgentResult
- [ ] 2.3 `erp_agent.py` execute() 接收 task + conversation_context，合并为完整查询
- [ ] 2.4 向后兼容：旧的 query 参数映射到 task
- [ ] 2.5 测试：新旧参数格式都正常

### Phase 3：输出改造（erp_agent → 主 Agent）
- [ ] 3.1 `erp_agent.py` _build_result() 返回 AgentResult（保留全部 5 项操作：文件注册/清理/经验/collected_files/confidence）
- [ ] 3.2 `chat_tool_mixin.py` AgentResult 处理：文件通道 + ask_user 冒泡 + display + token 统计
- [ ] 3.3 `chat_handler.py` content 支持 str | list[dict]，tool_context.update_from_result 传 summary
- [ ] 3.4 `chat_generate_mixin.py` 企微路径同步
- [ ] 3.5 `context_compressor.py` 新增 `_extract_text()` 兼容 list[dict] content（_is_archived + _build_loop_summary_input + estimate_tokens）
- [ ] 3.6 测试：结构化 content 正确注入 messages + 文件卡片 + ask_user 冒泡 + 上下文压缩不崩

### Phase 4：传输层适配
- [ ] 4.1 `kie/models.py` ChatContentPart 扩展 file_ref/data/insights
- [ ] 4.2 `kie/chat_adapter.py` format_messages 处理 list content + tool_call_id
- [ ] 4.3 DashScope adapter 验证（天然兼容，确认无问题）
- [ ] 4.4 测试：两个 adapter 都能正确传输结构化 content

### Phase 5：集成验证 + TOOL_SYSTEM_PROMPT 更新
- [ ] 5.1 TOOL_SYSTEM_PROMPT 更新 erp_agent 使用说明（task 参数描述）
- [ ] 5.2 全链路测试：统计/导出/筛选/追问/ask_user 冒泡
- [ ] 5.3 验证：主 Agent 能从 file_ref block 正确获取路径调 code_execute
- [ ] 5.4 验证：前端文件卡片仍正常显示（_pending_file_parts 通道）

### Phase 6：清理旧路径（协议稳定后）
- [ ] 6.1 删除 ERPAgentResult，统一用 AgentResult
- [ ] 6.2 删除 tool_executor 中旧的文件提取/ask_user/display/wrap 逻辑
- [ ] 6.3 删除 wrap_erp_agent_result 纯文本包装
- [ ] 6.4 删除 `[DATA_REF]` 文本标记生成（ToolOutput.to_message_content 内部）
- [ ] 6.5 迁移测试
