"""Layer 2a: 会话稳定层 —— 整会话不变的内容, prompt cache 友好.

v2 设计:
  把原 DynamicLayer 拆成两半:
    - L2a (本文件): 整会话不变 (permission_mode / user_preferences / persona / memory)
    - L2b (TurnDynamicLayer): 每条新 user 才变 (current_time)

  为什么拆:
    千问 prompt cache 是 prefix 字节哈希, 任何字节漂移作废 cache.
    把"整会话稳定"和"每次变"混在一起 → time 每次变破坏 cache.
    拆开后 L2a 命中 cache, L2b 每次重算 (小, 几十字符).

  mem0 改造 (v2 阶段 4): 召回结果只在会话首次拉一次, 整会话固定,
  所以 user_facts + user_memory 应放 L2a 而不是 L2b.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SessionStableContext:
    """L2a 输入数据 - 整会话不变."""

    permission_mode: str = "auto"               # 'auto' | 'plan' | 'ask'
    user_preferences: Optional[str] = None      # Custom Instructions (用户手写)
    user_facts: Optional[str] = None            # mem0 短事实清单 (已过 PersonaGate)
    user_memory: Optional[str] = None           # mem0 召回 (按会话首条 query, 一次性)


def _xml_section(tag: str, body: str) -> str:
    """生成 <tag>\\n body \\n</tag>, body 已 strip."""
    return f"<{tag}>\n{body.strip()}\n</{tag}>"


class SessionStableLayer:
    """Layer 2a 渲染器, 按存在与否拼接片段."""

    @staticmethod
    def render(ctx: SessionStableContext) -> str:
        sections: list[str] = []

        # <context> 仅含 permission_mode (整会话不变的运行时配置)
        sections.append(
            f"<context>\n<permission_mode>{ctx.permission_mode}</permission_mode>\n</context>"
        )

        # 用户偏好 (Custom Instructions, 可空)
        if ctx.user_preferences and ctx.user_preferences.strip():
            sections.append(_xml_section("user_preferences", ctx.user_preferences))

        # mem0 短事实 (已过 gate, 进来即注入)
        if ctx.user_facts and ctx.user_facts.strip():
            sections.append(_xml_section("user_facts", ctx.user_facts))

        # mem0 召回 (会话首次拉一次, 整会话固定)
        if ctx.user_memory and ctx.user_memory.strip():
            sections.append(_xml_section("user_memory", ctx.user_memory))

        return "\n\n".join(sections)
