"""emit 协议:沙盒内主动声明产物给主进程

LLM 调用 emit_xxx() → buffer 收集 payload → 沙盒 _exec_code 收尾时合并成
[EMIT]{json}[/EMIT] marker 拼到 stdout_text → kernel_worker JSON-Line 协议
传回主进程 → tool_loop_executor 解析路由。

为什么用 buffer 不用 print:
  kernel_worker 用 sys.stdin/stdout JSON-Line 跟主进程通信(沙盒响应通道)。
  emit 函数若直接 print() 会污染协议通道导致 JSON 解析失败。
  改成 buffer 收集 + _exec_code 末尾合并到用户 stdout 文本(走正确通道)。

设计文档:docs/document/TECH_沙盒IO统一协议_调研.md (推荐方案 B)
状态:POC 阶段(2026-06),受 settings.emit_protocol_enabled flag 控制

protocol(向 LLM/前端):
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


# ============================================================
# Payload 构造函数(纯函数,无副作用,沙盒/主进程/测试共用)
# ============================================================


def build_chart_payload(option: dict, title: str = "") -> dict:
    """构造 chart payload(ECharts option dict)"""
    if not isinstance(option, dict):
        raise TypeError(f"emit_chart option 必须是 dict,收到 {type(option).__name__}")
    return {
        "kind": "chart",
        "title": title or "",
        "option": option,
    }


def build_file_payload(path: str, label: str | None = None) -> dict:
    """构造 file payload(文件下载卡片)

    沙盒内调用 os.path.getsize(path) 拿真实大小;path 不存在 size=0。
    """
    if not path:
        raise ValueError("emit_file path 不能为空")
    name = os.path.basename(str(path))
    try:
        size = os.path.getsize(str(path)) if os.path.exists(str(path)) else 0
    except OSError:
        size = 0
    return {
        "kind": "file",
        "path": str(path),
        "label": label or name,
        "name": name,
        "size": size,
    }


def build_image_payload(path: str, alt: str = "") -> dict:
    """构造 image payload"""
    if not path:
        raise ValueError("emit_image path 不能为空")
    name = os.path.basename(str(path))
    return {
        "kind": "image",
        "path": str(path),
        "alt": alt or name,
        "name": name,
    }


def build_table_payload(data: Any, title: str = "") -> dict:
    """构造 table payload(pandas DataFrame / list[dict] / dict)"""
    rows: list[dict] = []
    columns: list[str] = []

    if hasattr(data, "to_dict") and callable(data.to_dict):
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

    return {
        "kind": "table",
        "title": title or "",
        "columns": columns,
        "rows": rows,
        "truncated": truncated,
        "total_rows": len(rows) if not truncated else f"{_TABLE_MAX_ROWS}+(截断)",
    }


# ============================================================
# 沙盒注入入口(_exec_code 调用)
# ============================================================


def install_emit_in_globals(sandbox_globals: dict, buffer: list[dict]) -> None:
    """在 sandbox_globals 注入 emit_xxx 函数,闭包绑定 buffer

    sandbox 执行用户代码前调一次。emit_xxx 调用时不打 print,而是把 payload
    append 到 buffer。_exec_code 末尾把 buffer 转 [EMIT] marker 拼到 stdout_text。

    Args:
        sandbox_globals: 沙盒执行环境的 globals dict
        buffer: 收集 emit payloads 的 list(同一对象,被闭包引用)
    """
    sandbox_globals["emit_chart"] = (
        lambda option, title="": buffer.append(build_chart_payload(option, title))
    )
    sandbox_globals["emit_file"] = (
        lambda path, label=None: buffer.append(build_file_payload(path, label))
    )
    sandbox_globals["emit_image"] = (
        lambda path, alt="": buffer.append(build_image_payload(path, alt))
    )
    sandbox_globals["emit_table"] = (
        lambda data, title="": buffer.append(build_table_payload(data, title))
    )


def format_emit_markers(buffer: list[dict]) -> str:
    """把 buffer 转成 [EMIT]{json}[/EMIT] marker 文本(每条一行)

    _exec_code 末尾调用,合并到用户 stdout_text 末尾。
    """
    if not buffer:
        return ""
    return "\n".join(
        f"{EMIT_MARKER_START}{json.dumps(p, ensure_ascii=False, default=str)}{EMIT_MARKER_END}"
        for p in buffer
    )


# ============================================================
# 测试/兼容包装(直接打印版,仅供测试,不建议生产用)
# ============================================================
# 旧版 API 兼容:有些守护测试还在 capture print 验证。保留 print 版本
# 但内部用 build_xxx_payload 共享逻辑。

def _print_payload(payload: dict) -> None:
    print(
        f"{EMIT_MARKER_START}"
        f"{json.dumps(payload, ensure_ascii=False, default=str)}"
        f"{EMIT_MARKER_END}"
    )


def emit_chart(option: dict, title: str = "") -> None:
    """直接打印版(仅供守护测试 capture stdout)"""
    _print_payload(build_chart_payload(option, title))


def emit_file(path: str, label: str | None = None) -> None:
    """直接打印版"""
    _print_payload(build_file_payload(path, label))


def emit_image(path: str, alt: str = "") -> None:
    """直接打印版"""
    _print_payload(build_image_payload(path, alt))


def emit_table(data: Any, title: str = "") -> None:
    """直接打印版"""
    _print_payload(build_table_payload(data, title))


# 沙盒不再用这个(改用 install_emit_in_globals);保留以兼容历史 import
EMIT_FUNCTIONS = {
    "emit_chart": emit_chart,
    "emit_file": emit_file,
    "emit_image": emit_image,
    "emit_table": emit_table,
}
