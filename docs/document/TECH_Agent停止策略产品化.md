# TECH: Agent 停止策略产品化 — 4 态停止模型 + 通用收尾机制

> 版本：v1.1 | 日期：2026-05-01 | 状态：方案评审
>
> v1.0 → v1.1 变更：
> - 控制权从 Hook 移到运行时主循环（§3.3 / §4.2）
> - 错误分类改为结构化信号优先，关键词仅 fallback（§4.1）
> - ask_user 由运行时 strip tools 强制触发，不依赖模型自觉（§4.3）
> - wrap_up 改为提前预留预算（`ExecutionBudget` 硬预留 1 轮），不是耗尽后补救（§4.4）
> - 合成输入改为多通道汇总（messages + content_blocks + file artifacts）（§4.4）
> - 阈值从写死改为 `StopPolicyConfig` 可配置，不同 Agent 可调（§4.6 / §8）
> - `allow_ask_user` 前置建模，无交互 Agent 自动降级为 wrap_up（§4.6）

## 1. 问题现状

### 1.1 连续失败无升级机制

当前 `FailureReflectionHook`（`loop_hooks.py:317-359`）是**无状态**的——不管连续失败几次，
每次都注入相同的三选一建议：

```
"工具 {tool_name} 返回了错误。请分析原因后选择：
 1) 换参数重试 2) 换工具 3) 用 ask_user 向用户确认"
```

同时系统提示词（`chat_tools.py:115`）明确说：

> "只有在调查后确实无法自行解决时，才用 ask_user 向用户求助，这不应该是遇到困难后的第一反应。"

**结果**：模型偏向选 1（重试），连续失败 10 次仍然重试，直到 max_turns=15 耗尽。

### 1.2 max_turns 退出无总结

当前退出逻辑（`chat_handler.py:877-895`）：

- 有部分文本 → 追加固定字符串 `"⚠️ 已达到执行上限（查询涉及多个步骤...）"`
- 无文本 → `on_error("BUDGET_EXCEEDED")`
- **两种情况都不做最终 LLM 总结调用**

用户看到的要么是一句系统提示，要么是错误页面，不知道 Agent 做到了哪一步、卡在哪、该怎么继续。

### 1.3 死循环检测太严格

当前 `_is_loop_detected`（`tool_loop_executor.py:175-192`）只检测**完全相同的工具+参数**连续 3 次。
真实死循环往往是"换个参数继续错"——工具名或参数不同，但错误本质一样。

---

## 2. 业内调研

### 2.1 连续失败处理

| 框架 | 做法 |
|------|------|
| **LangGraph** | 错误先分类（transient vs permanent），permanent 不重试直接升级 |
| **CrewAI** | `max_retry_limit=2`，超过返回 best-effort |
| **AutoGen** | `max_consecutive_auto_reply` 限制无人工输入的连续回复次数 |
| **学术研究** | "90.8% 的 ReAct Agent 重试是浪费的"——大部分错误重试不会成功 |

**业内共识**：三级升级 + 错误分类，不能让模型自己决定要不要停。

### 2.2 max_turns 退出处理

| 框架 | 做法 |
|------|------|
| **LangGraph** | `early_stopping_method="generate"` — 达到上限后**额外一次 LLM 调用**（不给 tools），基于已有上下文生成总结 |
| **LangGraph** | `early_stopping_method="force"` — 硬停，无总结（我们现在的做法） |
| **Claude Code SDK** | 返回 `error_max_turns` + session_id（可恢复），但不做总结 |
| **CrewAI** | 接近上限时模型"尽最大努力给出好答案"（软限制） |

**最佳实践**：LangGraph 的 `generate` 模式——预留最后一轮给 LLM 做总结，**不传 tools 参数**强制纯文本输出。

### 2.3 错误分类标准

```
Transient（可重试，最多 1 次）
├─ 网络超时
├─ HTTP 429（限流）
├─ HTTP 5xx（服务器错误）
└─ 临时不可用

Permanent（不可重试，直接升级）
├─ HTTP 400（参数无效）
├─ HTTP 401/403（权限不足）
├─ 工具不存在
├─ 上下文窗口超限
└─ Schema 不匹配
```

---

## 3. 架构设计

### 3.1 产品语义（一句话定义）

> Agent 会尽量自主完成；当信息不足、结果存在歧义、或继续尝试的收益已经低于风险时，
> 会主动暂停并向用户确认；当执行预算接近上限时，会优先输出当前结论、未完成部分和下一步建议，
> 而不是直接失败。

### 3.2 四态停止模型

主 Agent 每轮执行后，运行时评估并产出以下 4 种决策之一：

| 状态 | 语义 | 触发条件 | 运行时行为 |
|------|------|---------|-----------|
| `continue` | 正常推进 | 工具成功 / 可重试错误首次 | 继续循环 |
| `ask_user` | 缺关键信息/歧义/需确认 | 信息不足、多候选歧义、连续同类失败 2 次 | strip tools 只留 ask_user，强制模型追问 |
| `wrap_up` | 接近预算或无法继续 | 预算预留轮触发、连续失败 ≥ 3、FATAL + 有部分结果 | 不传 tools，LLM 生成四段总结 |
| `hard_fail` | 系统异常且无任何可用结果 | wrap_up 合成也失败 / 完全无部分结果 | `on_error()` |

