"""结构化附件元数据（XML <attachments> + status + 配对 <action> 行动指引）。

设计依据：Anthropic prompt engineering — XML 锚点 + 每个 status 配对显式行动
指引，避免模型跨段查 system prompt 拼信息。

_STATUS_ACTIONS：单一事实来源（DRY），每个 status 必须有对应 action。
"""

import html
from typing import Any, Dict, List, Optional

from loguru import logger


_DATA_EXTS = {".xlsx", ".xls", ".csv", ".tsv"}
_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_PDF_EXTS = {".pdf"}
_WORD_EXTS = {".docx", ".doc"}
_PPT_EXTS = {".pptx", ".ppt"}
_TEXT_EXTS = {
    ".txt", ".md", ".json", ".yaml", ".yml", ".xml", ".log",
    ".py", ".js", ".ts", ".html", ".css", ".sql",
}

# status → action 单一事实来源（修改时只动这里）
_STATUS_ACTIONS: Dict[str, str] = {
    "image": "已视觉注入，直接看图回答，无需任何文件工具",
    "raw": "调 file_analyze 转 Parquet（仅 .xlsx/.xls/.csv/.tsv 支持）",
    "analyzed": "用 <parquet> 字段在 code_execute 中 pd.read_parquet 读取，不要重复 file_analyze",
    "parquet": "用 <path> 字段在 code_execute 中 pd.read_parquet 读取",
    "doc": "用 code_execute + PyPDF2/python-docx/python-pptx 读取",
    "text": "用 code_execute open() 读取",
    "binary": "未知文件类型，询问用户期望如何处理",
}


def _fmt_size(size: Any) -> str:
    if not size:
        return ""
    size = int(size)
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def _esc(s: Any) -> str:
    return html.escape(str(s or ""), quote=False)


def _ext_of(name: str) -> str:
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


def format_attachments(
    workspace_files: List[Dict[str, Any]],
    conversation_id: Optional[str] = None,
    org_id: Optional[str] = None,
) -> str:
    """渲染结构化附件元数据(行业标准:状态 + 显式 action 行动指引)。

    设计原则(对齐 OpenAI Assistants / Claude / Gemini):
      - <id>: 短 ASCII file_id（fid_xxx），调工具时 LLM copy 这个，避免 pangu 化
      - <name>: 文件名，AI 跟用户对话时引用（"我已读取《4月销售》..."）
      - <path>: 沙盒内 open/pd.read_excel 读取用（沙盒已有去空格兜底）
      - <status>: 状态码（image/raw/analyzed/parquet/doc/text/binary）
      - <action>: 与 status 配对的最小化行动指引（单一事实来源 _STATUS_ACTIONS）
      - <parquet>: 仅 analyzed 数据文件有,LLM 直接 pd.read_parquet 读
    """
    if not workspace_files:
        return ""

    # 查 file_path_cache:已分析的数据文件拿 parquet 相对路径
    cache = None
    if conversation_id:
        try:
            from services.agent.file_path_cache import get_file_cache
            cache = get_file_cache(conversation_id)
        except Exception as e:
            logger.warning(f"format_attachments: get_file_cache 失败 | {e}")

    lines: list[str] = ["", "", f"<attachments count=\"{len(workspace_files)}\">"]

    for f in workspace_files:
        raw_name = f.get("name") or f.get("workspace_path", "")
        name = _esc(raw_name)
        wp = f.get("workspace_path") or ""
        size_str = _fmt_size(f.get("size"))
        ext = _ext_of(raw_name)

        # 状态分流(纯状态,不含指令)
        if ext in _IMG_EXTS:
            status = "image"
            parquet_rel = None
        elif ext in _DATA_EXTS:
            analyzed = cache.is_analyzed(raw_name) if cache else False
            if analyzed:
                status = "analyzed"
                # 取 parquet basename,渲染为 staging 相对路径
                # (沙盒 cwd=/workspace,实际路径 = /workspace/staging/{basename})
                parquet_rel = None
                if cache:
                    entry = cache._resolve_entry(raw_name)
                    if entry and entry.parquet:
                        import os as _os
                        parquet_rel = f"staging/{_os.path.basename(entry.parquet)}"
            else:
                status = "raw"
                parquet_rel = None
        elif ext == ".parquet":
            status = "parquet"
            parquet_rel = None
        elif ext in _PDF_EXTS or ext in _WORD_EXTS or ext in _PPT_EXTS:
            status = "doc"
            parquet_rel = None
        elif ext in _TEXT_EXTS:
            status = "text"
            parquet_rel = None
        else:
            status = "binary"
            parquet_rel = None

        lines.append("  <file>")
        if wp:
            from services.agent.file_id import compute_fid
            lines.append(f"    <id>{compute_fid(org_id, wp)}</id>")
        lines.append(f"    <name>{name}</name>")
        if wp:
            lines.append(f"    <path>{_esc(wp)}</path>")
        if size_str:
            lines.append(f"    <size>{size_str}</size>")
        if ext in _IMG_EXTS:
            w, h = f.get("width"), f.get("height")
            if w and h:
                lines.append(f"    <dimensions>{int(w)}×{int(h)}</dimensions>")
        if parquet_rel:
            lines.append(f"    <parquet>{_esc(parquet_rel)}</parquet>")
        lines.append(f"    <status>{status}</status>")
        lines.append(f"    <action>{_STATUS_ACTIONS[status]}</action>")
        lines.append("  </file>")

    lines.append("</attachments>")
    lines.append("")
    lines.append("【附件使用规则】")
    lines.append("- 调工具(file_analyze/file_delete 等)时，file_id 参数必须 copy `id` 字段（fid_xxx）")
    lines.append("- 回复用户、生成图表标题、说明分析对象时，引用 `name` 字段")
    lines.append("- 沙盒 code_execute 内读取数据时，用 `path` 字段（如 pd.read_excel(path)）")
    lines.append("- 已治理数据文件直接 pd.read_parquet(`parquet` 字段)；不要重复 file_analyze")
    return "\n".join(lines)


