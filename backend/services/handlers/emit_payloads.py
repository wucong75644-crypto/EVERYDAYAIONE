"""显式 emit payload 到消息内容协议的转换。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, Optional

from schemas.message import (
    ChartPart,
    ContentPart,
    DiagramPart,
    FilePart,
    ImagePart,
    TablePart,
    serialize_content_part,
)
from services.sandbox.emit_protocol import _TABLE_MAX_ROWS


def _extract_chart_title(option: Dict[str, Any]) -> str:
    title = option.get("title") if isinstance(option, dict) else None
    if isinstance(title, dict):
        return title.get("text", "") or ""
    if isinstance(title, list) and title and isinstance(title[0], dict):
        return title[0].get("text", "") or ""
    return ""


def _extract_chart_type(option: Dict[str, Any]) -> str:
    if not isinstance(option, dict):
        return ""
    series = option.get("series")
    if isinstance(series, list) and series and isinstance(series[0], dict):
        return series[0].get("type", "") or ""
    return ""


def build_block_from_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """把可信 emit payload 转为可持久化 content block。"""
    kind = payload.get("kind")
    if kind == "chart":
        option = payload.get("option") or {}
        return {
            "type": "chart",
            "option": option,
            "title": payload.get("title") or _extract_chart_title(option),
            "chart_type": _extract_chart_type(option),
            "spec_format": payload.get("spec_format") or "echarts",
        }
    if kind == "diagram":
        source = payload.get("source")
        if not isinstance(source, str) or not source.strip():
            return None
        return {
            "type": "diagram",
            "format": payload.get("format") or "mermaid",
            "source": source,
            "title": payload.get("title") or "",
        }
    if kind == "table":
        return {
            "type": "table",
            "title": payload.get("title", ""),
            "columns": payload.get("columns", []),
            "rows": payload.get("rows", []),
            "truncated": payload.get("truncated", False),
        }
    if kind == "image":
        url = payload.get("url")
        if not url and not payload.get("failed"):
            return None
        block = {
            "type": "image",
            "url": url,
            "alt": payload.get("alt") or payload.get("name", ""),
        }
        for field in (
            "width", "height", "workspace_path", "original_url",
            "thumbnail_url", "preview_url", "download_url",
        ):
            if payload.get(field):
                block[field] = payload[field]
        if payload.get("failed"):
            block["failed"] = True
            for field in ("error", "retry_context"):
                if payload.get(field):
                    block[field] = payload[field]
        return block
    if kind == "file":
        return {
            "type": "file",
            "url": payload.get("url", ""),
            "name": payload.get("name", ""),
            "mime_type": payload.get("mime_type", ""),
            "size": payload.get("size"),
            "workspace_path": payload.get("workspace_path"),
        }
    return None


def build_part_from_payload(payload: Dict[str, Any]) -> Optional[ContentPart]:
    """把可信 emit payload 转为后端 ContentPart。"""
    block = build_block_from_payload(payload)
    if not block:
        return None
    block_type = block["type"]
    if block_type == "image":
        return ImagePart(**block)
    if block_type == "file":
        return FilePart(**block)
    if block_type == "chart":
        return ChartPart(**block)
    if block_type == "diagram":
        return DiagramPart(**block)
    if block_type == "table":
        return TablePart(**block)
    return None


def build_table_payload_from_agent_result(result: Any) -> Optional[Dict[str, Any]]:
    """把 AgentResult(TABLE) 接入现有 emit/content-block 协议。

    AgentResult.to_message_content() 仍只服务模型上下文；用户侧统一走
    emit payload → content block，避免维护第二套表格渲染路径。
    """
    format_object = getattr(result, "format", None)
    format_value = getattr(format_object, "value", format_object)
    if format_value != "table":
        return None

    raw_columns = getattr(result, "columns", None)
    raw_rows = getattr(result, "data", None)
    if not isinstance(raw_columns, list) or not raw_columns:
        return None
    if not isinstance(raw_rows, list) or not raw_rows:
        return None

    columns: list[tuple[str, str]] = []
    for column in raw_columns:
        if isinstance(column, Mapping):
            name = column.get("name")
            label = column.get("label") or name
        else:
            name = getattr(column, "name", None)
            label = getattr(column, "label", None) or name
        if name:
            columns.append((str(name), str(label)))
    if not columns:
        return None

    rows: list[dict[str, Any]] = []
    for raw_row in raw_rows[:_TABLE_MAX_ROWS]:
        if not isinstance(raw_row, Mapping):
            continue
        rows.append({
            label: raw_row.get(name, raw_row.get(label))
            for name, label in columns
        })
    if not rows:
        return None

    metadata = getattr(result, "metadata", None)
    title = metadata.get("title", "") if isinstance(metadata, Mapping) else ""
    # 通过现有 ContentPart 序列化入口把 Decimal/datetime/UUID 等数据库
    # 原生类型转换为 JSON 安全值，确保 WebSocket 和 Actor Jsonb 看到同一份数据。
    serialized = serialize_content_part(
        TablePart(
            title=str(title or ""),
            columns=[label for _, label in columns],
            rows=rows,
            truncated=len(raw_rows) > _TABLE_MAX_ROWS,
        )
    )
    serialized.pop("type", None)
    serialized["kind"] = "table"
    return serialized