**核心原则**：

- `max_turns` 不应直接对应 `hard_fail`，应优先触发 `wrap_up`
- "不会做"时优先 `ask_user`，不是继续盲试
- "快没预算"时优先总结，不是继续扩张
- **运行时产出决策，不依赖模型自觉**

### 3.3 架构分层

```
┌──────────────────────────────────────────────────────┐
│  Layer 1: 运行时硬控制层（领域无关，主控制）              │
│                                                      │
│  ToolLoopExecutor.run() / ChatHandler while 循环       │
│  每轮工具执行后：                                       │
│    → classify_result()  分类结果                        │
│    → tracker.record()   更新失败状态                     │
│    → evaluate()         产出 StopDecision               │
│    → 根据 decision 执行：continue / strip tools / synth │
│                                                      │
│  ExecutionBudget 硬预留 wrap_up 轮次                     │
├──────────────────────────────────────────────────────┤
│  Layer 2: 提示词软引导层（领域无关，辅助）                │
│                                                      │
│  主 Agent system prompt 通用工作原则                     │
│  → 何时继续 / 何时停 / 何时问                            │
│  → 定位：锦上添花，不是兜底                               │
├──────────────────────────────────────────────────────┤
│  Layer 3: Hook 辅助层（副作用，不做决策）                 │
│                                                      │
│  FailureReflectionHook → 降为辅助提示注入                │
│  AmbiguityDetectionHook → 降为辅助提示注入               │
│  ToolAuditHook → 审计日志（不变）                        │
│  → Hook 不产出 StopDecision，只做 system message 注入    │
├──────────────────────────────────────────────────────┤
│  Layer 4: 领域事件上报层（领域相关）                      │
│                                                      │
│  每个 Agent/工具只上报"发生了什么类型的事件"               │
│  → AgentResult.status: success/error/empty/ask_user    │
│  → audit_status: success/timeout/error                 │
│  → 不同 Agent 不决定"怎么停"，只上报"发生了什么"          │
└──────────────────────────────────────────────────────┘
```

**关键变化（v1.0 → v1.1）**：Hook 从"主控制器"降为"辅助提示"。
状态转换发生在循环主体 `run()`，不在 Hook 回调里。

---

## 4. 详细设计

### 4.1 错误分类（结构化信号优先）

新增通用工具结果分类器。**优先吃现有结构化信号**，关键词仅作 fallback。

项目中已有的结构化信号：

| 信号 | 位置 | 可用值 |
|------|------|--------|
| `audit_status` | `tool_loop_helpers.py:74` | `"success"` / `"timeout"` / `"error"` |
| `AgentResult.status` | `agent_result.py:38` | `success` / `error` / `empty` / `partial` / `timeout` / `ask_user` / `plan` |
| `AgentResult.is_failure` | `agent_result.py:96` | 统一失败判断属性 |
| `AgentResult.error_message` | `agent_result.py:58` | 错误详情文本 |
| `isinstance(result, AgentResult)` | `tool_loop_executor.py:620` | 已有类型分支 |

```python
# services/agent/stop_policy.py（新文件）

from enum import Enum

class ResultClass(str, Enum):
    SUCCESS = "success"
    RETRYABLE = "retryable"         # 超时、瞬时网络错误、临时 DB 错误
    NEEDS_INPUT = "needs_input"     # 缺参数、缺确认、写操作被拒
    AMBIGUOUS = "ambiguous"         # 多候选、多种合理解释
    FATAL = "fatal"                 # 权限不足、工具不存在、schema 错误


class StopDecision(str, Enum):
    CONTINUE = "continue"
    ASK_USER = "ask_user"
    WRAP_UP = "wrap_up"
    HARD_FAIL = "hard_fail"


def classify_tool_result(
    result: Any,
    audit_status: str,
) -> ResultClass:
    """分类工具执行结果。

    优先级：结构化信号 > 关键词 fallback。
    """
    # ── 第一优先级：audit_status（来自 tool_loop_helpers） ──
    if audit_status == "success":
        return ResultClass.SUCCESS
    if audit_status == "timeout":
        return ResultClass.RETRYABLE

    # ── 第二优先级：AgentResult 结构化状态 ──
    from services.agent.agent_result import AgentResult
    if isinstance(result, AgentResult):
        if not result.is_failure:
            if result.status == "ask_user":
                return ResultClass.NEEDS_INPUT
            if result.status in ("empty", "partial"):
                return ResultClass.SUCCESS  # 空/部分结果不算错误
            return ResultClass.SUCCESS
        # is_failure = True（error / timeout）
        if result.status == "timeout":
            return ResultClass.RETRYABLE
        # status == "error"：从 error_message 进一步细分
        return _classify_error_text(result.error_message)

    # ── 第三优先级：纯字符串结果（老工具 fallback） ──
    if isinstance(result, str) and audit_status == "error":
        return _classify_error_text(result)

    return ResultClass.RETRYABLE  # 未知错误默认可重试一次


# ── 关键词 fallback（仅处理纯字符串错误） ──

_FATAL_PATTERNS = (
    "权限", "permission", "forbidden", "401", "403",
    "not found", "不存在", "schema", "invalid",
)
_NEEDS_INPUT_PATTERNS = (
    "缺少", "missing", "未指定", "请提供", "请确认",
    "无法确定", "need", "require",
)
_AMBIGUOUS_PATTERNS = (
    "匹配到", "多个", "候选", "multiple", "ambiguous",
    "哪一个", "which",
)

def _classify_error_text(text: str) -> ResultClass:
    """关键词 fallback 分类（仅在无结构化信号时使用）"""
    if not text:
        return ResultClass.RETRYABLE
    sample = text[:200].lower()
    # 顺序：fatal > needs_input > ambiguous > retryable
    if any(p in sample for p in _FATAL_PATTERNS):
        return ResultClass.FATAL
    if any(p in sample for p in _NEEDS_INPUT_PATTERNS):
        return ResultClass.NEEDS_INPUT
    if any(p in sample for p in _AMBIGUOUS_PATTERNS):
        return ResultClass.AMBIGUOUS
    return ResultClass.RETRYABLE
```