def _fmt_size_long(size: Any) -> str:
    if not size:
        return ""
    size = int(size)
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def build_workspace_prompt(
    workspace_files: List[Dict[str, Any]],
    conversation_id: Optional[str] = None,
    org_id: Optional[str] = None,
) -> str:
    """渲染工作区文件清单（注意力锚点 system message）。

    设计原则（与 attachments XML 解耦）：
      - 只声明附件存在 + 状态分类（元事实），让模型在含糊指代下保持注意力
      - 不硬编码工具名（file_analyze/code_execute）— 工具调用方式见 attachments XML 的 <action>
      - 状态来自 file_path_cache，与 attachments XML 的 <status> 保持一致

    数据驱动依据：含糊指代场景（"再算下"）下，独立 system message 能提供额外的
    注意力锚点，将 PASS 率从 80% 推到 100%（compare_description_fix.py 实测）。
    """
    if not workspace_files:
        return ""

    cache = None
    if conversation_id:
        try:
            from services.agent.file_path_cache import get_file_cache
            cache = get_file_cache(conversation_id)
        except Exception as e:
            logger.warning(f"build_workspace_prompt: get_file_cache 失败 | {e}")

    from services.agent.file_id import compute_fid

    lines: list[str] = [f"用户当前消息附加了 {len(workspace_files)} 个文件："]
    for f in workspace_files:
        raw_name = f.get("name") or f.get("workspace_path", "")
        wp = f.get("workspace_path") or ""
        size_str = _fmt_size_long(f.get("size"))
        ext = _ext_of(raw_name)
        fid_prefix = f"[{compute_fid(org_id, wp)}] " if wp else ""

        if ext in _IMG_EXTS:
            kind = "图片（已视觉注入）"
        elif ext in _DATA_EXTS:
            is_analyzed = cache.is_analyzed(raw_name) if cache else False
            kind = "数据文件（已分析）" if is_analyzed else "数据文件（待治理）"
        elif ext == ".parquet":
            kind = "Parquet 数据"
        elif ext in _PDF_EXTS or ext in _WORD_EXTS or ext in _PPT_EXTS:
            kind = "文档"
        elif ext in _TEXT_EXTS:
            kind = "文本文件"
        else:
            kind = "未知类型"

        size_suffix = f" ({size_str})" if size_str else ""
        lines.append(f"  - {fid_prefix}{raw_name}{size_suffix} — {kind}")

    return "\n".join(lines)
