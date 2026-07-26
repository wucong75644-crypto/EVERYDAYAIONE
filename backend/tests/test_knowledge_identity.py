"""Knowledge 身份必须由不可变 DatabaseScope 推导。"""

from uuid import uuid4

import pytest

from core.db_scope import DatabaseAccessKind, DatabaseScope
from services.knowledge_config import resolve_knowledge_identity


def _scope(
    *,
    actor_user_id: str | None,
    org_id: str | None,
    access_kind: DatabaseAccessKind,
) -> DatabaseScope:
    return DatabaseScope(
        actor_user_id=actor_user_id,
        org_id=org_id,
        access_kind=access_kind,
    )


def test_runtime_enterprise_identity_uses_scope_org() -> None:
    actor_id, org_id = str(uuid4()), str(uuid4())
    scope = _scope(
        actor_user_id=actor_id,
        org_id=org_id,
        access_kind=DatabaseAccessKind.RUNTIME,
    )

    assert resolve_knowledge_identity(scope, None) == (org_id, None)


def test_runtime_personal_identity_uses_actor_owner() -> None:
    actor_id = str(uuid4())
    scope = _scope(
        actor_user_id=actor_id,
        org_id=None,
        access_kind=DatabaseAccessKind.RUNTIME,
    )

    assert resolve_knowledge_identity(scope, None) == (None, actor_id)


def test_explicit_cross_org_argument_is_rejected() -> None:
    scope = _scope(
        actor_user_id=str(uuid4()),
        org_id=str(uuid4()),
        access_kind=DatabaseAccessKind.RUNTIME,
    )

    with pytest.raises(ValueError, match="KNOWLEDGE_ORG_SCOPE_MISMATCH"):
        resolve_knowledge_identity(scope, str(uuid4()))


def test_worker_global_identity_has_no_personal_owner() -> None:
    scope = _scope(
        actor_user_id=None,
        org_id=None,
        access_kind=DatabaseAccessKind.WORKER,
    )

    assert resolve_knowledge_identity(scope, None) == (None, None)


@pytest.mark.asyncio
async def test_personal_search_cache_is_partitioned_by_actor() -> None:
    from unittest.mock import AsyncMock, patch

    from services.knowledge_service import search_relevant

    actor_one, actor_two = str(uuid4()), str(uuid4())
    first_scope = _scope(
        actor_user_id=actor_one,
        org_id=None,
        access_kind=DatabaseAccessKind.RUNTIME,
    )
    second_scope = _scope(
        actor_user_id=actor_two,
        org_id=None,
        access_kind=DatabaseAccessKind.RUNTIME,
    )
    observed_keys: list[str] = []

    with (
        patch("services.knowledge_service.is_kb_available", return_value=True),
        patch(
            "services.knowledge_service.get_cached_search",
            side_effect=lambda key: observed_keys.append(key) or [],
        ),
        patch(
            "services.knowledge_service.compute_embedding",
            new_callable=AsyncMock,
        ) as embedding,
    ):
        await search_relevant("same", db_source=first_scope)
        await search_relevant("same", db_source=second_scope)

    embedding.assert_not_awaited()
    assert len(observed_keys) == 2
    assert observed_keys[0] != observed_keys[1]
    assert actor_one in observed_keys[0]
    assert actor_two in observed_keys[1]
