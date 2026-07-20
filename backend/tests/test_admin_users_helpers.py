"""管理员用户路由共享 helper 测试。"""
import json

from api.routes.admin_users_helpers import (
    _extract_upload_parts,
    _filename_from_url,
    _mask_phone,
    _safe_parse_content,
)


def test_safe_parse_content_supported_shapes() -> None:
    raw = json.dumps([
        {"type": "text", "text": "hi"},
        {"type": "image", "url": "u"},
    ])
    assert len(_safe_parse_content(raw)) == 2
    assert _safe_parse_content("hello") == "hello"
    assert _safe_parse_content(None) is None
    assert _safe_parse_content("[not valid json") == "[not valid json"
    parsed = [{"type": "text"}]
    assert _safe_parse_content(parsed) is parsed


def test_extract_upload_parts_supported_shapes() -> None:
    parts = [
        {
            "type": "image_url",
            "image_url": {"url": "https://x.com/a.jpg"},
        },
        {
            "type": "file",
            "url": "https://x.com/doc.pdf",
            "name": "doc.pdf",
            "size": 1024,
        },
        {"type": "text", "text": "hello"},
    ]
    result = _extract_upload_parts(parts)
    assert len(result) == 2
    assert result[0]["url"] == "https://x.com/a.jpg"
    assert result[0]["type"] == "image"
    assert result[1]["name"] == "doc.pdf"
    assert result[1]["size"] == 1024


def test_extract_upload_parts_rejects_invalid_shapes() -> None:
    parts = [
        None, "string", {"type": "file"}, {"type": "image", "url": ""},
    ]
    assert _extract_upload_parts(parts) == []
    assert _extract_upload_parts(None) == []
    assert _extract_upload_parts("string") == []


def test_filename_from_url() -> None:
    assert _filename_from_url(
        "https://x.com/path/photo.jpg",
    ) == "photo.jpg"
    assert _filename_from_url(
        "https://x.com/photo.jpg?token=abc",
    ) == "photo.jpg"
    assert _filename_from_url("not a url") == "not a url"
    assert _filename_from_url("") == "file"


def test_mask_phone() -> None:
    assert _mask_phone("13812345678") == "138****5678"
    assert _mask_phone(None) is None
    assert _mask_phone("short") == "short"
