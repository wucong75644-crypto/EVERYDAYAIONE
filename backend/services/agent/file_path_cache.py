"""
会话级文件路径缓存 + 编号注册表

所有文件出现时（上传/@插入/file_search/file_analyze产出/code_execute产出）
注册到此缓存，返回短编号（f1/f2/...）。

LLM 全链路用编号引用文件：
- 调工具：path="f1" → get_file 翻译成绝对路径
- 写代码：get_file('f1') → 返回绝对路径
- 对用户说话：用文件名（不暴露编号）

生命周期：对话级，随对话结束自动释放（LRU 淘汰最老对话）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Optional


class FilePathCache:
    """会话级文件路径缓存 — 编号注册表 + 路径查询"""

    __slots__ = (
        "_entries", "_max",
        "_counter", "_id_to_entry", "_path_to_id",
        "_staging_dir",
    )

    def __init__(self, max_entries: int = 500) -> None:
        # 旧结构保留：{key: (filename, abs_path)}  key = rel_path 或 filename
        self._entries: dict[str, tuple[str, str]] = {}
        self._max = max_entries

        # 编号系统
        self._counter: int = 0
        self._id_to_entry: dict[str, tuple[str, str]] = {}   # {f1: (filename, abs_path)}
        self._path_to_id: dict[str, str] = {}                 # {abs_path: f1} 防重复
        self._staging_dir: str = ""

    def set_staging_dir(self, staging_dir: str) -> None:
        """设置 staging 目录（供 write_manifest 写入）"""
        self._staging_dir = staging_dir

    def register(self, rel_path: str, abs_path: str) -> str:
        """注册文件，返回短编号（f1/f2/...）。

        同一 abs_path 不重复分配编号。
        同时按相对路径和纯文件名两个 key 存储（兼容旧查询）。
        """
        filename = os.path.basename(rel_path)
        entry = (filename, abs_path)

        if len(self._entries) >= self._max:
            first_key = next(iter(self._entries))
            del self._entries[first_key]

        # 旧结构：按相对路径 + 纯文件名存储
        self._entries[rel_path] = entry
        self._entries[filename] = entry

        # 编号分配：同一 abs_path 返回已有编号
        normalized = os.path.realpath(abs_path)
        existing_id = self._path_to_id.get(normalized)
        if existing_id:
            # 更新 entry（文件名可能变了，比如同路径不同 rel_path）
            self._id_to_entry[existing_id] = entry
            return existing_id

        self._counter += 1
        file_id = f"f{self._counter}"
        self._id_to_entry[file_id] = entry
        self._path_to_id[normalized] = file_id
        # 编号也作为 key 存入 _entries（让 resolve 统一查）
        self._entries[file_id] = entry
        return file_id

    def update_path(self, file_id: str, new_abs_path: str) -> None:
        """更新编号对应的路径（file_analyze 后把 xlsx 编号指向 parquet）。

        一个文件一个编号，不分裂。
        """
        old_entry = self._id_to_entry.get(file_id)
        if not old_entry:
            return
        old_filename = old_entry[0]
        new_entry = (old_filename, new_abs_path)
        # 更新 _id_to_entry
        self._id_to_entry[file_id] = new_entry
        # 更新 _entries 里编号 key 的路径
        self._entries[file_id] = new_entry
        # 更新 _path_to_id：删旧路径，加新路径
        old_normalized = os.path.realpath(old_entry[1])
        new_normalized = os.path.realpath(new_abs_path)
        if old_normalized in self._path_to_id:
            del self._path_to_id[old_normalized]
        self._path_to_id[new_normalized] = file_id

    def resolve(self, name: str) -> Optional[str]:
        """按编号/文件名/相对路径查绝对路径。

        查找顺序：_entries 精确 → _id_to_entry（防编号被 LRU 淘汰）→ basename 兜底。
        """
        entry = self._entries.get(name)
        if entry:
            return entry[1]
        # 编号可能被 _entries LRU 淘汰，从 _id_to_entry 兜底
        id_entry = self._id_to_entry.get(name)
        if id_entry:
            return id_entry[1]
        # basename 兜底：LLM 可能传完整路径但缓存 key 是相对路径
        basename = os.path.basename(name)
        entry = self._entries.get(basename)
        return entry[1] if entry else None

    def get_filename(self, name: str) -> Optional[str]:
        """按 key 查文件名。"""
        entry = self._entries.get(name)
        return entry[0] if entry else None

    def get_display_name(self, file_id: str) -> Optional[str]:
        """按编号查文件名（用于前端展示翻译）。"""
        entry = self._id_to_entry.get(file_id)
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

    def list_ids(self) -> list[tuple[str, str, str]]:
        """返回所有编号映射：[(file_id, filename, abs_path), ...]"""
        return [
            (fid, entry[0], entry[1])
            for fid, entry in self._id_to_entry.items()
        ]

    def write_manifest(self) -> None:
        """把编号→绝对路径映射写入 staging/_manifest.json。

        供沙盒子进程的 get_file() 读取。
        在 code_execute 执行前调用一次，包含当前全量映射。
        """
        if not self._staging_dir:
            return
        manifest: dict[str, str] = {
            fid: entry[1]
            for fid, entry in self._id_to_entry.items()
        }
        if not manifest:
            return
        staging = Path(self._staging_dir)
        staging.mkdir(parents=True, exist_ok=True)
        manifest_path = staging / "_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )


# ============================================================
# 全局对话级缓存池（TTL 7天 + 数量上限 1000 双重限制）
# ============================================================

import time as _time

_lock = Lock()
_caches: dict[str, tuple[float, FilePathCache]] = {}  # {conv_id: (last_access, cache)}
_MAX_CONVERSATIONS = 1000
_TTL_SECONDS = 7 * 24 * 3600  # 7 天


def get_file_cache(conversation_id: str) -> FilePathCache:
    """获取或创建对话级缓存。TTL 7 天 + 数量上限 1000。"""
    with _lock:
        entry = _caches.get(conversation_id)
        now = _time.time()

        if entry is not None:
            last_access, cache = entry
            # TTL 检查：过期则重建
            if now - last_access > _TTL_SECONDS:
                del _caches[conversation_id]
            else:
                # 更新访问时间
                _caches[conversation_id] = (now, cache)
                return cache

        # 数量上限兜底：淘汰最久未访问的
        if len(_caches) >= _MAX_CONVERSATIONS:
            oldest_key = min(_caches, key=lambda k: _caches[k][0])
            del _caches[oldest_key]

        cache = FilePathCache()
        _caches[conversation_id] = (now, cache)
        return cache
