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
    """渲染结构化附件元数据（XML <attachments> 块）追加到用户消息文本。

    每个 <file> 块包含：
      - name/type/format/size: 基础元数据
      - source: 来源（本轮上传 / 工作区引用）—— 根据 workspace_path 前缀粗判
      - dimensions: 图片专属（width×height）
      - status: LLM 行动指引核心字段（"未分析则调 file_analyze" / "已可视无需读取" 等）

    status 会根据 file_path_cache 的 analyzed 状态动态切换：
      xlsx/csv 未分析 → 引导调 file_analyze
      xlsx/csv 已分析 → 引导用 get_file + duckdb（schema 信息在 messages 历史里）
    """
    if not workspace_files:
        return ""

    # ── 查询 file_path_cache 的 analyzed 状态（已分析的 xlsx/csv 切换 status）──
    cache = None
    if conversation_id:
        try:
            from services.agent.file_path_cache import get_file_cache
            cache = get_file_cache(conversation_id)
        except Exception as e:
            logger.warning(f"format_attachments: get_file_cache 失败 | {e}")

    # ── 构建 XML ──
    lines: list[str] = []
    lines.append("")  # 与上面 user 文本留空行
    lines.append("")
    lines.append(
        f"<attachments count=\"{len(workspace_files)}\" "
        f"hint=\"status 字段是行动指引；每个文件按 status 决定下一步操作\">"
    )

    for f in workspace_files:
        raw_name = f.get("name") or f.get("workspace_path", "")
        name = _esc(raw_name)
        wp = f.get("workspace_path") or ""
        size_str = _fmt_size(f.get("size"))
        ext = _ext_of(raw_name)
        format_str = ext.lstrip(".") or "unknown"

        # 来源判断：上传/ 前缀视为"本轮上传"，否则"工作区引用"
        source = "本轮上传" if wp.startswith("上传/") else "工作区引用"

        # 类型 + status 按扩展名分流
        if ext in _IMG_EXTS:
            ftype = "图片"
            status = (
                "已自动注入视觉，可直接观察图片内容。"
                "不要调用任何文件读取工具。"
            )
        elif ext in _DATA_EXTS:
            ftype = "数据文件"
            analyzed = cache.is_analyzed(raw_name) if cache else False
            if analyzed:
                status = (
                    f"已分析。直接在 code_execute 中用 "
                    f"get_file(\"{raw_name}\") + duckdb 查询。"
                )
            else:
                status = (
                    f"未分析。如需查询数据，先调用 "
                    f"file_analyze(\"{raw_name}\")。"
                )
        elif ext in _PDF_EXTS:
            ftype = "文档"
            status = (
                f"PDF 文档。在 code_execute 中用 pdfplumber + "
                f"get_file(\"{raw_name}\") 读取内容。"
            )
        elif ext in _WORD_EXTS:
            ftype = "文档"
            status = (
                f"Word 文档。在 code_execute 中用 python-docx + "
                f"get_file(\"{raw_name}\") 读取内容。"
            )
        elif ext in _PPT_EXTS:
            ftype = "文档"
            status = (
                f"PowerPoint 文档。在 code_execute 中用 python-pptx + "
                f"get_file(\"{raw_name}\") 读取内容。"
            )
        elif ext in _TEXT_EXTS:
            ftype = "文本"
            status = (
                f"文本文件。在 code_execute 中用 "
                f"open(get_file(\"{raw_name}\")) 读取。"
            )
        else:
            ftype = "二进制"
            status = (
                f"未识别格式 .{format_str}。"
                f"如需处理，在 code_execute 中用 get_file 取路径后按需读取。"
            )

        lines.append("  <file>")
        lines.append(f"    <name>{name}</name>")
        lines.append(f"    <type>{ftype}</type>")
        lines.append(f"    <format>{_esc(format_str)}</format>")
        if size_str:
            lines.append(f"    <size>{size_str}</size>")
        if ext in _IMG_EXTS:
            w, h = f.get("width"), f.get("height")
            if w and h:
                lines.append(f"    <dimensions>{int(w)}×{int(h)}</dimensions>")
        lines.append(f"    <source>{source}</source>")
        lines.append(f"    <status>{_esc(status)}</status>")
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
