"""专用重试的请求重放及旧缓存兼容；不改变首次生成参数整理规则。"""

import json
import re
from copy import deepcopy
from urllib.parse import urlsplit

from services.adapters.kie.client import KieClient
from services.adapters.kie.models import CreateTaskRequest


def request_params(task: dict) -> dict:
    params = task.get("request_params") or {}
    if isinstance(params, str):
        params = json.loads(params)
    if not isinstance(params, dict):
        raise ValueError("Invalid task request parameters")
    return params


def safe_error(exc: Exception) -> str:
    """保留诊断内容，但不记录完整 URL、凭据或任意响应正文。"""
    text = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    text = re.sub(r"https?://\S+", "[url]", text)
    text = re.sub(r"(?i)(bearer\s+|(?:token|api[_-]?key|authorization)[=: ]+)[^\s,;]+", r"\1[redacted]", text)
    return text[:240]


def _url_mapping(cache: dict) -> dict[str, str]:
    sources, staged = cache["source_urls"], cache["staged_urls"]
    if not sources or len(sources) != len(staged) or len(set(sources)) != len(sources):
        raise ValueError("Incomplete shadow URL mapping")
    for url in sources + staged:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Invalid shadow image URL")
    return dict(zip(sources, staged))


def replay_request(task: dict, cache: dict, callback_url: str | None) -> CreateTaskRequest | None:
    """完整快照优先；不存在快照才允许调用旧参数兼容路径。"""
    mapping = _url_mapping(cache)
    snapshot = cache.get("request")
    if snapshot is None:
        return None
    if not isinstance(snapshot, dict) or snapshot.get("model") != task["model_id"]:
        raise ValueError("Fallback snapshot model mismatch")
    request = CreateTaskRequest.model_validate({**deepcopy(snapshot), "callBackUrl": callback_url})
    if set(KieClient._extract_shadow_image_urls(request)) != set(mapping):
        raise ValueError("Fallback snapshot image mapping mismatch")
    for key in KieClient.SHADOW_IMAGE_INPUT_KEYS:
        if key in request.input:
            values = request.input[key]
            if not isinstance(values, list) or not all(url in mapping for url in values):
                raise ValueError("Incomplete snapshot image mapping")
            request.input[key] = [mapping[url] for url in values]
    return request


def legacy_generate_kwargs(task: dict, cache: dict, adapter, callback_url: str | None) -> dict:
    """只为部署前没有实际请求快照的缓存复用首次提交函数。"""
    from services.handlers.image_request_settings import (
        build_image_generate_kwargs, resolve_batch_item_kwargs,
        resolve_image_generation_settings,
    )
    from services.oss_service import normalize_external_oss_url

    params = request_params(task)
    prompt = params.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Original image prompt unavailable")
    mapping = _url_mapping(cache)
    settings = resolve_image_generation_settings({**params, "model": task["model_id"]}, True)
    if settings["model_id"] != task["model_id"]:
        raise ValueError("Legacy fallback model mismatch")
    kwargs = build_image_generate_kwargs(
        prompt, cache["source_urls"], settings, params.get("output_format") or "png",
        callback_url, adapter.supports_resolution,
    )
    batch = params.get("_batch_prompts")
    if batch:
        index = 0 if params.get("operation") == "regenerate_single" else task.get("image_index")
        if not isinstance(batch, list) or not isinstance(index, int) or not 0 <= index < len(batch):
            raise ValueError("Original batch item unavailable")
        if not isinstance(batch[index], dict):
            raise ValueError("Invalid original batch item")
        kwargs, _ = resolve_batch_item_kwargs(kwargs, prompt, settings["aspect_ratio"], batch[index])
    urls = kwargs.get("image_urls") or []
    normalized = [normalize_external_oss_url(url) for url in urls]
    if not normalized or set(normalized) != set(mapping):
        raise ValueError("Legacy fallback image mapping mismatch")
    kwargs["image_urls"] = [mapping[url] for url in normalized]
    kwargs["_image_fetch_fallback"] = True
    return kwargs
