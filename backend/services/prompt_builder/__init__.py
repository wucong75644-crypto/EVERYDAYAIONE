"""PromptBuilder 模块: 统一 system prompt 构造入口。

设计文档: docs/document/TECH_PromptBuilder架构重构.md

替代 chat_context_mixin._build_llm_messages 的 11 处碎片化注入。

公共接口:
  - PromptBuilder: 主入口类
  - BuildInput / BuildResult: 输入输出数据
  - StaticLayer / DynamicLayer / UserLayer: 各层渲染器 (测试可单独调用)
  - PersonaGate: persona 注入门控
"""

from services.prompt_builder.builder import (
    BuildInput,
    BuildResult,
    PromptBuilder,
)
from services.prompt_builder.layers.dynamic_layer import (
    DynamicContext,
    DynamicLayer,
)
from services.prompt_builder.layers.static_layer import StaticLayer
from services.prompt_builder.layers.user_layer import UserLayer, UserMessageInput
from services.prompt_builder.persona_gate import PersonaGate, default_gate

__all__ = [
    "BuildInput",
    "BuildResult",
    "PromptBuilder",
    "StaticLayer",
    "DynamicContext",
    "DynamicLayer",
    "UserLayer",
    "UserMessageInput",
    "PersonaGate",
    "default_gate",
]
