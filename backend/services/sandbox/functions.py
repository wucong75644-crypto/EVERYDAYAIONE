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
    files_dict: Optional[Dict[str, str]] = None,  # 兼容保留,已不使用
    kernel_manager=None,
) -> SandboxExecutor:
    """构建沙盒执行器(Kernel 模式)。

    产物协议:LLM 在沙盒里调 emit_chart/file/image/table 主动声明产物 →
    [EMIT] marker → tool_loop_executor 解析 → AgentResult.emit_payloads。
    沙盒主进程不再扫描 output_dir 兜底上传(对齐 Jupyter/OpenAI 行业标准)。
    """
    from core.config import get_settings as _get_settings
    from pathlib import Path

    _file_settings = _get_settings()
    _conv_id = conversation_id or "default"

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

    _skills_dir = str(Path(__file__).resolve().parent.parent.parent / "skills")

    return SandboxExecutor(
        timeout=timeout,
        max_result_chars=max_result_chars,
        output_dir=_output_dir,
        staging_dir=_staging_dir,
        workspace_dir=_workspace_dir,
        kernel_manager=kernel_manager,
        conversation_id=conversation_id,
        skills_dir=_skills_dir,
    )


def compute_code_hash(code: str) -> str:
    """计算代码 MD5 指纹（执行日志去重用）"""
    return hashlib.md5(code.strip().encode()).hexdigest()[:12]
