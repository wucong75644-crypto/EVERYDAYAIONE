"""
Staging 清理模块单元测试。

覆盖场景：
- 保护伞：registry 中的文件不被删
- TTL：超 24h 孤儿文件被删
- LRU：超 20 条淘汰最旧
- 容量：超 500MB 删最旧非保护文件
- _tmp_ 前缀无条件删
- 并发安全：清理和文件写入不冲突
- 进程启动兜底扫描
"""

import os
import time
from pathlib import Path
import pytest

from services.staging_cleaner import (
    cleanup_staging,
    cleanup_all_staging,
)


# ── Fixtures ──


def _make_file(
    directory: Path,
    name: str,
    size: int = 100,
    age_seconds: float = 0,
) -> Path:
    """创建测试文件，可指定大小和年龄。"""
    f = directory / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x" * size)
    if age_seconds > 0:
        old_time = time.time() - age_seconds
        os.utime(f, (old_time, old_time))
    return f


# _make_registry / test_protected_files_not_deleted 已移除
# （SessionFileRegistry 保护伞已删除，staging 清理不再依赖 registry）


# ── Test: TTL ──


def test_ttl_old_files_deleted(tmp_path: Path):
    """超过 TTL 的孤儿文件被删除。"""
    staging = tmp_path / "conv1"
    staging.mkdir(parents=True)

    old = _make_file(staging, "old.parquet", age_seconds=90000)
    fresh = _make_file(staging, "fresh.parquet", age_seconds=100)

    result = cleanup_staging(str(staging), ttl_seconds=86400)

    assert not old.exists()
    assert fresh.exists()
    assert result["deleted"] == 1
    assert result["kept"] == 1


def test_ttl_no_files(tmp_path: Path):
    """空目录不报错。"""
    staging = tmp_path / "empty_conv"
    staging.mkdir(parents=True)

    result = cleanup_staging(str(staging))
    assert result == {"deleted": 0, "protected": 0, "kept": 0}


def test_nonexistent_dir():
    """不存在的目录不报错。"""
    result = cleanup_staging("/nonexistent/path/conv1")
    assert result == {"deleted": 0, "protected": 0, "kept": 0}


# ── Test: _tmp_ 前缀 ──


def test_tmp_prefix_always_deleted(tmp_path: Path):
    """_tmp_ 前缀文件无条件删除（写入中断残留）。"""
    staging = tmp_path / "conv1"
    staging.mkdir(parents=True)

    tmp_file = _make_file(staging, "_tmp_partial.parquet", age_seconds=10)
    normal = _make_file(staging, "normal.parquet", age_seconds=10)

    result = cleanup_staging(str(staging), ttl_seconds=86400)

    assert not tmp_file.exists(), "_tmp_ file should always be deleted"
    assert normal.exists(), "non-tmp fresh file should survive"
    assert result["deleted"] == 1


# LRU 淘汰测试已移除（evict_lru 随 SessionFileRegistry 一起删除）


# ── Test: 容量兜底 ──


def test_capacity_over_limit(tmp_path: Path):
    """目录总大小超过 max_size_mb 时从最旧文件开始删。"""
    staging = tmp_path / "conv1"
    staging.mkdir(parents=True)

    # 创建 3 个 200KB 文件 = 600KB > 500KB limit（测试用小值）
    oldest = _make_file(staging, "oldest.dat", size=200_000, age_seconds=3000)
    middle = _make_file(staging, "middle.dat", size=200_000, age_seconds=2000)
    newest = _make_file(staging, "newest.dat", size=200_000, age_seconds=1000)

    # max_size_mb=0 会立即触发（0MB 上限），用小值测试
    result = cleanup_staging(
        str(staging),
        ttl_seconds=86400,  # 都没超 TTL
        max_size_mb=0,  # 0MB → 所有文件都超标
    )

    # 至少删了一些文件（从最旧开始）
    assert result["deleted"] >= 1
    # 最旧的应该先被删
    assert not oldest.exists()


# test_capacity_protected_files_survive 已移除（registry 保护伞已删除）


# ── Test: 并发安全 ──


def test_concurrent_cleanup_file_disappears(tmp_path: Path):
    """清理期间文件被其他进程删除 → 静默跳过，不报错。"""
    staging = tmp_path / "conv1"
    staging.mkdir(parents=True)

    f = _make_file(staging, "vanishing.parquet", age_seconds=90000)
    # 在清理前删掉文件，模拟并发
    f.unlink()

    # 不应抛异常
    result = cleanup_staging(str(staging), ttl_seconds=86400)
    assert result["deleted"] == 0


# ── Test: 进程启动兜底扫描 ──


def test_startup_cleanup(tmp_path: Path):
    """启动扫描清理所有过期文件。"""
    ws_root = tmp_path / "workspace"

    # org 用户
    org_staging = ws_root / "org" / "org1" / "user1" / "staging" / "conv_old"
    org_staging.mkdir(parents=True)
    _make_file(org_staging, "old.parquet", age_seconds=90000)

    # personal 用户
    personal_staging = ws_root / "personal" / "abc12345" / "staging" / "conv_old"
    personal_staging.mkdir(parents=True)
    _make_file(personal_staging, "old.parquet", age_seconds=90000)

    # 新文件不应被删
    fresh_staging = ws_root / "org" / "org1" / "user1" / "staging" / "conv_fresh"
    fresh_staging.mkdir(parents=True)
    fresh_file = _make_file(fresh_staging, "fresh.parquet", age_seconds=100)

    deleted = cleanup_all_staging(str(ws_root), ttl_seconds=86400)

    assert deleted == 2  # 两个 old.parquet
    assert fresh_file.exists()


