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


def require_aware_datetime(value: datetime | None, field_name: str) -> None:
    """非空领域时间必须包含可计算 UTC offset 的时区。"""
    if value is not None and value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True)
class Lease:
    """数据库签发的有期限执行权。"""

    fencing_token: FencingToken
    expires_at: datetime

    def __post_init__(self) -> None:
        require_stable_value(self.fencing_token, "fencing_token")
        require_aware_datetime(self.expires_at, "expires_at")

    def validate(self, token: FencingToken, now: datetime) -> None:
        if token != self.fencing_token:
            raise FencingTokenMismatchError("fencing token does not match")
        require_aware_datetime(now, "now")
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
