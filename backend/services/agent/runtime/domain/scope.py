"""Agent Runtime 不可变租户与用户作用域。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from services.agent.runtime.domain.errors import ScopeMismatchError
from services.agent.runtime.domain.identity import require_stable_value


class ScopeKind(StrEnum):
    USER = "user"
    CHANNEL = "channel"
    SYSTEM = "system"


@dataclass(frozen=True)
class RuntimeScope:
    """所有 Session 子事实必须继承的不可变 Scope。"""

    kind: ScopeKind
    scope_id: str
    user_id: str | None
    org_id: str | None

    def __post_init__(self) -> None:
        require_stable_value(self.scope_id, "scope_id")
        if self.user_id is not None:
            require_stable_value(self.user_id, "user_id")
        if self.org_id is not None:
            require_stable_value(self.org_id, "org_id")
        if self.kind is ScopeKind.USER and self.user_id is None:
            raise ValueError("user scope requires user_id")
        if self.kind is ScopeKind.CHANNEL and self.org_id is None:
            raise ValueError("channel scope requires org_id")

    def require_child(self, child: "RuntimeScope") -> None:
        """父子 Scope 必须完全一致，子对象不能改变租户身份。"""
        if child != self:
            raise ScopeMismatchError("child scope must match parent scope")
