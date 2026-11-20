"""Personal, bearer-authenticated KIE video tools; no application-user billing."""

import asyncio
import time
import hashlib
import json
import re
import secrets
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from services.kie_mcp_player import PlayerMCP, PLAYER_URI, playable_urls
from mcp.types import ToolAnnotations
from pydantic import Field
from loguru import logger
from starlette.responses import JSONResponse

from core.config import get_settings
from core.redis import RedisClient
from services.kie_mcp_task_flow import store, task_view
from services.kie_video_models import MODELS, VideoModel, build_video_input, public_catalog

mcp = PlayerMCP(
    "KIE Video",
    instructions="Personal KIE account. create_video spends KIE credits. Submit once, then AUTOMATICALLY call wait_video_task repeatedly until success/failure without asking the user to remind you. On success call show_video with the saved task_id to display the player. Never embed MP4 using Markdown image syntax. If the client does not render MCP Apps, provide its video URL as a normal link and state that inline playback is unavailable. A bounded wait timing out does not mean generation failed. On disconnection use list_video_tasks to resume by saved ID; never resubmit. If repeated wait calls report query errors, report the outage instead of claiming success. Never retry an uncertain submission with a new request_key. Audio is OFF by default (sound=false). When the user requests speech, narration, dialogue, music, sound effects or ambient sound, set sound=true and describe the desired audio and exact spoken lines in prompt. Keep sound=false for silent videos or when audio is not requested. Enabling sound can increase KIE charges. Call list_video_models before choosing a model: each model has different durations, resolutions and image inputs. Respect user model choices; do not silently fall back or generate multiple paid candidates.",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
)


async def kie_request(method: str, path: str, **kwargs) -> dict:
    key = get_settings().kie_api_key
    if not key:
        raise ValueError("KIE_API_KEY is not configured on the server")
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.request(
                method, "https://api.kie.ai/api/v1/jobs/" + path,
                headers={"Authorization": f"Bearer {key}"}, **kwargs,
            )
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError):
        raise ValueError("KIE response unavailable. A submitted creation may still be running; do not resubmit with a new request_key.") from None
    if not isinstance(body, dict) or body.get("code") != 200 or not isinstance(body.get("data"), dict):
        code = body.get("code") if isinstance(body, dict) else None
        safe_code = code if isinstance(code, int) else "invalid_response"
        raise ValueError(f"KIE request failed (code={safe_code}); inspect account or parameters before retrying")
    return body["data"]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def list_video_models() -> dict:
    """List the explicitly supported video models and input limits; does not spend credits."""
    return {"models": public_catalog(),
            "selection_guidance": "Respect the requested model. Default to Kling 2.6 for simple 5/10s tasks; choose Kling 3.0 for 3–15s or higher resolution, Seedance/Wan 3.0 for first/last frames or multimodal references, Wan 3.0/Seedance 2.5 for up to 30s, Wan Flash for single-image 5/10/15s, and PixVerse V6 Max for 1–15s including 1–2s clips. These are capability-based choices, not measured quality/cost rankings. Omit duration_seconds/resolution to use model defaults. Never silently substitute a model or submit multiple paid jobs.",
            "audio_default": False,
            "audio_guidance": "默认关闭；需要声音时传sound=true并描述声音/台词。",
            "billing": "Charged directly to server KIE account, not app credits. Price depends on model, duration, resolution and audio; consult provider prices. No automatic paid fallback."}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
