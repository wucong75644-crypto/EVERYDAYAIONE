"""Lease、fencing 与幂等恢复合同。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from services.agent.runtime.domain.errors import (
    FencingTokenMismatchError,
    IdempotencyConflictError,
    LeaseExpiredError,
)
from services.agent.runtime.domain.identity import (
    FencingToken,
    IdempotencyKey,
    require_stable_value,
)
from services.agent.runtime.domain.scope import RuntimeScope


@dataclass(frozen=True)
class Lease:
    """数据库签发的有期限执行权。"""

    fencing_token: FencingToken
    expires_at: datetime

    def __post_init__(self) -> None:
        require_stable_value(self.fencing_token, "fencing_token")
        if self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")

    def validate(self, token: FencingToken, now: datetime) -> None:
        if token != self.fencing_token:
            raise FencingTokenMismatchError("fencing token does not match")
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        if now >= self.expires_at:
            raise LeaseExpiredError("lease has expired")


class IdempotencyOutcome(StrEnum):
    EXISTING = "existing"


@dataclass(frozen=True)
class IdempotencyRecord:
    """已提交逻辑请求的稳定去重身份。"""

    key: IdempotencyKey
    scope: RuntimeScope
    request_hash: str
    entity_id: str

    def __post_init__(self) -> None:
        require_stable_value(self.key, "idempotency key")
        require_stable_value(self.request_hash, "request_hash")
        require_stable_value(self.entity_id, "entity_id")

    def resolve(
        self,
        *,
        scope: RuntimeScope,
        request_hash: str,
    ) -> IdempotencyOutcome:
        """相同请求返回既有事实，键碰撞则失败关闭。"""
        if scope != self.scope or request_hash != self.request_hash:
            raise IdempotencyConflictError(
                "idempotency key belongs to a different request"
            )
        return IdempotencyOutcome.EXISTING
