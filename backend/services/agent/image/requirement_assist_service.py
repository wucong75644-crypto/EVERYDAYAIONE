"""电商图 AI 帮写共享核心服务。"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger
from pydantic import ValidationError

from core.config import get_settings
from core.exceptions import AppException
from schemas.ecom_requirement import RequirementAssistInput, RequirementAssistResult
from services.adapters.dashscope.chat_adapter import DashScopeChatAdapter
from services.agent.image.requirement_assist_prompts import build_multimodal_messages


_TOTAL_TIMEOUT_SECONDS = 100.0
_PRIMARY_TIMEOUT_SECONDS = 60.0
_CONFLICT_MARKETING_TERMS = (
    "暗示", "模拟", "容量加倍", "超大容量", "升级版", "加厚版", "替代词", "长续航",
)


@dataclass(frozen=True)
class RequirementAssistOutcome:
    result: RequirementAssistResult
    model: str
    fallback_used: bool
    latency_ms: int


class RequirementAssistService:
    """生成、校验并净化三套通用创作简报。"""

    async def generate(self, data: RequirementAssistInput) -> RequirementAssistOutcome:
        settings = get_settings()
        started_at = time.perf_counter()
        primary_error: Exception | None = None
        try:
            result = await self._run_model(
                data,
                settings.image_enhance_vl_model,
                _PRIMARY_TIMEOUT_SECONDS,
            )
            return self._outcome(result, settings.image_enhance_vl_model, False, started_at)
        except Exception as exc:
            primary_error = exc
            logger.warning(
                f"Requirement assist primary failed | user_id={data.user_id} | "
                f"source_id={data.source_id} | model={settings.image_enhance_vl_model} | "
                f"error_type={type(exc).__name__}"
            )

        elapsed = time.perf_counter() - started_at
        remaining = _TOTAL_TIMEOUT_SECONDS - elapsed
        if remaining <= 0:
            raise AppException("REQUIREMENT_ASSIST_TIMEOUT", "AI帮写超时，请重试", 504) from primary_error
        try:
            result = await self._run_model(
                data,
                settings.image_enhance_fallback_model,
                remaining,
            )
            return self._outcome(result, settings.image_enhance_fallback_model, True, started_at)
        except asyncio.TimeoutError as exc:
            self._log_final_failure(data, settings.image_enhance_fallback_model, exc)
            raise AppException("REQUIREMENT_ASSIST_TIMEOUT", "AI帮写超时，请重试", 504) from exc
        except InvalidRequirementOutput as exc:
            self._log_final_failure(data, settings.image_enhance_fallback_model, exc)
            raise AppException("REQUIREMENT_ASSIST_INVALID_OUTPUT", "AI返回内容无效，请重试", 502) from exc
        except Exception as exc:
            self._log_final_failure(data, settings.image_enhance_fallback_model, exc)
            raise AppException("REQUIREMENT_ASSIST_UNAVAILABLE", "AI帮写暂时不可用，请重试", 503) from exc

    async def _run_model(
        self,
        data: RequirementAssistInput,
        model: str,
        timeout_seconds: float,
    ) -> RequirementAssistResult:
        settings = get_settings()
        adapter = DashScopeChatAdapter(
            api_key=settings.dashscope_api_key or "",
            model=model,
            base_url=settings.dashscope_base_url,
            stream_timeout=timeout_seconds,
        )
        try:
            response = await asyncio.wait_for(
                adapter.chat_sync(messages=build_multimodal_messages(data)),
                timeout=timeout_seconds,
            )
        finally:
            await adapter.close()
        result = parse_requirement_result(response.content)
        validate_reference_ids(result, data)
        validate_no_output_urls(result)
        return apply_conflict_gate(result)

    @staticmethod
    def _outcome(
        result: RequirementAssistResult,
        model: str,
        fallback_used: bool,
        started_at: float,
    ) -> RequirementAssistOutcome:
        return RequirementAssistOutcome(
            result=result,
            model=model,
            fallback_used=fallback_used,
            latency_ms=max(0, round((time.perf_counter() - started_at) * 1000)),
        )

    @staticmethod
    def _log_final_failure(data: RequirementAssistInput, model: str, exc: Exception) -> None:
        logger.error(
            f"Requirement assist failed | user_id={data.user_id} | org_id={data.org_id} | "
            f"source_id={data.source_id} | model={model} | error_type={type(exc).__name__}"
        )


class InvalidRequirementOutput(ValueError):
    """模型输出无法满足 AI 帮写协议。"""


def parse_requirement_result(content: str) -> RequirementAssistResult:
    """解析直接 JSON、代码围栏或带少量前后文字的 JSON。"""
    cleaned = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    try:
        payload: Any = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise InvalidRequirementOutput("响应中没有 JSON 对象") from None
        try:
            payload = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise InvalidRequirementOutput("响应不是合法 JSON") from exc
    try:
        return RequirementAssistResult.model_validate(payload)
    except ValidationError as exc:
        raise InvalidRequirementOutput("响应不符合三方案协议") from exc


def validate_reference_ids(
    result: RequirementAssistResult,
    data: RequirementAssistInput,
) -> None:
    """禁止模型引用输入集合之外的参考图片。"""
    allowed = {image.id for image in data.reference_images}
    returned = {analysis.image_id for analysis in result.reference_analyses}
    if not returned.issubset(allowed):
        raise InvalidRequirementOutput("响应包含未知参考图 ID")


def validate_no_output_urls(result: RequirementAssistResult) -> None:
    """禁止模型在创作简报中生成新的外部 URL。"""
    if any(
        re.search(r"https?://", suggestion.brief_markdown, re.IGNORECASE)
        for suggestion in result.suggestions
    ):
        raise InvalidRequirementOutput("响应包含未授权 URL")


def apply_conflict_gate(result: RequirementAssistResult) -> RequirementAssistResult:
    """从可执行简报中移除冲突卖点和规避事实的营销表达。"""
    if not result.conflicts:
        return result
    blocked = {
        claim.strip()
        for conflict in result.conflicts
        for claim in conflict.blocked_claims
        if claim.strip()
    }
    replacements = {
        conflict.field: f"- 待确认：{conflict.message}"
        for conflict in result.conflicts
    }
    sanitized = [
        suggestion.model_copy(
            update={"brief_markdown": _sanitize_brief(suggestion.brief_markdown, blocked, replacements)}
        )
        for suggestion in result.suggestions
    ]
    return result.model_copy(update={"suggestions": sanitized})


def _sanitize_brief(
    brief: str,
    blocked: set[str],
    replacements: dict[str, str],
) -> str:
    lines: list[str] = []
    preserving_original = False
    replacement_added = False
    for line in brief.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            preserving_original = "用户需求原文" in stripped
        if stripped.startswith("用户需求原文"):
            preserving_original = True
        unsafe = any(claim in line for claim in blocked) or any(
            term in line for term in _CONFLICT_MARKETING_TERMS
        )
        if unsafe and not preserving_original:
            if not replacement_added:
                lines.extend(replacements.values())
                replacement_added = True
            continue
        lines.append(line)
    sanitized = "\n".join(lines).strip()
    if not sanitized:
        raise InvalidRequirementOutput("事实冲突闸门移除了全部简报内容")
    return sanitized
