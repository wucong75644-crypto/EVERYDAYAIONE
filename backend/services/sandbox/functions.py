"""
沙盒执行器工厂

构建纯计算沙盒（SandboxExecutor），仅注册计算和文件读取能力。
数据获取函数（ERP 翻页、搜索等）已迁移到 services/agent/tool_executor.py。
"""

import hashlib
from typing import Any, Dict, Optional

from services.sandbox.executor import SandboxExecutor


def build_sandbox_executor(
    timeout: float = 120.0,
    max_result_chars: int = 8000,
    user_id: str = "",
    org_id: Optional[str] = None,
    conversation_id: str = "",
    files_dict: Optional[Dict[str, str]] = None,  # 兼容保留，已不使用
) -> SandboxExecutor:
    """构建沙盒执行器（子进程隔离模式）

    主进程负责：AST 验证、文件快照、文件上传检测。
    子进程负责：chdir 到 workspace + exec 用户代码 + 返回结果。

    文件输出：
    - 子进程代码写 df.to_excel(OUTPUT_DIR + "/报表.xlsx") 到 OUTPUT_DIR
    - ossfs 自动同步到 OSS → 主进程生成 CDN 下载链接
    - 用户在工作区"下载/"文件夹直接可见可下载
    """
    from core.config import get_settings as _get_settings
    from pathlib import Path

    _file_settings = _get_settings()
    _conv_id = conversation_id or "default"

    # 1. 用户 workspace 目录（对标 OpenAI /mnt/data）
    from core.workspace import resolve_workspace_dir, resolve_staging_dir
    _workspace_dir = resolve_workspace_dir(
        _file_settings.file_workspace_root, user_id, org_id,
    )

    # 2. 输出目录 → workspace 下的 "下载/" 文件夹（对标电脑的下载文件夹）
    _output_dir = str(Path(_workspace_dir) / "下载")

    # 3. staging 数据目录（用户级隔离，工具结果分流 + db_export 写入）
    _staging_dir = resolve_staging_dir(
        _file_settings.file_workspace_root, user_id, org_id, _conv_id,
    )

    # 4. 文件检测函数 — 调用公共 auto_upload 模块
    async def _auto_upload(filename: str, size: int) -> str:
        """生成文件的 CDN URL（委托公共模块，零内存开销）"""
        from services.file_upload import auto_upload
        return await auto_upload(
            filename=filename, size=size,
            output_dir=_output_dir, user_id=user_id, org_id=org_id,
        )

    return SandboxExecutor(
        timeout=timeout,
        max_result_chars=max_result_chars,
        output_dir=_output_dir,
        staging_dir=_staging_dir,
        workspace_dir=_workspace_dir,
        upload_fn=_auto_upload,
    )


def compute_code_hash(code: str) -> str:
    """计算代码 MD5 指纹（执行日志去重用）"""
    return hashlib.md5(code.strip().encode()).hexdigest()[:12]
