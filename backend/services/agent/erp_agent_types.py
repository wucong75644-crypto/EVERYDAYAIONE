"""
ERP Agent 类型定义、常量与工具函数

从 erp_agent.py 拆分，保持主文件 <500 行。
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List


# ============================================================
# ERP Agent 结果
# ============================================================

@dataclass
class ERPAgentResult:
    """ERP Agent 执行结果"""
    text: str                       # 结论文本（给主 Agent 的精简版）
    full_text: str = ""             # 完整文本（给用户的详细版）
    status: str = "success"         # success | partial | error | timeout
    tokens_used: int = 0            # 消耗的总 tokens
    turns_used: int = 0             # 内部轮次数
    tools_called: List[str] = field(default_factory=list)  # 调用过的工具名
    is_truncated: bool = False      # 结果是否被截断


# ============================================================
# 上下文筛选
# ============================================================

def filter_erp_context(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从主 Agent 的 messages 中筛选 ERP 相关上下文

    筛选规则：
    - user 消息：全部保留
    - assistant + erp_agent 工具调用：保留
    - assistant 其他工具调用：跳过
    - tool 结果：保留
    - system 消息：跳过（ERP Agent 有自己的系统提示词）
    """
    result: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "system":
            continue
        if role == "user":
            result.append(msg)
        elif role == "assistant":
            tool_calls = msg.get("tool_calls", [])
            if not tool_calls:
                result.append(msg)
            elif any(
                tc.get("function", {}).get("name") == "erp_agent"
                for tc in tool_calls
            ):
                result.append(msg)
        elif role == "tool":
            result.append(msg)
    return result


# ============================================================
# 安全护栏常量
# ============================================================

TOOL_TIMEOUT = 30.0  # 单个工具最大超时（秒），实际超时由 ExecutionBudget 动态计算
MAX_TOTAL_TOKENS = 50000  # Token 预算上限
ERP_AGENT_DEADLINE = 120.0  # ERP Agent 总执行时间预算（秒）
MAX_ERP_TURNS = 20  # 工具循环最大轮次


# ============================================================
# 上下文超限检测
# ============================================================

_CONTEXT_LENGTH_RE = re.compile(
    r"context.?length|too.?long|maximum.?context|token.?limit|"
    r"max.?token|context.?window|input.?too.?large",
    re.IGNORECASE,
)


def is_context_length_error(error: Exception) -> bool:
    """判断异常是否为上下文超限错误（适配器无专用异常，只能关键词匹配）"""
    return bool(_CONTEXT_LENGTH_RE.search(str(error)))
