"""Private ActionLoop helpers kept separate from lifecycle orchestration."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Mapping

from services.agent.runtime.ports.action_repository import ActionRepositoryPort
from services.agent.runtime.ports.coordinator_recovery import ActionDispatchSnapshot
from services.agent.runtime.ports.executor import ExecutionOutcome, ExecutionReceipt


class CostReserveFailure(RuntimeError):
    pass


def required_result(receipt: ExecutionReceipt) -> Mapping[str, object]:
    if receipt.result is None:
        raise RuntimeError("ACTION_RESULT_REQUIRED")
    result = receipt.result
    return {
        "status": result.status.value,
        "summary": result.summary,
        "data": result.data,
        "artifact_ids": list(result.artifact_ids),
        "usage": dict(result.usage),
        "cost": dict(result.cost),
        "external_receipt": dict(result.receipt),
        "error_code": str(result.data.get("error_code")) if result.data and result.data.get("error_code") else None,
    }


def result(receipt: ExecutionReceipt) -> Mapping[str, object] | None:
    if receipt.outcome is ExecutionOutcome.COMPLETED:
        return required_result(receipt)
    if receipt.outcome is ExecutionOutcome.FAILED:
        return failure_result(receipt)
    return None


def failure_result(receipt: ExecutionReceipt) -> Mapping[str, object]:
    error_code = receipt.external_receipt.get("error_code", "executor_failed")
    return {
        "status": "error", "summary": str(receipt.external_receipt.get("summary", "")),
        "data": dict(receipt.external_receipt), "artifact_ids": [], "usage": {}, "cost": {},
        "external_receipt": dict(receipt.external_receipt), "error_code": str(error_code),
    }


def specialist_finalizer(
    facts: object, receipt: ExecutionReceipt,
    external: Mapping[str, object],
):
    evidence = external.get("evidence")
    if (
        receipt.outcome in {ExecutionOutcome.COMPLETED, ExecutionOutcome.FAILED}
        and external.get("provider") == "kie"
        and isinstance(evidence, Mapping)
        and evidence.get("cancel_unproven") is True
        and hasattr(facts, "media_cancel_readback_terminal")
    ):
        return facts.media_cancel_readback_terminal
    return facts.finalize


async def persist_specialist_unknown(
    facts: object, *, external: Mapping[str, object], attempt_id: str,
    token: str, state_version: int, request_hash: str,
    ambiguity_evidence: Mapping[str, object], next_reconcile_at: datetime,
) -> None:
    if external.get("provider") == "kie" and hasattr(
        facts, "media_provider_unknown",
    ):
        await facts.media_provider_unknown(
            attempt_id=attempt_id, execution_token=token,
            expected_state_version=state_version, request_hash=request_hash,
            provider_receipt=dict(external),
            ambiguity_evidence=dict(ambiguity_evidence),
            next_reconcile_at=next_reconcile_at,
        )
        return
    await facts.provider_unknown(
        attempt_id=attempt_id, execution_token=token,
        request_hash=request_hash,
        ambiguity_evidence=dict(ambiguity_evidence),
    )


def int_value(value: Mapping[str, object], field: str) -> int:
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int):
        raise RuntimeError(f"ACTION_{field.upper()}_REQUIRED")
    return item


def required(value: str | None, name: str) -> str:
    if value is None:
        raise RuntimeError(f"{name.upper()}_REQUIRED")
    return value


def required_int(value: int | None, name: str) -> int:
    if value is None:
        raise RuntimeError(f"{name.upper()}_REQUIRED")
    return value


def required_time(value):
    if value is None:
        raise RuntimeError("RECONCILIATION_LEASE_EXPIRY_REQUIRED")
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise RuntimeError("RECONCILIATION_LEASE_EXPIRY_INVALID") from None
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise RuntimeError("RECONCILIATION_LEASE_EXPIRY_INVALID")
    return value


def reserved_amount(snapshot: ActionDispatchSnapshot) -> int:
    arguments = snapshot.action.get("arguments", {})
    if not isinstance(arguments, Mapping):
        return 0
    value = arguments.get("reserved_credits", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def next_reconcile_at(lease_seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=max(60, lease_seconds))


def provider_idempotency_key(
    external: Mapping[str, object], fallback: str,
) -> str:
    evidence = external.get("evidence")
    value = evidence.get("provider_idempotency_key") if isinstance(
        evidence, Mapping
    ) else None
    if not isinstance(value, str):
        value = external.get("provider_idempotency_key", fallback)
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 300:
        raise RuntimeError("SPECIALIST_PROVIDER_IDEMPOTENCY_KEY_INVALID")
    return value.strip()


class ActionLease:
    def __init__(self, *, repository: ActionRepositoryPort, attempt_id: str, token: str,
                 state_version: int, lease_seconds: int, renew_interval: float,
                 reconciliation: bool) -> None:
        self._repository = repository
        self._attempt_id = attempt_id
        self._token = token
        self.state_version = state_version
        self._lease_seconds = lease_seconds
        self._renew_interval = renew_interval
        self._reconciliation = reconciliation
        self._lock = asyncio.Lock()

    async def run(self, awaitable: object) -> ExecutionReceipt:
        if not hasattr(awaitable, "__await__"):
            raise TypeError("ACTION_EXECUTOR_AWAITABLE_REQUIRED")
        work = asyncio.ensure_future(awaitable)
        renewal = asyncio.create_task(self._renew())
        try:
            done, _ = await asyncio.wait({work, renewal}, return_when=asyncio.FIRST_COMPLETED)
            if renewal in done:
                work.cancel()
                with suppress(asyncio.CancelledError):
                    await work
                error = renewal.exception()
                if error is not None:
                    raise error
                raise RuntimeError("ACTION_LEASE_LOST")
            result_value = await work
            async with self._lock:
                pass
            return result_value
        finally:
            renewal.cancel()
            with suppress(asyncio.CancelledError):
                await renewal

    async def _renew(self) -> None:
        while True:
            await asyncio.sleep(self._renew_interval)
            async with self._lock:
                if self._reconciliation:
                    receipt = await self._repository.renew_reconciliation(
                        attempt_id=self._attempt_id, reconciliation_token=self._token,
                        expected_state_version=self.state_version, lease_seconds=self._lease_seconds,
                    )
                else:
                    receipt = await self._repository.renew(
                        attempt_id=self._attempt_id, execution_token=self._token,
                        expected_state_version=self.state_version, lease_seconds=self._lease_seconds,
                    )
                self.state_version = required_int(receipt.state_version, "Action renewal state_version")