### 4.2 失败追踪与分级升级（运行时主控）

状态追踪和决策产出都在 `ToolLoopExecutor.run()` 主循环中完成，不在 Hook 里。

```python
# services/agent/stop_policy.py（续）

import hashlib
from dataclasses import dataclass


@dataclass
class FailureTracker:
    """连续失败追踪器（每次循环 run() 构造一份）"""
    consecutive_failures: int = 0
    same_error_streak: int = 0
    last_error_signature: str = ""
    total_failures: int = 0
    has_meaningful_progress: bool = False

    def record_success(self) -> None:
        """工具成功 → 重置连续计数，标记有进展"""
        self.consecutive_failures = 0
        self.same_error_streak = 0
        self.last_error_signature = ""
        self.has_meaningful_progress = True

    def record_failure(self, tool_name: str, error_text: str) -> None:
        """工具失败 → 更新计数 + 错误签名"""
        self.consecutive_failures += 1
        self.total_failures += 1
        sig = self._make_signature(tool_name, error_text)
        if sig == self.last_error_signature:
            self.same_error_streak += 1
        else:
            self.same_error_streak = 1
            self.last_error_signature = sig

    @staticmethod
    def _make_signature(tool_name: str, error_text: str) -> str:
        """错误签名：工具名 + 错误前缀 hash（不含参数值，检测同类错误）"""
        prefix = error_text[:80] if error_text else ""
        return f"{tool_name}:{hashlib.md5(prefix.encode()).hexdigest()[:8]}"


def evaluate(
    tracker: FailureTracker,
    result_class: ResultClass,
    config: "StopPolicyConfig",
    turns_remaining: int,
) -> StopDecision:
    """运行时主决策函数 — 每轮工具执行后调用，产出 StopDecision。

    调用方：ToolLoopExecutor.run() / ChatHandler while 循环。
    不在 Hook 里调用。
    """
    # ── 成功 → 继续 ──
    if result_class == ResultClass.SUCCESS:
        return StopDecision.CONTINUE

    # ── 永久性错误 → 不重试 ──
    if result_class == ResultClass.FATAL:
        if tracker.has_meaningful_progress:
            return StopDecision.WRAP_UP
        return _ask_or_wrap(config)

    # ── 需要用户输入 / 歧义 → 直接 ask_user ──
    if result_class in (ResultClass.NEEDS_INPUT, ResultClass.AMBIGUOUS):
        return _ask_or_wrap(config)

    # ── 可重试错误 → 按连续次数升级 ──
    if tracker.same_error_streak > config.max_same_error_retries:
        return _ask_or_wrap(config)  # 同类错误重试够了

    if tracker.consecutive_failures >= config.max_consecutive_for_wrap:
        return StopDecision.WRAP_UP   # 连续失败达上限，强制收尾

    if tracker.consecutive_failures >= config.max_consecutive_for_ask:
        return _ask_or_wrap(config)   # 连续失败中等，建议问用户

    if turns_remaining <= config.wrap_up_turns_reserved:
        return StopDecision.WRAP_UP   # 预算快没了，收尾

    return StopDecision.CONTINUE      # 允许重试一次


def _ask_or_wrap(config: "StopPolicyConfig") -> StopDecision:
    """allow_ask_user=False 时自动降级为 WRAP_UP"""
    if config.allow_ask_user:
        return StopDecision.ASK_USER
    return StopDecision.WRAP_UP
```

**在 `ToolLoopExecutor.run()` 中的接入点**（伪代码）：

