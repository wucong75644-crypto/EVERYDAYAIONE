"""复用 ModelGateway 的 Kimi K3 单份草稿服务。"""
import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from loguru import logger
from core.exceptions import AppException
from schemas.ecom_requirement import RequirementAssistInput, RequirementAssistResult
from services.agent.image.requirement_assist_prompts import build_multimodal_messages
from services.agent.image.ecommerce_planner.recovery import error_facts
from services.kie_image_fallback_request import safe_error
from services.agent.image.analysis_media import media_policy, prepare_analysis_media
from services.agent.image.requirement_assist_recovery import (
    InvalidRequirementOutput, parse_requirement_result, repair_context, repair_messages, apply_repair, diagnostic_fields,
)

_MODEL = "kimi-k3"
_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class RequirementAssistOutcome:
    result: RequirementAssistResult
    model: str
    fallback_used: bool
    latency_ms: int


class RequirementAssistService:
    def __init__(self, *, image_resolver=None):
        self.image_resolver = image_resolver

    async def generate(self, data: RequirementAssistInput) -> RequirementAssistOutcome:
        started = time.perf_counter()
        try:
            result = await self._run_model(data)
        except Exception as exc:
            logger.warning(
                f"Requirement assist failed | user_id={data.user_id} | "
                f"source_id={data.source_id} | model={_MODEL} | error_type={type(exc).__name__} | "
                f"http_status={getattr(exc, 'status_code', None)} | "
                f"provider_error_code={getattr(exc, 'error_code', None)} | "
                f"provider_request_id={getattr(exc, 'request_id', None)} | reason={safe_error(exc)}"
            )
            if isinstance(exc, AppException):
                raise
            if isinstance(exc, TimeoutError):
                raise AppException("REQUIREMENT_ASSIST_TIMEOUT", "AI帮写超时，已保留草稿，请重试", 504) from exc
            if isinstance(exc, InvalidRequirementOutput):
                raise AppException("REQUIREMENT_ASSIST_INVALID_OUTPUT", "AI返回内容无效，已保留草稿，请重试", 502) from exc
            code, category, _ = error_facts(exc)
            if category == "authentication":
                raise AppException("REQUIREMENT_ASSIST_AUTHENTICATION", "分析模型鉴权失败，请联系管理员；已保留草稿", 503) from exc
            if category == "balance":
                raise AppException("REQUIREMENT_ASSIST_QUOTA", "分析模型供应商额度不足；已保留草稿", 503) from exc
            if code == "DASHSCOPE_TRANSIENT_REJECTION":
                raise AppException("REQUIREMENT_ASSIST_UPSTREAM_REJECTED", "上游暂时无法处理图片或请求，已保留草稿，请稍后重试", 503) from exc
            raise AppException("REQUIREMENT_ASSIST_UNAVAILABLE", "Kimi AI帮写暂时不可用，请重试", 503) from exc
        return RequirementAssistOutcome(result, _MODEL, False, round((time.perf_counter() - started) * 1000))

    async def _run_model(self, data: RequirementAssistInput) -> RequirementAssistResult:
        from core.config import get_settings
        deadline = time.monotonic() + _TIMEOUT_SECONDS
        settings = get_settings()
        transport, json_mode = media_policy(settings, _MODEL)
        async with prepare_analysis_media(data.image_references, self.image_resolver, model=_MODEL,
                transport=transport, deadline=deadline, memory_mb=settings.ecom_analysis_memory_mb) as urls:
            return await self._call_model(build_multimodal_messages(data, urls), deadline, json_mode)

    async def _call_model(self, messages, deadline, json_mode):
        from services.model_gateway import ModelCallRequest, _collect_stream_response, get_model_gateway, get_model_attempt_context
        options = {"response_format": {"type": "json_object"}} if json_mode else {}
        repair = None
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            session = get_model_gateway().open_chat(ModelCallRequest(model_id=_MODEL, timeout=remaining))
            try:
                response = await asyncio.wait_for(
                    _collect_stream_response(session, messages=messages, reasoning_effort="low", **options),
                    timeout=remaining,
                )
                receipt = getattr(session, "last_result", None)
                context = get_model_attempt_context()
                request_id = getattr(context, "request_id", None)
                finish = response.finish_reason
                normal = receipt is not None and receipt.status == "completed" and finish == "stop"
                diagnostics = {
                    "attempt": attempt + 1, "phase": "repair" if repair else "draft",
                    "finish_reason": finish if finish in {"stop", "length", "content_filter", "tool_calls"} else "unknown",
                    "output_characters": len(response.content),
                    "output_sha256": hashlib.sha256(response.content.encode()).hexdigest(),
                    "partial_output": getattr(receipt, "partial_output", False),
                    "request_id": request_id if isinstance(request_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request_id) else None,
                    "remaining_ms": max(0, round((deadline - time.monotonic()) * 1000)),
                }
                logger.info("requirement_assist_response | model={} diagnostics={}", _MODEL, diagnostics)
                if not normal or not response.content.strip():
                    code, message = ("REQUIREMENT_ASSIST_TRUNCATED", "模型输出被截断，已保留草稿，请重试") if finish == "length" else (
                        ("REQUIREMENT_ASSIST_REFUSED", "模型未交付可用资料，已保留草稿，请调整输入") if finish == "content_filter" else
                        ("REQUIREMENT_ASSIST_INCOMPLETE", "模型输出未完整交付，已保留草稿，请重试"))
                    raise AppException(code, message, 502)
                try:
                    result = apply_repair(response.content, repair) if repair else parse_requirement_result(response.content)
                    validate_no_output_urls(result)
                    return result
                except InvalidRequirementOutput as exc:
                    logger.info("requirement_assist_format_error | diagnostics={} json={} fields={}",
                        diagnostics, exc.syntax, diagnostic_fields(exc.errors))
                    next_repair = repair_context(response.content, exc) if not repair and attempt == 0 else None
                    if next_repair is None:
                        raise
                    repair = next_repair
                    messages = repair_messages(repair)
            except Exception as exc:
                _code, _category, safe = error_facts(exc)
                partial = getattr(getattr(session, "last_result", None), "partial_output", False)
                if not safe or partial or attempt == 1 or repair:
                    raise
                logger.info("Requirement assist retry | model={} provider_error_code={} provider_request_id={}",
                    _MODEL, getattr(exc, "error_code", None), getattr(exc, "request_id", None))
            finally:
                await session.close()
            if repair is None:
                await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))
        raise InvalidRequirementOutput("格式修复未完成")


def validate_no_output_urls(result: RequirementAssistResult) -> None:
    if re.search(r"https?://", result.model_dump_json(), re.IGNORECASE):
        raise InvalidRequirementOutput("响应包含未授权 URL")
