"""复用 ModelGateway 的 Kimi K3 单份草稿服务。"""
import asyncio
import json
import re
import time
from dataclasses import dataclass
from loguru import logger
from pydantic import ValidationError
from core.exceptions import AppException
from schemas.ecom_requirement import RequirementAssistInput, RequirementAssistResult
from services.agent.image.requirement_assist_prompts import build_multimodal_messages

_MODEL = "kimi-k3"
_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class RequirementAssistOutcome:
    result: RequirementAssistResult
    model: str
    fallback_used: bool
    latency_ms: int


class RequirementAssistService:
    async def generate(self, data: RequirementAssistInput) -> RequirementAssistOutcome:
        started = time.perf_counter()
        try:
            result = await self._run_model(data)
        except Exception as exc:
            logger.warning(
                f"Requirement assist failed | user_id={data.user_id} | "
                f"source_id={data.source_id} | model={_MODEL} | error_type={type(exc).__name__}"
            )
            if isinstance(exc, TimeoutError):
                raise AppException("REQUIREMENT_ASSIST_TIMEOUT", "AI帮写超时，已保留草稿，请重试", 504) from exc
            if isinstance(exc, InvalidRequirementOutput):
                raise AppException("REQUIREMENT_ASSIST_INVALID_OUTPUT", "AI返回内容无效，已保留草稿，请重试", 502) from exc
            raise AppException("REQUIREMENT_ASSIST_UNAVAILABLE", "Kimi AI帮写暂时不可用，请重试", 503) from exc
        return RequirementAssistOutcome(result, _MODEL, False, round((time.perf_counter() - started) * 1000))

    async def _run_model(self, data: RequirementAssistInput) -> RequirementAssistResult:
        from services.model_gateway import ModelCallRequest, _collect_stream_response, get_model_gateway
        session = get_model_gateway().open_chat(ModelCallRequest(model_id=_MODEL, timeout=_TIMEOUT_SECONDS))
        try:
            response = await asyncio.wait_for(
                _collect_stream_response(
                    session, messages=build_multimodal_messages(data), reasoning_effort="low",
                ),
                timeout=_TIMEOUT_SECONDS,
            )
        finally:
            await session.close()
        result = parse_requirement_result(response.content)
        validate_no_output_urls(result)
        return result


class InvalidRequirementOutput(ValueError):
    """模型响应不能构成单份草稿。"""


def parse_requirement_result(content: str) -> RequirementAssistResult:
    cleaned = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise InvalidRequirementOutput("响应中没有 JSON 对象") from None
        try:
            payload = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError as exc:
            raise InvalidRequirementOutput("响应不是合法 JSON") from exc
    try:
        return RequirementAssistResult.model_validate(payload)
    except ValidationError as exc:
        raise InvalidRequirementOutput("响应不符合单份草稿协议") from exc


def validate_no_output_urls(result: RequirementAssistResult) -> None:
    if re.search(r"https?://", result.model_dump_json(), re.IGNORECASE):
        raise InvalidRequirementOutput("响应包含未授权 URL")
