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
    kernel_manager=None,
) -> SandboxExecutor:
    """构建沙盒执行器（子进程隔离模式）

    主进程负责：AST 验证、文件快照、文件上传检测。
    子进程负责：chdir 到 workspace + exec 用户代码 + 返回结果。

    文件输出：
    - 子进程代码写 df.to_excel('下载/报表.xlsx')（相对路径，沙盒 cwd=/workspace）
    - 主进程扫 output_dir 自动同步到 OSS → 生成 CDN 下载链接
    - 用户在工作区"下载/"文件夹直接可见可下载
    """
    from core.config import get_settings as _get_settings
    from pathlib import Path

    _file_settings = _get_settings()
    _conv_id = conversation_id or "default"

    # 1-3. 三个目录全部通过 resolve_* 获取(契约保证目录存在,无需手动 mkdir)
    from core.workspace import (
        resolve_workspace_dir, resolve_output_dir, resolve_staging_dir,
    )
    _workspace_dir = resolve_workspace_dir(
        _file_settings.file_workspace_root, user_id, org_id,
    )
    _output_dir = resolve_output_dir(
        _file_settings.file_workspace_root, user_id, org_id,
    )
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

    # 5. 文件处理技能目录（对标 Claude /mnt/skills/public/）
    _skills_dir = str(Path(__file__).resolve().parent.parent.parent / "skills")

    return SandboxExecutor(
        timeout=timeout,
        max_result_chars=max_result_chars,
        output_dir=_output_dir,
        staging_dir=_staging_dir,
        workspace_dir=_workspace_dir,
        upload_fn=_auto_upload,
        kernel_manager=kernel_manager,
        conversation_id=conversation_id,
        skills_dir=_skills_dir,
    )


def compute_code_hash(code: str) -> str:
    """计算代码 MD5 指纹（执行日志去重用）"""
    return hashlib.md5(code.strip().encode()).hexdigest()[:12]
