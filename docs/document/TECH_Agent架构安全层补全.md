# Agent 架构完整性补全方案

> 日期：2026-04-07 | 状态：**Phase 1 已完成** ✅ | Phase 2-4 待实施
> 参考：Claude Code 源码（claude-code-mirror / claw-code）

## 背景

对比 Claude Code 架构，我们的 Agent 系统（主 Agent + ERP Agent）在多个维度存在架构缺失。不是单个 Bug，而是**系统性缺陷**——缺少的是架构层，不是功能点。

---

## 架构全景图

一个完整的 Agent 系统需要 7 个架构层：

```
┌─────────────────────────────────────────────────┐
│                   用户请求                       │
├─────────────────────────────────────────────────┤
│  ⑦ 进化层    经验学习 / 失败记忆 / 用户偏好      │  20%
├─────────────────────────────────────────────────┤
│  ⑥ 体验层    进度推送 / 中断恢复 / 预期管理      │  35%
├─────────────────────────────────────────────────┤
│  ⑤ 协作层    主Agent↔子Agent 结果传递/上下文共享  │  45%
├─────────────────────────────────────────────────┤
│  ④ 感知层    审计日志 / 截断信号 / 可观测性       │  15%
├─────────────────────────────────────────────────┤
│  ③ 安全层    截断 / 超时 / 幂等 / 去重 / 压缩    │  20%
├─────────────────────────────────────────────────┤
│  ② 决策层    路由规则 / 多步规划 / 自我反思       │  45%
├─────────────────────────────────────────────────┤
│  ① 能力层    13个工具 + ERP子Agent + 沙盒        │  85%
├─────────────────────────────────────────────────┤
│                   工具执行                       │
└─────────────────────────────────────────────────┘
```

---

## 各层现状与 Claude Code 对比

### ① 能力层（85% — 基本完善）

| 能力 | 状态 | 说明 |
|------|------|------|
| 13 个核心工具 | ✅ | 聊天/图片/视频/ERP/沙盒/爬虫/搜索等 |
| ERP 子 Agent | ✅ | 独立工具循环，12 个本地 + 8 个远程工具 |
| code_execute 沙盒 | ✅ | Python 代码执行，可查 DB、生成文件 |
| 本地工具覆盖 | ⚠️ | 部分远程 API 功能未注册本地工具（如 local_shop_list 刚补） |

**缺失项**：
- 部分高频查询仍依赖远程 API（仓库列表等），后续按需补充

---

### ② 决策层（45% — 有基础但不够智能）

| 能力 | Claude Code | 我们 | 差距 |
|------|-------------|------|------|
| 工具路由引导 | 系统提示词 + 工具描述双层引导 | ✅ 刚重写了 ERP_ROUTING_PROMPT | 已补 |
| 反向引导（何时不用） | 每个工具说 "NEVER use X when Y" | ⚠️ 有部分，不够系统 | 需补 |
| 多步规划 | 靠模型自身能力（Claude 强推理） | ❌ Agent 一步一步试，无整体计划 | 模型能力限制 |
| 自我反思 | recovery message 让模型重新评估 | ❌ 失败就重试，不反思原因 | 需补 |
| 降级策略 | 模型看到错误后自主换策略 | ⚠️ 刚加了降级规则 | 已补 |

**Phase A：决策层增强**

**A1. 工具描述跨引用补全**

当前问题：各工具描述是孤立的，Agent 需要逐个读才知道关联关系。

方案：为每个 local 工具的描述添加 "相关工具" 引用，形成工具网络：

```python
# 示例：local_order_query 描述补充
"按商品编码查订单。需精确编码，模糊时先 local_product_identify。"
"按店铺维度统计用 local_global_stats(group_by='shop')。"  # ← 新增
"需要物流轨迹用 erp_trade_query(express_query)。"          # ← 新增
```

涉及文件：`config/erp_local_tools.py`（各工具描述补充 1-2 行）

**A2. 失败反思机制**

当前问题：工具返回错误后，Agent 盲目重试或放弃，不分析原因。

方案：在 `_run_tool_loop` 中，当工具返回错误时，注入一条 system message 引导模型反思：

```python
if "失败" in result or "错误" in result or "超时" in result:
    messages.append({
        "role": "system",
        "content": f"工具 {tool_name} 返回错误。请分析原因后选择：1)换参数重试 2)换工具 3)ask_user 确认",
    })
```

涉及文件：`services/erp_agent.py`（~5 行）

---

### ③ 安全层（20% — 最大缺口，直接导致 Bug）

