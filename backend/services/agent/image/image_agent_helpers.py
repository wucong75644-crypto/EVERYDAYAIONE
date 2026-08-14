"""Pure helpers for ImageAgent parsing, validation, and error payloads."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from services.agent.agent_result import AgentResult
from services.agent.safe_tool_logging import log_agent_event

from .image_processor import detect_dimensions


_ALLOWED_IMAGE_HOSTS = frozenset({
    "cdn.everydayai.com.cn",
    "img.everydayai.com.cn",
})


def parse_image_plan(content: str, agent: Any) -> dict[str, Any]:
    """Parse the image plan while preserving ImageAgent's fallback logging."""
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", content)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    log_agent_event(
        "warning",
        "ImageAgent plan parse failed",
        agent,
        "image_agent",
        "IMAGE_PLAN_PARSE_FAILED",
    )
    return {"product_insight": "", "visual_strategy": "", "images": []}


def validate_image_request(task: str, image_urls: list[str]) -> str | None:
    """Return the existing validation message, or None for a valid request."""
    if not task or not task.strip():
        return "提示词不能为空"
    if len(task) > 2000:
        return "提示词过长，请精简到 2000 字以内"
    for url in image_urls:
        host = urlparse(url).hostname or ""
        if host and host not in _ALLOWED_IMAGE_HOSTS:
            return f"不支持的图片来源: {host}"
    return None


def build_image_error_result(
    summary: str,
    task: str,
    image_urls: list[str],
    platform: str,
    style_directive: str,
) -> AgentResult:
    """Build the existing failed image payload and retry context."""
    width, height = detect_dimensions(task, platform)
    return AgentResult(
        status="error",
        summary=summary,
        source="image_agent",
        error_message=summary,
        emit_payloads=[{
            "kind": "image",
            "url": None,
            "width": width,
            "height": height,
            "alt": task[:50],
            "failed": True,
            "error": summary,
            "retry_context": {
                "task": task,
                "image_urls": image_urls,
                "platform": platform,
                "style_directive": style_directive,
            },
        }],
    )