def test_startup_cleanup_tmp_files(tmp_path: Path):
    """启动扫描清理 _tmp_ 前缀文件（不论年龄）。"""
    ws_root = tmp_path / "workspace"
    staging = ws_root / "org" / "org1" / "user1" / "staging" / "conv1"
    staging.mkdir(parents=True)

    tmp_file = _make_file(staging, "_tmp_interrupted.parquet", age_seconds=10)

    deleted = cleanup_all_staging(str(ws_root), ttl_seconds=86400)
    assert deleted == 1
    assert not tmp_file.exists()


def test_startup_cleanup_nonexistent_root():
    """workspace_root 不存在时返回 0。"""
    assert cleanup_all_staging("/nonexistent/root") == 0


def test_startup_cleanup_legacy_staging(tmp_path: Path):
    """兼容旧的全局 staging 目录。"""
    ws_root = tmp_path / "workspace"
    old_staging = ws_root / "staging" / "old_conv"
    old_staging.mkdir(parents=True)
    _make_file(old_staging, "legacy.parquet", age_seconds=90000)

    deleted = cleanup_all_staging(str(ws_root), ttl_seconds=86400)
    assert deleted == 1



# evict_lru / _protected_paths 测试已移除（随 SessionFileRegistry 一起删除）


# ── Test: 子目录递归清理 ──


def test_cleanup_subdirectory_files(tmp_path: Path):
    """staging 下子目录中的文件也能递归清理。"""
    staging = tmp_path / "conv1"
    subdir = staging / "exports"
    subdir.mkdir(parents=True)

    old_sub = _make_file(subdir, "export.csv", age_seconds=90000)
    fresh_root = _make_file(staging, "fresh.parquet", age_seconds=100)

    result = cleanup_staging(str(staging), ttl_seconds=86400)

    assert not old_sub.exists(), "old file in subdirectory should be deleted"
    assert fresh_root.exists()
    assert result["deleted"] == 1
    assert not subdir.exists(), "empty subdirectory should be removed"


# ── Test: cleanup_all_staging 空目录移除 ──


def test_startup_cleanup_removes_empty_conv_dirs(tmp_path: Path):
    """清理完成后空的会话目录被 rmdir。"""
    ws_root = tmp_path / "workspace"
    conv_dir = ws_root / "org" / "org1" / "user1" / "staging" / "conv_empty"
    conv_dir.mkdir(parents=True)
    _make_file(conv_dir, "old.parquet", age_seconds=90000)

    cleanup_all_staging(str(ws_root), ttl_seconds=86400)

    assert not conv_dir.exists(), "empty conv dir should be removed after cleanup"



# ── Test: _is_protected ──


class TestIsProtected:
    """新增的文件保护规则"""

    def test_duckdb_file_protected(self):
        from services.staging_cleaner import _is_protected
        assert _is_protected(Path(".duckdb.db")) is True

    def test_manifest_protected(self):
        from services.staging_cleaner import _is_protected
        assert _is_protected(Path("_manifest.json")) is True

    def test_backup_file_protected(self):
        from services.staging_cleaner import _is_protected
        assert _is_protected(Path("_bak_1700000000_report.xlsx")) is True

    def test_duckdb_temp_dir_protected(self):
        from services.staging_cleaner import _is_protected
        assert _is_protected(Path(".duckdb_temp")) is True

    def test_normal_parquet_not_protected(self):
        from services.staging_cleaner import _is_protected
        assert _is_protected(Path("data_001.parquet")) is False

    def test_normal_csv_not_protected(self):
        from services.staging_cleaner import _is_protected
        assert _is_protected(Path("export.csv")) is False

    def test_tmp_file_not_protected(self):
        """_tmp_ 前缀不在保护列表（由 TTL 逻辑专门处理）"""
        from services.staging_cleaner import _is_protected
        assert _is_protected(Path("_tmp_partial.parquet")) is False


def test_protected_files_survive_ttl(tmp_path: Path):
    """_manifest.json、_bak_*、.duckdb.db 即使超过 TTL 也不被删"""
    staging = tmp_path / "conv1"
    staging.mkdir(parents=True)

    # 全部设为超 TTL 的旧文件
    _make_file(staging, "_manifest.json", age_seconds=90000)
    _make_file(staging, "_bak_1700000000_data.csv", age_seconds=90000)
    _make_file(staging, ".duckdb.db", age_seconds=90000)
    normal = _make_file(staging, "old_data.parquet", age_seconds=90000)

    result = cleanup_staging(str(staging), ttl_seconds=86400)

    # 受保护文件存活
    assert (staging / "_manifest.json").exists()
    assert (staging / "_bak_1700000000_data.csv").exists()
    assert (staging / ".duckdb.db").exists()
    # 普通文件被清理
    assert not normal.exists()
    assert result["protected"] == 3
    assert result["deleted"] == 1
