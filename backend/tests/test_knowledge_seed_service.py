"""Behavior tests for seed knowledge import."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import knowledge_seed_service


@pytest.mark.asyncio
async def test_returns_zero_when_knowledge_base_is_unavailable() -> None:
    with patch.object(
        knowledge_seed_service, "is_kb_available", return_value=False,
    ):
        assert await knowledge_seed_service.load_seed_knowledge() == 0


@pytest.mark.asyncio
async def test_returns_zero_when_seed_file_is_missing(tmp_path) -> None:
    with patch.object(
        knowledge_seed_service, "is_kb_available", return_value=True,
    ):
        result = await knowledge_seed_service.load_seed_knowledge(
            str(tmp_path / "missing.json"),
        )
    assert result == 0


@pytest.mark.asyncio
async def test_returns_zero_when_seed_json_is_invalid(tmp_path) -> None:
    seed_path = tmp_path / "invalid.json"
    seed_path.write_text("{", encoding="utf-8")

    with patch.object(
        knowledge_seed_service, "is_kb_available", return_value=True,
    ):
        result = await knowledge_seed_service.load_seed_knowledge(
            str(seed_path),
        )
    assert result == 0


@pytest.mark.asyncio
async def test_imports_seeds_builds_edges_and_invalidates_cache(
    tmp_path,
) -> None:
    seeds = [
        {
            "category": "model",
            "node_type": "model",
            "title": "A",
            "content": "alpha",
            "metadata": {"model_id": "a"},
        },
        {
            "category": "tool",
            "title": "B",
            "content": "beta",
        },
    ]
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(seeds), encoding="utf-8")
    add_knowledge = AsyncMock(side_effect=["node-a", None])

    with (
        patch.object(
            knowledge_seed_service, "is_kb_available", return_value=True,
        ),
        patch.object(
            knowledge_seed_service, "get_pg_connection",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "services.knowledge_service.add_knowledge", add_knowledge,
        ),
        patch.object(
            knowledge_seed_service, "_build_seed_edges",
            new=AsyncMock(),
        ) as build_edges,
        patch.object(
            knowledge_seed_service, "invalidate_search_cache",
        ) as invalidate,
    ):
        result = await knowledge_seed_service.load_seed_knowledge(
            str(seed_path), db_source="db",
        )

    assert result == 1
    assert add_knowledge.await_count == 2
    build_edges.assert_awaited_once_with(seeds, db_source="db")
    invalidate.assert_called_once_with()


@pytest.mark.asyncio
async def test_cleans_old_seeds_before_import(tmp_path) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text("[]", encoding="utf-8")
    cursor = AsyncMock()
    cursor.rowcount = 2
    cursor_context = MagicMock()
    cursor_context.__aenter__ = AsyncMock(return_value=cursor)
    cursor_context.__aexit__ = AsyncMock(return_value=None)
    connection = MagicMock()
    connection.cursor.return_value = cursor_context
    connection_context = MagicMock()
    connection_context.__aenter__ = AsyncMock(return_value=connection)
    connection_context.__aexit__ = AsyncMock(return_value=None)

    with (
        patch.object(
            knowledge_seed_service, "is_kb_available", return_value=True,
        ),
        patch.object(
            knowledge_seed_service, "get_pg_connection",
            new=AsyncMock(return_value=connection_context),
        ),
        patch.object(
            knowledge_seed_service, "_build_seed_edges",
            new=AsyncMock(),
        ),
        patch.object(
            knowledge_seed_service, "invalidate_search_cache",
        ),
    ):
        result = await knowledge_seed_service.load_seed_knowledge(
            str(seed_path),
        )

    assert result == 0
    assert cursor.execute.await_count == 2


@pytest.mark.asyncio
async def test_build_seed_edges_links_existing_related_models() -> None:
    seeds = [{
        "metadata": {
            "model_id": "source",
            "related_models": ["target", "missing"],
        },
    }]
    get_node = AsyncMock(
        side_effect=[{"id": "source-id"}, {"id": "target-id"}, None],
    )

    with (
        patch(
            "services.knowledge_service.get_node_by_metadata", get_node,
        ),
        patch.object(
            knowledge_seed_service.graph_service, "add_edge",
            new=AsyncMock(),
        ) as add_edge,
    ):
        await knowledge_seed_service._build_seed_edges(
            seeds, db_source="db",
        )

    add_edge.assert_awaited_once_with(
        db_source="db",
        source_id="source-id",
        target_id="target-id",
        relation_type="related_to",
    )
