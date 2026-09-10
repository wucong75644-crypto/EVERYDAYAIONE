"""文件 ID 协议核心：无状态确定性哈希。

设计依据见 docs/document/TECH_文件ID协议化.md

哈希生成不依赖缓存；反向定位仍需当前授权范围的路径集合。
旧 fid 兼容由 FileTargetResolver 在文件清单/工作区中唯一匹配，
不能仅凭短哈希授权或假设无碰撞。新调用优先使用签名 resource_ref。
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

    仅 32 位哈希，可能碰撞；调用方必须在当前范围核验唯一性。
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

    只遍历注册表公开的唯一精确键，不把歧义文件名或模糊名称当作文件身份。
    对每个 key 计算 compute_fid(org_id, key)；若哈希命中多个源文件则不解析。

    Returns:
        workspace 绝对路径，或 None（未找到）
    """
    if not is_valid_fid(file_id):
        return None
    registered_paths = getattr(cache, "registered_paths", None)
    if not callable(registered_paths):
        return None
    matches = {
        entry.workspace for key, entry in registered_paths()
        if compute_fid(org_id, key) == file_id and entry.workspace
    }
    return next(iter(matches)) if len(matches) == 1 else None
