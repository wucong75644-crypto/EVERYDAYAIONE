"""ECharts 独立 chart block 构造器(路径协议 v2 新增)。

Phase 1 路径协议改造后,.echart.json 写入 staging 目录(中转数据,读完即删),
不再走 _auto_upload_new_files → FilePart → chat_handler 的旧渲染链路。

但前端 chart 渲染依赖 chat_handler 推送 type=chart 的 content block。
本模块提供独立的"_chart_options → chart blocks"转换,在 chat_handler 处理完
file blocks 后调用,确保 staging 模式产出的 charts 仍能正确推送到前端。

向后兼容:旧链路(echart.json 进 OSS → FilePart)产生的 chart block 在
existing_blocks 中已存在,本函数会按 title 去重不重复推送。
"""
from __future__ import annotations

from typing import Any


def build_orphan_chart_blocks(
    chart_options: dict[str, Any],
    existing_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """从 _chart_options 构造 type=chart 的 content blocks,跳过已生成的。

    Args:
        chart_options: filename → ECharts option 字典
                       (executor._chart_options,新协议下由 _scan_chart_options 填充)
        existing_blocks: 当前已推送的 content blocks 列表
                        (用于检测旧链路 FilePart 是否已生成同 title 的 chart)

    Returns:
        待新推送的 chart block 列表(每个 block 含 type/option/title/chart_type)。
        existing_blocks 中已有同 title 的 chart 不重复生成。
    """
    if not chart_options:
        return []

    # 旧链路兼容:已通过 FilePart 路径生成的 chart titles
    consumed_titles: set[str] = {
        b.get("title", "")
        for b in existing_blocks
        if isinstance(b, dict) and b.get("type") == "chart"
    }

    blocks: list[dict[str, Any]] = []
    for _filename, opt in chart_options.items():
        if not isinstance(opt, dict):
            continue
        title = _extract_title(opt)
        if title and title in consumed_titles:
            continue  # 旧链路已渲染,不重复
        chart_type = _extract_chart_type(opt)
        blocks.append({
            "type": "chart",
            "option": opt,
            "title": title,
            "chart_type": chart_type,
        })
    return blocks


def _extract_title(opt: dict[str, Any]) -> str:
    """从 ECharts option 提取 title.text(兼容 dict / list / 缺失)"""
    title = opt.get("title")
    if isinstance(title, dict):
        return title.get("text", "") or ""
    if isinstance(title, list) and title:
        first = title[0]
        if isinstance(first, dict):
            return first.get("text", "") or ""
    return ""


def _extract_chart_type(opt: dict[str, Any]) -> str:
    """从 ECharts option 提取 series[0].type"""
    series = opt.get("series")
    if isinstance(series, list) and series:
        first = series[0]
        if isinstance(first, dict):
            return first.get("type", "") or ""
    return ""
