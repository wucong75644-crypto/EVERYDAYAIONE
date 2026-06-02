"""
沙盒错误格式化 — 核心信息结构化输出

设计原则与项目其他模块（AgentResult / <attachments>）保持一致：
  - 核心 4 字段（type/message/user_line/user_code）信息密度优先
  - 冗余 traceback 默认丢弃，仅提取不到 user_frame 时 fallback 附带
  - XML 标签确保关键字段（type/message）不被 token 预算截断

输出格式：
  <sandbox_error>
    <type>DuckDBException</type>
    <message>Catalog Error: Table 'shop_sales' does not exist</message>
    <user_line>23</user_line>
    <user_code>result = duckdb.sql("SELECT ... FROM shop_sales")</user_code>
  </sandbox_error>

无法提取 user_frame 时的 fallback（罕见，如 numpy C 内部错、SIGKILL）：
  <sandbox_error>
    <type>...</type>
    <message>...</message>
    <traceback_excerpt>...末尾 5 行...</traceback_excerpt>
  </sandbox_error>
"""

from __future__ import annotations

import html
import traceback
from typing import Optional


# 沙盒内用户代码用 compile(tree, "<sandbox>", "exec") 编译，
# traceback frame 的 filename 永远是这个常量，用它定位用户代码栈帧。
_SANDBOX_FILENAME = "<sandbox>"


def format_sandbox_error(
    exc: BaseException,
    source_code: Optional[str] = None,
) -> str:
    """格式化沙盒异常为 XML 结构化字符串。

    Args:
        exc: 异常对象（含 __traceback__ 链）。
        source_code: 用户提交的代码字符串。沙盒代码编译时 filename = "<sandbox>"
                     不是真实文件，traceback frame.line 在 Python 3.11- 上可能
                     为 None，需要从 source_code 按 lineno 切片提取出错行。

    Returns:
        XML 字符串，至少包含 <type> 和 <message>。其他字段尽力提取。
        防御性：内部 try-except 包裹所有提取逻辑，永远不抛异常
        （错误处理路径不能再抛错，否则整个错误返回链路崩溃）。
    """
    # ── 核心字段：type + message（异常本身始终可拿） ──
    try:
        exc_type = type(exc).__name__
    except Exception:
        exc_type = "UnknownException"

    try:
        exc_message = str(exc) or "<empty>"
    except Exception:
        exc_message = "<message extraction failed>"

    # ── 用户代码字段：user_line + user_code（尽力提取） ──
    user_line: Optional[int] = None
    user_code: Optional[str] = None
    try:
        tb_frames = traceback.extract_tb(exc.__traceback__)
        user_frames = [f for f in tb_frames if f.filename == _SANDBOX_FILENAME]
        if user_frames:
            last = user_frames[-1]
            user_line = last.lineno
            # 优先 frame.line（Python 自带），否则从 source_code 切片
            if last.line:
                user_code = last.line.strip()
            elif source_code and user_line:
                lines = source_code.split("\n")
                if 1 <= user_line <= len(lines):
                    user_code = lines[user_line - 1].strip()
    except Exception:
        pass  # 提取失败不影响核心字段

    # ── 组装 XML（核心字段在最前，确保 token 预算截断时仍能透出） ──
    parts: list[str] = ["<sandbox_error>"]
    parts.append(f"  <type>{html.escape(exc_type)}</type>")
    parts.append(f"  <message>{html.escape(exc_message)}</message>")

    if user_line is not None:
        parts.append(f"  <user_line>{user_line}</user_line>")
    if user_code:
        parts.append(f"  <user_code>{html.escape(user_code)}</user_code>")

    # ── Fallback：无用户 frame 时附简短 traceback excerpt（罕见） ──
    if user_line is None:
        try:
            tb_str = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ).strip()
            tb_lines = tb_str.split("\n")
            excerpt = "\n".join(tb_lines[-5:])
            parts.append(f"  <traceback_excerpt>{html.escape(excerpt)}</traceback_excerpt>")
        except Exception:
            pass

    parts.append("</sandbox_error>")
    return "\n".join(parts)
