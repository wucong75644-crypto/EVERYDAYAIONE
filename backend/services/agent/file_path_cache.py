"""
会话级文件路径缓存 + 归一化匹配

所有文件出现时（上传/@插入/file_search/file_analyze产出/code_execute产出）
注册到此缓存。

LLM 全链路用文件名引用文件：
- 调工具：path="销售报表.xlsx" → 归一化匹配 → 正确绝对路径
- 写代码：get_file('销售报表.xlsx') → 归一化匹配 → 正确绝对路径
- file_analyze 后原始文件名自动指向 parquet 路径

归一化规则：NFKC + 只保留中文/字母/数字 + 扩展名点
匹配策略：精确 → 归一化 → stem（无扩展名）→ 前缀（截断）

生命周期：对话级，TTL 7 天 + 数量上限 1000。
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from threading import Lock
from typing import Optional


# ============================================================
# 归一化函数（公共，sandbox_worker 复制一份）
# ============================================================

def normalize_filename(name: str) -> str:
    """归一化文件名：NFKC + 只保留中文/字母/数字 + 扩展名点。

    '4月 销售-分析.xlsx' → '4月销售分析.xlsx'
    '利润表（1-4月）.csv' → '利润表14月.csv'
    """
    stem, ext = os.path.splitext(name)
    stem = unicodedata.normalize("NFKC", stem)
    stem = re.sub(r'[^\u4e00-\u9fff\da-zA-Z]', '', stem)
    return (stem + ext).lower()


class FilePathCache:
    """会话级文件路径缓存 — 归一化匹配 + 路径查询"""

    __slots__ = ("_entries", "_normalized", "_max", "_staging_dir")

    def __init__(self, max_entries: int = 500) -> None:
        # {key: (filename, abs_path)}  key = rel_path / filename
        self._entries: dict[str, tuple[str, str]] = {}
        # {归一化文件名: (原始文件名, abs_path)}  归一化匹配用
        self._normalized: dict[str, tuple[str, str]] = {}
        self._max = max_entries
        self._staging_dir: str = ""

    def set_staging_dir(self, staging_dir: str) -> None:
        """设置 staging 目录（供 write_manifest 写入）"""
        self._staging_dir = staging_dir

    def register(self, rel_path: str, abs_path: str) -> None:
        """注册文件。按相对路径、纯文件名、归一化文件名三个维度存储。"""
        filename = os.path.basename(rel_path)
        entry = (filename, abs_path)

        if len(self._entries) >= self._max:
            first_key = next(iter(self._entries))
            del self._entries[first_key]

        # 按相对路径 + 纯文件名存储
        self._entries[rel_path] = entry
        self._entries[filename] = entry
        # 归一化 key 存储（容错匹配用）
        norm_key = normalize_filename(filename)
        self._normalized[norm_key] = entry

    def update_path(self, filename: str, new_abs_path: str) -> None:
        """更新文件名对应的路径（file_analyze 后 xlsx → parquet）。

        直接替换，LLM 用同一个文件名始终拿到最新路径。
        """
        norm_key = normalize_filename(filename)
        old_entry = self._normalized.get(norm_key)
        if not old_entry:
            return
        orig_filename = old_entry[0]
        new_entry = (orig_filename, new_abs_path)
        # 更新所有维度
        self._normalized[norm_key] = new_entry
        self._entries[orig_filename] = new_entry
        # 同时更新可能存在的 rel_path key
        for key, val in self._entries.items():
            if val[0] == orig_filename and val[1] != new_abs_path:
                self._entries[key] = new_entry

    def resolve(self, name: str) -> Optional[str]:
        """按文件名查绝对路径。四级递进匹配。

        1. 精确匹配
        2. 归一化匹配（去符号后比较）
        3. Stem 匹配（去扩展名后归一化比较，用户可能没带扩展名）
        4. 前缀匹配（归一化后是前缀，≥6字符，LLM 截断时兜底）
        """
        # 1. 精确匹配
        entry = self._entries.get(name)
        if entry:
            return entry[1]
        # basename 兜底
        basename = os.path.basename(name)
        entry = self._entries.get(basename)
        if entry:
            return entry[1]

        # 2. 归一化匹配
        norm_input = normalize_filename(name)
        entry = self._normalized.get(norm_input)
        if entry:
            return entry[1]

        # 3. Stem 匹配（用户没带扩展名）
        input_stem = os.path.splitext(norm_input)[0]
        if input_stem:
            for norm_key, entry in self._normalized.items():
                registered_stem = os.path.splitext(norm_key)[0]
                if input_stem == registered_stem:
                    return entry[1]

        # 4. 前缀匹配（LLM 截断文件名，≥6 字符防误匹配）
        if len(input_stem) >= 6:
            for norm_key, entry in self._normalized.items():
                registered_stem = os.path.splitext(norm_key)[0]
                if registered_stem.startswith(input_stem) or input_stem.startswith(registered_stem):
                    return entry[1]

        return None

    def get_filename(self, name: str) -> Optional[str]:
        """按 key 查原始文件名。"""
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

    def write_manifest(self) -> None:
        """把文件名→绝对路径映射写入 staging/_manifest.json。

        供沙盒子进程的 get_file() 读取。
        在 code_execute 执行前调用一次，包含当前全量映射。
        写入两份：原始文件名 + 归一化文件名，沙盒也能做归一化匹配。
        """
        if not self._staging_dir:
            return
        manifest: dict[str, str] = {}
        seen_paths: set[str] = set()
        # 原始文件名 → 路径
        for filename, abs_path in self._entries.values():
            if abs_path not in seen_paths:
                seen_paths.add(abs_path)
                manifest[filename] = abs_path
        # 归一化文件名 → 路径（沙盒归一化匹配用）
        for norm_key, (_, abs_path) in self._normalized.items():
            if norm_key not in manifest:
                manifest[norm_key] = abs_path
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
_caches: dict[str, tuple[float, FilePathCache]] = {}
_MAX_CONVERSATIONS = 1000
_TTL_SECONDS = 7 * 24 * 3600  # 7 天


def get_file_cache(conversation_id: str) -> FilePathCache:
    """获取或创建对话级缓存。TTL 7 天 + 数量上限 1000。"""
    with _lock:
        entry = _caches.get(conversation_id)
        now = _time.time()

        if entry is not None:
            last_access, cache = entry
            if now - last_access > _TTL_SECONDS:
                del _caches[conversation_id]
            else:
                _caches[conversation_id] = (now, cache)
                return cache

        if len(_caches) >= _MAX_CONVERSATIONS:
            oldest_key = min(_caches, key=lambda k: _caches[k][0])
            del _caches[oldest_key]

        cache = FilePathCache()
        _caches[conversation_id] = (now, cache)
        return cache
