"""
会话级文件路径缓存 + 归一化匹配 + 三字段注册表

注册表结构：每个文件三个字段
- name: 显示名（原始文件名）
- workspace: 工作区绝对路径（原始文件位置）
- parquet: staging 里的 parquet 路径（file_analyze 后才有）

get_file(name, usage) 按用途返回对应路径 + 自检：
- usage="code"    → 返回 parquet（没有则拦截提示调 file_analyze）
- usage="analyze" → 返回 workspace（源文件）
- usage="delete"  → 返回 workspace

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
    """三字段文件条目：name + workspace + parquet"""
    __slots__ = ("name", "workspace", "parquet")

    def __init__(self, name: str, workspace: str = "", parquet: str = "") -> None:
        self.name = name
        self.workspace = workspace
        self.parquet = parquet

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "workspace": self.workspace, "parquet": self.parquet}


class FilePathCache:
    """会话级文件路径缓存 — 三字段注册表 + 归一化匹配 + get_file 自检"""

    __slots__ = ("_entries", "_normalized", "_max", "_staging_dir")

    def __init__(self, max_entries: int = 500) -> None:
        # {key: FileEntry}  key = rel_path / filename
        self._entries: dict[str, FileEntry] = {}
        # {归一化文件名: FileEntry}  归一化匹配用
        self._normalized: dict[str, FileEntry] = {}
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
        norm_key = normalize_filename(filename)

        # 已存在 → 合并更新（非空字段才覆盖）
        existing = self._normalized.get(norm_key)
        if existing:
            if workspace:
                existing.workspace = workspace
            if parquet:
                existing.parquet = parquet
            return

        # 新注册
        entry = FileEntry(name=filename, workspace=workspace, parquet=parquet)

        if len(self._entries) >= self._max:
            first_key = next(iter(self._entries))
            del self._entries[first_key]

        self._entries[rel_path] = entry
        self._entries[filename] = entry
        self._normalized[norm_key] = entry

    def set_parquet(self, filename: str, parquet_path: str) -> None:
        """设置 parquet 路径（file_analyze 完成后调用）。"""
        entry = self._resolve_entry(filename)
        if entry:
            entry.parquet = parquet_path

    def get_file(self, name: str, usage: str = "code") -> str:
        """按文件名 + 用途获取路径，含自检拦截。

        usage:
            "code"    → 返回 parquet（沙盒 duckdb 查询用）
            "analyze" → 返回 workspace（file_analyze/file_read 源文件）
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
        # 1. 精确匹配
        entry = self._entries.get(name)
        if entry:
            return entry
        basename = os.path.basename(name)
        entry = self._entries.get(basename)
        if entry:
            return entry

        # 2. 归一化匹配
        norm_input = normalize_filename(name)
        entry = self._normalized.get(norm_input)
        if entry:
            return entry

        # 3. Stem 匹配（用户没带扩展名）
        input_stem = os.path.splitext(norm_input)[0]
        if input_stem:
            for norm_key, entry in self._normalized.items():
                registered_stem = os.path.splitext(norm_key)[0]
                if input_stem == registered_stem:
                    return entry

        # 4. 前缀匹配（LLM 截断文件名，≥6 字符防误匹配）
        if input_stem and len(input_stem) >= 6:
            for norm_key, entry in self._normalized.items():
                registered_stem = os.path.splitext(norm_key)[0]
                if registered_stem.startswith(input_stem) or input_stem.startswith(registered_stem):
                    return entry

        return None

    def get_filename(self, name: str) -> Optional[str]:
        """按 key 查原始文件名。"""
        entry = self._entries.get(name)
        return entry.name if entry else None

    def list_all(self) -> list[dict[str, str]]:
        """返回所有去重的文件条目。"""
        seen: set[str] = set()
        result: list[dict[str, str]] = []
        for entry in self._normalized.values():
            if entry.name not in seen:
                seen.add(entry.name)
                result.append(entry.to_dict())
        return result

    def write_manifest(self) -> None:
        """把三字段映射写入 staging/_manifest.json。

        供沙盒子进程的 get_file() 读取。
        沙盒只需要 parquet 路径，但也写入 name 和归一化 key 方便匹配。
        """
        if not self._staging_dir:
            return
        # {文件名: parquet路径, 归一化文件名: parquet路径}
        manifest: dict[str, str] = {}
        for entry in self._normalized.values():
            if entry.parquet:
                manifest[entry.name] = entry.parquet
                norm_key = normalize_filename(entry.name)
                if norm_key != entry.name:
                    manifest[norm_key] = entry.parquet
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
