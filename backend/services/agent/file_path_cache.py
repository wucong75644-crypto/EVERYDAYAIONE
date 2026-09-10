"""
会话级文件路径缓存 + 归一化匹配 + 三字段注册表

注册表结构：每个文件三个字段
- name: 显示名（原始文件名）
- workspace: 工作区绝对路径（原始文件位置）
- parquet: staging 里的 parquet 路径（file_analyze 后才有）

归一化规则：NFKC + 只保留中文/字母/数字 + 扩展名点
匹配策略：完整路径 → 唯一名字 → 唯一归一化 → 唯一 stem → 唯一前缀

生命周期：对话级，TTL 7 天 + 数量上限 1000。
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from threading import Lock
from typing import Any, Optional

from loguru import logger


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


# ============================================================
# 三字段文件条目
# ============================================================

class FileEntry:
    """文件条目：name + workspace + parquet + analyzed

    analyzed: 是否已被 file_analyze 处理过（跨轮持久），驱动 attachments status 切换：
        False → "未分析。如需查询数据，调用 file_analyze(...)。"
        True  → "已分析。直接用 code_execute + get_file + duckdb 查询。"
    """
    __slots__ = ("name", "workspace", "parquet", "analyzed", "source_version")

    def __init__(
        self, name: str, workspace: str = "", parquet: str = "",
        analyzed: bool = False,
    ) -> None:
        self.name = name
        self.workspace = workspace
        self.parquet = parquet
        self.analyzed = analyzed
        self.source_version = None

    def refresh(self):
        from services.file_resources import file_version
        try:
            version = file_version(Path(self.workspace)) if self.workspace else None
        except (OSError, ValueError):
            version = None
        if self.source_version != version:
            self.parquet = ""
            self.analyzed = False
            self.source_version = version

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "workspace": self.workspace, "parquet": self.parquet}


class FilePathCache:
    """会话级文件路径缓存 — 三字段注册表 + 归一化匹配 + get_file 自检"""

    __slots__ = ("_entries", "_paths", "_files", "_max", "_staging_dir")

    def __init__(self, max_entries: int = 500) -> None:
        # 完整路径标识文件；名字只是别名，可能对应多个文件。
        self._entries: dict[str, set[FileEntry]] = {}
        self._paths: dict[str, set[FileEntry]] = {}
        self._files: list[FileEntry] = []
        self._max = max_entries
        self._staging_dir: str = ""

    def set_staging_dir(self, staging_dir: str) -> None:
        """设置 staging 目录（供 write_manifest 写入）"""
        self._staging_dir = staging_dir

    def register(
        self, rel_path: str,
        workspace: str = "", parquet: str = "",
    ) -> None:
        """注册文件。三字段：name + workspace + parquet。

        重复注册同一文件时：
        - workspace/parquet 非空才更新（不覆盖已有值）
        - 防止后续 register 把 analyze 后的 parquet 清空
        """
        filename = os.path.basename(rel_path)
        # 只有相同工作区路径才能合并；归一化文件名不代表文件身份。
        entry = next((f for f in self._files if workspace and f.workspace == workspace), None)
        if entry is None:
            candidates = self._paths.get(rel_path, self._entries.get(rel_path, set()))
            if not workspace and len(candidates) > 1:
                return  # 无完整路径的歧义更新不能选择任意文件。
            compatible = {f for f in candidates if not workspace or not f.workspace}
            entry = self._unique(compatible)
        if entry is None:
            if self._max <= 0:
                return
            if len(self._files) >= self._max:
                self._evict_oldest()
            entry = FileEntry(name=filename)
            self._files.append(entry)
        if workspace:
            entry.workspace = workspace
            entry.refresh()
        if parquet:
            entry.parquet = parquet
        # 拿到目录限定路径后，先前登记的 basename 仅保留为兼容别名。
        # 否则子目录 report.csv 的别名会遮住根目录真正的 report.csv。
        if os.path.dirname(rel_path) and not os.path.isabs(rel_path):
            bare_paths = self._paths.get(filename)
            if bare_paths:
                bare_paths.discard(entry)
                if not bare_paths:
                    del self._paths[filename]
            self._paths.setdefault(rel_path, set()).add(entry)
        elif os.path.isabs(rel_path) or not any(
            entry in candidates and os.path.dirname(key) and not os.path.isabs(key)
            for key, candidates in self._paths.items()
        ):
            self._paths.setdefault(rel_path, set()).add(entry)
        # 重复登记仍补全每个键，避免先登记 basename 后丢失 rel_path。
        for key in (rel_path, filename, entry.workspace):
            if key:
                self._entries.setdefault(key, set()).add(entry)

    @staticmethod
    def _unique(entries: set[FileEntry]) -> Optional[FileEntry]:
        return next(iter(entries)) if len(entries) == 1 else None

    def _evict_oldest(self) -> None:
        entry = self._files.pop(0)
        for index in (self._entries, self._paths):
            for key, candidates in list(index.items()):
                candidates.discard(entry)
                if not candidates:
                    del index[key]

    def registered_paths(self) -> tuple[tuple[str, FileEntry], ...]:
        """供 ID 解析使用的唯一精确键快照，不含歧义名字或模糊匹配。"""
        return tuple(
            (key, next(iter(candidates)))
            for key, candidates in (self._entries | self._paths).items()
            if len(candidates) == 1
        )

    def set_parquet(self, filename: str, parquet_path: str) -> None:
        """设置 parquet 路径（file_analyze 完成后调用）。"""
        entry = self._resolve_entry(filename)
        if entry:
            entry.parquet = parquet_path

    def set_analyzed(self, filename: str, analyzed: bool = True) -> None:
        """标记文件已被 file_analyze 处理过（驱动 attachments status 切换）。"""
        entry = self._resolve_entry(filename)
        if entry:
            entry.analyzed = analyzed

    def is_analyzed(self, filename: str) -> bool:
        """查询文件是否已被 file_analyze 处理过。"""
        entry = self._resolve_entry(filename)
        return entry.analyzed if entry else False

    def resolve_path(self, name: str, usage: str = "code") -> str:
        """按文件名 + 用途获取路径，含自检拦截。

        usage:
            "code"    → 返回 parquet（沙盒 duckdb 查询用）
            "analyze" → 返回 workspace（file_analyze 源文件 / file_search 命中图片走多模态时复用）
            "delete"  → 返回 workspace（file_delete 删除源文件）

        自检拦截：
            - 文件未注册 → FileNotFoundError
            - code 但没 parquet → FileNotFoundError（提示调 file_analyze）
            - 路径文件不存在 → FileNotFoundError（提示重新操作）
        """
        entry = self._resolve_entry(name)
        if not entry:
            raise FileNotFoundError(
                f"文件 '{name}' 未注册，请先用 file_search 搜索文件"
            )

        if usage == "code":
            path = entry.parquet
            if not path:
                raise FileNotFoundError(
                    f"文件 '{entry.name}' 尚未分析，"
                    f"请先调用 file_analyze(path=\"{entry.name}\")"
                )
        else:
            path = entry.workspace
            if not path:
                raise FileNotFoundError(
                    f"文件 '{entry.name}' 缺少工作区路径"
                )

        if not os.path.exists(path):
            if usage == "code":
                raise FileNotFoundError(
                    f"Parquet 缓存已失效，"
                    f"请重新调用 file_analyze(path=\"{entry.name}\")"
                )
            raise FileNotFoundError(f"文件不存在: {path}")

        return path

    def resolve(self, name: str, usage: str = "code") -> Optional[str]:
        """按文件名查路径（不拦截，返回 None 表示未找到）。

        供 _resolve_file_ids 等需要静默失败的场景使用。
        """
        entry = self._resolve_entry(name)
        if not entry:
            return None
        if usage == "code":
            return entry.parquet or None
        return entry.workspace or None

    def _resolve_entry(self, name: str) -> Optional[FileEntry]:
        """四级递进匹配查找 FileEntry。"""
        for entry in self._files:
            entry.refresh()
        # 1. 精确匹配
        if name in self._paths:
            return self._unique(self._paths[name])
        if name in self._entries:
            return self._unique(self._entries[name])
        if os.path.dirname(name):
            return None
        basename = os.path.basename(name)
        if basename in self._entries:
            return self._unique(self._entries[basename])

        # 2. 归一化匹配
        norm_input = normalize_filename(name)
        normalized = [(normalize_filename(f.name), f) for f in self._files]
        matches = {f for key, f in normalized if key == norm_input}
        if matches:
            return self._unique(matches)

        # 3. Stem 匹配（用户没带扩展名）
        input_stem = os.path.splitext(norm_input)[0]
        if input_stem:
            matches = {f for key, f in normalized if os.path.splitext(key)[0] == input_stem}
            if matches:
                return self._unique(matches)

        # 4. 前缀匹配（LLM 截断文件名，≥6 字符防误匹配）
        if input_stem and len(input_stem) >= 6:
            matches = set()
            for norm_key, entry in normalized:
                registered_stem = os.path.splitext(norm_key)[0]
                if registered_stem.startswith(input_stem) or input_stem.startswith(registered_stem):
                    matches.add(entry)
            return self._unique(matches)

        return None

    def get_filename(self, name: str) -> Optional[str]:
        """按 key 查原始文件名。"""
        entry = self._unique(self._paths.get(name, self._entries.get(name, set())))
        return entry.name if entry else None

    def list_all(self) -> list[dict[str, str]]:
        """返回所有去重的文件条目。"""
        return [entry.to_dict() for entry in self._files]

    # write_manifest 已删除(路径协议改造)
    # 原用途:为沙盒 get_file 函数生成名字→路径映射表
    # 新协议:attachments XML 直接给完整相对路径字符串,AI 字面 copy,无需查表
    # file_path_cache 仍保留三字段注册 + 4 级匹配,供 file_search/file_delete 等后端工具使用


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
