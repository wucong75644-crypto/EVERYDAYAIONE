"""Tool Loop Executor 共用类型定义

设计文档：参考 OpenAI Agents SDK / LangGraph / Anthropic Claude Code 的模式：
- LoopConfig：数值参数（max_turns/max_tokens/timeout/...）
- LoopStrategy：结构性决策（exit_signals/tool_expansion）
- LoopHook：行为差异（progress notify/audit/temporal validation/failure reflection）
- HookContext：单次 run 的可变上下文，hook 间共享

ERPAgent 与 ScheduledTaskAgent 共用同一 ToolLoopExecutor，
通过装配不同的 config/strategy/hooks 实现行为差异，零代码重复。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ============================================================
# 配置（数值参数）
# ============================================================

@dataclass(frozen=True)
class LoopConfig:
    """工具循环数值配置"""
    max_turns: int                                # 最大轮次
    max_tokens: int                               # Token 预算上限
    tool_timeout: float                           # 单工具超时上限（秒）
    context_compression_threshold: float = 0.7   # 超过该比例触发主动压缩
    context_recovery_target: float = 0.5         # 上下文超限恢复时压缩到该比例
    thinking_mode: Optional[str] = "enabled"     # 透传给 adapter（qwen3.5 需要开启才走 function calling）
    no_synthesis_fallback_text: str = (
        "查询过程中未能生成完整结论，请重新提问或缩小查询范围。"
    )


# ============================================================
# 策略（结构性决策）
# ============================================================

@dataclass(frozen=True)
class LoopStrategy:
    """工具循环结构性策略

    所有"行为"差异（审计/进度/校验/反思）走 hooks，不放这里。
    这里只放影响循环控制流的决策。
    """
    # 退出信号工具集（命中后立即结束循环）
    # ERPAgent: {"route_to_chat", "ask_user"}
    # ScheduledTaskAgent: frozenset()  # 无交互场景
    exit_signals: frozenset = field(default_factory=frozenset)

    # 是否允许工具自动扩展
    # ERPAgent: True（隐藏的远程 erp_* 工具按需注入）
    # ScheduledTaskAgent: False（启动即 13 工具全可见）
    enable_tool_expansion: bool = False

    # 是否强制至少调用一次工具
    # ERPAgent: True（用户问 ERP 问题，模型不查数据直接编结果是错误的）
    # ScheduledTaskAgent: False（任务可能就是"打个招呼"，无需工具）
    # True 时，模型在调任何工具前直接输出文本会被强制再走一轮（最多 2 次）
    force_tool_use_first: bool = True


# ============================================================
# 结果
# ============================================================

@dataclass
class LoopResult:
    """工具循环执行结果（agent 自己包成对外类型）"""
    text: str
    total_tokens: int
    turns: int
    is_llm_synthesis: bool          # True = LLM 合成的结论；False = 走兜底
    exit_via_ask_user: bool = False  # 是否通过 ask_user 退出


# ============================================================
# Hook 上下文
# ============================================================

@dataclass
class HookContext:
    """Hook 共享上下文（每次 ToolLoopExecutor.run() 构造一份）

    hook 可以读所有字段，可以 mutate `messages`（用于失败反思注入）。
    其他字段被 hook 修改是未定义行为。
    """
    # 不变标识
    db: Any
    user_id: str
    org_id: str
    conversation_id: str
    task_id: Optional[str]
    request_ctx: Any  # utils.time_context.RequestContext

    # 运行时状态（loop 执行过程中持续更新）
    turn: int = 0  # 当前轮次（从 1 开始）
    messages: List[Dict[str, Any]] = field(default_factory=list)
    tools_called: List[str] = field(default_factory=list)
    selected_tools: List[Dict[str, Any]] = field(default_factory=list)
    budget: Any = None  # ExecutionBudget
