"""Small compatibility helper for scoped async and legacy sync RPC clients."""

from __future__ import annotations

import inspect
from typing import Any, Mapping


async def execute_rpc(
    database: Any, name: str, params: Mapping[str, object],
) -> Any:
    """Execute one RPC without changing the existing query implementation."""
    result = database.rpc(name, dict(params)).execute()
    if inspect.isawaitable(result):
        return await result
    return result


__all__ = ["execute_rpc"]