```python
# tool_loop_executor.py run() 主循环内，工具执行后（约 line 616 后）

for tc, tool_name, args, result, audit_status, is_cached, elapsed_ms in results:
    # ... 现有的归一化/文件收集/截断/入 messages ...

    # ── v1.1 新增：运行时主决策 ──
    result_class = classify_tool_result(result, audit_status)

    if result_class == ResultClass.SUCCESS:
        tracker.record_success()
    else:
        error_text = result.error_message if isinstance(result, AgentResult) else str(result)
        tracker.record_failure(tool_name, error_text)

    decision = evaluate(
        tracker, result_class, self.stop_config,
        turns_remaining=self.config.max_turns - (turn + 1),
    )

    if decision == StopDecision.ASK_USER:
        # strip tools 只留 ask_user → 下一轮模型被迫调 ask_user
        self._force_ask_user_next_turn = True
    elif decision == StopDecision.WRAP_UP:
        # 立即跳出循环，进入 _synthesize_wrap_up()
        wrap_up_reason = f"consecutive_failures={tracker.consecutive_failures}"
        break

# ... 循环继续前检查 _force_ask_user_next_turn ...
if self._force_ask_user_next_turn:
    selected_tools = [t for t in selected_tools if t["function"]["name"] == "ask_user"]
    self._force_ask_user_next_turn = False
```

### 4.3 ask_user 强制机制（运行时 strip tools）

**v1.0 问题**：注入 system message 说"必须用 ask_user"，模型有概率不听。

**v1.1 方案**：运行时决定 `ASK_USER` 后，下一轮 LLM 调用**只提供 `[ask_user]` 这一个工具**。
模型没有别的工具可调，被迫用 ask_user 生成追问。

```python
# tool_loop_executor.py — ask_user 强制触发

if decision == StopDecision.ASK_USER:
    # 注入辅助提示（让模型知道为什么要问）
    messages.append({
        "role": "system",
        "content": (
            f"工具 {tool_name} 执行遇到问题，继续重试不太可能成功。"
            "请用 ask_user 向用户说明："
            "1) 你在尝试做什么 2) 遇到了什么 3) 需要用户提供什么。"
        ),
    })
    # 硬约束：只留 ask_user
    selected_tools = [t for t in all_tools if t["function"]["name"] == "ask_user"]
    # 继续循环 → 下一轮 LLM 只能调 ask_user
```

**ChatHandler 侧**：主 Agent 的 `ask_user` 冻结链路（`chat_handler.py:803-838`）已完整，
运行时 strip tools 后模型调 `ask_user` → 触发现有冻结逻辑，无需改动冻结链路本身。

**无交互 Agent（如 ScheduledTaskAgent）**：`StopPolicyConfig(allow_ask_user=False)`，
`_ask_or_wrap()` 自动降级为 `WRAP_UP`，不会走到 strip tools 逻辑。

### 4.4 wrap_up 收尾机制（提前预留 + 多通道合成）

**v1.0 问题**：等预算真的耗尽后再补一次 synthesis，此时可能 token 不够、时间不够。

**v1.1 方案**：两层保障——

1. **ExecutionBudget 硬预留**：`stop_reason` 在达到 `max_turns - reserved` 时
   返回 `"wrap_up"`（新状态），而不是等到 `max_turns` 才返回 `"max_turns"`
2. **多通道合成输入**：不只喂 messages，还汇总 content_blocks / file artifacts

#### 4.4.1 ExecutionBudget 预留

```python
# execution_budget.py — 新增 wrap_up 预留

@dataclass
class ExecutionBudget:
    # ... 现有字段 ...
    _wrap_up_reserved: int = 1  # 预留给 wrap_up 的轮次

    @property
    def stop_reason(self) -> Optional[str]:
        """返回第一个触发的限制"""
        # ── 新增：提前触发 wrap_up ──
        if self._turns_used >= self._max_turns - self._wrap_up_reserved:
            return "wrap_up"
        if self._turns_used >= self._max_turns:
            return "max_turns"   # 最终兜底（wrap_up 合成也用完轮次时）
        if self._tokens_used >= self._max_tokens:
            return "max_tokens"
        if self.elapsed >= self._max_wall_time:
            return "wall_timeout"
        return None
```

#### 4.4.2 合成输入：多通道汇总

```python
# services/agent/stop_policy.py（续）

def build_synthesis_context(
    messages: list[dict],
    content_blocks: list[dict] | None = None,
    collected_files: list[dict] | None = None,
) -> str:
    """构建 wrap_up 合成的补充上下文。

    从 content_blocks 和 file artifacts 中提取摘要，
    补充 messages 可能因压缩而丢失的信息。
    """
    parts: list[str] = []

    # 工具结果摘要（从 content_blocks 提取）
    if content_blocks:
        tool_summaries = []
        for block in content_blocks:
            if block.get("type") == "tool_step":
                name = block.get("tool_name", "unknown")
                status = block.get("status", "unknown")
                # 取结果前 200 字符作为摘要
                result_preview = str(block.get("result", ""))[:200]
                tool_summaries.append(f"- {name}({status}): {result_preview}")
        if tool_summaries:
            parts.append("## 工具执行记录\n" + "\n".join(tool_summaries))

    # 文件产物列表
    if collected_files:
        file_list = [f"- {f.get('name', '?')} ({f.get('mime_type', '?')})" for f in collected_files]
        parts.append("## 已生成的文件\n" + "\n".join(file_list))

    return "\n\n".join(parts)
```

