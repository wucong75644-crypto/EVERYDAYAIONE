"""Behavior tests for the global seed knowledge RPC client."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import knowledge_seed_service


def _connection_context(result=None, *, error: Exception | None = None):
    cursor = AsyncMock()
    cursor.execute.side_effect = error
    cursor.fetchone.return_value = (
        {"imported_count": 0, "edge_count": 0}
        if result is None else result,
    )
    cursor_context = MagicMock()
    cursor_context.__aenter__ = AsyncMock(return_value=cursor)
    cursor_context.__aexit__ = AsyncMock(return_value=None)
    connection = MagicMock()
    connection.cursor.return_value = cursor_context
    connection_context = MagicMock()
    connection_context.__aenter__ = AsyncMock(return_value=connection)
    connection_context.__aexit__ = AsyncMock(return_value=None)
    return connection_context, cursor


def _seed(
    *,
    model_id: str | None = "model-a",
    related_models: list[str] | None = None,
) -> dict:
    metadata = {}
    if model_id is not None:
        metadata["model_id"] = model_id
    if related_models is not None:
        metadata["related_models"] = related_models
    return {
        "category": "model",
        "node_type": "model",
        "title": f"Title {model_id}",
        "content": f"Content {model_id}",
        "source": "seed",
        "confidence": 1.0,
        "metadata": metadata,
    }


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


@pytest.mark.parametrize("content", ["{", json.dumps({"nodes": []})])
@pytest.mark.asyncio
async def test_rejects_invalid_json_or_top_level_schema(
    tmp_path, content: str,
) -> None:
    seed_path = tmp_path / "invalid.json"
    seed_path.write_text(content, encoding="utf-8")
    get_connection = AsyncMock()
    with (
        patch.object(
            knowledge_seed_service, "is_kb_available", return_value=True,
        ),
        patch.object(
            knowledge_seed_service, "get_pg_connection", get_connection,
        ),
    ):
        assert await knowledge_seed_service.load_seed_knowledge(
            str(seed_path),
        ) == 0
    get_connection.assert_not_awaited()


@pytest.mark.parametrize(
    "seeds",
    [
        [{"category": "invalid", "title": "A", "content": "B"}],
        [_seed(related_models=["missing"])],
        [_seed(related_models=["model-a", "model-a"])],
    ],
)
@pytest.mark.asyncio
async def test_rejects_invalid_node_missing_endpoint_or_duplicate_edge(
    tmp_path, seeds,
) -> None:
    seed_path = tmp_path / "invalid-schema.json"
    seed_path.write_text(json.dumps(seeds), encoding="utf-8")
    get_connection = AsyncMock()
    with (
        patch.object(
            knowledge_seed_service, "is_kb_available", return_value=True,
        ),
        patch.object(
            knowledge_seed_service, "get_pg_connection", get_connection,
        ),
    ):
        assert await knowledge_seed_service.load_seed_knowledge(
            str(seed_path),
        ) == 0
    get_connection.assert_not_awaited()


@pytest.mark.asyncio
async def test_imports_nodes_and_edges_through_one_rpc_then_invalidates_cache(
    tmp_path,
) -> None:
    seeds = [
        _seed(model_id="source", related_models=["target"]),
        _seed(model_id="target"),
    ]
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps(seeds), encoding="utf-8")
    connection_context, cursor = _connection_context({
        "imported_count": 2,
        "edge_count": 1,
    })
    embedding = [0.25] * 1024
    events = []

    async def compute(text: str):
        events.append(("embedding", text))
        return embedding if len(events) == 1 else None

    async def get_connection(_db_source):
        events.append(("database", _db_source))
        return connection_context

    with (
        patch.object(
            knowledge_seed_service, "is_kb_available", return_value=True,
        ),
        patch.object(
            knowledge_seed_service, "compute_embedding", new=compute,
        ),
        patch.object(
            knowledge_seed_service, "get_pg_connection", new=get_connection,
        ),
        patch.object(
            knowledge_seed_service, "invalidate_search_cache",
        ) as invalidate,
    ):
        result = await knowledge_seed_service.load_seed_knowledge(
            str(seed_path), db_source="worker-scope",
        )

    assert result == 2
    cursor.execute.assert_awaited_once()
    sql, params = cursor.execute.await_args.args
    assert sql == "SELECT worker_replace_global_knowledge_seed(%s::jsonb)"
    payload = json.loads(params[0])
    assert payload["version"] == 1
    assert payload["nodes"][0]["seed_key"] == "node:0"
    assert payload["nodes"][0]["embedding"] == embedding
    assert payload["nodes"][1]["embedding"] is None
    assert payload["edges"] == [{
        "source_key": "node:0",
        "target_key": "node:1",
        "relation_type": "related_to",
    }]
    assert all("source" not in node for node in payload["nodes"])
    assert [event[0] for event in events] == [
        "embedding", "embedding", "database",
    ]
    invalidate.assert_called_once_with()


@pytest.mark.asyncio
async def test_embedding_none_is_sent_as_null_before_rpc(tmp_path) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps([_seed()]), encoding="utf-8")
    connection_context, cursor = _connection_context({
        "imported_count": 1,
        "edge_count": 0,
    })
    with (
        patch.object(
            knowledge_seed_service, "is_kb_available", return_value=True,
        ),
        patch.object(
            knowledge_seed_service, "compute_embedding",
            new=AsyncMock(return_value=None),
        ) as compute,
        patch.object(
            knowledge_seed_service, "get_pg_connection",
            new=AsyncMock(return_value=connection_context),
        ),
        patch.object(
            knowledge_seed_service, "invalidate_search_cache",
        ),
    ):
        assert await knowledge_seed_service.load_seed_knowledge(
            str(seed_path), db_source="worker-scope",
        ) == 1

    compute.assert_awaited_once_with("Title model-a Content model-a")
    payload = json.loads(cursor.execute.await_args.args[1][0])
    assert payload["nodes"][0]["embedding"] is None


@pytest.mark.asyncio
async def test_empty_seed_replaces_snapshot_and_repeated_import_is_idempotent(
    tmp_path,
) -> None:
    seed_path = tmp_path / "empty.json"
    seed_path.write_text("[]", encoding="utf-8")
    first_context, first_cursor = _connection_context()
    second_context, second_cursor = _connection_context()
    get_connection = AsyncMock(
        side_effect=[first_context, second_context],
    )
    with (
        patch.object(
            knowledge_seed_service, "is_kb_available", return_value=True,
        ),
        patch.object(
            knowledge_seed_service, "get_pg_connection", get_connection,
        ),
        patch.object(
            knowledge_seed_service, "invalidate_search_cache",
        ) as invalidate,
    ):
        assert await knowledge_seed_service.load_seed_knowledge(
            str(seed_path), db_source="worker-scope",
        ) == 0
        assert await knowledge_seed_service.load_seed_knowledge(
            str(seed_path), db_source="worker-scope",
        ) == 0

    assert first_cursor.execute.await_count == 1
    assert second_cursor.execute.await_count == 1
    assert invalidate.call_count == 2


@pytest.mark.asyncio
async def test_rpc_failure_does_not_invalidate_cache(tmp_path) -> None:
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps([_seed()]), encoding="utf-8")
    connection_context, _ = _connection_context(
        error=RuntimeError("transaction aborted"),
    )
    with (
        patch.object(
            knowledge_seed_service, "is_kb_available", return_value=True,
        ),
        patch.object(
            knowledge_seed_service, "get_pg_connection",
            new=AsyncMock(return_value=connection_context),
        ),
        patch.object(
            knowledge_seed_service, "invalidate_search_cache",
        ) as invalidate,
    ):
        assert await knowledge_seed_service.load_seed_knowledge(
            str(seed_path), db_source="worker-scope",
        ) == 0
    invalidate.assert_not_called()
