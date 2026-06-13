"""Layer 2b: 本轮动态层 —— 每条新 user 才变, 不参与 cache.

v2 设计 (跟 SessionStableLayer 配对):
  - L2a 整会话不变 → cache 命中
  - L2b 本轮动态 → 不 cache

  L2b 当前只放 current_time. 未来如果有"本条 query 触发的实时数据"也在这里.
  保持 L2b 小 (几十字符) 是关键, 这样每次重算成本可忽略.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TurnDynamicContext:
    """L2b 输入数据 - 每条新 user 都变."""

    current_time_text: str                       # RequestContext.for_prompt_injection() 输出
    user_location: Optional[str] = None          # 极少用, 默认 None


class TurnDynamicLayer:
    """Layer 2b 渲染器."""

    @staticmethod
    def render(ctx: TurnDynamicContext) -> str:
        lines = [f"<current_time>{ctx.current_time_text.strip()}</current_time>"]
        if ctx.user_location:
            lines.append(f"<user_location>{ctx.user_location.strip()}</user_location>")
        return "<turn>\n" + "\n".join(lines) + "\n</turn>"
