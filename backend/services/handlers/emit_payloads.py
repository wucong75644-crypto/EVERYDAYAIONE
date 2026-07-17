"""显式 emit payload 到消息内容协议的转换。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from schemas.message import ChartPart, ContentPart, FilePart, ImagePart


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
    return None
