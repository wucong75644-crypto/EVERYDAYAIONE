"""AR-05 RuntimeScope 构造与父子一致性测试。"""

from __future__ import annotations

import pytest

from services.agent.runtime.domain.errors import ScopeMismatchError
from services.agent.runtime.domain.scope import RuntimeScope, ScopeKind


def test_personal_scope_allows_null_org() -> None:
    scope = RuntimeScope(ScopeKind.USER, "user:u-1", "u-1", None)
    assert scope.org_id is None


def test_parent_accepts_identical_child_scope() -> None:
    parent = RuntimeScope(ScopeKind.USER, "user:u-1", "u-1", "o-1")
    parent.require_child(
        RuntimeScope(ScopeKind.USER, "user:u-1", "u-1", "o-1")
    )


@pytest.mark.parametrize(
    "child",
    (
        RuntimeScope(ScopeKind.USER, "user:u-2", "u-2", "o-1"),
        RuntimeScope(ScopeKind.USER, "user:u-1", "u-1", "o-2"),
        RuntimeScope(ScopeKind.CHANNEL, "channel:u-1", "u-1", "o-1"),
    ),
)
def test_parent_rejects_any_child_scope_change(child: RuntimeScope) -> None:
    parent = RuntimeScope(ScopeKind.USER, "user:u-1", "u-1", "o-1")
    with pytest.raises(ScopeMismatchError):
        parent.require_child(child)


def test_user_scope_requires_user() -> None:
    with pytest.raises(ValueError, match="requires user_id"):
        RuntimeScope(ScopeKind.USER, "user:missing", None, None)


def test_channel_scope_requires_org() -> None:
    with pytest.raises(ValueError, match="requires org_id"):
        RuntimeScope(ScopeKind.CHANNEL, "channel:c-1", "u-1", None)


@pytest.mark.parametrize("user_id", ("", " "))
def test_non_null_user_id_must_be_stable(user_id: str) -> None:
    with pytest.raises(ValueError, match="user_id is required"):
        RuntimeScope(ScopeKind.SYSTEM, "system:runtime", user_id, None)


@pytest.mark.parametrize("org_id", ("", " "))
def test_non_null_org_id_must_be_stable(org_id: str) -> None:
    with pytest.raises(ValueError, match="org_id is required"):
        RuntimeScope(ScopeKind.SYSTEM, "system:runtime", None, org_id)