#### 4.4.3 wrap_up 合成（统一入口）

ToolLoopExecutor 和 ChatHandler 共用同一个合成逻辑：

```python
# services/agent/stop_policy.py（续）

WRAP_UP_SYSTEM_PROMPT = (
    "任务执行已停止。请基于以下信息，直接给用户一个总结性回答：\n"
    "1) 已确认的结果\n"
    "2) 未完成的部分及原因\n"
    "3) 建议用户接下来怎么做\n\n"
    "如果已有足够结果，直接给出完整回答即可。不要调用工具。"
)

async def synthesize_wrap_up(
    adapter,
    messages: list[dict],
    content_blocks: list[dict] | None = None,
    collected_files: list[dict] | None = None,
    reason: str = "",
) -> str | None:
    """Final Synthesis Turn — 不传 tools，强制纯文本输出。

    返回合成文本，失败返回 None（调用方走 hard_fail 兜底）。
    """
    # 构建补充上下文
    extra_ctx = build_synthesis_context(content_blocks, collected_files)
    system_content = WRAP_UP_SYSTEM_PROMPT
    if extra_ctx:
        system_content += f"\n\n{extra_ctx}"
    if reason:
        system_content += f"\n\n停止原因：{reason}"

    messages_copy = list(messages)
    messages_copy.append({"role": "system", "content": system_content})

    try:
        # 关键：不传 tools → 模型无法调用工具，只能生成文本
        response = await adapter.chat(messages=messages_copy)
        text = response.text if hasattr(response, "text") else str(response)
        return text.strip() if text and text.strip() else None
    except Exception as e:
        logger.warning(f"wrap_up synthesis failed | error={e}")
        return None
```

#### 4.4.4 ChatHandler 调用方式

```python
# chat_handler.py — 预算耗尽后的新逻辑

_stop = _budget.stop_reason

if _stop in ("wrap_up", "max_turns", "max_tokens", "wall_timeout"):
    logger.warning(
        f"Budget stop | task={task_id} | reason={_stop} | "
        f"turns={_budget.turns_used} | tokens={_budget.tokens_used}"
    )

    # ── Final Synthesis Turn ──
    from services.agent.stop_policy import synthesize_wrap_up

    synthesis = await synthesize_wrap_up(
        adapter=self._adapter,
        messages=messages,
        content_blocks=_content_blocks,
        collected_files=_collected_files_list,
        reason=_STOP_MESSAGES.get(_stop, _stop),
    )

    if synthesis:
        # 流式输出给用户
        await self._stream_text_chunk(task_id, synthesis)
        accumulated_text = synthesis
    elif accumulated_text:
        # 合成失败但有部分结果 → 追加提示
        accumulated_text += f"\n\n> ⚠️ 已达到执行上限，以上为部分结果。"
    else:
        # 完全无结果 → hard_fail
        await self.on_error(
            task_id=task_id,
            error_code="BUDGET_EXCEEDED",
            error_message=_STOP_MESSAGES.get(_stop, "执行超限，请稍后重试"),
        )
        _budget_error_sent = True
```

#### 4.4.5 ToolLoopExecutor 调用方式

```python
# tool_loop_executor.py — _finalize 改造

async def _finalize(self, ...) -> LoopResult:
    if not is_llm_synthesis and accumulated_text:
        # 有工具结果但没有 LLM 总结 → wrap_up 合成
        synthesis = await synthesize_wrap_up(
            adapter=self.adapter,
            messages=hook_ctx.messages,
            reason=wrap_up_reason or "loop_exit_without_synthesis",
        )
        if synthesis:
            accumulated_text = synthesis
            is_llm_synthesis = True
        else:
            accumulated_text = self.config.no_synthesis_fallback_text
    elif not is_llm_synthesis:
        accumulated_text = self.config.no_synthesis_fallback_text

    # ... 后续 hook 链不变
```

### 4.5 提示词改造（Layer 2 软引导）

定位：辅助引导，不是兜底。运行时已做硬控制，提示词只让模型"更配合"。

替换 `chat_tools.py:110-116`，改为通用版：

```python
TOOL_SYSTEM_PROMPT = """# 做事原则

- 用户的请求以数据查询、文件处理和业务分析为主。收到不明确的指令时，结合这些场景理解意图。
- 不掌握业务数据，不能凭印象回答。必须通过工具获取真实数据。
- 先给结论，再补充必要的解释。回答的详略匹配问题的复杂度。
- 如果执行失败，先诊断原因再调整方案——读错误信息、检查自己的假设、做针对性修正。不要盲目重试相同的操作，也不要一次失败就放弃可行的思路。
- 如果工具连续失败且没有带来新的有效信息，不要反复重试。应总结当前进展、说明阻塞原因，并给出下一步建议。
- 如果缺少完成任务所必需的信息，不要猜测；用 ask_user 提出一个最小必要的问题。
- 如果任务存在多种合理解释且不同解释会影响结果，不要自行选择；用 ask_user 向用户确认。
- 当接近执行上限时，停止继续扩展任务范围，优先输出已确认结果、未完成部分和建议。
- 如实汇报结果：数据有异常就说有异常，执行失败就说失败。不要为了给出"完整"的回答而掩盖过程中发现的问题。同样，成功了就直接说成功，不要加多余的保留语。
- 只有在能够可靠推进时才继续调用工具。不要为了显得自主而编造结论或假设用户未提供的信息。
"""
```