| 能力 | Claude Code | 我们 | 差距 |
|------|-------------|------|------|
| 输出截断 | 三层（per-tool + per-message + aggregate 200K） | ❌ 各工具各自为政 | 关键缺失 |
| 截断信号 | `<persisted-output>` 明确告知模型 | ❌ 静默截断 | 关键缺失 |
| 大结果持久化 | 存文件，模型可用 Read 再看 | ❌ 压缩后丢失 | 需补 |
| 上下文压缩 | auto-compact 主动触发 | ⚠️ 主 Agent 四层压缩 / ERP Agent 无 | 半缺失 |
| 上下文恢复 | recovery message 继续 | ❌ 超限报错 | 需补 |
| 全局执行预算 | max_turns + 动态 token budget | ❌ 无时间预算 | 需补 |
| 请求去重 | prompt cache + 复用 | ❌ 无 | 需补 |
| 幂等保护 | 读可重试，写不重试 | ❌ 无区分 | 需补 |

**Phase B：安全层建设（6 个子任务，优先级最高）**

**B1. 输出截断 + 信号层（ToolResultEnvelope）**

统一工具结果包装层，所有工具返回经过它：

```python
class ToolResultEnvelope:
    TEXT_BUDGET = 3000
    
    @staticmethod
    def wrap(tool_name: str, result: str, limit_hint: int = None) -> str:
        """检测截断并标注"""
        # 1. 文本超 TEXT_BUDGET → 截断 + 标注
        # 2. 列表条数 = limit_hint → "可能还有更多"
        # 3. 短结果不处理
```

接入点：
- 主 Agent：`chat_tool_mixin._execute_single_tool`（替代 `compress_tool_result`）
- ERP Agent：`erp_agent._execute_tools`（新增）

截断标注格式：
```
[工具结果前 N 字符]
⚠ 输出已截断（原始 12,500 字符，显示前 3,000 字符）。需要完整数据请说"导出"。
```

大结果持久化（附属）：截断时将完整结果写入 `/tmp/tool_results/{task_id}_{tool_call_id}.txt`，标注中带路径，`code_execute` 可读取。

涉及文件：
- 新建 `services/tool_result_envelope.py`（~80 行）
- 改 `services/handlers/chat_tool_mixin.py`
- 改 `services/erp_agent.py`
- 改 `services/handlers/context_compressor.py`（合并职责）

**B2. ERP Agent 上下文压缩**

每轮开始前检测 messages 总大小，超 70% token 预算时压缩：

```python
estimated_tokens = sum(len(m.get("content", "")) for m in messages) // 3
if estimated_tokens > _MAX_TOTAL_TOKENS * 0.7:
    messages = _compact_messages(messages)
```

压缩策略：保留 system + 最近 3 轮 + 所有 user message，中间轮次 tool result 替换为一行摘要。

涉及文件：`services/erp_agent.py`（~30 行）

**B3. 全局执行时间预算（ExecutionBudget）**

```python
class ExecutionBudget:
    def __init__(self, deadline_seconds: float = 120.0):
        self._start = time.monotonic()
        self._deadline = deadline_seconds
    
    def tool_timeout(self, max_per_tool: float = 30.0) -> float:
        return min(self.remaining, max_per_tool)
```

- ERP Agent 总预算：120s
- 主 Agent 总预算：300s
- 每轮工具超时 = `min(30s, 剩余预算)`

涉及文件：
- 新建 `services/execution_budget.py`（~30 行）
- 改 `services/erp_agent.py`
- 改 `services/handlers/chat_handler.py`

**B4. 请求去重/缓存（QueryCache）**

会话级缓存，只缓存读操作，TTL 5 分钟：

```python
class QueryCache:
    def get_or_none(self, tool_name: str, args: dict) -> Optional[str]: ...
    def put(self, tool_name: str, args: dict, result: str): ...
```

涉及文件：`services/erp_agent.py`

**B5. 写操作幂等保护**

写操作前生成 request_id，结果写 Redis（TTL 10min），重试时先检查：

```python
request_id = hash(tool_name + json.dumps(args))
cached = await redis.get(f"write:{request_id}")
if cached:
    return f"⚠ 该操作已执行过，结果：{cached}"
```

涉及文件：`services/erp_agent.py`

**B6. 上下文恢复**

上下文超限时不直接报错，而是注入 recovery message：

```python
if api_error == "context_length_exceeded":
    messages = _compact_messages(messages)
    messages.append({
        "role": "user",
        "content": "上下文过长已自动压缩。请直接继续当前任务，不要重复已完成的步骤。",
    })
    continue  # 重试
```

涉及文件：`services/erp_agent.py`

---

### ④ 感知层（15% — 几乎为空）

| 能力 | Claude Code | 我们 | 差距 |
|------|-------------|------|------|
| 结构化审计 | trace_id 全链路 | ❌ loguru 散日志 | 关键缺失 |
| 工具调用追溯 | 完整记录每次调用+结果 | ❌ 日志格式不统一 | 关键缺失 |
| 截断信号元数据 | 每条结果带 is_truncated/size 等 | ❌ 无（Phase B1 补） | 随 B1 补 |

