from __future__ import annotations

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from schemas.message import ChartPart, DiagramPart, FilePart, ImagePart, VideoPart
from services.handlers.emit_payloads import build_block_from_payload, build_part_from_payload
from services.handlers.chat.outcome_builder import build_content_parts


def test_image_payload_block_preserves_media_url_fields():
    block = build_block_from_payload({
        "kind": "image",
        "url": "https://cdn.example.com/workspace/a.png",
        "original_url": "https://cdn.example.com/workspace/a.png",
        "thumbnail_url": "https://cdn.example.com/workspace-thumbnails/a.w360.webp",
        "preview_url": "https://cdn.example.com/workspace/a.png",
        "download_url": "https://cdn.example.com/workspace/a.png",
        "workspace_path": "下载/AI图片/a.png",
        "_asset_source_kind": "media_tool",
    })

    assert block == {
        "type": "image",
        "url": "https://cdn.example.com/workspace/a.png",
        "alt": "",
        "workspace_path": "下载/AI图片/a.png",
        "original_url": "https://cdn.example.com/workspace/a.png",
        "thumbnail_url": "https://cdn.example.com/workspace-thumbnails/a.w360.webp",
        "preview_url": "https://cdn.example.com/workspace/a.png",
        "download_url": "https://cdn.example.com/workspace/a.png",
    }


def test_explicit_image_payload_builds_image_part():
    part = build_part_from_payload({
        "kind": "image",
        "url": "https://cdn.example.com/a.png",
        "name": "a.png",
    })

    assert isinstance(part, ImagePart)
    assert part.url == "https://cdn.example.com/a.png"


def test_explicit_file_payload_builds_file_part():
    part = build_part_from_payload({
        "kind": "file",
        "url": "https://cdn.example.com/a.xlsx",
        "name": "a.xlsx",
        "mime_type": "application/vnd.ms-excel",
        "size": 128,
    })

    assert isinstance(part, FilePart)
    assert part.name == "a.xlsx"


def test_video_payload_builds_video_part_and_final_content():
    payload = {
        "kind": "video",
        "url": "https://cdn.example.com/demo.mp4",
        "duration": 10,
        "thumbnail_url": "https://cdn.example.com/thumb.jpg",
    }

    block = build_block_from_payload(payload)
    part = build_part_from_payload(payload)
    final_parts = build_content_parts(
        [block], fallback_text="",
    )

    assert isinstance(part, VideoPart)
    assert part.duration == 10
    assert isinstance(final_parts[0], VideoPart)
    assert final_parts[0].thumbnail.endswith("thumb.jpg")


def test_chart_payload_uses_option_title_and_builds_chart_part():
    payload = {
        "kind": "chart",
        "option": {
            "title": [{"text": "销售趋势"}],
            "series": [{"type": "line", "data": [1, 2]}],
        },
    }

    block = build_block_from_payload(payload)
    part = build_part_from_payload(payload)

    assert block["title"] == "销售趋势"
    assert block["chart_type"] == "line"
    assert isinstance(part, ChartPart)


def test_diagram_payload_builds_block_and_part():
    payload = {
        "kind": "diagram",
        "format": "mermaid",
        "title": "订单流程",
        "source": "flowchart TD\nA-->B",
    }

    block = build_block_from_payload(payload)
    part = build_part_from_payload(payload)

    assert block == {
        "type": "diagram",
        "format": "mermaid",
        "title": "订单流程",
        "source": "flowchart TD\nA-->B",
    }
    assert isinstance(part, DiagramPart)
    assert part.source == payload["source"]


def test_empty_diagram_payload_is_rejected():
    payload = {"kind": "diagram", "format": "mermaid", "source": "  "}

    assert build_block_from_payload(payload) is None
    assert build_part_from_payload(payload) is None


def test_invalid_image_payload_is_rejected():
    payload = {"kind": "image", "url": None, "failed": False}

    assert build_block_from_payload(payload) is None
    assert build_part_from_payload(payload) is None


def test_failed_image_payload_preserves_failure_details():
    part = build_part_from_payload({
        "kind": "image",
        "url": None,
        "failed": True,
        "error": "timeout",
        "retry_context": {"provider": "test-provider"},
    })

    assert isinstance(part, ImagePart)
    assert part.failed is True
    assert part.error == "timeout"
    assert part.retry_context == {"provider": "test-provider"}


def test_table_and_unknown_payloads_have_no_content_part():
    table = {"kind": "table", "columns": ["name"], "rows": [{"name": "A"}]}

    assert build_block_from_payload(table)["type"] == "table"
    assert build_part_from_payload(table) is None
    assert build_part_from_payload({"kind": "unknown"}) is None
