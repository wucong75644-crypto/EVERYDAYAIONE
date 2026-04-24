"""
沙盒执行器工厂

构建纯计算沙盒（SandboxExecutor），仅注册计算和文件读取能力。
数据获取函数（ERP 翻页、搜索等）已迁移到 services/agent/tool_executor.py。
"""

import hashlib
from typing import Any, Optional

from services.sandbox.executor import SandboxExecutor


def build_sandbox_executor(
    timeout: float = 120.0,
    max_result_chars: int = 8000,
    user_id: str = "",
    org_id: Optional[str] = None,
    conversation_id: str = "",
) -> SandboxExecutor:
    """构建沙盒执行器（纯计算引擎）

    沙盒只注册计算和输出能力，不注册数据获取函数。
    数据获取必须走 Agent 工具层（local_* > erp_* > fetch_all_pages）。

    文件输出：
    - LLM 代码写 df.to_excel(OUTPUT_DIR + "/报表.xlsx") 到 OUTPUT_DIR（workspace/下载/）
    - ossfs 自动同步到 OSS → 平台生成 CDN 下载链接
    - 用户在工作区"下载/"文件夹直接可见可下载

    已注册能力：
    - read_file: 读取 staging 目录下的预获取数据（仅限 staging/）
    - 标准库: pandas, math, datetime, Decimal, Counter, io, json

    Args:
        timeout: 执行超时（秒）
        max_result_chars: 结果最大字符数
        conversation_id: 会话ID（用于隔离输出目录）

    Returns:
        配置好的 SandboxExecutor 实例
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

    executor = SandboxExecutor(
        timeout=timeout,
        max_result_chars=max_result_chars,
        output_dir=_output_dir,
        staging_dir=_staging_dir,
        workspace_dir=_workspace_dir,
        upload_fn=_auto_upload,
    )

    # read_file: 仅允许读取 staging 目录（对标 OpenAI Code Interpreter）
    from core.config import get_settings as _get_settings
    from services.file_executor import FileExecutor as _FileExecutor

    _file_settings = _get_settings()

    def _make_file_executor() -> "_FileExecutor":
        return _FileExecutor(
            workspace_root=_file_settings.file_workspace_root,
            user_id=user_id,
            org_id=org_id,
        )

    async def _read_file(path: str, encoding: str = "utf-8") -> str:
        if not path.startswith("staging/"):
            return (
                "❌ 沙盒内只能读取 staging 目录下的数据文件。"
                "请先用 local_db_export 或 fetch_all_pages 工具获取数据。"
            )
        if path.endswith(".parquet"):
            return (
                "❌ Parquet 文件不能用 read_file 读取，"
                "请用 pd.read_parquet(STAGING_DIR + '/文件名') 读取。"
            )
        # 直接读原始文件内容（不走 file_read 的行号格式化），
        # 这样 pandas 的 pd.read_json(io.StringIO(raw), lines=True) 能正确解析
        fe = _make_file_executor()
        target = fe.resolve_safe_path(path)
        if not target.exists():
            return f"❌ 文件不存在: {path}"
        return target.read_text(encoding=encoding)

    executor.register("read_file", _read_file)

    # upload_file 已删除 — 所有文件输出走 OUTPUT_DIR（workspace/下载/），ossfs 自动同步到 OSS

    return executor


def compute_code_hash(code: str) -> str:
    """计算代码 MD5 指纹（执行日志去重用）"""
    return hashlib.md5(code.strip().encode()).hexdigest()[:12]