**Phase C：感知层建设**

**C1. 结构化审计日志**

```python
@dataclass
class ToolAuditEntry:
    trace_id: str          # conversation_id + session_id
    turn: int
    tool_name: str
    tool_call_id: str
    args_summary: str      # 参数摘要（脱敏）
    result_length: int
    is_truncated: bool
    elapsed_ms: int
    status: str            # success | timeout | error
    created_at: datetime
```

写入 `tool_audit_logs` 表，支持按 conversation_id 查完整调用链。

涉及文件：
- 新建 `services/tool_audit.py`（~50 行）
- 新建迁移 SQL
- 改 `services/erp_agent.py`
- 改 `services/handlers/chat_tool_mixin.py`

---

### ⑤ 协作层（45% — 能跑通但粗糙）

| 能力 | Claude Code | 我们 | 差距 |
|------|-------------|------|------|
| 子 Agent 调用 | subagent + worktree 隔离 | ✅ ERP Agent 独立循环 | 有 |
| 结果传递格式 | 结构化 TurnSummary | ❌ 纯文本 | 需改 |
| 上下文共享 | 父子共享完整 messages | ⚠️ 最近 10 条筛选 | 基本够 |
| 错误升级 | 子 Agent 错误带类型返回 | ❌ 纯文本错误 | 需改 |

**Phase D：协作层增强**

**D1. ERPAgentResult 结构化**

当前 `ERPAgentResult.text` 是纯文本，主 Agent 无法区分"数据结论"和"操作失败"。

方案：增加 `status` 字段：

```python
@dataclass
class ERPAgentResult:
    text: str
    full_text: str = ""
    status: str = "success"      # success | partial | error | timeout
    tokens_used: int = 0
    turns_used: int = 0
    tools_called: List[str] = field(default_factory=list)
    is_truncated: bool = False   # 结果是否被截断
```

主 Agent 收到 `status="partial"` 时可以追问或提示用户导出。

涉及文件：
- 改 `services/erp_agent.py`（ERPAgentResult 增加字段）
- 改 `services/handlers/chat_tool_mixin.py`（解析 status）

---

### ⑥ 体验层（35% — 有基础但粗糙）

| 能力 | Claude Code | 我们 | 差距 |
|------|-------------|------|------|
| 进度推送 | 无（CLI 直接输出） | ✅ WebSocket 推送工具名 | 有但粗 |
| 进度粒度 | N/A | ❌ 只有 "thinking" / 工具名 | 需细化 |
| 中断恢复 | abort signal + 保存已有输出 | ⚠️ 超时保存内容+退积分 | 不能续 |
| 预期管理 | max_turns 显示 | ❌ 用户不知道要等多久 | 需补 |

**Phase E：体验层增强**

**E1. 进度粒度细化**

当前 WebSocket 推送：`{"tool_name": "local_global_stats", "status": "running", "turn": 3}`

增加：`{"progress": "3/20", "elapsed_s": 12, "tools_completed": ["local_shop_list", "local_global_stats"]}`

涉及文件：
- 改 `services/erp_agent.py`（`_notify_progress` 增加字段）
- 改 `schemas/websocket.py`（`build_agent_step` 增加字段）
- 改前端进度组件

**E2. 预期管理**

复杂查询开始时推送预估时间：

```python
estimated_seconds = len(selected_tools) * 5  # 粗估每工具 5s
await self._notify_progress(0, "planning", estimated=estimated_seconds)
```

涉及文件：`services/erp_agent.py`

---

### ⑦ 进化层（20% — 有雏形）

| 能力 | Claude Code | 我们 | 差距 |
|------|-------------|------|------|
| 经验存储 | 无（靠 CLAUDE.md） | ✅ knowledge_service | 有 |
| 路由经验 | 无 | ❌ 不记录 "这类查询该怎么路由" | 需补 |
| 失败记忆 | 无 | ❌ 同样的错误会反复犯 | 需补 |
| 用户偏好 | 无 | ❌ 不记得用户偏好格式 | 后续 |

**Phase F：进化层增强**

**F1. 路由经验记录**

当 Agent 成功完成查询后，自动记录路由路径：

```python
# 成功完成后
await knowledge_service.add_knowledge(
    category="routing",
    title=f"查询路由：{query[:30]}",
    content=f"查询：{query}\n路径：{' → '.join(tools_called)}\n耗时：{elapsed}s",
)
```

下次相似查询时，知识库检索会返回这个路由经验，Agent 直接复用。

涉及文件：`services/erp_agent.py`（成功退出时记录）

**F2. 失败记忆**

当 Agent 失败退出时（非 LLM 合成），记录失败原因：

