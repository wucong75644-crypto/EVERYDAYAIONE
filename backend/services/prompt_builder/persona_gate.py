"""Persona 显示门控。

决定 AI 自动学习的 user persona 是否注入 prompt。

行业证据 (arXiv 2311.10054): AI 自动生成的 persona 对客观任务"基本随机", 常拖累准确率。
ChatGPT 的实现是双轨制 + 用户可见可关。我们对齐此模式:

  1. 用户级别开关 (memory_persona_enabled): 用户可全局关闭 AI persona 注入
  2. 默认开启 (保持现状行为, 避免一刀切回归)
  3. 后续可扩展: 按 query 相关性 score 二次过滤 (远期)

接入点: PromptBuilder 在调用 DynamicLayer 前调 gate.should_inject() 决定是否传 persona。
"""

from __future__ import annotations

from typing import Optional


class PersonaGate:
    """Persona 注入门控。"""

    def __init__(self, enabled: bool = True):
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def should_inject(self, persona_text: Optional[str]) -> bool:
        """是否应该注入此 persona。

        当前规则:
          1. 总开关关闭 → False
          2. persona 为空 → False
          3. 其他 → True

        未来扩展: 加入 query 相关性 score 二次过滤。
        """
        if not self._enabled:
            return False
        if not persona_text or not persona_text.strip():
            return False
        return True

    def filter(self, persona_text: Optional[str]) -> Optional[str]:
        """过滤后的 persona, 不应注入则返回 None。"""
        return persona_text if self.should_inject(persona_text) else None


# 默认全局 gate (开启), 测试可注入自定义 gate
_default_gate = PersonaGate(enabled=True)


def default_gate() -> PersonaGate:
    return _default_gate
