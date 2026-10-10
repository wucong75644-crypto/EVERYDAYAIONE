"""Bounded analysis representations of trusted, frozen workspace images.

Only the page and requirement-assist callers opt in. Original references and
the chat/image-generation transports are never rewritten.
"""
from __future__ import annotations

import asyncio
import base64
import os
import time
from contextlib import AsyncExitStack, asynccontextmanager
from weakref import WeakKeyDictionary

import httpx
from loguru import logger

from core.exceptions import AppException
from services.file_resources import FileTargetError, file_version
from services.tools.resource_access import ResourceAccessError
from services.handlers.image_dimensions import image_bytes_facts
from services.workspace_coordination import finish_file_io

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_BASE64_BYTES = 10_000_000
_gates = WeakKeyDictionary()


class AnalysisMediaError(AppException):
    def __init__(self, code, message, *, status=400):
        super().__init__(code, message, status)


def media_policy(settings, model):
    if model == "kimi-k3":
        return ("inline" if getattr(settings, "detail_kimi_inline_images", True) else "cdn",
                bool(getattr(settings, "detail_kimi_json_output", True)))
    if model == "gemini-3.8-flash":
        return ("kie_upload" if getattr(settings, "detail_gemini_overseas_images", True) else "cdn", False)
    if model == "gpt-6-luna":
        return "kie_upload", False
    raise AnalysisMediaError("ANALYSIS_MODEL_UNSUPPORTED", "该模型尚未接入此分析图片协议")


class _ByteGate:
    def __init__(self):
        self.used = 0
        self.condition = asyncio.Condition()

    @asynccontextmanager
    async def hold(self, size, limit):
        if size > limit:
            raise AnalysisMediaError("ANALYSIS_IMAGE_TOTAL_TOO_LARGE", "图片总量过大，请减小图片文件后再分析")
        async with self.condition:
            await self.condition.wait_for(lambda: self.used + size <= limit)
            self.used += size
        try:
            yield
        finally:
            async with self.condition:
                self.used -= size
                self.condition.notify_all()


def _gate():
    loop = asyncio.get_running_loop()
    if loop not in _gates:
        _gates[loop] = _ByteGate()
    return _gates[loop]


@asynccontextmanager
async def _preparation_window(deadline):
    try:
        async with asyncio.timeout_at(deadline):
            yield
    except TimeoutError as exc:
        raise AnalysisMediaError("ANALYSIS_MEDIA_TIMEOUT", "图片准备超时，本次尚未调用分析模型，请稍后重试", status=504) from exc


