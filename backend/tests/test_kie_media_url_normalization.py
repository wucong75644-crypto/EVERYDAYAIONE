"""KIE 图片/视频输入 URL 规范化回归测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from services.adapters.kie.image_adapter import KieImageAdapter
from services.adapters.kie.video_adapter import KieVideoAdapter


_SOURCE_URL = "https://cdn.example.com/workspace/personal/abcd/客户素材/产品图.jpg"
_ENCODED_URL = (
    "https://cdn.example.com/workspace/personal/abcd/"
    "%E5%AE%A2%E6%88%B7%E7%B4%A0%E6%9D%90/"
    "%E4%BA%A7%E5%93%81%E5%9B%BE.jpg"
)


def _cdn_settings():
    return patch("services.oss_service.settings")


def test_gpt_image_input_uses_normalized_own_cdn_url():
    adapter = KieImageAdapter(MagicMock(), "gpt-image-2-image-to-image")
    with _cdn_settings() as settings:
        settings.oss_cdn_domain = "cdn.example.com"
        settings.oss_bucket_name = None
        settings.oss_endpoint = None
        params = adapter._build_input_params(
            prompt="edit",
            image_urls=[_SOURCE_URL],
            size="1:1",
            output_format="png",
            resolution="1K",
        )

    assert params["input_urls"] == [_ENCODED_URL]


def test_image_to_video_input_uses_normalized_own_cdn_url():
    adapter = KieVideoAdapter(MagicMock(), "sora-2-image-to-video")
    with _cdn_settings() as settings:
        settings.oss_cdn_domain = "cdn.example.com"
        settings.oss_bucket_name = None
        settings.oss_endpoint = None
        params = adapter._build_input_params(
            prompt="animate",
            image_urls=[_SOURCE_URL],
            n_frames="10",
            aspect_ratio="landscape",
            remove_watermark=True,
        )

    assert params["image_urls"] == [_ENCODED_URL]
