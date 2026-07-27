"""ContentPart 跨语言线协议契约生成。"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from schemas.chart import ChartPart
from schemas.diagram import DiagramPart
from schemas.media_parts import AudioPart, FilePart, ImagePart, TextPart, VideoPart
from schemas.message import (
    ContentPart,
    EcomPlanPart,
    FormPart,
    ThinkingPart,
    ToolResultPart,
    ToolStepPart,
    serialize_content_part,
)
from schemas.structured_parts import InterruptMarkerPart, TablePart


CONTRACT_VERSION = 1


def build_content_part_contract() -> dict[str, Any]:
    """生成供后端和前端共同验证的版本化协议及代表性样例。"""
    valid_parts: list[ContentPart] = [
        TextPart(text="hello"),
        ImagePart(url=None, failed=True),
        ImagePart(
            url="https://example.com/input.png",
            original_url=None,
            thumbnail_url=None,
            width=None,
            name=None,
            workspace_path=None,
        ),
        VideoPart(url="https://example.com/video.mp4"),
        AudioPart(url="https://example.com/audio.mp3"),
        FilePart(
            url="https://example.com/report.pdf",
            name="report.pdf",
            mime_type="application/pdf",
            size=None,
            workspace_path=None,
            asset_id=None,
        ),
        ThinkingPart(text="reasoning"),
        ToolStepPart(tool_name="code_execute", tool_call_id="call-1"),
        ToolResultPart(tool_name="erp_agent", text="done"),
        FormPart(
            form_type="confirm",
            form_id="form-1",
            fields=[{"type": "text", "name": "answer", "label": "回答"}],
        ),
        ChartPart(option={"series": []}),
        DiagramPart(source="flowchart TD\nA-->B"),
        TablePart(columns=["name"], rows=[{"name": "A"}]),
        EcomPlanPart(),
        InterruptMarkerPart(
            interrupted_at="2026-07-27T00:00:00Z",
            reason="user_cancel",
        ),
    ]
    valid_examples = [serialize_content_part(part) for part in valid_parts]
    valid_examples.extend([
        {
            "type": "image",
            "url": "https://example.com/historical-input.png",
            "original_url": None,
            "thumbnail_url": None,
            "width": None,
            "height": None,
            "alt": None,
            "failed": None,
            "name": None,
            "workspace_path": None,
        },
        {
            "type": "file",
            "url": "https://example.com/historical.pdf",
            "name": "historical.pdf",
            "mime_type": "application/pdf",
            "size": None,
            "workspace_path": None,
            "asset_id": None,
        },
    ])
    return {
        "contract": "everydayai.content-part",
        "version": CONTRACT_VERSION,
        "schema": TypeAdapter(ContentPart).json_schema(mode="validation"),
        "valid_examples": valid_examples,
        "invalid_examples": [
            {"type": "image"},
            {"type": "file", "url": "/a", "name": "a.txt"},
            {
                "type": "interrupt_marker",
                "interrupted_at": "2026-07-27T00:00:00Z",
                "reason": "unknown",
            },
            {"type": "unknown", "value": 1},
        ],
    }