### 4.6 StopPolicyConfig（可配置，不写死）

```python
# services/agent/stop_policy.py（续）

from dataclasses import dataclass

@dataclass(frozen=True)
class StopPolicyConfig:
    """停止策略配置 — 不同 Agent 可装配不同参数"""

    # ── 交互能力 ──
    allow_ask_user: bool = True
    # ERPAgent: True（主 Agent 有冻结链路）
    # ScheduledTaskAgent: False（无交互，自动降级为 wrap_up）

    # ── 失败阈值 ──
    max_same_error_retries: int = 1         # 同类错误最多重试 N 次
    max_consecutive_for_ask: int = 2        # 连续失败 N 次 → ask_user
    max_consecutive_for_wrap: int = 3       # 连续失败 N 次 → wrap_up

    # ── 收尾预算 ──
    wrap_up_turns_reserved: int = 1         # 预留给 wrap_up 合成的轮次
    # 主 Agent: 1（预留 1 轮做总结）
    # 子 Agent: 1（同上）

    # ── wrap_up 提前引导 ──
    soft_wrap_up_turns: int = 2             # 剩余 N 轮时注入软引导提示
    # 与 wrap_up_turns_reserved 的区别：
    # soft_wrap_up_turns：注入提示词建议收尾（但模型仍可调工具）
    # wrap_up_turns_reserved：硬预留，budget.stop_reason 返回 "wrap_up"
```

**各 Agent 装配示例**：

```python
# ERPAgent（有交互，高容忍度）
StopPolicyConfig(
    allow_ask_user=True,
    max_same_error_retries=1,
    max_consecutive_for_ask=2,
    max_consecutive_for_wrap=3,
    wrap_up_turns_reserved=1,
    soft_wrap_up_turns=2,
)

# ScheduledTaskAgent（无交互，低容忍度）
StopPolicyConfig(
    allow_ask_user=False,          # 自动降级为 wrap_up
    max_same_error_retries=1,
    max_consecutive_for_ask=2,     # 实际会被降级为 wrap_up
    max_consecutive_for_wrap=2,    # 更早收尾
    wrap_up_turns_reserved=1,
    soft_wrap_up_turns=1,
)
```

### 4.7 `_STOP_MESSAGES` 去领域化

```python
# chat_handler.py

_STOP_MESSAGES = {
    "wrap_up":      "接近执行上限，正在总结当前进展。",
    "max_turns":    "已达到单次对话工具调用上限。",
    "max_tokens":   "本次任务消耗的资源过大，请缩小范围或分步进行。",
    "wall_timeout": "任务耗时过长，请稍后重试。",
}
```

有了 Final Synthesis Turn 后，这些固定文案只在 LLM 总结也失败时才作为最终兜底。

### 4.8 Hook 降级（FailureReflectionHook 精简）

`FailureReflectionHook` 保留，但职责收窄为**辅助提示注入**，不做决策：

```python
# loop_hooks.py — 精简后的 FailureReflectionHook

class FailureReflectionHook(LoopHook):
    """工具错误时注入辅助提示（仅 Layer 3 辅助，不做决策）。

    决策由 ToolLoopExecutor.run() 中的 stop_policy.evaluate() 完成。
    本 Hook 只负责注入 system message 帮助模型理解错误原因。
    """

    _ERROR_PREFIXES = (
        "工具执行失败:", "工具执行超时",
        "工具参数JSON格式错误:", "❌", "Traceback",
    )

    async def on_tool_end(self, ctx, tool_name, args, result, status, **kw):
        if not result:
            return
        text = str(result)
        if not (text.startswith(self._ERROR_PREFIXES) or "Error:" in text[:100]):
            return
        # 只注入错误描述，不给"选项"——选项由运行时 strip tools 决定
        ctx.messages.append({
            "role": "system",
            "content": f"工具 {tool_name} 返回了错误：{text[:200]}",
        })
```

---

## 5. 文件改动清单

### 5.1 新增文件

| 文件 | 职责 | 预估行数 |
|------|------|---------|
| `services/agent/stop_policy.py` | 通用停止策略（ResultClass / StopDecision / FailureTracker / evaluate / StopPolicyConfig / synthesize_wrap_up） | ~220 |

### 5.2 修改文件

