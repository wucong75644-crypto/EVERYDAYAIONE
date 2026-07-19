"""手动 Curated Memory 服务测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.exceptions import AppException, NotFoundError
from services.memory.manual_memory_service import ManualMemoryService


USER_ID = "11111111-1111-1111-1111-111111111111"
MEMORY_ID = "22222222-2222-2222-2222-222222222222"


def _service() -> tuple[ManualMemoryService, MagicMock]:
    db = MagicMock()
    service = ManualMemoryService(db=db)
    return service, db


@pytest.mark.asyncio
async def test_add_saves_one_original_memory(monkeypatch) -> None:
    service, db = _service()
    response = MagicMock(data={
        "outcome": "created",
        "id": MEMORY_ID,
        "created_at": "2026-07-19T00:00:00Z",
        "updated_at": "2026-07-19T00:00:00Z",
    })
    db.rpc.return_value.execute.return_value = response
    monkeypatch.setattr(
        "services.memory.manual_memory_service.get_embedding",
        AsyncMock(return_value=[0.1, 0.2]),
    )

    result = await service.add_memory(USER_ID, "  我喜欢简洁回答  ")

    assert result[0]["memory"] == "我喜欢简洁回答"
    assert result[0]["metadata"] == {"source": "manual"}
    name, params = db.rpc.call_args.args
    assert name == "create_manual_memory"
    assert params["p_org_id"] is None
    assert params["p_content"] == "我喜欢简洁回答"
    assert params["p_embedding"] == "[0.1,0.2]"


@pytest.mark.asyncio
async def test_add_fails_closed_when_embedding_unavailable(monkeypatch) -> None:
    service, db = _service()
    monkeypatch.setattr(
        "services.memory.manual_memory_service.get_embedding",
        AsyncMock(return_value=None),
    )

    with pytest.raises(AppException) as exc_info:
        await service.add_memory(USER_ID, "长期偏好")

    assert exc_info.value.code == "MEMORY_UNAVAILABLE"
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_add_maps_limit_outcome() -> None:
    service, db = _service()
    service_module = "services.memory.manual_memory_service.get_embedding"
    db.rpc.return_value.execute.return_value = MagicMock(
        data={"outcome": "limit_reached"}
    )
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(service_module, AsyncMock(return_value=[0.1]))
        with pytest.raises(AppException) as exc_info:
            await service.add_memory(USER_ID, "长期偏好")

    assert exc_info.value.code == "MEMORY_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_update_rejects_non_manual_or_cross_scope_target(
    monkeypatch,
) -> None:
    service, db = _service()
    db.rpc.return_value.execute.return_value = MagicMock(
        data={"outcome": "not_found"}
    )
    monkeypatch.setattr(
        "services.memory.manual_memory_service.get_embedding",
        AsyncMock(return_value=[0.1]),
    )

    with pytest.raises(NotFoundError):
        await service.update_memory(MEMORY_ID, "新内容", USER_ID)


@pytest.mark.asyncio
async def test_delete_uses_personal_scope_and_hides_missing_target() -> None:
    service, db = _service()
    db.rpc.return_value.execute.return_value = MagicMock(
        data={"outcome": "not_found"}
    )

    with pytest.raises(NotFoundError):
        await service.delete_memory(MEMORY_ID, USER_ID, org_id=None)

    _, params = db.rpc.call_args.args
    assert params["p_org_id"] is None
    assert params["p_user_id"] == USER_ID


@pytest.mark.asyncio
async def test_list_filters_personal_scope_and_formats_sources() -> None:
    service, db = _service()
    query = db.table.return_value.select.return_value
    query.eq.return_value = query
    query.is_.return_value = query
    query.order.return_value = query
    query.limit.return_value = query
    query.execute.return_value = MagicMock(data=[{
        "id": MEMORY_ID,
        "content": "用户偏好简洁回答",
        "source_kind": "conversation",
        "metadata": {},
        "created_at": None,
        "updated_at": None,
    }])

    result = await service.get_all_memories(USER_ID, org_id=None)

    query.is_.assert_called_once_with("org_id", "null")
    assert result[0]["metadata"]["source"] == "auto"


@pytest.mark.asyncio
async def test_list_maps_database_failure_to_unavailable() -> None:
    service, db = _service()
    db.table.side_effect = RuntimeError("database unavailable")

    with pytest.raises(AppException) as exc_info:
        await service.get_all_memories(USER_ID)

    assert exc_info.value.code == "MEMORY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_clear_maps_successful_rpc() -> None:
    service, db = _service()
    db.rpc.return_value.execute.return_value = MagicMock(
        data={"outcome": "cleared", "deleted_count": 2}
    )

    assert await service.delete_all_memories(USER_ID, org_id=None) is None
