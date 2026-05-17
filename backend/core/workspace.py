"""
Workspace 路径计算 — 全局唯一真相源

所有 staging / workspace / output 目录的路径计算统一在此，
避免各模块各自拼路径导致不一致。

目录结构：
    {workspace_root}/
    ├── org/{org_id}/{user_id}/         ← 企业用户 workspace
    │   ├── 下载/                       ← 沙盒输出（显式同步到 OSS）
    │   └── staging/{conv_id}/          ← 临时数据（工具结果分流 + db_export）
    └── personal/{hash}/                ← 个人用户 workspace
        ├── 下载/
        └── staging/{conv_id}/
"""

import hashlib
from pathlib import Path
from typing import Optional


def resolve_workspace_dir(
    workspace_root: str,
    user_id: str = "",
    org_id: Optional[str] = None,
) -> str:
    """计算用户级 workspace 目录（绝对路径）"""
    base = Path(workspace_root).resolve()
    if org_id:
        return str(base / "org" / str(org_id) / str(user_id))
    elif user_id:
        user_hash = hashlib.md5(str(user_id).encode()).hexdigest()[:8]
        return str(base / "personal" / user_hash)
    return str(base)


def resolve_staging_dir(
    workspace_root: str,
    user_id: str = "",
    org_id: Optional[str] = None,
    conversation_id: str = "default",
) -> str:
    """计算用户级 staging 目录（绝对路径）

    staging 存在于用户 workspace 下，和下载/工作区同级：
        {workspace_dir}/staging/{conversation_id}/

    用户隔离：不同用户的 staging 在不同目录下，互不可见。
    会话隔离：同一用户不同会话的 staging 用 conversation_id 区分。
    """
    ws_dir = resolve_workspace_dir(workspace_root, user_id, org_id)
    return str(Path(ws_dir) / "staging" / (conversation_id or "default"))