| 文件 | 改动点 | 预估改动量 |
|------|--------|-----------|
| `services/agent/tool_loop_executor.py` | `run()` 接入 FailureTracker + evaluate() 主决策 + strip tools + _finalize 调 synthesize_wrap_up | ~70 行改 |
| `services/agent/loop_types.py` | LoopConfig 新增 `stop_config: StopPolicyConfig`；LoopResult 新增 `stop_reason` / `wrap_up_reason` | ~15 行加 |
| `services/agent/loop_hooks.py` | FailureReflectionHook 精简为辅助提示，删除三选一决策逻辑 | ~15 行改 |
| `services/agent/execution_budget.py` | `stop_reason` 新增 `"wrap_up"` 状态（提前 1 轮触发） | ~10 行改 |
| `services/handlers/chat_handler.py` | 预算耗尽后调 synthesize_wrap_up + _STOP_MESSAGES 去领域化 + strip tools 逻辑 | ~50 行改 |
| `services/handlers/chat_generate_mixin.py` | 非流式路径同步走 synthesize_wrap_up | ~30 行改 |
| `config/chat_tools.py` | TOOL_SYSTEM_PROMPT 工作原则改通用表述 | ~10 行改 |

### 5.3 不改动的文件

- `config/phase_tools.py` — `ask_user` 工具定义不变
- `services/agent/tool_audit.py` — 审计 Hook 不变
- 各子 Agent — 通过 ToolLoopExecutor + StopPolicyConfig 装配自动获益

---

## 6. 状态流转图

```
                    ┌──────────────┐
                    │  工具调用完成  │
                    └──────┬───────┘
                           │
                ┌──────────▼──────────┐
                │  classify_tool_     │
                │  result()           │
                │  结构化信号优先       │
                │  关键词 fallback     │
                └──────────┬──────────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────▼─────┐ ┌───▼────┐ ┌────▼──────┐
        │  SUCCESS   │ │RETRYABLE│ │NEEDS_INPUT│
        │            │ │ /FATAL │ │ /AMBIGUOUS│
        └─────┬─────┘ └───┬────┘ └────┬──────┘
              │            │            │
              │     ┌──────▼───────┐    │
     tracker  │     │FailureTracker│    │
     .record  │     │.record_      │    │
     _success │     │ failure()    │    │
              │     └──────┬───────┘    │
              │            │            │
              │     ┌──────▼───────┐    │
              │     │  evaluate()  │    │
              │     │  运行时主决策  │◄───┘
              │     └──┬───┬───┬──┘
              │        │   │   │
       ┌──────▼──┐  ┌──▼┐ ┌▼──────┐ ┌────────┐
       │CONTINUE │  │   │ │WRAP_UP│ │ASK_USER│
       │         │  │   │ │       │ │        │
       └─────────┘  │   │ └───┬───┘ └───┬────┘
                    │   │     │         │
                    │   │     │    ┌────▼──────┐
                    │   │     │    │strip tools│
                    │   │     │    │只留ask_user│
                    │   │     │    └────┬──────┘
                    │   │     │         │
                    │   │  ┌──▼─────────▼──┐
                    │   │  │下一轮 LLM 调用 │
                    │   │  │（无tools/只有  │
                    │   │  │  ask_user）    │
                    │   │  └──┬─────────┬──┘
                    │   │     │         │
                    │   │  ┌──▼──┐  ┌───▼────┐
                    │   │  │四段  │  │冻结状态 │
                    │   │  │总结  │  │追问用户 │
                    │   │  └─────┘  └────────┘
                    │   │
                    │   │  合成失败 + 无部分结果
                    │   │     │
                    │   └─────▼──┐
                    │   │HARD_FAIL│
                    │   │on_error │
                    │   └────────┘
                    │
              继续循环
```

---

## 7. 实施计划

### Phase 1：行为改对（解决两个核心痛点）

> 目标：不再"硬撞上限 + 直接失败"，不再"连续失败靠模型自觉停"

| 步骤 | 内容 | 文件 |
|------|------|------|
| 1.1 | 新建 `stop_policy.py`：ResultClass + StopDecision + FailureTracker + StopPolicyConfig + evaluate() + classify_tool_result() | 新文件 |
| 1.2 | `ExecutionBudget.stop_reason` 新增 `"wrap_up"` 硬预留状态 | `execution_budget.py` |
| 1.3 | `LoopConfig` 新增 `stop_config` 字段 | `loop_types.py` |
| 1.4 | `ToolLoopExecutor.run()` 接入 FailureTracker + evaluate() + strip tools + wrap_up break | `tool_loop_executor.py` |
| 1.5 | `ToolLoopExecutor._finalize` 调 `synthesize_wrap_up()`（多通道输入） | `tool_loop_executor.py` |
| 1.6 | `FailureReflectionHook` 精简为辅助提示 | `loop_hooks.py` |
| 1.7 | `ChatHandler` 预算耗尽后调 `synthesize_wrap_up()` + strip tools 逻辑 | `chat_handler.py` |
| 1.8 | `ChatGenerateMixin` 同步改造 | `chat_generate_mixin.py` |
| 1.9 | `TOOL_SYSTEM_PROMPT` + `_STOP_MESSAGES` 去领域化 | `chat_tools.py` / `chat_handler.py` |

### Phase 2：体验做漂亮

> 目标：总结更稳定、更像产品

| 步骤 | 内容 |
|------|------|
| 2.1 | `LoopResult` 扩展结构化字段（`stop_reason` / `wrap_up_reason` / `partial_summary` / `next_step_suggestion`） |
| 2.2 | 前端专门渲染"部分完成 + 建议下一步"卡片 |
| 2.3 | 子 Agent 返回结构化 `stop_reason`，主 Agent 可据此决策 |
| 2.4 | wrap_up 总结质量监控（采集 wrap_up 频率、用户后续行为） |

