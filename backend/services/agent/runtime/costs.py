"""Idempotent non-model Action Cost Ledger contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from services.agent.runtime.executors.specialist_contracts import CostReservation


class ActionCostPort(Protocol):
    async def reserve(self, item: CostReservation) -> object: ...
    async def settle(self, item: CostReservation, actual_amount: int) -> object: ...
    async def release(self, item: CostReservation) -> object: ...
    async def refund(self, item: CostReservation, reason: str) -> object: ...
    async def adjustment(self, item: CostReservation, receipt_hash: str, amount: int) -> object: ...


@dataclass
class InMemoryActionCostLedger:
    """Deterministic fake ledger for unit tests; production uses narrow RPCs."""

    entries: dict[tuple[str, str, str], CostReservation]

    def __init__(self) -> None:
        self.entries = {}

    async def reserve(self, item: CostReservation) -> CostReservation:
        return self._put(item)

    async def settle(self, item: CostReservation, actual_amount: int) -> CostReservation:
        if actual_amount < 0:
            raise ValueError("ACTION_COST_NEGATIVE")
        return self._put(item)

    async def release(self, item: CostReservation) -> CostReservation:
        return self._put(item)

    async def refund(self, item: CostReservation, reason: str) -> CostReservation:
        if not reason.strip():
            raise ValueError("ACTION_COST_REASON_REQUIRED")
        return self._put(item)

    async def adjustment(self, item: CostReservation, receipt_hash: str, amount: int) -> CostReservation:
        if len(receipt_hash) != 64 or amount < 0:
            raise ValueError("ACTION_COST_ADJUSTMENT_INVALID")
        return self._put(item)

    def _put(self, item: CostReservation) -> CostReservation:
        key = (item.action_id, item.attempt_id, item.kind)
        existing = self.entries.get(key)
        if existing is not None and existing != item:
            raise ValueError("ACTION_COST_IDEMPOTENCY_CONFLICT")
        self.entries[key] = item
        return item


__all__ = ["ActionCostPort", "InMemoryActionCostLedger"]
