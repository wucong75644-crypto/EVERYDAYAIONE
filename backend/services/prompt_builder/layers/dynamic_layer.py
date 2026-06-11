"""Layer 2: 动态层 —— 每次请求变化的 system prompt 内容。

包含 5 段（按存在与否注入）:
  <context>
    <current_time>...</current_time>
    <user_location>...</user_location>     可选
    <permission_mode>auto/plan/ask</permission_mode>
  </context>
  <user_preferences>...</user_preferences>  用户手写, 可空
  <user_profile>...</user_profile>         AI 学的 persona, 已经过 gate
  <relevant_memory>...</relevant_memory>   L1 prepend, 已过千问评分

本层不参与 prompt cache (每次重算)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DynamicContext:
    """动态层输入数据。"""

    current_time_text: str                # RequestContext.for_prompt_injection() 输出
    permission_mode: str = "auto"         # 'auto' | 'plan' | 'ask'
    user_location: Optional[str] = None   # 来自前端定位
    user_preferences: Optional[str] = None  # Custom Instructions
    persona: Optional[str] = None         # AI persona, 已 gate
    relevant_memory: Optional[str] = None # L1 prepend, 已过千问精排


def _xml_section(tag: str, body: str) -> str:
    """生成 <tag>\\n body \\n</tag>，body 已 strip。"""
    return f"<{tag}>\n{body.strip()}\n</{tag}>"


class DynamicLayer:
    """Layer 2 渲染器，按存在与否拼接片段。"""

    @staticmethod
    def render(ctx: DynamicContext) -> str:
        sections: list[str] = []

        # <context> 块: 时间 + 位置 + permission_mode
        context_lines = [f"<current_time>{ctx.current_time_text.strip()}</current_time>"]
        if ctx.user_location:
            context_lines.append(
                f"<user_location>{ctx.user_location.strip()}</user_location>"
            )
        context_lines.append(
            f"<permission_mode>{ctx.permission_mode}</permission_mode>"
        )
        sections.append("<context>\n" + "\n".join(context_lines) + "\n</context>")

        # 用户偏好 (Custom Instructions, 可空)
        if ctx.user_preferences and ctx.user_preferences.strip():
            sections.append(_xml_section("user_preferences", ctx.user_preferences))

        # AI persona (已经过 persona_gate, 进来即注入)
        if ctx.persona and ctx.persona.strip():
            sections.append(_xml_section("user_profile", ctx.persona))

        # L1 相关记忆 (已经过千问精排, 进来即注入)
        if ctx.relevant_memory and ctx.relevant_memory.strip():
            sections.append(_xml_section("relevant_memory", ctx.relevant_memory))

        return "\n\n".join(sections)
