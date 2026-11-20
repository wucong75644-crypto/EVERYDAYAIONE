"""Explicitly supported KIE video inputs, checked against official docs 2026-11-20."""
from typing import Literal
from urllib.parse import urlsplit

VideoModel = Literal[
    "kling-2.6/text-to-video", "kling-2.6/image-to-video", "kling-3.0/video",
    "bytedance/seedance-2", "bytedance/seedance-2-fast", "bytedance/seedance-2-5",
    "wan/3-0-video", "wan/3-0-video-prime", "wan/2-6-flash-image-to-video",
    "pixverse-v6-max/text-to-video", "pixverse-v6-max/image-to-video",
]

_BASE = ["16:9", "9:16", "1:1"]
_SEEDANCE_RATIOS = _BASE + ["4:3", "3:4", "21:9", "adaptive"]
_WAN_RATIOS = _BASE + ["4:3", "3:4", "adaptive"]
_PIXVERSE_RATIOS = _BASE + ["4:3", "3:4", "2:3", "3:2", "21:9"]
CATALOG = {
    "kling-2.6/text-to-video": dict(
        family="kling26", use_for="文生视频；普通5/10秒任务的默认选择", image="none",
        durations=[5, 10], default_duration=5, aspect_ratios=_BASE,
        resolutions=[], prompt_min=1, prompt_max=2500, audio="optional",
        docs="https://docs.kie.ai/market/kling/text-to-video"),
    "kling-2.6/image-to-video": dict(
        family="kling26", use_for="单张商品图/首帧转5/10秒视频", image="required",
        durations=[5, 10], default_duration=5, aspect_ratios=_BASE,
        resolutions=[], prompt_min=1, prompt_max=2500, audio="optional",
        docs="https://docs.kie.ai/market/kling/image-to-video"),
    "kling-3.0/video": dict(
        family="kling30", use_for="3–15秒；可选首尾帧、720p/1080p/4K；此工具仅接入单镜头模式",
        image="optional", end_frame=True,
        durations=list(range(3, 16)), default_duration=5, aspect_ratios=_BASE,
        resolutions=["720p", "1080p", "4k"], default_resolution="720p",
        prompt_min=1, prompt_max=2500, audio="optional",
        docs="https://docs.kie.ai/market/kling/kling-3-0"),
    "bytedance/seedance-2": dict(
        family="seedance", use_for="4–15秒文生/首尾帧/最多9张参考图；支持1080p/4K，4K为HEVC编码",
        image="optional", end_frame=True, reference_images=9, reference_videos=3, reference_audios=3,
        durations=list(range(4, 16)), default_duration=5, aspect_ratios=_SEEDANCE_RATIOS,
        resolutions=["480p", "720p", "1080p", "4k"], default_resolution="720p",
        prompt_min=3, prompt_max=20000, audio="optional",
        docs="https://docs.kie.ai/market/bytedance/seedance-2"),
    "bytedance/seedance-2-fast": dict(
        family="seedance", use_for="Fast变体；4–15秒文生/首尾帧/最多9张参考图；限480p/720p",
        image="optional", end_frame=True, reference_images=9, reference_videos=3, reference_audios=3,
        durations=list(range(4, 16)), default_duration=5, aspect_ratios=_SEEDANCE_RATIOS,
        resolutions=["480p", "720p"], default_resolution="720p",
        prompt_min=3, prompt_max=20000, audio="optional",
        docs="https://docs.kie.ai/market/bytedance/seedance-2-fast"),

    "bytedance/seedance-2-5": dict(
        family="seedance", use_for="4–30秒；首尾帧或最多30图/10视频/10音频参考；最高4K(HEVC)",
        image="optional", end_frame=True, reference_images=30, reference_videos=10, reference_audios=10,
        durations=list(range(4, 31)), default_duration=5, aspect_ratios=_SEEDANCE_RATIOS,
        resolutions=["480p", "720p", "1080p", "4k"], default_resolution="720p",
        prompt_min=1, prompt_max=20480, audio="optional",
        docs="https://docs.kie.ai/market/bytedance/seedance-2-5"),
    "wan/3-0-video": dict(
        family="wan3", use_for="Wan标准版；2–30秒；文生/首尾帧或最多10图/5视频/5音频参考",
        image="optional", end_frame=True, reference_images=10, reference_videos=5, reference_audios=5,
        durations=list(range(2, 31)), default_duration=5, aspect_ratios=_WAN_RATIOS,
        resolutions=["480p", "720p", "1080p"], default_resolution="1080p",
        prompt_min=1, prompt_max=20000, audio="optional",
        docs="https://docs.kie.ai/market/wan/3-0-video"),
    "wan/3-0-video-prime": dict(
        family="wan3", use_for="Wan高速版；2–30秒；文生/首尾帧或最多10图/5视频/5音频参考",
        image="optional", end_frame=True, reference_images=10, reference_videos=5, reference_audios=5,
        durations=list(range(2, 31)), default_duration=5, aspect_ratios=_WAN_RATIOS,
        resolutions=["480p", "720p", "1080p"], default_resolution="1080p",
        prompt_min=1, prompt_max=20000, audio="optional",
        docs="https://docs.kie.ai/market/wan/3-0-video-prime"),
    "wan/2-6-flash-image-to-video": dict(
        family="wanflash", use_for="Wan Flash单张商品图转视频；5/10/15秒；本工具使用单镜头模式",
        image="required", durations=[5, 10, 15], default_duration=5, aspect_ratios=_BASE,
        resolutions=["720p", "1080p"], default_resolution="1080p",
        prompt_min=2, prompt_max=1500, audio="optional",
        docs="https://docs.kie.ai/market/wan/2-6-flash-image-to-video"),
    "pixverse-v6-max/text-to-video": dict(
        family="pixverse", use_for="1–15秒文生视频；360p/540p/720p/1080p",
        image="none", durations=list(range(1, 16)), default_duration=5, aspect_ratios=_PIXVERSE_RATIOS,
        resolutions=["360p", "540p", "720p", "1080p"], default_resolution="720p",
        prompt_min=1, prompt_max=5000, audio="optional",
        docs="https://docs.kie.ai/market/pixverse/v6-max-text-to-video"),
    "pixverse-v6-max/image-to-video": dict(
        family="pixverse", use_for="1–15秒单张商品图转视频；输出比例跟随图片",
        image="required", durations=list(range(1, 16)), default_duration=5, aspect_ratios=_PIXVERSE_RATIOS,
        resolutions=["360p", "540p", "720p", "1080p"], default_resolution="720p",
        prompt_min=1, prompt_max=5000, audio="optional",
        docs="https://docs.kie.ai/market/pixverse/v6-max-image-to-video"),

}
MODELS = tuple(CATALOG)


