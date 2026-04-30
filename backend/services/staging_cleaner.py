"""
Staging 文件清理模块。

设计文档：docs/document/TECH_data_query工具设计.md §九

三层清理策略：
1. Registry 保护伞 — registry 中的文件不删
2. TTL 淘汰 — 不在 registry 且超 24h 的孤儿文件删除
3. 容量兜底 — 目录总大小 > 500MB 时从最旧非保护文件开始删

触发时机：
- 消息驱动（主）：每次消息处理开始时 fire-and-forget
- 进程启动（兜底）：扫描所有用户 staging 目录
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from services.agent.session_file_registry import SessionFileRegistry


def _protected_paths(registry: SessionFileRegistry | None) -> set[str]:
    """提取 registry 中所有受保护文件的绝对路径。"""
    if not registry:
        return set()
    return {ref.path for _, ref in registry.list_all() if ref.path}


def cleanup_staging(
    staging_dir: str,
    registry: SessionFileRegistry | None = None,
    *,
    ttl_seconds: int = 86400,
    max_size_mb: int = 500,
) -> dict[str, int]:
    """清理单个会话的 staging 目录。

    Args:
        staging_dir: staging/{conv_id}/ 的绝对路径
        registry: 当前会话的文件注册表（None = 无保护伞，纯 TTL）
        ttl_seconds: 孤儿文件过期时间（默认 24h）
        max_size_mb: 目录总大小上限（MB）

    Returns:
        {"deleted": N, "protected": N, "kept": N}
    """
    staging = Path(staging_dir)
    if not staging.exists() or not staging.is_dir():
        return {"deleted": 0, "protected": 0, "kept": 0}

    protected = _protected_paths(registry)
    now = time.time()
    deleted = 0
    kept = 0
    protected_count = 0
    # Phase 1 遍历时收集存活的非保护文件，供 Phase 2 容量兜底复用
    survivors: list[tuple[Path, float, int]] = []  # (path, mtime, size)

    # ── Phase 1: TTL + 保护伞清理 ──
    for f in _list_files(staging):
        abs_path = str(f)
        if abs_path in protected:
            protected_count += 1
            continue
        try:
            stat = f.stat()
        except OSError:
            continue
        # _tmp_ 前缀 = 写入中断残留，无条件删
        if f.name.startswith("_tmp_"):
            _safe_delete(f)
            deleted += 1
        elif now - stat.st_mtime > ttl_seconds:
            _safe_delete(f)
            deleted += 1
        else:
            kept += 1
            survivors.append((f, stat.st_mtime, stat.st_size))

    # ── Phase 2: 容量兜底（复用 Phase 1 收集的存活文件列表）──
    max_bytes = max_size_mb * 1024 * 1024
    total_size = sum(s for _, _, s in survivors)
    if total_size > max_bytes:
        survivors.sort(key=lambda x: x[1])  # oldest first
        for f, _, size in survivors:
            if total_size <= max_bytes:
                break
            _safe_delete(f)
            total_size -= size
            deleted += 1
            kept -= 1

    # 清理空子目录
    _remove_empty_dirs(staging)

    if deleted:
        logger.info(
            f"Staging cleanup | dir={staging_dir} | "
            f"deleted={deleted} protected={protected_count} kept={kept}"
        )
    return {"deleted": deleted, "protected": protected_count, "kept": max(kept, 0)}


def evict_lru(
    registry: SessionFileRegistry,
    max_entries: int = 20,
) -> list[str]:
    """LRU 淘汰：registry 条目 > max_entries 时淘汰最旧的。

    被淘汰的文件失去保护，下次清理按 TTL 处理。
    通过 registry 的 public 方法操作，不直接访问私有属性。

    Returns:
        被淘汰的 key 列表
    """
    count = registry.entries_count()
    if count <= max_entries:
        return []

    evict_count = count - max_entries
    oldest_keys = registry.get_oldest_keys(evict_count)

    for key in oldest_keys:
        registry.remove(key)

    if oldest_keys:
        logger.info(
            f"Registry LRU eviction | evicted={len(oldest_keys)} "
            f"remaining={registry.entries_count()}"
        )
    return oldest_keys


def cleanup_all_staging(
    workspace_root: str,
    *,
    ttl_seconds: int = 86400,
) -> int:
    """进程启动兜底：扫描所有用户 staging 目录，清理过期文件。

    不做 registry 保护（启动时无活跃 registry），纯 TTL + _tmp_ 清理。
    """
    ws_root = Path(workspace_root)
    if not ws_root.exists():
        return 0

    total_deleted = 0
    now = time.time()

    def _clean_conv_dir(conv_dir: Path) -> int:
        """清理单个会话目录：删过期文件 → 移除空子目录 → 空目录自身 rmdir。"""
        n = _cleanup_single_dir(conv_dir, now, ttl_seconds)
        _remove_empty_dirs(conv_dir)
        if conv_dir.exists() and not any(conv_dir.iterdir()):
            try:
                conv_dir.rmdir()
            except OSError:
                pass
        return n

    # 遍历所有 staging 目录：org/{org_id}/{user_id}/staging/ 和 personal/{hash}/staging/
    patterns = ["org/*/*/staging", "personal/*/staging"]
    for pattern in patterns:
        for staging_parent in ws_root.glob(pattern):
            if not staging_parent.is_dir():
                continue
            for conv_dir in staging_parent.iterdir():
                if conv_dir.is_dir():
                    total_deleted += _clean_conv_dir(conv_dir)

    # 兼容旧的全局 staging（迁移过渡期）
    old_staging = ws_root / "staging"
    if old_staging.exists():
        for conv_dir in old_staging.iterdir():
            if conv_dir.is_dir():
                total_deleted += _clean_conv_dir(conv_dir)

    if total_deleted:
        logger.info(f"Startup staging cleanup | deleted={total_deleted} files")
    return total_deleted


# ── 内部工具函数 ──


def _cleanup_single_dir(conv_dir: Path, now: float, ttl_seconds: int) -> int:
    """清理单个会话目录中的过期文件（纯 TTL，无 registry 保护）。"""
    deleted = 0
    for f in _list_files(conv_dir):
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if f.name.startswith("_tmp_") or now - mtime > ttl_seconds:
            _safe_delete(f)
            deleted += 1
    return deleted


def _list_files(directory: Path) -> list[Path]:
    """递归列出目录下所有文件（不含目录本身）。"""
    try:
        return [f for f in directory.rglob("*") if f.is_file()]
    except OSError:
        return []


def _safe_delete(path: Path) -> bool:
    """安全删除单个文件，失败静默跳过。"""
    try:
        path.unlink()
        return True
    except OSError as e:
        logger.warning(f"Staging file delete failed | path={path} | error={e}")
        return False


def _remove_empty_dirs(directory: Path) -> None:
    """递归删除空子目录（不删 directory 本身）。"""
    try:
        for d in sorted(directory.rglob("*"), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
    except OSError:
        pass


def _extract_timestamp(key: str) -> int:
    """从 registry key 中提取 timestamp（key = domain:tool_name:timestamp）。"""
    parts = key.rsplit(":", 1)
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return 0
