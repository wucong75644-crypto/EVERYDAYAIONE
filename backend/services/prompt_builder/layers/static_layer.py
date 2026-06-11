"""Layer 1: 静态层 —— 永久不变的 system prompt 内容。

包含 5 段模板（templates/*.md）按 XML 包裹后拼接：
  <role>...</role>           角色 + 工作场景
  <rules>...</rules>         做事原则 + 行动边界
  <workflow>...</workflow>   直接 / 计划 / 提问 三模式
  <tool_strategy>...</tool_strategy>  工具触发策略 + 数字 cite 约束 + 业务规则
  <permission_mode>...</permission_mode>  auto / plan / ask 模式约束

该层内容长期不变，命中 prompt cache 长期段，所有请求共享。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"

_SECTION_TAGS = (
    ("role", "role.md"),
    ("rules", "rules.md"),
    ("workflow", "workflow.md"),
    ("tool_strategy", "tool_strategy.md"),
    ("permission_mode", "modes.md"),
)


@lru_cache(maxsize=1)
def _read_template(filename: str) -> str:
    """读取 templates/<filename>.md，进程级缓存。"""
    path = _TEMPLATE_DIR / filename
    return path.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def render_static_system() -> str:
    """渲染 Layer 1 完整内容。

    返回 XML 包裹的多段拼接结果，进程内永远不变。

    格式:
    <role>
    ...
    </role>

    <rules>
    ...
    </rules>
    ...
    """
    sections = []
    for tag, filename in _SECTION_TAGS:
        body = _read_template(filename)
        sections.append(f"<{tag}>\n{body}\n</{tag}>")
    return "\n\n".join(sections)


class StaticLayer:
    """Layer 1 渲染器，无状态，render() 进程级缓存。"""

    @staticmethod
    def render() -> str:
        return render_static_system()
