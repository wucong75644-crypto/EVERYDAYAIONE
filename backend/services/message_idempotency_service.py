"""消息生成 POST 的幂等执行权、指纹和响应重放。"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from loguru import logger

from core.exceptions import AppException
from schemas.message import GenerateRequest, GenerateResponse, MessageOperation


_RUNTIME_PARAM_KEYS = {
    "_task_slot_id",
    "_org_id",
    "_user_location",
}
_CLIENT_TASK_NAMESPACE = uuid.UUID("16012b8a-f9aa-4a65-943b-e3002ce22831")
_ASSISTANT_MESSAGE_NAMESPACE = uuid.UUID("f94bb2af-b530-48c8-9fc4-2e23c2fa70c4")


@dataclass(frozen=True)
class IdempotencyClaim:
    request_id: str
    replay_response: GenerateResponse | None = None


class MessageIdempotencyService:
    """在业务副作用前抢占请求，并保存可重放终态。"""

    def __init__(self, db: Any, user_id: str, org_id: str | None) -> None:
        self.db = db
        self.user_id = user_id
        self.org_id = org_id

    def ensure_identity(
        self,
        request: Request,
        conversation_id: str,
        body: GenerateRequest,
    ) -> None:
        """为旧客户端补齐统一生成事务需要的幂等标识。"""
        header_key = request.headers.get("idempotency-key")
        if not body.client_request_id:
            body.client_request_id = header_key or str(uuid.uuid4())
        identity_key = (
            f"{self.user_id}:{self.org_id or ''}:{conversation_id}:"
            f"{body.client_request_id}"
        )
        if not body.client_task_id:
            body.client_task_id = str(uuid.uuid5(_CLIENT_TASK_NAMESPACE, identity_key))
        if body.operation in (MessageOperation.RETRY, MessageOperation.REGENERATE_SINGLE):
            if body.original_message_id:
                body.assistant_message_id = body.original_message_id
        elif not body.assistant_message_id:
            body.assistant_message_id = str(
                uuid.uuid5(_ASSISTANT_MESSAGE_NAMESPACE, identity_key)
            )

    def claim(
        self,
        request: Request,
        conversation_id: str,
        body: GenerateRequest,
    ) -> IdempotencyClaim | None:
        """抢占执行权；旧客户端未传幂等键时保持原行为。"""
        key = self._resolve_key(request, body)
        if key is None:
            return None
        if not 1 <= len(key) <= 100:
            raise AppException(
                code="IDEMPOTENCY_KEY_INVALID",
                message="幂等标识长度必须在 1 到 100 个字符之间",
                status_code=400,
            )
        if not body.client_task_id or not body.assistant_message_id:
            raise AppException(
                code="IDEMPOTENCY_IDS_REQUIRED",
                message="幂等请求缺少客户端任务或消息 ID",
                status_code=400,
            )

        fingerprint = self.build_fingerprint(conversation_id, body)
        result = self.db.rpc(
            "claim_message_generation_request",
            {
                "p_org_id": self.org_id,
                "p_user_id": self.user_id,
                "p_conversation_id": conversation_id,
                "p_idempotency_key": key,
                "p_request_fingerprint": fingerprint,
                "p_client_task_id": body.client_task_id,
                "p_assistant_message_id": body.assistant_message_id,
            },
        ).execute()
        data = result.data
        if not isinstance(data, dict) or not data.get("request_id"):
            raise AppException(
                code="IDEMPOTENCY_CLAIM_FAILED",
                message="消息请求状态初始化失败",
                status_code=500,
            )

        outcome = data.get("outcome")
        request_id = str(data["request_id"])
        logger.info(
            "Message idempotency claim | "
            f"user_id={self.user_id} | conversation_id={conversation_id} | "
            f"client_request_id={key} | outcome={outcome}"
        )
        if outcome == "claimed":
            return IdempotencyClaim(request_id=request_id)
        if outcome == "fingerprint_mismatch":
            raise AppException(
                code="IDEMPOTENCY_KEY_REUSED",
                message="同一请求标识不能用于不同的消息内容",
                status_code=422,
            )
        if outcome == "processing":
            raise AppException(
                code="IDEMPOTENCY_REQUEST_IN_PROGRESS",
                message="该消息正在处理中",
                status_code=409,
                details={"retry_after": 1},
            )
        if outcome == "completed":
            response_body = data.get("stored_response_body")
            if not isinstance(response_body, dict):
                raise AppException(
                    code="IDEMPOTENCY_REPLAY_INVALID",
                    message="消息请求的历史响应不可用",
                    status_code=500,
                )
            response = GenerateResponse.model_validate(response_body)
            return IdempotencyClaim(request_id=request_id, replay_response=response)
        if outcome == "failed":
            self._raise_stored_failure(data)
        raise AppException(
            code="IDEMPOTENCY_STATE_INVALID",
            message="消息请求状态异常",
            status_code=500,
        )

    @staticmethod
    def build_fingerprint(conversation_id: str, body: GenerateRequest) -> str:
        """对稳定业务字段生成 SHA-256；运行时注入参数不参与。"""
        params = {
            key: value
            for key, value in (body.params or {}).items()
            if key not in _RUNTIME_PARAM_KEYS
        }
        payload = {
            "conversation_id": conversation_id,
            "operation": body.operation.value,
            "content": [part.model_dump(mode="json") for part in body.content],
            "generation_type": body.generation_type.value if body.generation_type else None,
            "model": body.model,
            "params": params,
            "original_message_id": body.original_message_id,
            "assistant_message_id": body.assistant_message_id,
            "client_task_id": body.client_task_id,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def complete(self, claim: IdempotencyClaim | None, response: GenerateResponse) -> None:
        if claim is None:
            return
        user_message_id = response.user_message.id if response.user_message else None
        self._update(
            claim.request_id,
            {
                "status": "completed",
                "user_message_id": user_message_id,
                "response_status": 200,
                "response_body": response.model_dump(mode="json"),
                "error_code": None,
            },
        )

    def fail(self, claim: IdempotencyClaim | None, error: AppException) -> None:
        if claim is None:
            return
        self._update(
            claim.request_id,
            {
                "status": "failed",
                "response_status": error.status_code,
                "response_body": {
                    "error": {
                        "code": error.code,
                        "message": error.message,
                        "details": error.details,
                    }
                },
                "error_code": error.code,
            },
        )

    def fail_unexpected(
        self,
        claim: IdempotencyClaim | None,
        error: Exception,
    ) -> None:
        """最佳努力记录统一 500，且绝不覆盖原始异常。"""
        if claim is None:
            return
        try:
            self.fail(
                claim,
                AppException(
                    code="INTERNAL_SERVER_ERROR",
                    message="消息请求处理失败",
                    status_code=500,
                ),
            )
        except Exception as persistence_error:
            logger.error(
                "Message idempotency unexpected failure persistence failed | "
                f"user_id={self.user_id} | request_id={claim.request_id} | "
                f"original_error={type(error).__name__} | "
                f"persistence_error={type(persistence_error).__name__}"
            )

    def _update(self, request_id: str, values: dict[str, Any]) -> None:
        values["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.db.table("message_generation_requests").update(values).eq(
            "id", request_id
        ).execute()

    @staticmethod
    def _resolve_key(request: Request, body: GenerateRequest) -> str | None:
        header_key = request.headers.get("idempotency-key")
        body_key = body.client_request_id
        if header_key and body_key and header_key != body_key:
            raise AppException(
                code="IDEMPOTENCY_KEY_MISMATCH",
                message="请求头与请求体的幂等标识不一致",
                status_code=400,
            )
        return header_key or body_key

    @staticmethod
    def _raise_stored_failure(data: dict[str, Any]) -> None:
        response_body = data.get("stored_response_body")
        error = response_body.get("error") if isinstance(response_body, dict) else None
        if not isinstance(error, dict):
            raise AppException(
                code="IDEMPOTENCY_REPLAY_INVALID",
                message="消息请求的历史错误不可用",
                status_code=500,
            )
        raise AppException(
            code=str(error.get("code") or data.get("stored_error_code") or "REQUEST_FAILED"),
            message=str(error.get("message") or "消息请求失败"),
            status_code=int(data.get("stored_response_status") or 500),
            details=error.get("details") if isinstance(error.get("details"), dict) else {},
        )
