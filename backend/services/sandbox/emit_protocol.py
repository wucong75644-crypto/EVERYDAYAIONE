"""emit 协议:沙盒内主动声明产物给主进程

LLM 调用 emit_xxx() → 沙盒 print [EMIT]{json}[/EMIT] marker
→ 主进程 tool_loop_executor 解析 → 按 kind 路由 → chat_handler 推前端 block

设计文档:docs/document/TECH_沙盒IO统一协议_调研.md (推荐方案 B)
状态:POC 阶段(2026-06),受 settings.emit_protocol_enabled flag 控制

protocol:
  [EMIT]{"kind":"chart","title":"...","option":{...}}[/EMIT]
  [EMIT]{"kind":"file","path":"下载/x.xlsx","label":"销售报表","size":2048}[/EMIT]
  [EMIT]{"kind":"image","path":"下载/x.png","alt":""}[/EMIT]
  [EMIT]{"kind":"table","title":"...","columns":[...],"rows":[...]}[/EMIT]
"""
from __future__ import annotations

import json
import os
from typing import Any

EMIT_MARKER_START = "[EMIT]"
EMIT_MARKER_END = "[/EMIT]"

# 表格行数上限(防大表把 marker 撑爆)
_TABLE_MAX_ROWS = 200


def _print_marker(payload: dict[str, Any]) -> None:
    """统一 marker 输出格式,中文不转义"""
    print(
        f"{EMIT_MARKER_START}"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}"
        f"{EMIT_MARKER_END}"
    )


def emit_chart(option: dict, title: str = "") -> None:
    """声明 ECharts 交互式图表

    Args:
        option: 完整 ECharts option dict(含 series/xAxis/yAxis/title 等)
        title: 图表标题(可选,会覆盖 option.title.text)

    示例:
        option = {"xAxis":{"data":["A","B"]}, "yAxis":{}, "series":[{"type":"bar","data":[1,2]}]}
        emit_chart(option, title="销售额")
    """
    if not isinstance(option, dict):
        raise TypeError(f"emit_chart option 必须是 dict,收到 {type(option).__name__}")
    _print_marker({
        "kind": "chart",
        "title": title or "",
        "option": option,
    })


def emit_file(path: str, label: str | None = None) -> None:
    """声明文件下载卡片

    Args:
        path: 文件路径(相对 workspace,如 '下载/x.xlsx')
        label: 显示名(可选,默认用 basename)

    LLM 必须先 df.to_excel(path) 或类似写文件,再 emit_file(path)。
    没 emit 的文件不会推送给用户。
    """
    if not path:
        raise ValueError("emit_file path 不能为空")
    name = os.path.basename(str(path))
    try:
        size = os.path.getsize(str(path)) if os.path.exists(str(path)) else 0
    except OSError:
        size = 0
    _print_marker({
        "kind": "file",
        "path": str(path),
        "label": label or name,
        "name": name,
        "size": size,
    })


def emit_image(path: str, alt: str = "") -> None:
    """声明图片(PNG/JPG/SVG 等),前端按 type=image 渲染

    Args:
        path: 图片路径(相对 workspace)
        alt: 替代文本(可选)
    """
    if not path:
        raise ValueError("emit_image path 不能为空")
    name = os.path.basename(str(path))
    _print_marker({
        "kind": "image",
        "path": str(path),
        "alt": alt or name,
        "name": name,
    })


def emit_table(data: Any, title: str = "") -> None:
    """声明交互式表格(pandas DataFrame 或 list[dict])

    Args:
        data: pandas.DataFrame 或 list[dict] 或 dict(单行)
        title: 表格标题
    """
    rows: list[dict] = []
    columns: list[str] = []

    if hasattr(data, "to_dict") and callable(data.to_dict):
        # pandas DataFrame
        rows = data.to_dict("records")
        columns = list(getattr(data, "columns", []))
    elif isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
        columns = list(rows[0].keys()) if rows else []
    elif isinstance(data, dict):
        rows = [data]
        columns = list(data.keys())
    else:
        raise TypeError(
            f"emit_table data 必须是 DataFrame/list[dict]/dict,收到 {type(data).__name__}"
        )

    truncated = len(rows) > _TABLE_MAX_ROWS
    if truncated:
        rows = rows[:_TABLE_MAX_ROWS]

    _print_marker({
        "kind": "table",
        "title": title or "",
        "columns": columns,
        "rows": rows,
        "truncated": truncated,
        "total_rows": len(rows) if not truncated else f"{_TABLE_MAX_ROWS}+(截断)",
    })


# 沙盒注入入口:由 sandbox_worker._build_sandbox_globals 调用
EMIT_FUNCTIONS = {
    "emit_chart": emit_chart,
    "emit_file": emit_file,
    "emit_image": emit_image,
    "emit_table": emit_table,
}
