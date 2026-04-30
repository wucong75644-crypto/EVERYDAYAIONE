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
    evict_lru,
    _protected_paths,
    _extract_timestamp,
)
from services.agent.session_file_registry import SessionFileRegistry
from services.agent.tool_output import ColumnMeta, FileRef


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


def _make_registry(file_paths: list[str]) -> SessionFileRegistry:
    """创建包含指定文件路径的 registry。"""
    registry = SessionFileRegistry()
    for i, path in enumerate(file_paths):
        ref = FileRef(
            path=path,
            filename=f"test_{i}.parquet",
            format="parquet",
            row_count=10,
            size_bytes=100,
            columns=[ColumnMeta(name="id", dtype="int64", label="ID")],
            created_at=time.time(),
        )
        registry.register("test", "tool", ref)
    return registry


# ── Test: 保护伞 ──


def test_protected_files_not_deleted(tmp_path: Path):
    """registry 中的文件不被删除，即使超过 TTL。"""
    staging = tmp_path / "staging" / "conv1"
    staging.mkdir(parents=True)

    protected = _make_file(staging, "protected.parquet", age_seconds=90000)
    orphan = _make_file(staging, "orphan.parquet", age_seconds=90000)

    registry = _make_registry([str(protected)])
    result = cleanup_staging(str(staging), registry, ttl_seconds=86400)

    assert protected.exists(), "protected file should survive"
    assert not orphan.exists(), "orphan file should be deleted"
    assert result["protected"] == 1
    assert result["deleted"] == 1


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


# ── Test: LRU 淘汰 ──


def test_lru_eviction_over_limit():
    """超过 max_entries 时淘汰最旧的条目。"""
    registry = SessionFileRegistry()
    # 注册 25 个文件，timestamp 递增
    for i in range(25):
        ref = FileRef(
            path=f"/tmp/file_{i}.parquet",
            filename=f"file_{i}.parquet",
            format="parquet",
            row_count=10,
            size_bytes=100,
            columns=[],
            created_at=time.time(),
        )
        # 手动设置 key 以控制 timestamp
        key = f"test:tool:{1000 + i}"
        registry._files[key] = ref

    evicted = evict_lru(registry, max_entries=20)

    assert len(evicted) == 5
    assert len(registry._files) == 20
    # 最旧的 5 个（timestamp 1000-1004）应该被淘汰
    for key in evicted:
        ts = int(key.rsplit(":", 1)[-1])
        assert ts < 1005


def test_lru_no_eviction_under_limit():
    """条目数 <= max_entries 时不淘汰。"""
    registry = SessionFileRegistry()
    for i in range(15):
        ref = FileRef(
            path=f"/tmp/file_{i}.parquet",
            filename=f"file_{i}.parquet",
            format="parquet",
            row_count=10,
            size_bytes=100,
            columns=[],
        )
        registry._files[f"test:tool:{1000 + i}"] = ref

    evicted = evict_lru(registry, max_entries=20)
    assert evicted == []
    assert len(registry._files) == 15


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


def test_capacity_protected_files_survive(tmp_path: Path):
    """容量清理时 registry 中的文件仍然受保护。"""
    staging = tmp_path / "conv1"
    staging.mkdir(parents=True)

    protected = _make_file(staging, "protected.dat", size=300_000, age_seconds=5000)
    expendable = _make_file(staging, "expendable.dat", size=300_000, age_seconds=3000)

    registry = _make_registry([str(protected)])

    result = cleanup_staging(
        str(staging),
        registry,
        ttl_seconds=86400,
        max_size_mb=0,
    )

    assert protected.exists(), "protected file must survive capacity cleanup"
    assert not expendable.exists()


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


# ── Test: evict_lru access_counts 清理 ──


def test_lru_eviction_cleans_access_counts():
    """淘汰条目时同步清理 _access_counts（验证 file_id key 修复）。"""
    registry = SessionFileRegistry()
    for i in range(5):
        ref = FileRef(
            path=f"/tmp/file_{i}.parquet",
            filename=f"file_{i}.parquet",
            format="parquet",
            row_count=10,
            size_bytes=100,
            columns=[],
            id=f"uuid-{i}",
        )
        registry._files[f"test:tool:{1000 + i}"] = ref
        registry.record_access(f"uuid-{i}")

    assert len(registry._access_counts) == 5

    evicted = evict_lru(registry, max_entries=3)

    assert len(evicted) == 2
    assert len(registry._files) == 3
    assert "uuid-0" not in registry._access_counts
    assert "uuid-1" not in registry._access_counts
    assert "uuid-2" in registry._access_counts


# ── Test: _protected_paths 空 path 过滤 ──


def test_protected_paths_filters_empty():
    """FileRef.path 为空时不加入保护集合。"""
    registry = SessionFileRegistry()
    ref_with_path = FileRef(
        path="/tmp/real.parquet", filename="real.parquet",
        format="parquet", row_count=1, size_bytes=10, columns=[],
    )
    ref_no_path = FileRef(
        path="", filename="ghost.parquet",
        format="parquet", row_count=1, size_bytes=10, columns=[],
    )
    registry._files["a:b:1"] = ref_with_path
    registry._files["a:b:2"] = ref_no_path

    paths = _protected_paths(registry)
    assert paths == {"/tmp/real.parquet"}


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


# ── Test: _extract_timestamp 异常格式 ──


def test_extract_timestamp_normal():
    """正常 key 提取 timestamp。"""
    assert _extract_timestamp("domain:tool:1234567890") == 1234567890


def test_extract_timestamp_no_colon():
    """无冒号的 key 返回 0。"""
    assert _extract_timestamp("nocolon") == 0


def test_extract_timestamp_non_numeric():
    """timestamp 部分非数字返回 0。"""
    assert _extract_timestamp("domain:tool:abc") == 0


def test_extract_timestamp_empty():
    """空字符串返回 0。"""
    assert _extract_timestamp("") == 0
