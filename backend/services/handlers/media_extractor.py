"""
媒体 URL 提取器

从 ChatHandler 累积文本中提取图片/视频 URL，
生成混合 ContentPart 列表（TextPart + ImagePart + VideoPart）。
前端根据 type 渲染不同的卡片组件。
"""

import re
from typing import List

from schemas.message import ContentPart, ImagePart, TextPart, VideoPart

# 匹配图片 URL（常见图片扩展名 + 无扩展名的 CDN URL 带图片参数）
_IMAGE_URL_PATTERN = re.compile(
    r'(https?://[^\s]+\.(?:png|jpg|jpeg|gif|webp|bmp|svg)(?:\?[^\s]*)?)',
    re.IGNORECASE,
)

# 匹配视频 URL
_VIDEO_URL_PATTERN = re.compile(
    r'(https?://[^\s]+\.(?:mp4|mov|avi|webm|mkv)(?:\?[^\s]*)?)',
    re.IGNORECASE,
)

# 工具返回的标记行（提取后删除）
_MEDIA_MARKER = re.compile(r'(?:图片|视频)已生成[：:]\s*\n?')


def extract_media_parts(text: str) -> List[ContentPart]:
    """扫描文本中的媒体 URL，提取为 ImagePart/VideoPart

    返回混合列表：[TextPart(清理后文本), ImagePart(url), VideoPart(url)]
    无媒体 URL 时返回原始 [TextPart(text)]
    """
    if not text:
        return [TextPart(text="")]

    image_urls = _IMAGE_URL_PATTERN.findall(text)
    video_urls = _VIDEO_URL_PATTERN.findall(text)

    if not image_urls and not video_urls:
        return [TextPart(text=text)]

    # 清理文本中的 URL 和标记行
    clean_text = text
    for url in image_urls + video_urls:
        clean_text = clean_text.replace(url, "")
    clean_text = _MEDIA_MARKER.sub("", clean_text).strip()

    parts: List[ContentPart] = []
    if clean_text:
        parts.append(TextPart(text=clean_text))
    for url in image_urls:
        parts.append(ImagePart(url=url))
    for url in video_urls:
        parts.append(VideoPart(url=url))

    return parts if parts else [TextPart(text=text)]
