"""
KIE 适配器便捷函数

从各适配器文件提取的便捷封装函数，简化调用。
推荐使用统一工厂 adapters.create_* 系列函数。
"""

from typing import List, Optional, Dict, Any

from loguru import logger

from .client import KieClient, KieAPIError, KieTaskFailedError, KieTaskTimeoutError


# ============================================================
# Chat 便捷函数
# ============================================================


async def create_kie_chat_adapter(
    api_key: str,
    model: str = "gemini-3-flash",
):
    """
    创建 KIE Chat 适配器（KIE 专用便捷函数）

    推荐使用统一工厂: from services.adapters import create_chat_adapter

    Args:
        api_key: KIE API 密钥
        model: 模型名称

    Returns:
        KIE Chat 适配器实例
    """
    from .chat_adapter import KieChatAdapter

    try:
        client = KieClient(api_key)
        return KieChatAdapter(client, model)
    except Exception as e:
        logger.error(f"Create KIE chat adapter failed: model={model}, error={e}")
        raise


# ============================================================
# Image 便捷函数
# ============================================================


async def generate_image(
    api_key: str,
    prompt: str,
    model: str = "google/nano-banana",
    **kwargs,
) -> Dict[str, Any]:
    """快速生成图像 (默认使用 google/nano-banana)"""
    from .image_adapter import KieImageAdapter

    try:
        async with KieClient(api_key) as client:
            adapter = KieImageAdapter(client, model)
            return await adapter.generate(prompt, **kwargs)
    except (KieAPIError, KieTaskFailedError, KieTaskTimeoutError, ValueError):
        raise
    except Exception as e:
        logger.error(f"generate_image failed: model={model}, error={e}")
        raise KieAPIError(f"generate_image failed: {e}") from e


async def edit_image(
    api_key: str,
    prompt: str,
    image_urls: List[str],
    **kwargs,
) -> Dict[str, Any]:
    """快速编辑图像 (使用 google/nano-banana-edit)"""
    from .image_adapter import KieImageAdapter

    try:
        async with KieClient(api_key) as client:
            adapter = KieImageAdapter(client, "google/nano-banana-edit")
            return await adapter.generate(prompt, image_urls=image_urls, **kwargs)
    except (KieAPIError, KieTaskFailedError, KieTaskTimeoutError, ValueError):
        raise
    except Exception as e:
        logger.error(f"edit_image failed: image_count={len(image_urls)}, error={e}")
        raise KieAPIError(f"edit_image failed: {e}") from e


async def generate_image_pro(
    api_key: str,
    prompt: str,
    resolution: str = "2K",
    reference_images: Optional[List[str]] = None,
    **kwargs,
) -> Dict[str, Any]:
    """高级图像生成 (使用 nano-banana-pro, 支持 1K/2K/4K 分辨率)"""
    from .image_adapter import KieImageAdapter

    try:
        async with KieClient(api_key) as client:
            adapter = KieImageAdapter(client, "nano-banana-pro")
            return await adapter.generate(
                prompt,
                image_urls=reference_images,
                resolution=resolution,
                **kwargs,
            )
    except (KieAPIError, KieTaskFailedError, KieTaskTimeoutError, ValueError):
        raise
    except Exception as e:
        logger.error(f"generate_image_pro failed: resolution={resolution}, error={e}")
        raise KieAPIError(f"generate_image_pro failed: {e}") from e


# ============================================================
# Video 便捷函数
# ============================================================


async def text_to_video(
    api_key: str,
    prompt: str,
    duration: int = 10,
    aspect_ratio: str = "landscape",
    remove_watermark: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    """
    文本生成视频

    Args:
        api_key: KIE API 密钥
        prompt: 视频描述
        duration: 时长 (10/15 秒)
        aspect_ratio: 宽高比
        remove_watermark: 是否去水印
        **kwargs: 其他参数

    Returns:
        生成结果
    """
    from .video_adapter import KieVideoAdapter

    try:
        async with KieClient(api_key) as client:
            adapter = KieVideoAdapter(client, "sora-2-text-to-video")
            return await adapter.generate(
                prompt=prompt,
                duration_seconds=duration,
                aspect_ratio=aspect_ratio,
                remove_watermark=remove_watermark,
                **kwargs,
            )
    except (KieAPIError, KieTaskFailedError, KieTaskTimeoutError, ValueError):
        raise
    except Exception as e:
        logger.error(f"text_to_video failed: duration={duration}s, error={e}")
        raise KieAPIError(f"text_to_video failed: {e}") from e


async def image_to_video(
    api_key: str,
    prompt: str,
    image_url: str,
    duration: int = 10,
    aspect_ratio: str = "landscape",
    remove_watermark: bool = True,
    **kwargs,
) -> Dict[str, Any]:
    """
    图片生成视频

    Args:
        api_key: KIE API 密钥
        prompt: 视频描述
        image_url: 首帧图片 URL
        duration: 时长
        aspect_ratio: 宽高比
        remove_watermark: 是否去水印
        **kwargs: 其他参数

    Returns:
        生成结果
    """
    from .video_adapter import KieVideoAdapter

    try:
        async with KieClient(api_key) as client:
            adapter = KieVideoAdapter(client, "sora-2-image-to-video")
            return await adapter.generate(
                prompt=prompt,
                image_urls=[image_url],
                duration_seconds=duration,
                aspect_ratio=aspect_ratio,
                remove_watermark=remove_watermark,
                **kwargs,
            )
    except (KieAPIError, KieTaskFailedError, KieTaskTimeoutError, ValueError):
        raise
    except Exception as e:
        logger.error(f"image_to_video failed: duration={duration}s, error={e}")
        raise KieAPIError(f"image_to_video failed: {e}") from e


async def storyboard_video(
    api_key: str,
    duration: int = 15,
    storyboard_images: Optional[List[str]] = None,
    aspect_ratio: str = "landscape",
    **kwargs,
) -> Dict[str, Any]:
    """
    故事板视频生成

    Args:
        api_key: KIE API 密钥
        duration: 时长 (10/15/25 秒)
        storyboard_images: 故事板图片列表
        aspect_ratio: 宽高比
        **kwargs: 其他参数

    Returns:
        生成结果
    """
    from .video_adapter import KieVideoAdapter

    try:
        async with KieClient(api_key) as client:
            adapter = KieVideoAdapter(client, "sora-2-pro-storyboard")
            return await adapter.generate(
                image_urls=storyboard_images,
                duration_seconds=duration,
                aspect_ratio=aspect_ratio,
                **kwargs,
            )
    except (KieAPIError, KieTaskFailedError, KieTaskTimeoutError, ValueError):
        raise
    except Exception as e:
        logger.error(f"storyboard_video failed: duration={duration}s, error={e}")
        raise KieAPIError(f"storyboard_video failed: {e}") from e