def _read_image(resolver, ref, model, transport, position):
    try:
        target = resolver._target(ref, digest=False)
        before = target.validate()
        if before[1] > MAX_FILE_BYTES:
            raise AnalysisMediaError("ANALYSIS_IMAGE_TOO_LARGE", f"图片{position}超过10MiB，请减小文件后重试")
        with target.path.open("rb") as source:
            data = source.read(MAX_FILE_BYTES + 1)
        after = file_version(target.path)
    except ResourceAccessError as exc:
        raise AnalysisMediaError(exc.code, "无权读取分析图片，请重新选择获准图片", status=403) from exc
    except (FileTargetError, ValueError, FileNotFoundError) as exc:
        raise AnalysisMediaError("IMAGE_REFERENCE_CHANGED", "原图已缺失或变化，请重新选择图片") from exc
    except OSError as exc:
        raise AnalysisMediaError("ANALYSIS_IMAGE_UNAVAILABLE", f"图片{position}暂时无法读取，请重新选择图片") from exc
    if len(data) > MAX_FILE_BYTES or after != before:
        raise AnalysisMediaError("IMAGE_REFERENCE_CHANGED", "原图已变化，请重新选择图片")
    try:
        facts = image_bytes_facts(data)
    except ValueError as exc:
        raise AnalysisMediaError("ANALYSIS_IMAGE_INVALID", f"图片{position}无法解码，请更换图片") from exc
    if facts["content_sha256"] != ref["content_sha256"]:
        raise AnalysisMediaError("IMAGE_REFERENCE_CHANGED", "原图已变化，请重新选择图片")
    if facts["mime_type"] not in {"image/jpeg", "image/png", "image/webp"}:
        raise AnalysisMediaError("ANALYSIS_IMAGE_FORMAT", f"图片{position}请使用JPEG、PNG或WebP格式")
    width, height = facts["width"], facts["height"]
    if model == "kimi-k3":
        if min(width, height) <= 10 or max(width, height) > 200 * min(width, height):
            raise AnalysisMediaError("ANALYSIS_IMAGE_DIMENSIONS", f"图片{position}宽高须大于10像素，长短边比例不能超过200:1")
        if facts["mime_type"] == "image/webp" and (max(width, height) >= 3840 or min(width, height) >= 2160):
            raise AnalysisMediaError("ANALYSIS_IMAGE_FORMAT", f"图片{position}为大尺寸WebP，请改用JPEG或PNG")
    if transport == "inline" and 4 * ((len(data) + 2) // 3) > MAX_BASE64_BYTES:
        raise AnalysisMediaError("ANALYSIS_IMAGE_ENCODING_TOO_LARGE", f"图片{position}编码后超过10MB，请减小文件后重试")
    return data, facts


@asynccontextmanager
async def prepare_analysis_media(refs, resolver, *, model, transport, deadline, memory_mb=384, api_key=None):
    """Hold a conservative byte allowance until all model requests release URLs."""
    if not refs or len(refs) > 9 or resolver is None:
        raise AnalysisMediaError("ANALYSIS_IMAGE_BINDING_REQUIRED", "图片绑定不可用，请重新选择图片")
    if transport not in {"inline", "kie_upload", "cdn"}:
        raise AnalysisMediaError("ANALYSIS_TRANSPORT_INVALID", "分析图片传输配置无效")
    if (transport == "inline" and model != "kimi-k3"
            or transport == "kie_upload" and model not in {"gemini-3.8-flash", "gpt-6-luna"}):
        raise AnalysisMediaError("ANALYSIS_TRANSPORT_INVALID", "模型与分析图片传输配置不匹配")
    started = time.monotonic()
    total = 0
    sizes = []
    for position, ref in enumerate(refs, 1):
        version = ref.get("file_version", ())
        if len(version) != 4 or any(type(value) is not int for value in version) or version[1] < 1:
            raise AnalysisMediaError("ANALYSIS_IMAGE_BINDING_REQUIRED", "图片绑定不可用，请重新选择图片")
        size = version[1]
        if size > MAX_FILE_BYTES:
            raise AnalysisMediaError("ANALYSIS_IMAGE_TOO_LARGE", f"图片{position}超过10MiB，请减小文件后重试")
        if transport == "inline" and 4 * ((size + 2) // 3) > MAX_BASE64_BYTES:
            raise AnalysisMediaError("ANALYSIS_IMAGE_ENCODING_TOO_LARGE", f"图片{position}编码后超过10MB，请减小文件后重试")
        sizes.append(size)
    # Reserve raw bytes plus representation and HTTP serialization copies.
    allowance = sum(size + 3 * (4 * ((size + 2) // 3) + 128) for size in sizes)
    async with AsyncExitStack() as stack:
        async with _preparation_window(deadline):
            await stack.enter_async_context(_gate().hold(allowance, memory_mb * 1024 * 1024))
            urls = []
            upload_ms = 0
            if transport == "kie_upload":
                from services.adapters.kie.client import KieClient
                proxy = os.getenv(KieClient.SHADOW_OVERSEAS_PROXY_ENV)
                if not proxy or not api_key:
                    raise AnalysisMediaError("ANALYSIS_UPLOAD_CONFIG", "海外图片上传配置不可用，请联系管理员", status=503)
                upload_started = time.monotonic()
                async with httpx.AsyncClient(proxy=proxy, trust_env=False,
                        headers={"Authorization": f"Bearer {api_key}"},
                        timeout=KieClient.SHADOW_UPLOAD_TIMEOUT) as client:
                    for position, ref in enumerate(refs, 1):
                        data, facts = await finish_file_io(_read_image, resolver, ref, model, transport, position)
                        total += len(data)
                        # Bytes and SHA, not arbitrary URLs, are the upload source.
                        suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[facts["mime_type"]]
                        try:
                            url = await KieClient.upload_image_bytes(client, data, facts["mime_type"],
                                f"analysis-{facts['content_sha256']}{suffix}")
                        except Exception as exc:
                            raise AnalysisMediaError("ANALYSIS_IMAGE_UPLOAD_FAILED", f"图片{position}海外上传暂时失败，请重试", status=503) from exc
                        urls.append(url)
                        del data
                upload_ms = round((time.monotonic() - upload_started) * 1000)
            else:
                for position, ref in enumerate(refs, 1):
                    data, facts = await finish_file_io(_read_image, resolver, ref, model, transport, position)
                    total += len(data)
                    urls.append(f"data:{facts['mime_type']};base64," + base64.b64encode(data).decode("ascii")
                        if transport == "inline" else resolver.preview(ref))
                    del data
            logger.info("analysis_media_ready | model={} transport={} images={} source_bytes={} upload_ms={} prepare_ms={}",
                model, transport, len(refs), total, upload_ms, round((time.monotonic() - started) * 1000))
        try:
            yield urls
        finally:
            urls.clear()
