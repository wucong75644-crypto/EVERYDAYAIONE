"""
对话级文件路径缓存 — 复用 [FILE] 标记设计思想。

解决的问题：
  LLM 重新生成长文件名时加空格导致找不到文件。
  本模块在 file_list/file_search 发现文件时注册 文件名→绝对路径 映射，
  data_query/file_read 执行时查缓存替换，LLM 打的文件名不需要完全精确。

对话级生命周期（复用 session_file_registry 的模块级 dict 模式）：
  - file_list 发现文件 → register()
  - data_query/file_read 解析文件 → resolve()
  - 新对话 → file_list 重新注册
"""
from __future__ import annotations

import time as _time

# ============================================================
# 对话级缓存（模块级 dict，按 conversation_id 隔离）
# ============================================================

_MAX_CONVERSATIONS = 200


class FilePathCache:
    """单个对话的文件路径缓存。

    线程安全：单个对话串行执行工具调用，无并发写入。
    """

    __slots__ = ("_name_to_path", "_normalized_index")

    def __init__(self) -> None:
        self._name_to_path: dict[str, str] = {}      # 文件名 → 绝对路径
        self._normalized_index: dict[str, str] = {}   # 去空格文件名 → 绝对路径

    def register(self, filename: str, abs_path: str) -> None:
        """注册文件。重复文件名覆盖（同名文件取最新路径）。"""
        self._name_to_path[filename] = abs_path
        self._normalized_index[filename.replace(" ", "")] = abs_path

    def resolve(self, filename: str) -> str | None:
        """解析文件名 → 绝对路径。

        优先精确匹配，其次去空格匹配。
        """
        # 精确匹配
        path = self._name_to_path.get(filename)
        if path:
            return path
        # 去空格匹配（LLM 常在中文-数字、连字符两边加空格）
        normalized = filename.replace(" ", "")
        return self._normalized_index.get(normalized)

    @property
    def count(self) -> int:
        return len(self._name_to_path)

    def __repr__(self) -> str:
        names = list(self._name_to_path.keys())[:5]
        return f"FilePathCache({len(self._name_to_path)} files: {names})"


# ============================================================
# 对话级缓存管理
# ============================================================


_caches: dict[str, FilePathCache] = {}
_access_times: dict[str, float] = {}


def get_file_cache(conversation_id: str) -> FilePathCache:
    """获取对话级文件缓存（不存在则创建）。"""
    _access_times[conversation_id] = _time.time()
    if conversation_id not in _caches:
        _caches[conversation_id] = FilePathCache()
        _enforce_lru()
    return _caches[conversation_id]


def _enforce_lru() -> None:
    """超过上限时淘汰最久未访问的对话。"""
    if len(_caches) <= _MAX_CONVERSATIONS:
        return
    sorted_ids = sorted(
        _caches.keys(),
        key=lambda cid: _access_times.get(cid, 0.0),
    )
    evict_count = len(_caches) - _MAX_CONVERSATIONS
    for cid in sorted_ids[:evict_count]:
        _caches.pop(cid, None)
        _access_times.pop(cid, None)
