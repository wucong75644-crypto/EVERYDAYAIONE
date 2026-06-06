"""Phase 8: 结构化附件元数据（XML <attachments> + status 行动指引）。

设计依据：Anthropic prompt engineering 文档 — XML 标签让 Claude 系模型
给附件元数据稳定的注意力锚点；project 已用 <system-reminder> 等 XML 范式。
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
) -> str:
    """渲染结构化附件元数据(行业标准:纯状态声明 + 相对路径)。

    设计原则(对齐 OpenAI Assistants / Claude / Gemini):
      - <status> 仅是状态(raw / analyzed / parquet / ready),不含可执行代码
      - <path>:相对 workspace 的路径,LLM 字面 copy 用
      - <parquet>:仅 analyzed 数据文件有,LLM 直接 pd.read_parquet 读
      - LLM 看 tools(API tools 字段) + status 自主决策选哪个工具

    状态枚举:
      - raw      : 未分析的 xlsx/csv,需要 file_analyze 工具治理
      - analyzed : 已分析,parquet_path 字段直接给 pd.read_parquet 用
      - parquet  : 已是 parquet 格式,直接读 path 即可
      - image    : 图片(多模态注入,无需工具)
      - doc      : 文档(PDF/Word/PPT)用 code_execute + 相应库读
      - text     : 文本文件
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
        lines.append("  </file>")

    lines.append("</attachments>")
    return "\n".join(lines)


def build_workspace_prompt(workspace_files: List[Dict[str, Any]]) -> str:
    """生成工作区文件提示——告知文件名，引导数据文件用 file_analyze。"""
    if not workspace_files:
        return ""

    def _fmt_size_long(size: Any) -> str:
        if not size:
            return ""
        size = int(size)
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    lines: list[str] = ["用户当前消息附加的文件："]
    for f in workspace_files:
        wp = f.get("workspace_path", "")
        size_str = _fmt_size_long(f.get("size"))
        name = f.get("name", wp)
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        if ext in {".xlsx", ".xls", ".csv", ".tsv"}:
            lines.append(f"  '{name}'  ({size_str}) — 数据文件，用 file_analyze 读取")
        else:
            lines.append(f"  '{name}'  ({size_str})")

    return "\n".join(lines)
