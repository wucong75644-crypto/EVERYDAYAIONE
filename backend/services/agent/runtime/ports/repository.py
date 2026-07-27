"""当前 PostgreSQL foundation 已落地的聚合持久化 SPI。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Protocol

from services.agent.runtime.domain import (
    FencingToken,
    ModelStepId,
    RunAttempt,
    RunId,
    RuntimeScope,
    SessionCommand,
    SessionId,
)


class MutationOutcome(StrEnum):
    CREATED = "created"
    ALREADY_EXISTS = "already_exists"
    RENEWED = "renewed"
    TRANSITIONED = "transitioned"
    COMPLETED = "completed"
    ALREADY_COMPLETED = "already_completed"
    FAILED = "failed"
    ALREADY_FAILED = "already_failed"
    CANCELLED = "cancelled"
    ALREADY_CANCELLED = "already_cancelled"
    NOT_READY = "not_ready"


class ClaimOutcome(StrEnum):
    CLAIMED = "claimed"
    BUSY = "busy"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class MutationReceipt:
    outcome: MutationOutcome
    entity_id: str | None = None
    state_version: int | None = None
    event_sequence: int | None = None
    result_entity_id: str | None = None


@dataclass(frozen=True)
class RunClaim:
    outcome: ClaimOutcome
    attempt: RunAttempt | None = None
    state_version: int | None = None
    event_sequence: int | None = None


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: SessionId
    conversation_id: str
    scope: RuntimeScope
    created_by_user_id: str | None
    agent_definition_id: str
    agent_definition_revision: str
    next_event_sequence: int
    state_version: int


class RuntimeRepositoryPort(Protocol):
    """只描述 migrations 212～215 已落地的用例型 RPC。"""

    async def ensure_session(
        self,
        conversation_id: str,
        scope: RuntimeScope,
        created_by_user_id: str,
        agent_definition_id: str,
        agent_definition_revision: str,
    ) -> MutationReceipt:
        """按 Conversation 一对一确保 Runtime Session。"""

    async def submit_command(
        self,
        command: SessionCommand,
    ) -> MutationReceipt:
        """按命令幂等键提交，重复请求返回既有 receipt。"""

    async def get_session(self, session_id: SessionId) -> SessionSnapshot | None:
        """读取 Session 协调视图。"""

    async def create_run(
        self,
        session_id: SessionId,
        command_id: str,
        idempotency_key: str,
        run_kind: str,
        context_receipt: Mapping[str, object],
        config_snapshot: Mapping[str, object],
        capability_snapshot: Mapping[str, object],
    ) -> MutationReceipt:
        """幂等创建 queued Run。"""

    async def claim_run(self, run_id: RunId, worker_id: str) -> RunClaim:
        """签发或恢复 Run 执行权。"""

    async def renew_run(
        self,
        run_id: RunId,
        token: FencingToken,
        lease_seconds: int = 90,
    ) -> MutationReceipt:
        """续租当前 Run claim。"""

    async def set_run_waiting(
        self,
        run_id: RunId,
        token: FencingToken,
        state_version: int,
        waiting_status: str,
    ) -> MutationReceipt:
        """把 running Run 原子推进到等待态。"""

    async def wake_run(
        self,
        run_id: RunId,
        state_version: int,
    ) -> MutationReceipt:
        """满足数据库前置条件后将等待 Run 重新排队。"""

    async def complete_run(
        self,
        run_id: RunId,
        token: FencingToken,
        state_version: int,
        result_hash: str,
    ) -> MutationReceipt:
        """完成 Run。"""

    async def fail_run(
        self, run_id: RunId, token: FencingToken,
        state_version: int, error_code: str,
    ) -> MutationReceipt:
        """失败 Run。"""

    async def cancel_run(
        self, run_id: RunId, state_version: int, reason: str,
    ) -> MutationReceipt:
        """取消 Run。"""

    async def create_model_step(
        self, run_id: RunId, token: FencingToken, *,
        model_id: str, provider: str, model_revision: str,
        prompt_revision: str, tool_catalog_revision: str,
        request_receipt: Mapping[str, object],
    ) -> MutationReceipt:
        """创建 ModelStep。"""

    async def complete_model_step(
        self, model_step_id: ModelStepId, token: FencingToken,
        state_version: int, *, response_receipt: Mapping[str, object],
        stop_reason: str, provider_stop_reason: str | None,
        input_tokens: int, output_tokens: int, reasoning_tokens: int,
    ) -> MutationReceipt:
        """完成 ModelStep。"""

    async def fail_model_step(
        self, model_step_id: ModelStepId, token: FencingToken,
        state_version: int, error_code: str,
    ) -> MutationReceipt:
        """失败 ModelStep。"""
