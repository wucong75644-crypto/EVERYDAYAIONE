"""Typed ecommerce recovery hints; hints never grant a tool or reset a budget."""
from __future__ import annotations

import json
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from services.agent.agent_result import AgentResult


class RecoveryAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["repair_arguments", "ask_user", "resume_plan", "wait_existing", "report_error", "generate"]
    remaining_attempts: int = Field(default=0, ge=0, le=3)
    preserved_stages: list[int] = Field(default_factory=list)
    allowed_changes: list[str] = Field(default_factory=list)


class RecoveryReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    workflow: Literal["ecommerce_main_images"] = "ecommerce_main_images"
    status: Literal["ready", "needs_input", "insufficient", "error", "planning"]
    plan_id: str | None = None
    failed_stage: int | None = Field(default=None, ge=1, le=3)
    error: dict[str, str] = Field(default_factory=dict)
    recovery: RecoveryAction
    generation_allowed: bool = False


class PlannerRecoveryError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def error_facts(error):
    """Use the existing classifier, with stricter paid-call replay evidence."""
    from core.error_classifier import classify_error
    from services.adapters.dashscope.chat_adapter import DashScopeAPIError
    from services.adapters.kie.client import KieAuthenticationError, KieRateLimitError, KieInsufficientBalanceError
    if isinstance(error, KieAuthenticationError):
        return "KIE_AUTHENTICATION_FAILED", "authentication", False
    if isinstance(error, KieRateLimitError):
        return "KIE_RATE_LIMITED", "transient_rejection", True
    if isinstance(error, KieInsufficientBalanceError):
        return "KIE_INSUFFICIENT_BALANCE", "balance", False
    if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return "MODEL_CONNECTION_NOT_ESTABLISHED", "transient_rejection", True
    if isinstance(error, DashScopeAPIError):
        if isinstance(error.__cause__, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
            return "MODEL_CONNECTION_NOT_ESTABLISHED", "transient_rejection", True
        if error.retryable_rejection:
            return "DASHSCOPE_TRANSIENT_REJECTION", "transient_rejection", True
        if error.request_rejected:
            category = ("authentication" if error.status_code in {401, 403} else
                        "balance" if error.quota_rejection else "business")
            return "DASHSCOPE_REQUEST_REJECTED", category, False
    code = getattr(error, "code", "")
    known = ("ECOM_PLAN_RETRY_EXHAUSTED", "ECOM_PLAN_EXECUTION_UNCERTAIN", "ECOM_PLAN_PARENT_BUDGET_EXHAUSTED",
             "ECOM_PLAN_INSUFFICIENT_CREDITS", "ECOM_PLAN_LEASE_LOST", "ECOM_PLAN_ATTEMPT_CONFLICT")
    # SQL diagnostics are matched only against our fixed protocol codes, never
    # forwarded or interpreted as natural-language recovery instructions.
    code = code or next((c for c in known if c in str(error)), "")
    if code:
        category = ("uncertain" if code == "ECOM_PLAN_EXECUTION_UNCERTAIN" else
                    "budget" if code in known[:1] or "BUDGET" in code else "business")
        return code, category, False
    if isinstance(error, (ValueError, json.JSONDecodeError)):
        return "PLANNER_OUTPUT_VALIDATION_FAILED", "output_validation", False
    classified = classify_error(error, model_call=True)
    # A read timeout/partial stream may already have consumed provider tokens.
    return classified.error_code, ("uncertain" if classified.is_transient else classified.category.value), False


def attach_receipt(result, *, failed_stage=None, preserved=(), category=None, action=None):
    metadata = result.metadata
    domain_status = metadata.get("status")
    if domain_status not in {"ready", "needs_input", "insufficient", "planning"}:
        domain_status = "error" if result.is_failure else "planning"
    if action is None:
        action = ("generate" if domain_status == "ready" else "ask_user" if domain_status in {"needs_input", "insufficient"}
                  else "wait_existing" if domain_status == "planning" or category == "uncertain" or result.error_message in {"ECOM_PLAN_LEASE_BUSY", "ECOM_PLAN_EXECUTION_UNCERTAIN"}
                  else "report_error")
    receipt = RecoveryReceipt(status=domain_status, plan_id=metadata.get("plan_id"), failed_stage=failed_stage,
        error={"code": result.error_message, "category": category or "business"} if result.is_failure else {},
        recovery=RecoveryAction(action=action, preserved_stages=list(preserved)),
        generation_allowed=domain_status == "ready")
    metadata["retry_context"] = receipt.model_dump(mode="json")
    metadata["retryable"] = action in {"repair_arguments", "resume_plan"}
    metadata["stop_workflow"] = result.is_failure and action not in {"repair_arguments", "resume_plan"}
    return result


def model_projection(metadata):
    raw = metadata.get("retry_context")
    if not isinstance(raw, dict) or raw.get("workflow") != "ecommerce_main_images":
        return None
    return json.dumps(RecoveryReceipt.model_validate(raw).model_dump(mode="json"), ensure_ascii=False)


def failure_summary(code, category, stage=None, preserved=()):
    reason = {
        "authentication": "策划模型鉴权失败，需要修复平台模型配置后重试。",
        "balance": "平台策划模型额度不足，需要补充供应商额度后重试。",
        "output_validation": "策划结果未通过格式或引用校验，已停止自动处理。",
        "budget": "主图策划已达到本次自动执行次数或时间上限。",
        "uncertain": "策划调用结果尚未确定，已停止自动重发，请核验已有执行记录。",
        "infra": "策划记录保存失败，请核验已有记录后重试。",
    }.get(category, "主图策划服务调用失败，请检查服务配置或稍后重试。")
    if code == "ECOM_PLAN_INSUFFICIENT_CREDITS":
        reason = "主图策划积分不足，请充值后重试。"
    progress = f"失败阶段：{stage}。" if stage else ""
    saved = f"已保留有效阶段：{','.join(map(str, preserved))}。" if preserved else ""
    return reason + progress + saved + "当前策划未完成，未按此方案提交生图。"
