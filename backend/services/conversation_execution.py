"""Conversation Actor 的执行权协调与原子终态出口。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from loguru import logger
from psycopg import IntegrityError
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class GenerationClaim:
    """数据库签发的一次生成执行权。"""

    task_id: str
    execution_token: str
    conversation_id: str
    turn_id: str
    input_message_id: str
    base_context_revision: int
    context_through_message_id: str | None
    execution_attempt: int
    execution_mode: str

    @classmethod
    def from_rpc(
        cls,
        data: Mapping[str, Any],
        conversation_id: str,
        execution_mode: str,
    ) -> "GenerationClaim | None":
        if data.get("outcome") != "claimed":
            return None
        required = ("task_id", "execution_token", "turn_id", "input_message_id")
        if any(not data.get(field) for field in required):
            raise RuntimeError("ACTOR_CLAIM_RESULT_INVALID")
        base_revision = data.get("base_context_revision")
        attempt = data.get("execution_attempt")
        if not isinstance(base_revision, int) or not isinstance(attempt, int):
            raise RuntimeError("ACTOR_CLAIM_RESULT_INVALID")
        return cls(
            task_id=str(data["task_id"]),
            execution_token=str(data["execution_token"]),
            conversation_id=conversation_id,
            turn_id=str(data["turn_id"]),
            input_message_id=str(data["input_message_id"]),
            base_context_revision=base_revision,
            context_through_message_id=data.get("context_through_message_id"),
            execution_attempt=attempt,
            execution_mode=execution_mode,
        )


@dataclass(frozen=True)
class GenerationOutcome:
    """纯执行器产物；不包含数据库副作用。"""

    result_content: list[dict[str, Any]]
    usage: dict[str, Any]
    credits_cost: int
    tool_digest: dict[str, Any] | None = None
    data_evidence: list[dict[str, Any]] | None = None
    context_items: list[dict[str, Any]] | None = None
    artifacts: list[dict[str, Any]] | None = None
    context_receipts: list[dict[str, Any]] | None = None
    compaction: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.result_content, list):
            raise TypeError("result_content must be a list")
        if not isinstance(self.usage, dict):
            raise TypeError("usage must be a dict")
        if self.credits_cost < 0:
            raise ValueError("credits_cost must be non-negative")
        if self.tool_digest is not None and not isinstance(self.tool_digest, dict):
            raise TypeError("tool_digest must be a dict or None")
        if self.data_evidence is not None and not isinstance(
            self.data_evidence, list
        ):
            raise TypeError("data_evidence must be a list or None")
        for name in ("context_items", "artifacts", "context_receipts"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, list):
                raise TypeError(f"{name} must be a list or None")
        if self.compaction is not None and not isinstance(
            self.compaction, dict
        ):
            raise TypeError("compaction must be a dict or None")


class GenerationExecutor(Protocol):
    """Chat 纯执行器协议；具体实现由后续 ChatHandler 拆分阶段提供。"""

    async def execute(
        self,
        task: Mapping[str, Any],
        claim: GenerationClaim,
        cancellation_event: asyncio.Event,
    ) -> GenerationOutcome:
        """执行一次固定 ContextSnapshot 的生成。"""


class TerminalObserver(Protocol):
    """数据库终态完成后的 best-effort 外部副作用。"""

    async def notify(
        self,
        task: Mapping[str, Any],
        terminal_result: Mapping[str, Any],
    ) -> None:
        """发送终态通知并释放外部资源。"""


class _OwnershipLost(RuntimeError):
    pass


class ConversationExecutionService:
    """协调 claim、租约、纯执行器和数据库原子终态。"""

    def __init__(
        self,
        db: Any,
        executor: GenerationExecutor,
        *,
        lease_seconds: int = 90,
        renew_interval_seconds: float = 30,
        max_renew_failures: int = 2,
        max_attempts: int = 3,
        terminal_observer: TerminalObserver | None = None,
    ) -> None:
        if not 15 <= lease_seconds <= 300:
            raise ValueError("lease_seconds must be between 15 and 300")
        if renew_interval_seconds <= 0 or renew_interval_seconds >= lease_seconds:
            raise ValueError("renew_interval_seconds must be within the lease window")
        if max_renew_failures < 1:
            raise ValueError("max_renew_failures must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._db = db
        self._executor = executor
        self._lease_seconds = lease_seconds
        self._renew_interval = renew_interval_seconds
        self._max_renew_failures = max_renew_failures
        self._max_attempts = max_attempts
        self._terminal_observer = terminal_observer

    async def claim_serial(self, conversation_id: str) -> GenerationClaim | None:
        data = await self._rpc(
            "claim_next_serial_generation_turn",
            {
                "p_conversation_id": conversation_id,
                "p_lease_seconds": self._lease_seconds,
                "p_max_attempts": self._max_attempts,
            },
        )
        return GenerationClaim.from_rpc(data, conversation_id, "serial")

    async def claim_branch(
        self,
        task_id: str,
        conversation_id: str,
    ) -> GenerationClaim | None:
        data = await self._rpc(
            "claim_branch_generation_turn",
            {
                "p_task_id": task_id,
                "p_lease_seconds": self._lease_seconds,
                "p_max_attempts": self._max_attempts,
            },
        )
        return GenerationClaim.from_rpc(data, conversation_id, "branch")

    async def execute_claim(self, claim: GenerationClaim) -> dict[str, Any]:
        task = await self._load_task(claim.task_id)
        if task.get("conversation_id") != claim.conversation_id:
            raise RuntimeError("ACTOR_TASK_SCOPE_MISMATCH")
        output_message_id = task.get("assistant_message_id")
        if not output_message_id:
            raise RuntimeError("ACTOR_TASK_OUTPUT_MISSING")

        ownership_lost = asyncio.Event()
        renew_task = asyncio.create_task(self._renew_loop(claim, ownership_lost))
        try:
            try:
                outcome = await self._execute_until_lost(
                    task, claim, ownership_lost,
                )
            except _OwnershipLost:
                return {"outcome": "ownership_lost"}
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if ownership_lost.is_set():
                    return {"outcome": "ownership_lost"}
                result = await self._fail(claim, error)
                await self._notify_terminal(task, result)
                return result

            if ownership_lost.is_set():
                await self._cleanup_artifacts(
                    outcome,
                    task_id=claim.task_id,
                )
                return {"outcome": "ownership_lost"}
            try:
                result = await self._commit(
                    claim,
                    str(output_message_id),
                    outcome,
                )
            except BaseException as error:
                await self._cleanup_artifacts(
                    outcome,
                    task_id=claim.task_id,
                )
                if isinstance(error, IntegrityError):
                    result = await self._fail(claim, error)
                    await self._notify_terminal(task, result)
                    return result
                raise
            if result.get("outcome") != "committed":
                await self._cleanup_artifacts(
                    outcome,
                    task_id=claim.task_id,
                )
            await self._notify_terminal(task, result)
            return result
        finally:
            renew_task.cancel()
            try:
                await renew_task
            except asyncio.CancelledError:
                pass

    async def _execute_until_lost(
        self,
        task: Mapping[str, Any],
        claim: GenerationClaim,
        ownership_lost: asyncio.Event,
    ) -> GenerationOutcome:
        execution_task = asyncio.create_task(
            self._executor.execute(task, claim, ownership_lost)
        )
        lost_task = asyncio.create_task(ownership_lost.wait())
        try:
            done, _ = await asyncio.wait(
                {execution_task, lost_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if lost_task in done and not execution_task.done():
                execution_task.cancel()
                try:
                    await execution_task
                except asyncio.CancelledError:
                    pass
                raise _OwnershipLost
            outcome = await execution_task
            if not isinstance(outcome, GenerationOutcome):
                raise TypeError("executor must return GenerationOutcome")
            return outcome
        finally:
            for pending_task in (execution_task, lost_task):
                if not pending_task.done():
                    pending_task.cancel()
            await asyncio.gather(
                execution_task, lost_task, return_exceptions=True,
            )

    async def _renew_loop(
        self,
        claim: GenerationClaim,
        ownership_lost: asyncio.Event,
    ) -> None:
        failures = 0
        while not ownership_lost.is_set():
            await asyncio.sleep(self._renew_interval)
            try:
                result = await self._rpc(
                    "renew_generation_lease",
                    {
                        "p_task_id": claim.task_id,
                        "p_execution_token": claim.execution_token,
                        "p_lease_seconds": self._lease_seconds,
                    },
                )
                outcome = result.get("outcome")
                if outcome == "renewed":
                    failures = 0
                    continue
                if outcome in {"ownership_lost", "terminal"}:
                    ownership_lost.set()
                    return
                failures += 1
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures += 1
                logger.warning(
                    "actor_lease_renew_failed | "
                    f"conversation_id={claim.conversation_id} | "
                    f"task_id={claim.task_id} | turn_id={claim.turn_id} | "
                    f"attempt={failures} | error={type(error).__name__}"
                )
            if failures >= self._max_renew_failures:
                ownership_lost.set()
                return

    async def _commit(
        self,
        claim: GenerationClaim,
        output_message_id: str,
        outcome: GenerationOutcome,
    ) -> dict[str, Any]:
        return await self._rpc(
            "commit_generation_turn",
            {
                "p_task_id": claim.task_id,
                "p_execution_token": claim.execution_token,
                "p_output_message_id": output_message_id,
                "p_result_content": Jsonb(outcome.result_content),
                "p_usage": Jsonb(outcome.usage),
                "p_credits_cost": outcome.credits_cost,
                "p_tool_digest": (
                    Jsonb(outcome.tool_digest)
                    if outcome.tool_digest is not None else None
                ),
                "p_data_evidence": Jsonb(outcome.data_evidence or []),
                "p_context_items": Jsonb(outcome.context_items or []),
                "p_artifacts": Jsonb(outcome.artifacts or []),
                "p_context_receipts": Jsonb(
                    outcome.context_receipts or []
                ),
                "p_compaction": (
                    Jsonb(outcome.compaction)
                    if outcome.compaction is not None else None
                ),
            },
        )

    async def _cleanup_artifacts(
        self,
        outcome: GenerationOutcome,
        *,
        task_id: str,
    ) -> None:
        from services.agent.runtime.artifacts import (
            cleanup_materialized_artifacts,
        )

        await cleanup_materialized_artifacts(
            outcome.artifacts or [],
            task_id=task_id,
        )

    async def _fail(
        self,
        claim: GenerationClaim,
        error: Exception,
    ) -> dict[str, Any]:
        logger.error(
            "actor_execution_failed | "
            f"conversation_id={claim.conversation_id} | task_id={claim.task_id} | "
            f"turn_id={claim.turn_id} | error={type(error).__name__}"
        )
        return await self._rpc(
            "fail_generation_turn",
            {
                "p_task_id": claim.task_id,
                "p_execution_token": claim.execution_token,
                "p_error_code": type(error).__name__.upper()[:50],
                "p_error_message": str(error) or type(error).__name__,
            },
        )

    async def _load_task(self, task_id: str) -> dict[str, Any]:
        result = await (
            self._db.table("tasks")
            .select("*")
            .eq("id", task_id)
            .maybe_single()
            .execute()
        )
        if not result.data:
            raise RuntimeError("ACTOR_TASK_NOT_FOUND")
        return dict(result.data)

    async def _notify_terminal(
        self,
        task: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        if not self._terminal_observer:
            return
        try:
            await self._terminal_observer.notify(task, result)
        except Exception as error:
            logger.warning(
                "actor_terminal_observer_failed | "
                f"task_id={task.get('id')} | error={type(error).__name__}"
            )

    async def _rpc(
        self,
        name: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        result = await self._db.rpc(name, params).execute()
        if not result or not isinstance(result.data, dict):
            raise RuntimeError(f"ACTOR_RPC_RESULT_INVALID:{name}")
        return result.data
