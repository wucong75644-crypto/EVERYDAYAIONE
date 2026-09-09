"""Bind existing internal handlers; never call ToolExecutor.execute."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from services.agent.tool_executor import ToolExecutor


class LegacyToolHandler:
    def __init__(self, executor: ToolExecutor, handler_key: str) -> None:
        self._handler = executor._handlers.get(handler_key)
        if self._handler is None:
            raise ValueError(f"Unknown sync tool: {handler_key}")

    async def __call__(self, arguments: dict[str, Any]) -> Any:
        return await self._handler(arguments)


def build_legacy_handlers(executor: ToolExecutor) -> dict[tuple[str, str], LegacyToolHandler]:
    """Bind one request's executor, including custom tools installed by that request."""
    return {("legacy", key): LegacyToolHandler(executor, key) for key in executor._handlers}
