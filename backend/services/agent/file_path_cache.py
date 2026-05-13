"""
会话级文件路径缓存

file_search / code_execute / file_delete 等工具共享。
file_search 扫到文件后注册，后续工具按文件名或相对路径查询精确的绝对路径。

生命周期：对话级，随对话结束自动释放（LRU 淘汰最老对话）。
"""

from __future__ import annotations

import os
from threading import Lock
from typing import Optional


class FilePathCache:
    """会话级文件路径缓存 — 存储文件名 + 绝对路径"""

    __slots__ = ("_entries", "_max")

    def __init__(self, max_entries: int = 500) -> None:
        # {key: (filename, abs_path)}  key = rel_path 或 filename
        self._entries: dict[str, tuple[str, str]] = {}
        self._max = max_entries

    def register(self, rel_path: str, abs_path: str) -> None:
        """注册文件。同时按相对路径和纯文件名两个 key 存储。"""
        filename = os.path.basename(rel_path)
        entry = (filename, abs_path)

        if len(self._entries) >= self._max:
            # 简单淘汰：删最早的一个
            first_key = next(iter(self._entries))
            del self._entries[first_key]

        # 按相对路径存（精确）
        self._entries[rel_path] = entry
        # 按纯文件名存（方便 LLM 只传文件名时查找）
        self._entries[filename] = entry

    def resolve(self, name: str) -> Optional[str]:
        """按文件名或相对路径查绝对路径。"""
        entry = self._entries.get(name)
        if entry:
            return entry[1]  # abs_path
        # basename 兜底：LLM 可能传完整路径但缓存 key 是相对路径
        basename = os.path.basename(name)
        entry = self._entries.get(basename)
        return entry[1] if entry else None

    def get_filename(self, name: str) -> Optional[str]:
        """按 key 查文件名。"""
        entry = self._entries.get(name)
        return entry[0] if entry else None

    def list_all(self) -> list[tuple[str, str]]:
        """返回所有去重的 (filename, abs_path) 列表。"""
        seen: set[str] = set()
        result: list[tuple[str, str]] = []
        for filename, abs_path in self._entries.values():
            if abs_path not in seen:
                seen.add(abs_path)
                result.append((filename, abs_path))
        return result


# ============================================================
# 全局对话级缓存池
# ============================================================

_lock = Lock()
_caches: dict[str, FilePathCache] = {}
_MAX_CONVERSATIONS = 200


def get_file_cache(conversation_id: str) -> FilePathCache:
    """获取或创建对话级缓存。"""
    with _lock:
        cache = _caches.get(conversation_id)
        if cache is not None:
            return cache

        # LRU 淘汰
        if len(_caches) >= _MAX_CONVERSATIONS:
            oldest = next(iter(_caches))
            del _caches[oldest]

        cache = FilePathCache()
        _caches[conversation_id] = cache
        return cache
