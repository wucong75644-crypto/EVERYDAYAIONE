"""
Agent Loop 数据结构与安全护栏

提供 AgentLoop 的核心数据类型：
- AgentResult: Agent Loop 执行结果
- PendingAsyncTool: 待分发的异步工具调用
- AgentGuardrails: 安全护栏（循环检测/token预算/轮次限制）
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from schemas.message import GenerationType


@dataclass
class AgentResult:
    """Agent Loop 执行结果"""

    generation_type: GenerationType
    model: str = ""
    system_prompt: Optional[str] = None
    search_context: Optional[str] = None
    tool_params: Dict[str, Any] = field(default_factory=dict)
    batch_prompts: Optional[List[Dict[str, Any]]] = None
    direct_reply: Optional[str] = None
    render_hints: Optional[Dict[str, Any]] = None
    turns_used: int = 1
    total_tokens: int = 0
    routed_by: str = "agent_loop"


@dataclass
class PendingAsyncTool:
    """待分发的异步工具调用"""

    tool_name: str
    arguments: Dict[str, Any]


class AgentGuardrails:
    """安全护栏 — 用代码实现，不依赖提示词"""

    def __init__(self, max_turns: int = 3, max_total_tokens: int = 3000) -> None:
        self.max_turns = max_turns
        self.max_total_tokens = max_total_tokens
        self.tokens_used: int = 0
        self._recent_calls: List[str] = []

    def detect_loop(self, tool_name: str, arguments: Dict[str, Any]) -> bool:
        """检测连续相同调用（如连续 3 次相同 web_search）"""
        args_hash = hashlib.md5(
            json.dumps(arguments, sort_keys=True).encode()
        ).hexdigest()[:8]
        key = f"{tool_name}:{args_hash}"
        self._recent_calls.append(key)
        if len(self._recent_calls) >= 3:
            if len(set(self._recent_calls[-3:])) == 1:
                return True
        return False

    def should_abort(self) -> bool:
        """是否超出 token 预算"""
        return self.tokens_used >= self.max_total_tokens

    def add_tokens(self, tokens: int) -> None:
        self.tokens_used += tokens
