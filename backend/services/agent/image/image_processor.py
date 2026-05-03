"""
电商图片处理器

- 背景去除（rembg，失败降级用原图）
- 尺寸检测（从 task 描述解析预期尺寸）
- 平台裁切（按平台规范调整尺寸）

设计文档：docs/document/TECH_电商图片Agent.md §7.2
"""

from __future__ import annotations

from io import BytesIO
from typing import Optional

from loguru import logger

from .platform_sizes import PLATFORM_SIZES, get_default_main_size


async def remove_background(image_bytes: bytes) -> bytes:
    """使用 rembg 去除背景，返回透明 PNG。

    失败时返回原图（降级，不中断流程）。
    """
    try:
        from rembg import remove
        result = remove(image_bytes)
        logger.debug(f"rembg 去背景成功 | input={len(image_bytes)} → output={len(result)}")
        return result
    except ImportError:
        logger.warning("rembg 未安装，跳过去背景")
        return image_bytes
    except Exception as e:
        logger.warning(f"rembg 去背景失败，降级用原图 | error={e}")
        return image_bytes


def detect_dimensions(task: str, platform: str) -> tuple[int, int]:
    """从 task 描述中解析预期图片尺寸。

    匹配规则：
    - "750×1000" / "3:4" / "竖图" → (750, 1000)
    - "480×480" → (480, 480)
    - 其他 → 平台默认主图尺寸

    Returns:
        (width, height) 元组
    """
    if "750×1000" in task or "750x1000" in task or "3:4" in task or "竖图" in task:
        return (750, 1000)
    if "480×480" in task or "480x480" in task:
        return (480, 480)
    return get_default_main_size(platform)


def detect_aspect_ratio(task: str) -> str:
    """从 task 描述中解析宽高比（传给 KIE adapter 的 size 参数）。"""
    if "750×1000" in task or "750x1000" in task or "3:4" in task or "竖图" in task:
        return "3:4"
    if "16:9" in task or "横图" in task:
        return "16:9"
    if "9:16" in task:
        return "9:16"
    if "4:3" in task:
        return "4:3"
    return "1:1"


def validate_image_bytes(image_data: bytes) -> bool:
    """校验图片数据完整性（Pillow 能打开+加载即合法）。"""
    if not image_data:
        return False
    try:
        from PIL import Image
        img = Image.open(BytesIO(image_data))
        img.load()  # verify() 后对象不可用，用 load() 更可靠
        return True
    except Exception:
        return False


def resize_image(
    image_data: bytes,
    target_width: int,
    target_height: int,
    output_format: str = "PNG",
) -> Optional[bytes]:
    """按目标尺寸裁切/缩放图片。

    使用居中裁切（cover 模式）确保填满目标尺寸。

    Returns:
        裁切后的图片 bytes，失败返回 None
    """
    try:
        from PIL import Image

        img = Image.open(BytesIO(image_data))
        if img.mode in ("RGBA", "P") and output_format.upper() == "JPEG":
            img = img.convert("RGB")

        # 计算 cover 裁切
        src_ratio = img.width / img.height
        dst_ratio = target_width / target_height

        if src_ratio > dst_ratio:
            # 原图更宽：按高度缩放，裁左右
            new_h = target_height
            new_w = int(new_h * src_ratio)
        else:
            # 原图更高：按宽度缩放，裁上下
            new_w = target_width
            new_h = int(new_w / src_ratio)

        img = img.resize((new_w, new_h), Image.LANCZOS)

        # 居中裁切
        left = (new_w - target_width) // 2
        top = (new_h - target_height) // 2
        img = img.crop((left, top, left + target_width, top + target_height))

        buf = BytesIO()
        img.save(buf, format=output_format, quality=95)
        return buf.getvalue()
    except Exception as e:
        logger.error(f"图片裁切失败 | target={target_width}×{target_height} | error={e}")
        return None