---

## 8. 阈值配置

所有阈值通过 `StopPolicyConfig` 配置，不同 Agent 可调。

| 参数 | 默认值 | 主 Agent | ERPAgent | ScheduledTaskAgent |
|------|--------|---------|---------|-------------------|
| `allow_ask_user` | `True` | `True` | `True` | **`False`** |
| `max_same_error_retries` | `1` | `1` | `1` | `1` |
| `max_consecutive_for_ask` | `2` | `2` | `2` | `2`（降级为 wrap_up） |
| `max_consecutive_for_wrap` | `3` | `3` | `3` | **`2`** |
| `wrap_up_turns_reserved` | `1` | `1` | `1` | `1` |
| `soft_wrap_up_turns` | `2` | `2` | `2` | **`1`** |

`budget_max_turns` / 子 Agent `max_turns` 保持不变（15 / 20 / 12）。

---

## 9. 测试用例清单

### 9.1 连续失败升级（运行时决策）

| 用例 | 预期 StopDecision | 验证重点 |
|------|-------------------|---------|
| 工具首次超时（audit_status="timeout"） | `CONTINUE` | classify → RETRYABLE，tracker.consecutive=1 < 2 |
| AgentResult(status="error") 第 2 次 | `ASK_USER` | consecutive=2 ≥ max_consecutive_for_ask |
| 同一工具同类错误签名第 2 次 | `ASK_USER` | same_error_streak=2 > max_same_error_retries |
| 连续 3 次不同工具失败 | `WRAP_UP` | consecutive=3 ≥ max_consecutive_for_wrap |
| 权限错误（classify → FATAL） | `ASK_USER`（无进展）/ `WRAP_UP`（有进展） | 不重试 |
| AgentResult(status="ask_user") | `ASK_USER` | classify → NEEDS_INPUT |
| 失败后成功 | `CONTINUE` + tracker 重置 | consecutive/same_error 归零 |

### 9.2 ask_user 强制（strip tools）

| 用例 | 预期行为 |
|------|---------|
| evaluate() → ASK_USER | 下一轮 selected_tools 只含 ask_user |
| 模型在 strip 后调 ask_user | 正常触发冻结链路 |
| allow_ask_user=False 时 evaluate() → ASK_USER | 自动降级为 WRAP_UP |

### 9.3 wrap_up 合成（多通道）

| 用例 | 预期行为 |
|------|---------|
| 预算 wrap_up 触发（turns_remaining=1） | budget.stop_reason="wrap_up"，调 synthesize_wrap_up |
| 有 content_blocks + files | 合成输入包含工具执行记录 + 文件列表 |
| 合成成功 | 用户看到四段总结 |
| 合成失败 + 有部分文本 | 追加 "⚠️ 以上为部分结果" |
| 合成失败 + 无文本 | on_error("BUDGET_EXCEEDED") → hard_fail |

### 9.4 提示词通用性

| 用例 | 预期行为 |
|------|---------|
| ERP 查询失败 | 引导文案不含"查询"特定词 |
| 图片生成参数不足 | classify → NEEDS_INPUT → ask_user |
| 文件处理超时 | classify → RETRYABLE → 允许重试 |
| 数据分析口径歧义 | classify → AMBIGUOUS → ask_user |

### 9.5 ChatGenerateMixin 一致性

| 用例 | 预期行为 |
|------|---------|
| 非流式路径 + 预算耗尽 | 同样调 synthesize_wrap_up，不是固定文案 |

---

## 10. 风险与注意事项

1. **Final Synthesis Turn 的成本**：多一次 LLM 调用。但只在预算耗尽 / 强制收尾时触发，
   频率低，用户体验提升远大于成本增加。

2. **不传 tools 是硬约束**：光靠提示词说"不要再调工具"，模型有概率不听。
   不传 tools（wrap_up）或只传 ask_user（强制追问）是唯一可靠的方式。

3. **strip tools 后模型可能输出纯文本而不调 ask_user**：
   需要在 ChatHandler 侧判断——如果 strip 了 tools 后模型输出纯文本，
   将该文本包装成 ask_user 的 message 字段进入冻结链路。

4. **结构化信号覆盖率**：AgentResult 覆盖了 ERPAgent / 本地工具，
   但部分老工具（如直接返回字符串的）仍需关键词 fallback。
   随着工具统一返回 AgentResult，fallback 路径会逐步退役。

5. **ExecutionBudget 新增 "wrap_up" 状态的兼容性**：
   所有消费 `stop_reason` 的地方需要处理新状态。
   当前消费方：ChatHandler / ChatGenerateMixin / ToolLoopExecutor 的 `_pre_turn_checks`。

6. **与 ChatGenerateMixin（非流式路径）的一致性**：
   `chat_generate_mixin.py:177-182` 的预算耗尽处理也需要同步改造，
   走统一的 `synthesize_wrap_up()`，避免 WebSocket 和非 WebSocket 行为不一致。
