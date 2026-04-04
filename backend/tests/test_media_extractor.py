"""media_extractor 单元测试 — URL 提取与 ContentPart 混合列表"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from schemas.message import ImagePart, TextPart, VideoPart
from services.handlers.media_extractor import extract_media_parts


class TestExtractMediaParts:
    """extract_media_parts 核心逻辑"""

    def test_plain_text_returns_single_textpart(self):
        result = extract_media_parts("你好，这是普通文字")
        assert len(result) == 1
        assert isinstance(result[0], TextPart)
        assert result[0].text == "你好，这是普通文字"

    def test_empty_string_returns_empty_textpart(self):
        result = extract_media_parts("")
        assert len(result) == 1
        assert isinstance(result[0], TextPart)
        assert result[0].text == ""

    def test_single_image_url_extracted(self):
        text = "图片已生成：\nhttps://cdn.example.com/img/cat.png"
        result = extract_media_parts(text)
        images = [p for p in result if isinstance(p, ImagePart)]
        assert len(images) == 1
        assert images[0].url == "https://cdn.example.com/img/cat.png"

    def test_image_url_with_query_params(self):
        text = "图片已生成：\nhttps://cdn.example.com/img/cat.jpg?token=abc&size=large"
        result = extract_media_parts(text)
        images = [p for p in result if isinstance(p, ImagePart)]
        assert len(images) == 1
        assert "token=abc" in images[0].url

    def test_video_url_extracted(self):
        text = "视频已生成：\nhttps://cdn.example.com/video/demo.mp4"
        result = extract_media_parts(text)
        videos = [p for p in result if isinstance(p, VideoPart)]
        assert len(videos) == 1
        assert videos[0].url == "https://cdn.example.com/video/demo.mp4"

    def test_mixed_text_and_image(self):
        text = "A款库存128件。\n图片已生成：\nhttps://example.com/chart.png"
        result = extract_media_parts(text)
        texts = [p for p in result if isinstance(p, TextPart)]
        images = [p for p in result if isinstance(p, ImagePart)]
        assert len(texts) == 1
        assert "128件" in texts[0].text
        assert len(images) == 1

    def test_marker_line_removed_from_text(self):
        text = "图片已生成：\nhttps://example.com/a.png\n剩余说明文字"
        result = extract_media_parts(text)
        texts = [p for p in result if isinstance(p, TextPart)]
        assert len(texts) == 1
        assert "图片已生成" not in texts[0].text
        assert "剩余说明文字" in texts[0].text

    def test_multiple_image_urls(self):
        text = (
            "图片已生成：\n"
            "https://example.com/a.png\n"
            "https://example.com/b.jpg"
        )
        result = extract_media_parts(text)
        images = [p for p in result if isinstance(p, ImagePart)]
        assert len(images) == 2

    def test_image_and_video_mixed(self):
        text = (
            "https://example.com/photo.png\n"
            "https://example.com/clip.mp4"
        )
        result = extract_media_parts(text)
        images = [p for p in result if isinstance(p, ImagePart)]
        videos = [p for p in result if isinstance(p, VideoPart)]
        assert len(images) == 1
        assert len(videos) == 1

    def test_no_media_url_returns_original_text(self):
        text = "https://example.com/api/v1/data 这是普通链接"
        result = extract_media_parts(text)
        assert len(result) == 1
        assert isinstance(result[0], TextPart)

    def test_webp_extension_recognized(self):
        text = "https://cdn.example.com/image.webp"
        result = extract_media_parts(text)
        images = [p for p in result if isinstance(p, ImagePart)]
        assert len(images) == 1

    def test_case_insensitive_extension(self):
        text = "https://cdn.example.com/photo.PNG"
        result = extract_media_parts(text)
        images = [p for p in result if isinstance(p, ImagePart)]
        assert len(images) == 1

    def test_url_removed_from_text_part(self):
        """图片 URL 不应重复出现在 TextPart 中"""
        text = "结果如下 https://example.com/result.jpg 以上"
        result = extract_media_parts(text)
        texts = [p for p in result if isinstance(p, TextPart)]
        images = [p for p in result if isinstance(p, ImagePart)]
        assert len(images) == 1
        if texts:
            assert "https://example.com/result.jpg" not in texts[0].text
