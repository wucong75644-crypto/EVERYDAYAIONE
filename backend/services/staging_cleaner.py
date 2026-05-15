"""
Staging 文件清理模块。

对齐 Claude 模式后简化：不再依赖 session_file_registry 保护伞。

两层清理策略：
1. TTL 淘汰 — 超 24h 的文件删除（排除 DuckDB 和备份文件）
2. 容量兜底 — 目录总大小 > 500MB 时从最旧文件开始删

排除项（不按 TTL 清理）：
- .duckdb.db / .duckdb_temp/ — DuckDB 磁盘模式文件，跟会话同生命周期
- _bak_* — workspace 备份文件，restore_file 依赖
- _manifest.json — file_search 的文件索引

触发时机：
- 消息驱动（主）：每次消息处理开始时 fire-and-forget
- 进程启动（兜底）：扫描所有用户 staging 目录
"""

from __future__ import annotations

import time
from pathlib import Path

from loguru import logger

# 受保护的文件名/前缀（不按 TTL 清理）
_PROTECTED_NAMES = frozenset({".duckdb.db", "_manifest.json", "session_files.json"})
_PROTECTED_PREFIXES = ("_bak_", ".duckdb_temp")


def _is_protected(path: Path) -> bool:
    """判断文件/目录是否受保护（不按 TTL 清理）"""
    name = path.name
    if name in _PROTECTED_NAMES:
        return True
    for prefix in _PROTECTED_PREFIXES:
        if name.startswith(prefix):
            return True
    return False


def cleanup_staging(
    staging_dir: str,
    registry=None,  # 兼容旧调用签名，不再使用
    *,
    ttl_seconds: int = 86400,
    max_size_mb: int = 500,
) -> dict[str, int]:
    """清理单个会话的 staging 目录。

    Args:
        staging_dir: staging/{conv_id}/ 的绝对路径
        registry: 已废弃，保留参数兼容旧调用方
        ttl_seconds: 文件过期时间（默认 24h）
        max_size_mb: 目录总大小上限（MB）

    Returns:
        {"deleted": N, "protected": N, "kept": N}
    """
    staging = Path(staging_dir)
    if not staging.exists() or not staging.is_dir():
        return {"deleted": 0, "protected": 0, "kept": 0}

    now = time.time()
    deleted = 0
    kept = 0
    protected_count = 0
    survivors: list[tuple[Path, float, int]] = []  # (path, mtime, size)

    # ── Phase 1: TTL + 保护清理 ──
    for f in _list_files(staging):
        if _is_protected(f):
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

    # ── Phase 2: 容量兜底 ──
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


def cleanup_all_staging(
    workspace_root: str,
    *,
    ttl_seconds: int = 86400,
) -> int:
    """进程启动兜底：扫描所有用户 staging 目录，清理过期文件。"""
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

    # 遍历所有 staging 目录
    patterns = ["org/*/*/staging", "personal/*/staging"]
    for pattern in patterns:
        for staging_parent in ws_root.glob(pattern):
            if not staging_parent.is_dir():
                continue
            for conv_dir in staging_parent.iterdir():
                if conv_dir.is_dir():
                    total_deleted += _clean_conv_dir(conv_dir)

    # 兼容旧的全局 staging
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
    """清理单个会话目录中的过期文件（纯 TTL，含保护规则）。"""
    deleted = 0
    for f in _list_files(conv_dir):
        if _is_protected(f):
            continue
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