def public_catalog() -> list[dict]:
    return [{"model": model, **{k: v for k, v in config.items() if k != "family"},
             "audio_default": False,
             "audio_guidance": "默认sound=false；需要旁白/对白/音乐/音效时传sound=true并在prompt写明",
             "image_ratio": "Kling/PixVerse图生时按首帧比例；其他模型支持比例见aspect_ratios",
             "reference_guidance": "首尾帧与reference_*互斥；素材尺寸、格式、时长与总时长约束见docs，由供应商校验。自动时长(-1)未开放。",
             } for model, config in CATALOG.items()]


def validate_url(value: str) -> None:
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("Media inputs require public HTTPS URLs without credentials")


def build_video_input(model: str, prompt: str, duration_seconds: int | None,
                      aspect_ratio: str, image_url: str | None, sound: bool,
                      resolution: str | None, end_image_url: str | None,
                      reference_image_urls: list[str] | None,
                      reference_video_urls: list[str] | None = None,
                      reference_audio_urls: list[str] | None = None) -> dict:
    if model not in CATALOG:
        raise ValueError("Unsupported model; call list_video_models")
    c = CATALOG[model]
    if not prompt.strip() or not c["prompt_min"] <= len(prompt) <= c["prompt_max"]:
        raise ValueError(f"prompt length must be {c['prompt_min']}–{c['prompt_max']} for {model}")
    duration = c["default_duration"] if duration_seconds is None else duration_seconds
    if type(duration) is not int or duration not in c["durations"]:
        raise ValueError(f"Unsupported duration for {model}; call list_video_models")
    if aspect_ratio not in c["aspect_ratios"]:
        raise ValueError(f"Unsupported aspect_ratio for {model}")
    if resolution is not None and resolution not in c["resolutions"]:
        raise ValueError(f"Unsupported resolution for {model}; call list_video_models")
    if c["image"] == "required" and not image_url:
        raise ValueError("image-to-video requires one public HTTPS image_url")
    if c["image"] == "none" and image_url:
        raise ValueError("Use an image-to-video model for an image reference")
    if end_image_url and (not c.get("end_frame") or not image_url):
        raise ValueError("end_image_url needs image_url and a model supporting first/last frames")
    references = {"reference_image_urls": (reference_image_urls, "reference_images"),
                  "reference_video_urls": (reference_video_urls, "reference_videos"),
                  "reference_audio_urls": (reference_audio_urls, "reference_audios")}
    for field, (urls, limit_key) in references.items():
        if urls:
            limit = c.get(limit_key, 0)
            if not limit or len(urls) > limit:
                raise ValueError(f"{field} unsupported or exceeds model limit (at most {limit}); call list_video_models")
            if image_url or end_image_url:
                raise ValueError("Do not mix reference inputs with first/last frame inputs")
    for url in [image_url, end_image_url, *(reference_image_urls or []),
                *(reference_video_urls or []), *(reference_audio_urls or [])]:
        if url is not None:
            validate_url(url)

    params = {"prompt": prompt, "duration": str(duration)}
    family = c["family"]
    if family in {"seedance", "wan3"}:
        params.update(duration=duration, aspect_ratio=aspect_ratio,
                      resolution=resolution or c["default_resolution"])
        params["generate_audio" if family == "seedance" else "audio"] = sound
        if family == "wan3":
            params["resolution"] = params["resolution"].upper()
        if image_url:
            params["first_frame_url"] = image_url
        if end_image_url:
            params["last_frame_url"] = end_image_url
        for field, (urls, _) in references.items():
            if urls:
                params[field] = urls
    elif family == "wanflash":
        params.update(image_urls=[image_url], audio=sound, multi_shots=False,
                      resolution=resolution or c["default_resolution"])
    elif family == "pixverse":
        params.update(duration=duration, quality=resolution or c["default_resolution"],
                      generate_audio_switch=sound)
        if image_url:
            params["image_urls"] = [image_url]
        else:
            params["aspect_ratio"] = aspect_ratio
    else:
        params["sound"] = sound
        if image_url:
            params["image_urls"] = [image_url] + ([end_image_url] if end_image_url else [])
        else:
            params["aspect_ratio"] = aspect_ratio
        if family == "kling30":
            params.update(mode={"720p": "std", "1080p": "pro", "4k": "4K"}[resolution or "720p"], multi_shots=False)
    return params
