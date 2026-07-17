"""通道无关 Chat 执行事件出口。"""

from __future__ import annotations

from typing import Any, Protocol


class ExecutionSink(Protocol):
    """执行内核的可选过程事件接收器。"""

    async def start(self) -> None:
        """生成开始。"""

    async def on_text(self, text: str) -> None:
        """接收模型文本增量。"""

    async def on_thinking(self, text: str) -> None:
        """接收模型思考增量。"""

    async def on_block(self, block: dict[str, Any]) -> None:
        """接收已形成的结构化内容块。"""

    async def flush(self) -> None:
        """提交剩余过程状态并结束流。"""


class CollectingExecutionSink:
    """企微和 Actor 使用的无副作用收集器。"""

    def __init__(self) -> None:
        self.text = ""
        self.thinking = ""
        self.blocks: list[dict[str, Any]] = []

    async def start(self) -> None:
        return None

    async def on_text(self, text: str) -> None:
        self.text += text

    async def on_thinking(self, text: str) -> None:
        self.thinking += text

    async def on_block(self, block: dict[str, Any]) -> None:
        self.blocks.append(block)

    async def flush(self) -> None:
        return None
