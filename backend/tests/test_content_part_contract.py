"""ContentPart 权威协议、artifact 与序列化边界测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import TypeAdapter, ValidationError

from schemas.content_part_contract import build_content_part_contract
from schemas.media_parts import FilePart, ImagePart, VideoPart
from schemas.message import ContentPart, serialize_content_part, serialize_content_parts
from services.handlers.image_handler import ImageHandler
from services.handlers.video_handler import VideoHandler


CONTRACT_PATH = (
    Path(__file__).parent.parent
    / "schemas"
    / "contracts"
    / "content_part.v1.json"
)
ADAPTER = TypeAdapter(ContentPart)


def test_committed_contract_matches_authoritative_models() -> None:
    committed = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert committed == build_content_part_contract()


def test_all_contract_examples_follow_backend_validation() -> None:
    contract = build_content_part_contract()

    for example in contract["valid_examples"]:
        ADAPTER.validate_python(example)
    for example in contract["invalid_examples"]:
        with pytest.raises(ValidationError):
            ADAPTER.validate_python(example)


def test_image_url_is_required_but_nullable() -> None:
    assert ImagePart(url=None).url is None

    with pytest.raises(ValidationError):
        ImagePart()


def test_runtime_image_slot_fields_are_complete_and_bounded() -> None:
    slot = ImagePart(
        url=None,
        slot_id="slot-10",
        slot_index=9,
        slot_status="accepted",
        slot_revision=2,
    )

    assert slot.slot_index == 9
    assert slot.slot_status == "accepted"

    with pytest.raises(ValidationError):
        ImagePart(url=None, slot_id="partial-slot")
    with pytest.raises(ValidationError):
        ImagePart(
            url=None,
            slot_id="slot-11",
            slot_index=10,
            slot_status="pending",
            slot_revision=0,
        )


def test_serialization_preserves_nullable_protocol_and_file_identity() -> None:
    parts = [
        ImagePart(url=None, failed=True),
        FilePart(
            url="/report.pdf",
            name="report.pdf",
            mime_type="application/pdf",
            asset_id="asset-1",
        ),
    ]

    serialized = serialize_content_parts(parts)

    assert serialized[0]["url"] is None
    assert serialized[0]["failed"] is True
    assert serialize_content_part(parts[1])["asset_id"] == "asset-1"


def test_media_handlers_use_canonical_null_omission() -> None:
    image = ImageHandler(MagicMock())._convert_content_parts_to_dicts([
        ImagePart(url="https://example.com/image.png"),
    ])
    video = VideoHandler(MagicMock())._convert_content_parts_to_dicts([
        VideoPart(url="https://example.com/video.mp4"),
    ])

    assert image == [{"type": "image", "url": "https://example.com/image.png"}]
    assert video == [{"type": "video", "url": "https://example.com/video.mp4"}]
