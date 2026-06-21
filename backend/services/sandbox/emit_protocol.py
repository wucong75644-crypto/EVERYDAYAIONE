"""emit 协议:沙盒内主动声明产物给主进程(流派 2 多字段 IPC,对齐 OpenAI/Anthropic)

LLM 调用 emit_xxx() → buffer 收集 payload → _exec_code 返回 (stdout, payloads)
→ kernel_worker JSON-Line 协议独立字段传回主进程 → executor 拿 emit_payloads。

为什么用 buffer 不用 print:
  kernel_worker 用 sys.stdin/stdout JSON-Line 跟主进程通信(沙盒响应通道)。
  emit 函数若直接 print() 会污染协议通道导致 JSON 解析失败。
  改成 buffer 收集 → _exec_code 返回 (stdout, payloads) 元组 → IPC 独立字段。

设计文档:docs/document/TECH_沙盒IO统一协议.md
状态:正式协议(流派 2 字段分离 2026-06),全局始终启用

产物 payload kind:
  chart: {kind, spec_format, title, option}   — ECharts/plotly/vegalite spec
  file:  {kind, path, label, name, size, url} — 文件下载卡片
  image: {kind, path, alt, name, width, height, url} — 图片
  table: {kind, title, columns, rows, truncated, total_rows} — 表格
"""
from __future__ import annotations

import os
from typing import Any

# 表格行数上限(防大表撑爆 IPC / 前端渲染)
# 注意:前端 TableBlock.tsx 也有 MAX_PREVIEW_ROWS,两端必须一致
_TABLE_MAX_ROWS = 200


# ============================================================
# Payload 构造函数(纯函数,无副作用,沙盒/主进程/测试共用)
# ============================================================


def build_chart_payload(option: dict, title: str = "") -> dict:
    """构造 chart payload(ECharts option dict)。

    手动 emit_chart 入口 → spec_format 固定 echarts。
    plotly/vegalite 走 emit_auto_hooks 的 publish 链路,自带各自 spec_format。
    """
    if not isinstance(option, dict):
        raise TypeError(f"emit_chart option 必须是 dict,收到 {type(option).__name__}")
    return {
        "kind": "chart",
        "spec_format": "echarts",
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
    """构造 image payload(沙盒内调用,自动读取 PIL 尺寸)。

    width/height 在沙盒内用 PIL 读取直接填进 payload,
    避免后端 chat_handler 反查 _image_dims 字典。
    """
    if not path:
        raise ValueError("emit_image path 不能为空")
    name = os.path.basename(str(path))
    payload: dict = {
        "kind": "image",
        "path": str(path),
        "alt": alt or name,
        "name": name,
    }
    # PIL 读尺寸(失败不阻断,前端可走默认)
    try:
        from PIL import Image  # type: ignore
        with Image.open(str(path)) as im:
            payload["width"] = im.width
            payload["height"] = im.height
    except Exception:
        pass
    return payload


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
    """在 sandbox_globals 注入 emit_xxx 函数,闭包绑定 buffer。

    sandbox 执行用户代码前调一次。emit_xxx 调用时不打 print,而是把 payload
    append 到 buffer。_exec_code 末尾把 buffer 作为 emit_payloads 返回,
    走 IPC 独立字段传到主进程,完整无截断。

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
