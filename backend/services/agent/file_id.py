"""文件 ID 协议核心：无状态确定性哈希。

设计依据见 docs/document/TECH_文件ID协议化.md

为何用无状态哈希而非缓存登记：
- 不依赖运行时 cache：服务重启即恢复
- 不依赖 DB：多 worker 间天然一致
- 历史对话回放可即时翻译老 path → fid
- (org_id, path) 元组保证多租户隔离
"""

import hashlib
import re
from typing import Optional

_FID_PATTERN = re.compile(r"^fid_[a-z0-9]{8}$")


def compute_fid(org_id: Optional[str], workspace_path: str) -> str:
    """确定性哈希：同 (org_id, workspace_path) 永远得同 fid。

    Args:
        org_id: 组织 ID，None 视为 "" (单租户兼容)
        workspace_path: 工作区相对路径，e.g. "已整理表格/饶/4月销售.xlsx"

    Returns:
        12 位 ASCII，格式 "fid_<8位hex>"，e.g. "fid_a3f2b1c9"

    冲突概率：4 字节 hash 空间下，单 org 1000 文件 ~10⁻⁸
    """
    seed = f"{org_id or ''}:{workspace_path}".encode("utf-8")
    digest = hashlib.blake2b(seed, digest_size=4).hexdigest()
    return f"fid_{digest}"


def is_valid_fid(value: str) -> bool:
    """校验是否为合法 fid 格式（不验证是否存在）。"""
    return bool(_FID_PATTERN.match(value or ""))


def resolve_fid_to_workspace(
    file_id: str, org_id: Optional[str], cache: object,
) -> Optional[str]:
    """从 file_path_cache 反查 fid 对应的 workspace 绝对路径。

    遍历 cache._entries 已注册的 key（rel_path 和 basename 都会被 register 进去），
    对每个 key 计算 compute_fid(org_id, key) 匹配 file_id；命中即返回 entry.workspace。

    Returns:
        workspace 绝对路径，或 None（未找到）
    """
    if not is_valid_fid(file_id):
        return None
    entries = getattr(cache, "_entries", None)
    if not entries:
        return None
    for key, entry in entries.items():
        if compute_fid(org_id, key) == file_id:
            ws = getattr(entry, "workspace", "")
            return ws or None
    return None
