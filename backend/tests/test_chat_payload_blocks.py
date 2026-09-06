from __future__ import annotations

import sys
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from schemas.message import ChartPart, DiagramPart, FilePart, ImagePart, TablePart
from services.agent.agent_result import AgentResult
from services.agent.tool_output import ColumnMeta, OutputFormat
from services.handlers.emit_payloads import (
    build_block_from_payload,
    build_part_from_payload,
    build_table_payload_from_agent_result,
)


def test_image_payload_block_preserves_media_url_fields():
    block = build_block_from_payload({
        "kind": "image",
        "url": "https://cdn.example.com/workspace/a.png",
        "original_url": "https://cdn.example.com/workspace/a.png",
        "thumbnail_url": "https://cdn.example.com/workspace-thumbnails/a.w360.webp",
        "preview_url": "https://cdn.example.com/workspace/a.png",
        "download_url": "https://cdn.example.com/workspace/a.png",
        "workspace_path": "下载/AI图片/a.png",
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


def test_table_payload_builds_content_part():
    table = {"kind": "table", "columns": ["name"], "rows": [{"name": "A"}]}

    assert build_block_from_payload(table)["type"] == "table"
    part = build_part_from_payload(table)
    assert isinstance(part, TablePart)
    assert part.rows == [{"name": "A"}]
    assert build_part_from_payload({"kind": "unknown"}) is None


def test_agent_result_table_uses_column_labels_and_existing_preview_limit():
    result = AgentResult(
        summary="订单统计",
        format=OutputFormat.TABLE,
        columns=[
            ColumnMeta("valid_orders", "integer", "有效订单数"),
            ColumnMeta("valid_amount", "numeric", "有效金额"),
        ],
        data=[{"valid_orders": 128, "valid_amount": 2260.5}],
        metadata={"title": "付款订单"},
    )

    payload = build_table_payload_from_agent_result(result)

    assert payload == {
        "kind": "table",
        "title": "付款订单",
        "columns": ["有效订单数", "有效金额"],
        "rows": [{"有效订单数": 128, "有效金额": 2260.5}],
        "truncated": False,
    }


def test_agent_result_table_serializes_database_native_values():
    result = AgentResult(
        summary="订单明细",
        format=OutputFormat.TABLE,
        columns=[
            ColumnMeta("amount", "numeric", "金额"),
            ColumnMeta("created_at", "timestamp", "创建时间"),
            ColumnMeta("record_id", "text", "记录 ID"),
        ],
        data=[{
            "amount": Decimal("12.34"),
            "created_at": datetime(2026, 9, 6, tzinfo=timezone.utc),
            "record_id": UUID("00000000-0000-0000-0000-000000000001"),
        }],
    )

    payload = build_table_payload_from_agent_result(result)

    assert payload["rows"] == [{
        "金额": "12.34",
        "创建时间": "2026-09-06T00:00:00Z",
        "记录 ID": "00000000-0000-0000-0000-000000000001",
    }]
    json.dumps(payload, ensure_ascii=False)
