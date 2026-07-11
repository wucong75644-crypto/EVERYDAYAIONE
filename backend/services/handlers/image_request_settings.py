"""图片请求中影响模型提交和计费的参数解析。"""

from typing import Any, Dict

from config import kie_models
from services.adapters.factory import DEFAULT_IMAGE_MODEL_ID


def resolve_image_generation_settings(
    params: Dict[str, Any],
    has_image_urls: bool,
) -> Dict[str, Any]:
    """返回图片任务提交与积分预检共用的标准参数。"""
    model_id = params.get("model") or DEFAULT_IMAGE_MODEL_ID
    if has_image_urls and "text-to-image" in model_id:
        model_id = model_id.replace("text-to-image", "image-to-image")

    aspect_ratio = params.get("aspect_ratio") or "1:1"
    resolution = params.get("resolution") or None
    resolution_normalize = {
        "1024x1024": "1K", "1024": "1K",
        "2048x2048": "2K", "2048": "2K", "2560x1440": "2K",
        "4096x4096": "4K", "4096": "4K",
    }
    if resolution and resolution not in ("1K", "2K", "4K"):
        resolution = resolution_normalize.get(resolution, "1K")

    model_config = kie_models.get_model_config(model_id)
    if model_config and model_config.get("supports_resolution") and not resolution:
        resolution = "1K"
    if resolution:
        if aspect_ratio == "auto" and resolution != "1K":
            resolution = "1K"
        elif aspect_ratio == "1:1" and resolution == "4K":
            resolution = "2K"

    is_regenerate_single = params.get("operation") == "regenerate_single"
    batch_prompts = params.get("_batch_prompts")
    if is_regenerate_single:
        num_images = 1
    elif batch_prompts:
        num_images = min(len(batch_prompts), 8)
    else:
        num_images = max(1, min(4, int(params.get("num_images", 1))))

    cost_result = kie_models.calculate_image_cost(
        model_name=model_id,
        image_count=num_images,
        resolution=resolution,
    )
    return {
        "model_id": model_id,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "num_images": num_images,
        "total_credits": cost_result["user_credits"],
    }


def build_image_generate_kwargs(
    prompt: str,
    image_urls: list[str],
    settings: Dict[str, Any],
    output_format: str,
    callback_url: str,
    supports_resolution: bool,
) -> Dict[str, Any]:
    """构造图片适配器使用的标准提交参数。"""
    generate_kwargs: Dict[str, Any] = {
        "prompt": prompt,
        "image_urls": image_urls or None,
        "size": settings["aspect_ratio"],
        "output_format": output_format,
        "callback_url": callback_url,
        "wait_for_result": False,
    }
    if settings["resolution"] and supports_resolution:
        generate_kwargs["resolution"] = settings["resolution"]
    return generate_kwargs


def resolve_batch_item_kwargs(
    generate_kwargs: Dict[str, Any],
    default_prompt: str,
    default_aspect_ratio: str,
    item: Dict[str, Any],
) -> tuple[Dict[str, Any], str]:
    """将单张图片的覆盖参数合并到批次公共参数。"""
    task_kwargs = {
        **generate_kwargs,
        "prompt": item.get("prompt", default_prompt),
        "size": item.get("aspect_ratio", default_aspect_ratio),
    }
    if item.get("image_urls") is not None:
        task_kwargs["image_urls"] = item["image_urls"] or None
    if item.get("resolution"):
        task_kwargs["resolution"] = item["resolution"]
    return task_kwargs, item.get("prompt", default_prompt)
