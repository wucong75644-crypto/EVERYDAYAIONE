"""统一生成请求的数据库生命周期边界。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from loguru import logger
from psycopg.types.json import Jsonb

from core.exceptions import AppException
from services.handlers.context_snapshot import ContextAnchor

MAX_PREPARED_TASKS = 16
_PREPARE_CONFLICT_MARKERS = (
    "TURN_MESSAGE_RELATION_MISMATCH",
    "GENERATION_PREPARE_MESSAGE_CONFLICT",
    "GENERATION_PREPARE_TASK_CONFLICT",
    "GENERATION_PREPARE_TURN_CONFLICT",
    "GENERATION_PREPARE_REQUEST_MISMATCH",
    "GENERATION_PREPARE_ANCHOR_MISSING",
)


@dataclass(frozen=True)
class GenerationPreparation:
    """数据库原子准备返回的权威 Turn、消息和任务锚点。"""

    request_id: str
    conversation_id: str
    turn_id: str
    input_message_id: str
    output_message_id: str
    base_context_revision: int
    context_through_message_id: str | None
    task_ids: tuple[str, ...]
    already_prepared: bool

    @classmethod
    def from_rpc(cls, data: Mapping[str, Any]) -> "GenerationPreparation":
        """严格解析 prepare_generation 的 JSONB 返回值。"""
        required = (
            "request_id",
            "conversation_id",
            "turn_id",
            "input_message_id",
            "output_message_id",
        )
        if any(not data.get(field) for field in required):
            raise RuntimeError("GENERATION_PREPARE_RESULT_INVALID")
        revision = data.get("base_context_revision")
        task_ids = data.get("task_ids")
        replayed = data.get("already_prepared")
        if (
            not isinstance(revision, int)
            or revision < 0
            or not isinstance(task_ids, list)
            or not 1 <= len(task_ids) <= MAX_PREPARED_TASKS
            or any(not task_id for task_id in task_ids)
            or not isinstance(replayed, bool)
        ):
            raise RuntimeError("GENERATION_PREPARE_RESULT_INVALID")
        return cls(
            request_id=str(data["request_id"]),
            conversation_id=str(data["conversation_id"]),
            turn_id=str(data["turn_id"]),
            input_message_id=str(data["input_message_id"]),
            output_message_id=str(data["output_message_id"]),
            base_context_revision=revision,
            context_through_message_id=data.get("context_through_message_id"),
            task_ids=tuple(str(task_id) for task_id in task_ids),
            already_prepared=replayed,
        )

    def context_anchor(self, task_id: str, org_id: str | None) -> ContextAnchor:
        """为已准备 task 构造不可变上下文锚点。"""
        if task_id not in self.task_ids:
            raise ValueError("GENERATION_PREPARE_TASK_ID_UNKNOWN")
        return ContextAnchor(
            task_id=task_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            input_message_id=self.input_message_id,
            base_revision=self.base_context_revision,
            through_message_id=self.context_through_message_id,
            org_id=org_id,
        )


@dataclass(frozen=True)
class ExternalTaskAttachment:
    """供应商 task 附加结果。"""

    task_id: str
    already_attached: bool


@dataclass(frozen=True)
class PreparedTaskFailure:
    """preparing task 失败终态结果。"""

    task_id: str
    already_failed: bool


class GenerationLifecycle:
    """调用统一生成生命周期 RPC，并校验所有返回值。"""

    def __init__(self, db: Any) -> None:
        self._db = db

    def prepare(
        self,
        *,
        request_id: str,
        operation: str,
        conversation_id: str,
        user_id: str,
        org_id: str | None,
        turn_id: str | None,
        input_message: Mapping[str, Any],
        output_message: Mapping[str, Any],
        tasks: Sequence[Mapping[str, Any]],
    ) -> GenerationPreparation:
        """原子创建或验证 Turn、输入/输出消息与本地任务。"""
        if not 1 <= len(tasks) <= MAX_PREPARED_TASKS:
            raise ValueError("GENERATION_PREPARE_TASK_COUNT_INVALID")
        try:
            response = self._db.rpc(
                "prepare_generation",
                {
                    "p_request_id": request_id,
                    "p_operation": operation,
                    "p_conversation_id": conversation_id,
                    "p_user_id": user_id,
                    "p_org_id": org_id,
                    "p_turn_id": turn_id,
                    "p_input_message": Jsonb(dict(input_message)),
                    "p_output_message": Jsonb(dict(output_message)),
                    "p_tasks": Jsonb([dict(task) for task in tasks]),
                },
            ).execute()
        except Exception as error:
            marker = _prepare_conflict_marker(error)
            if marker is None:
                raise
            logger.warning(
                "generation_prepare_conflict | "
                f"marker={marker} | request_id={request_id} | org_id={org_id} | "
                f"user_id={user_id} | conversation_id={conversation_id}"
            )
            raise AppException(
                code="GENERATION_PREPARE_CONFLICT",
                message="生成请求状态已变化，请刷新后重试",
                status_code=409,
            ) from error
        data = response.data if response else None
        if not isinstance(data, dict):
            raise RuntimeError("GENERATION_PREPARE_RESULT_INVALID")
        result = GenerationPreparation.from_rpc(data)
        logger.info(
            "generation_prepared | "
            f"request_id={result.request_id} | org_id={org_id} | "
            f"user_id={user_id} | conversation_id={result.conversation_id} | "
            f"turn_id={result.turn_id} | task_ids={','.join(result.task_ids)} | "
            f"already_prepared={result.already_prepared}"
        )
        return result

    def attach_external_task(
        self,
        *,
        task_id: str,
        external_task_id: str,
        credit_transaction_id: str | None,
        org_id: str | None,
        user_id: str,
        provider: str,
        actual_model_id: str | None = None,
        actual_request_params: Mapping[str, Any] | None = None,
    ) -> ExternalTaskAttachment:
        """把供应商 task 附加到 preparing 本地任务。"""
        response = self._db.rpc(
            "attach_generation_external_task",
            {
                "p_task_id": task_id,
                "p_external_task_id": external_task_id,
                "p_credit_transaction_id": credit_transaction_id,
                "p_org_id": org_id,
                "p_actual_model_id": actual_model_id,
                "p_actual_request_params": (
                    Jsonb(dict(actual_request_params))
                    if actual_request_params is not None else None
                ),
            },
        ).execute()
        data = response.data if response else None
        result = _parse_transition_result(data, "already_attached", task_id)
        logger.info(
            "media_submission_attached | "
            f"task_id={task_id} | external_task_id={external_task_id} | "
            f"org_id={org_id} | user_id={user_id} | provider={provider} | "
            f"already_attached={result[1]}"
        )
        return ExternalTaskAttachment(result[0], result[1])

    def fail_prepared_task(
        self,
        *,
        task_id: str,
        terminal_reason: str,
        error_message: str | None,
        org_id: str | None,
        user_id: str,
    ) -> PreparedTaskFailure:
        """把 preparing task 置为失败，并保留可补偿业务上下文。"""
        response = self._db.rpc(
            "fail_prepared_generation_task",
            {
                "p_task_id": task_id,
                "p_terminal_reason": terminal_reason,
                "p_error_message": error_message,
                "p_org_id": org_id,
            },
        ).execute()
        data = response.data if response else None
        result = _parse_transition_result(data, "already_failed", task_id)
        logger.warning(
            "prepared_task_failed | "
            f"task_id={task_id} | org_id={org_id} | user_id={user_id} | "
            f"terminal_reason={terminal_reason} | already_failed={result[1]}"
        )
        return PreparedTaskFailure(result[0], result[1])


def _parse_transition_result(
    data: Any,
    replay_key: str,
    expected_task_id: str,
) -> tuple[str, bool]:
    """解析 attach/fail RPC 的公共返回结构。"""
    if (
        not isinstance(data, dict)
        or not data.get("task_id")
        or str(data["task_id"]) != expected_task_id
        or not isinstance(data.get(replay_key), bool)
    ):
        raise RuntimeError("GENERATION_TRANSITION_RESULT_INVALID")
    return str(data["task_id"]), data[replay_key]


def _prepare_conflict_marker(error: Exception) -> str | None:
    """只把数据库声明的生成关系冲突转换为客户端可重试冲突。"""
    serialized = str(error)
    return next(
        (marker for marker in _PREPARE_CONFLICT_MARKERS if marker in serialized),
        None,
    )
