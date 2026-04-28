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

    # 4. 文件检测函数 — 生成 workspace CDN URL（不上传 OSS，文件已通过 ossfs 在 OSS 上）
    async def _auto_upload(filename: str, size: int) -> str:
        """生成文件的 CDN URL（不读文件内容，零内存开销）"""
        import mimetypes
        safe_name = Path(filename).name
        mime_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"

        # 直接算 workspace CDN URL（文件在 WORKSPACE_DIR/下载/ 下，ossfs 自动同步到 OSS）
        from core.config import get_settings as _cdn_settings
        _cs = _cdn_settings()
        if _cs.oss_cdn_domain:
            _ws_base = Path(_cs.file_workspace_root).resolve()
            _file_path = Path(_output_dir) / safe_name
            try:
                from urllib.parse import quote
                object_key = str(_file_path.relative_to(_ws_base))
                # URL 编码路径（中文+括号等特殊字符），保留 /
                encoded_key = quote(object_key, safe="/")
                url = f"https://{_cs.oss_cdn_domain}/workspace/{encoded_key}"
                return (
                    f"✅ 文件已生成: {safe_name}\n"
                    f"[FILE]{url}|{safe_name}|{mime_type}|{size}[/FILE]"
                )
            except ValueError:
                pass

        # 兜底：无 CDN 配置时读文件上传 OSS
        try:
            from services.oss_service import get_oss_service
            _file_path = Path(_output_dir) / safe_name
            content = _file_path.read_bytes()
            ext = Path(safe_name).suffix.lstrip(".")
            oss = get_oss_service()
            result = oss.upload_bytes(
                content=content, user_id=user_id, ext=ext,
                category="generated", content_type=mime_type, org_id=org_id,
            )
            return (
                f"✅ 文件已生成: {safe_name}\n"
                f"[FILE]{result['url']}|{safe_name}|{mime_type}|{result['size']}[/FILE]"
            )
        except Exception as e:
            return f"❌ 文件处理失败: {safe_name} ({e})"

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
