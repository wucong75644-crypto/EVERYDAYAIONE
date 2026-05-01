"""Agent 四态停止策略

四态停止模型：continue → ask_user → wrap_up → hard_fail
运行时硬控制，不依赖模型自觉。

设计文档：docs/document/TECH_Agent停止策略产品化.md

核心组件：
- ResultClass：工具结果分类（SUCCESS/RETRYABLE/NEEDS_INPUT/AMBIGUOUS/FATAL）
- StopDecision：运行时决策（CONTINUE/ASK_USER/WRAP_UP/HARD_FAIL）
- FailureTracker：连续失败追踪器
- StopPolicyConfig：可配置阈值（不同 Agent 可调）
- classify_tool_result()：结构化信号优先 + 关键词 fallback
- evaluate()：运行时主决策函数
- synthesize_wrap_up()：Final Synthesis Turn（不传 tools，强制纯文本）
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from loguru import logger


# ============================================================
# 结果分类
# ============================================================

class ResultClass(str, Enum):
    """工具执行结果的语义分类

    severity 由枚举值的 _SEVERITY 排序定义，数值越大越严重。
    """
    SUCCESS = "success"
    RETRYABLE = "retryable"       # 超时、瞬时网络错误
    NEEDS_INPUT = "needs_input"   # 缺参数、缺确认
    AMBIGUOUS = "ambiguous"       # 多候选、多种合理解释
    FATAL = "fatal"               # 权限不足、工具不存在、schema 错误


# 严重度排序（数值越大越严重），用于多工具并行时取最严重结果
_SEVERITY: dict[ResultClass, int] = {
    ResultClass.SUCCESS: 0,
    ResultClass.RETRYABLE: 1,
    ResultClass.AMBIGUOUS: 2,
    ResultClass.NEEDS_INPUT: 3,
    ResultClass.FATAL: 4,
}


def most_severe(classes: list[ResultClass]) -> ResultClass:
    """从多个 ResultClass 中取最严重的（多工具并行场景）"""
    if not classes:
        return ResultClass.SUCCESS
    return max(classes, key=lambda c: _SEVERITY.get(c, 0))


class StopDecision(str, Enum):
    """运行时停止决策"""
    CONTINUE = "continue"
    ASK_USER = "ask_user"
    WRAP_UP = "wrap_up"
    HARD_FAIL = "hard_fail"


# ============================================================
# 错误分类器
# ============================================================

def classify_tool_result(
    result: Any,
    audit_status: str,
) -> ResultClass:
    """分类工具执行结果。

    优先级：audit_status > AgentResult 结构化状态 > 关键词 fallback。
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


# ============================================================
# 失败追踪器
# ============================================================

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


# ============================================================
# 停止策略配置
# ============================================================

@dataclass(frozen=True)
class StopPolicyConfig:
    """停止策略配置 — 不同 Agent 可装配不同参数"""

    # ── 交互能力 ──
    allow_ask_user: bool = True

    # ── 失败阈值 ──
    max_same_error_retries: int = 1       # 同类错误最多重试 N 次
    max_consecutive_for_ask: int = 2      # 连续失败 N 次 → ask_user
    max_consecutive_for_wrap: int = 3     # 连续失败 N 次 → wrap_up

    # ── 收尾预算 ──
    wrap_up_turns_reserved: int = 1       # 预留给 wrap_up 合成的轮次


# ============================================================
# 运行时主决策
# ============================================================

def evaluate(
    tracker: FailureTracker,
    result_class: ResultClass,
    config: StopPolicyConfig,
    turns_remaining: int,
) -> StopDecision:
    """运行时主决策函数 — 每轮工具执行后调用，产出 StopDecision。

    调用方：ToolLoopExecutor.run() 主循环。
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


def _ask_or_wrap(config: StopPolicyConfig) -> StopDecision:
    """allow_ask_user=False 时自动降级为 WRAP_UP"""
    if config.allow_ask_user:
        return StopDecision.ASK_USER
    return StopDecision.WRAP_UP


# ============================================================
# wrap_up 合成
# ============================================================

_WRAP_UP_SYSTEM_PROMPT = (
    "任务执行已停止。请基于以下信息，直接给用户一个总结性回答：\n"
    "1) 已确认的结果\n"
    "2) 未完成的部分及原因\n"
    "3) 建议用户接下来怎么做\n\n"
    "如果已有足够结果，直接给出完整回答即可。不要调用工具。"
)


def build_synthesis_context(
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
                result_preview = str(block.get("result", ""))[:200]
                tool_summaries.append(f"- {name}({status}): {result_preview}")
        if tool_summaries:
            parts.append("## 工具执行记录\n" + "\n".join(tool_summaries))

    # 文件产物列表
    if collected_files:
        file_list = [
            f"- {f.get('name', '?')} ({f.get('mime_type', '?')})"
            for f in collected_files
        ]
        parts.append("## 已生成的文件\n" + "\n".join(file_list))

    return "\n\n".join(parts)


def _slim_messages_for_synthesis(
    messages: list[dict],
    keep_recent: int = 3,
) -> list[dict]:
    """精简 messages 用于 wrap_up 合成，降低 token 消耗。

    保留：所有 system 消息（含 Hook 注入的错误提示）+ 最近 N 轮非 system 交互。
    评审结论：合成时精简 messages 输入，content_blocks 摘要补全局覆盖。
    """
    if len(messages) <= keep_recent * 2 + 1:
        return list(messages)

    # 收集所有 system 消息（开头的系统提示 + 中间 Hook 注入的错误描述）
    system_msgs = [m for m in messages if m.get("role") == "system"]

    # 收集最近 N 轮非 system 交互（assistant + tool + user）
    non_system = [m for m in messages if m.get("role") != "system"]
    recent = non_system[-(keep_recent * 2):]

    return system_msgs + recent


_WRAP_UP_TIMEOUT = 15.0  # wrap_up 合成超时（秒）


async def synthesize_wrap_up(
    adapter: Any,
    messages: list[dict],
    content_blocks: list[dict] | None = None,
    collected_files: list[dict] | None = None,
    reason: str = "",
    timeout: float = _WRAP_UP_TIMEOUT,
) -> str | None:
    """Final Synthesis Turn — 不传 tools，强制纯文本输出。

    返回合成文本，失败返回 None（调用方走 hard_fail 兜底）。
    超时默认 15 秒，避免 LLM 响应慢时无限阻塞。
    """
    import asyncio

    # 构建补充上下文
    extra_ctx = build_synthesis_context(content_blocks, collected_files)
    system_content = _WRAP_UP_SYSTEM_PROMPT
    if extra_ctx:
        system_content += f"\n\n{extra_ctx}"
    if reason:
        system_content += f"\n\n停止原因：{reason}"

    # 精简 messages 降低 token 消耗
    slim = _slim_messages_for_synthesis(messages)
    slim.append({"role": "system", "content": system_content})

    async def _collect() -> str | None:
        text = ""
        async for chunk in adapter.stream_chat(
            messages=slim, tools=None, temperature=0.3,
        ):
            if chunk.content:
                text += chunk.content
        return text.strip() if text and text.strip() else None

    try:
        # 关键：不传 tools → 模型无法调用工具，只能生成文本
        return await asyncio.wait_for(_collect(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"wrap_up synthesis timeout | timeout={timeout}s")
        return None
    except Exception as e:
        logger.warning(f"wrap_up synthesis failed | error={e}")
        return None