async def create_video(
    request_key: str,
    prompt: str,
    model: Annotated[VideoModel, Field(description="模型ID；先调用list_video_models查看用途、输入类型、时长、分辨率与声音限制，再根据用户需求选择。")] = "kling-2.6/text-to-video",
    duration_seconds: Annotated[int | None, Field(description="Seconds; omit for model default (5 seconds). Allowed ranges differ; call list_video_models.")] = None,
    aspect_ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive", "2:3", "3:2"] = "16:9",
    image_url: Annotated[str | None, Field(description="单张公开HTTPS参考图；作为首帧/参考图，支持情况见模型目录。图生模型必填；文生模型不支持。")] = None,
    sound: Annotated[bool, Field(description="默认关闭声音（false）。用户需要旁白、对白、音乐、音效或环境声时，主动设置 true，并在 prompt 中描述声音及具体台词；静音或未要求声音时保持 false。开启声音会增加 KIE 费用。 Audio is OFF by default; pass true when audio is requested.")] = False,
    resolution: Annotated[Literal["360p", "480p", "540p", "720p", "1080p", "4k"] | None, Field(description="Omit for model default. Consult list_video_models; Kling 2.6 does not accept resolution. Seedance 4k is HEVC.")] = None,
    end_image_url: Annotated[str | None, Field(description="Optional last-frame HTTPS image for Kling 3.0/Seedance/Wan 3.0; requires image_url as first frame.")] = None,
    reference_image_urls: Annotated[list[str] | None, Field(description="Public HTTPS reference images: Seedance 2.0/Fast up to 9, Seedance 2.5 up to 30, Wan 3.0/Prime up to 10. Cannot combine with image_url/end_image_url.", max_length=30)] = None,
    reference_video_urls: Annotated[list[str] | None, Field(description="HTTPS video references: Seedance 2.0/Fast up to 3, Seedance 2.5 up to 10, Wan 3.0/Prime up to 5. Cannot mix with first/last frames. Provider validates media duration/size and total-duration limits; see model docs.", max_length=10)] = None,
    reference_audio_urls: Annotated[list[str] | None, Field(description="HTTPS audio references: Seedance 2.0/Fast up to 3, Seedance 2.5 up to 10, Wan 3.0/Prime up to 5. Cannot mix with first/last frames. Output sound remains OFF unless sound=true.", max_length=10)] = None,
) -> dict:
    """Spend KIE credits to submit one video. Use a unique request_key (8-80 letters/digits/hyphens) for each intended video, reuse it after uncertainty. Returns task_id/local_task_id immediately; AUTOMATICALLY call wait_video_task until completed or failed, and display the returned video. On interruption, recover with list_video_tasks. Do not end the turn merely because creation returned an ID. Audio defaults to OFF (sound=false). For requested speech, narration, dialogue, music, effects or ambience, set sound=true and specify the audio and spoken lines in prompt. Otherwise keep sound=false. Audio can increase provider charges. Call list_video_models for model selection and per-model input limits. Seedance/Wan 3.0 accept multimodal references; first/last frames cannot mix with reference inputs. All models default to sound=false, overriding provider defaults. This tool uses single-shot mode for Kling 3.0 and Wan Flash."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_key):
        raise ValueError("request_key must contain 8-80 letters, digits, underscores or hyphens")
    params = build_video_input(model, prompt, duration_seconds, aspect_ratio, image_url,
                               sound, resolution, end_image_url, reference_image_urls,
                               reference_video_urls, reference_audio_urls)
    payload = {"model": model, "input": params}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    redis = await RedisClient.get_client()
    key = "kie:personal-mcp:create:" + request_key
    # Preserve old Redis guards across rolling upgrades. Old uncertain submissions cannot be recreated.
    legacy = await redis.get(key)
    existing = await store.by_key(request_key)
    if legacy and existing is None:
        previous = json.loads(legacy)
        if previous["fingerprint"] != fingerprint:
            raise ValueError("request_key already belongs to a different request")
        if previous.get("task_id"):
            return {"task_id": previous["task_id"], "reused": True}
        raise ValueError("Previous submission is pending or uncertain. Do not submit again; inspect KIE task history.")
    callback_base = get_settings().kie_mcp_callback_base_url
    url = urlsplit(callback_base or "")
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Server callback base must be a public HTTPS origin")
    row, created = await store.reserve(request_key, fingerprint, payload, callback_base)
    if not created:
        return {**task_view(row), "reused": True}
    reserved = json.dumps({"fingerprint": fingerprint, "state": "submission_unknown"})
    if not await redis.set(key, reserved, nx=True):
        # Another old worker may have reserved this key; never perform a second POST.
        return task_view(row)
    try:
        data = await kie_request("POST", "createTask", json=row['payload'])
        task_id = data.get("taskId")
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", task_id):
            return task_view(row)
        row = await store.bind(row['id'], task_id)
    except Exception:
        # The durable local ID and callback allow recovery even when the POST response was lost.
        return task_view(row)
    try:
        await redis.set(key, json.dumps({"fingerprint": fingerprint, "task_id": task_id}))
    except Exception as error:
        logger.warning("kie_mcp_legacy_cache_update_failed | error_type={}", type(error).__name__)
    result = task_view(row)
    if row['state'] == 'waiting':
        result['state'] = 'submitted'
    return result



@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def get_video_task(task_id: str) -> dict:
    """Query an existing KIE task without creating a new one. Poll at intervals of at least 10 seconds; completed tasks include video URLs."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", task_id):
        raise ValueError("Invalid task_id")
    row = await store.get(task_id)
    if row:
        return task_view(row)
    # Legacy IDs from before durable tracking remain queryable.
    data = await kie_request("GET", "recordInfo", params={"taskId": task_id})
    raw = data.get("resultJson") or "{}"
    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
        urls = result.get("resultUrls", []) if isinstance(result, dict) else []
    except (ValueError, TypeError):
        urls = []
    urls = [u for u in urls if isinstance(u, str) and u.startswith("https://")] if isinstance(urls, list) else []
    return {"task_id": task_id, "state": data.get("state"), "video_urls": urls,
            "failed": data.get("state") == "fail", "credits_consumed": data.get("creditsConsumed")}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def list_video_tasks(
    before: str | None = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> dict:
    """Recover your shared personal-account video tasks across devices/restarts. Use next_cursor for older pages. Resume wait_video_task using task_id; NEVER create again to recover a task. Only new tracked tasks appear; old provider IDs remain directly queryable."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be 1–50")
    rows = await store.listing(before, limit)
    return {"tasks": [task_view(row) for row in rows],
            "next_cursor": str(rows[-1]['id']) if len(rows) == limit else None}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def wait_video_task(
    task_id: str,
    wait_seconds: Annotated[int, Field(ge=1, le=40)] = 40,
) -> dict:
    """AUTOMATIC follow-up after create_video: wait up to 40s per call. If continue_waiting=true, call again yourself without asking the user. Do not interpret wait timeout as generation failure. Never recreate a task. On success show returned video URLs; on fail explain the error. Closing a client does not lose server tasks; recover with list_video_tasks."""
    if type(wait_seconds) is not int or not 1 <= wait_seconds <= 40:
        raise ValueError("wait_seconds must be 1–40")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", task_id):
        raise ValueError("Invalid task_id")
    result = {"task_id": task_id, "state": "unknown", "video_urls": [], "failed": False}
    try:
        async with asyncio.timeout(wait_seconds):
            while True:
                result = await get_video_task(task_id)
                if result['state'] in ('success', 'fail'):
                    return {**result, "continue_waiting": False, "wait_timed_out": False}
                await asyncio.sleep(2 if result.get('local_task_id') else 10)
    except TimeoutError:
        return {**result, "continue_waiting": True, "wait_timed_out": True}
    except Exception:
        return {**result, "continue_waiting": True, "wait_timed_out": False,
                "query_error": "TASK_QUERY_UNAVAILABLE", "guidance": "Retry waiting without recreating the task; report persistent query errors."}


@mcp.resource(PLAYER_URI, name="kie_video_player", mime_type="text/html")
def player_resource() -> str:
    """Player HTML is provided by the metadata-aware resource handler."""
    from pathlib import Path
    return Path(__file__).with_name('kie_mcp_player.html').read_text()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def show_video(task_id: str) -> dict:
    """Display an existing completed video using MCP Apps. Read-only, no generation or credits. Call after success. If no player appears, show video_urls as normal links; never embed an MP4 as an image. Pending permanent storage: wait then call again."""
    result = await get_video_task(task_id)
    return {"task_id": result['task_id'], "state": result.get('state'),
            "video_urls": playable_urls(result.get('video_urls', [])),
            "fallback_urls": result.get('video_urls', []),
            "storage_state": result.get('storage_state'),
            "display_guidance": "MCP Apps player requires client support. If absent, provide a normal fallback_urls link; never claim inline playback was verified."}


class BearerAuth:
    """Fail closed even if the token is absent; never put credentials into URLs."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = get_settings().kie_mcp_token
        headers = dict(scope.get("headers", []))
        supplied = headers.get(b"authorization", b"")
        expected = ("Bearer " + token).encode() if token else b""
        if not token or len(token) < 32 or not secrets.compare_digest(supplied, expected):
            return await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
        # This endpoint is for MCP clients, not cross-origin browser requests.
        if b"origin" in headers:
            return await JSONResponse({"error": "browser_origin_denied"}, status_code=403)(scope, receive, send)
        await self.app(scope, receive, send)


authenticated_app = BearerAuth(mcp.streamable_http_app())