```python
if not is_llm_synthesis:
    await knowledge_service.add_knowledge(
        category="failure",
        title=f"查询失败：{query[:30]}",
        content=f"查询：{query}\n尝试：{tools_called}\n失败原因：{accumulated_text}",
    )
```

下次相似查询时，Agent 看到失败记忆，会避开同样的路径。

涉及文件：`services/erp_agent.py`（失败退出时记录）

---

## 实施路线图

### 第一阶段：安全层（修 Bug，防崩溃）— ✅ 已完成

| 任务 | 内容 | 状态 | 实现文件 |
|------|------|------|---------|
| B1 | 输出截断 + 信号层 + 大结果内存暂存 | ✅ | `services/agent/tool_result_envelope.py` |
| B2 | ERP Agent 上下文压缩 | ✅ | `services/agent/erp_agent.py` (estimate_tokens + enforce_budget) |
| B3 | 全局执行时间预算 | ✅ | `services/agent/execution_budget.py` → ERP Agent + 主 Agent |
| B4 | 请求去重/缓存 | ✅ | `services/agent/erp_agent.py` (_cache_get/_cache_put) |
| B6 | 上下文恢复 | ✅ | `services/agent/erp_agent.py` (one-shot recovery) |
| A2 | 失败反思机制（提前完成） | ✅ | `services/agent/erp_agent.py` (error prefix detection) |
| D1 | ERPAgentResult 结构化（提前完成） | ✅ | `services/agent/erp_agent_types.py` (status/is_truncated) |

### 第二阶段：决策 + 感知（更聪明，可追溯）— 待实施

| 任务 | 内容 | 工作量 | 依赖 |
|------|------|--------|------|
| A1 | 工具描述跨引用补全 | 0.5 天 | 无 |
| C1 | 结构化审计日志 | 1 天 | 无 |

### 第三阶段：协作 + 安全补充（更稳健）— 待实施

| 任务 | 内容 | 工作量 | 依赖 |
|------|------|--------|------|
| B5 | 写操作幂等保护（Redis request_id） | 0.5 天 | 无 |
| E1 | 进度粒度细化 | 0.5 天 | 无 |

### 第四阶段：体验 + 进化（锦上添花）— 待实施

| 任务 | 内容 | 工作量 | 依赖 |
|------|------|--------|------|
| E2 | 预期管理 | 0.5 天 | E1 |
| F1 | 路由经验记录 | 0.5 天 | C1 |

### 总计：Phase 1 已完成 | 剩余 ~3.5 天

```
第一阶段（完成）  ████████████████████████████████  安全层 — ✅ 已完成
第二阶段（1.5天）░░░░░░░░░░░░░░░░░░░█████████████  决策+感知 — 待实施
第三阶段（1天）  ░░░░░░░░░░░░░░░░░░░░░░░░░████████  协作+安全 — 待实施
第四阶段（1天）  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░████  体验+进化 — 待实施
```

---

## 核心设计原则

参考 Claude Code 的设计哲学：

1. **架构层统一保证，工具不需要自己处理** — 截断/去重/审计/预算都在执行器出口拦截
2. **模型永远不会看到静默截断的数据** — 截断必须标注，模型做知情决策
3. **系统级问题透明处理，决策级问题交给模型** — 超时自动重试，数据不完整告诉模型
4. **失败是学习机会** — 记录失败路由，避免重蹈覆辙
5. **主 Agent 和子 Agent 共用同一套架构层** — 不重复建设

---

## 架构层与文件的映射关系

```
services/
├── agent/                              ← 迁移后的 Agent 核心模块
│   ├── erp_agent.py                    ← B2/B4/B6/A2 + ERP Agent 核心（506行）
│   ├── erp_agent_types.py              ← D1 ERPAgentResult + 常量 + filter_erp_context
│   ├── erp_tool_executor.py            ← ERP 工具调度 Mixin
│   ├── tool_executor.py                ← B1 接入 wrap_erp_agent_result
│   ├── tool_selector.py                ← 工具选择逻辑
│   ├── tool_result_envelope.py         ← B1 输出截断+信号+大结果暂存 ✅
│   └── execution_budget.py             ← B3 全局时间预算 ✅
├── handlers/
│   ├── chat_tool_mixin.py              ← B1 接入 wrap() + raw_summary
│   ├── chat_handler.py                 ← B3 接入 ExecutionBudget
│   └── context_compressor.py           ← 层4 (estimate_tokens/enforce_budget)
├── sandbox/
│   └── functions.py                    ← B1 get_persisted_result 注册
├── erp_agent.py                        ← 兼容性 re-export（不含逻辑）
├── tool_executor.py                    ← 兼容性 re-export
├── erp_tool_executor.py                ← 兼容性 re-export
└── tool_selector.py                    ← 兼容性 re-export
```
