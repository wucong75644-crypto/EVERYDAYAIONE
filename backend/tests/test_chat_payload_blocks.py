from __future__ import annotations

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.handlers.chat_handler import _build_block_from_payload


def test_image_payload_block_preserves_media_url_fields():
    block = _build_block_from_payload({
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
