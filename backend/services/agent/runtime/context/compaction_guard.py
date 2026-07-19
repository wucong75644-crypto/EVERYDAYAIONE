"""当前 Run 压缩的 single-flight 与失败抑制。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from typing import Any


_MAX_SUPPRESSED_PREFIXES = 1_024
_in_flight: set[tuple[str, str]] = set()
_suppressed: OrderedDict[tuple[str, str], None] = OrderedDict()
_state_lock = asyncio.Lock()


def compaction_prefix_fingerprint(
    messages: list[dict[str, Any]],
    indices: list[int],
) -> str:
    """为实际待替换消息生成稳定、不含正文的 SHA-256 指纹。"""
    payload = [messages[index] for index in indices]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def acquire_loop_compaction(
    scope: str,
    prefix_fingerprint: str,
) -> str:
    """尝试取得本 Run 压缩资格，返回 acquired/in_flight/suppressed。"""
    key = (scope, prefix_fingerprint)
    async with _state_lock:
        if key in _suppressed:
            return "suppressed"
        if key in _in_flight:
            return "in_flight"
        _in_flight.add(key)
    return "acquired"


async def finish_loop_compaction(
    scope: str,
    prefix_fingerprint: str,
    *,
    suppress: bool,
) -> None:
    """释放 in-flight；失败时仅抑制同 Run 的相同前缀。"""
    key = (scope, prefix_fingerprint)
    async with _state_lock:
        _in_flight.discard(key)
        if not suppress:
            return
        _suppressed[key] = None
        _suppressed.move_to_end(key)
        while len(_suppressed) > _MAX_SUPPRESSED_PREFIXES:
            _suppressed.popitem(last=False)


async def clear_loop_compaction_scope(scope: str) -> None:
    """Run 结束时清除该 scope 的失败抑制状态。"""
    async with _state_lock:
        for key in [item for item in _suppressed if item[0] == scope]:
            _suppressed.pop(key, None)
        _in_flight.difference_update(
            [item for item in _in_flight if item[0] == scope]
        )
