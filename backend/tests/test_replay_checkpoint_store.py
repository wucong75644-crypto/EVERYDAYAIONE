from __future__ import annotations

from services.replay_checkpoint_store import (
    DatabaseReplayCheckpointStore,
    ReplayCheckpointBoundary,
)


class _Response:
    def __init__(self, data):
        self.data = data


class _Rpc:
    def __init__(self, db, name, params):
        self.db = db
        self.name = name
        self.params = params

    async def execute(self):
        self.db.calls.append((self.name, self.params))
        return _Response(self.db.result)


class _Db:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def rpc(self, name, params):
        return _Rpc(self, name, params)


async def test_write_uses_boundary_and_json_payload():
    db = _Db({"outcome": "saved"})
    store = DatabaseReplayCheckpointStore(db)

    result = await store.write(
        task_id="task-1",
        execution_token="token-1",
        boundary=ReplayCheckpointBoundary.AFTER_TOOL,
        payload={"messages": [{"role": "assistant"}]},
        context_revision=3,
        checkpoint_id="checkpoint-1",
    )

    assert result["outcome"] == "saved"
    name, params = db.calls[0]
    assert name == "save_generation_checkpoint"
    assert params["p_safe_point"] == "after_tool"
    assert params["p_state"].obj["checkpoint_id"] == "checkpoint-1"
    assert params["p_state"].obj["context_revision"] == 3


async def test_read_latest_can_filter_boundary():
    db = _Db({
        "outcome": "loaded",
        "safe_point": "before_model",
        "version": 2,
        "state": {"messages": []},
    })
    store = DatabaseReplayCheckpointStore(db)

    result = await store.read_latest(
        task_id="task-1",
        execution_token="token-1",
        boundary=ReplayCheckpointBoundary.BEFORE_MODEL,
    )

    assert result["outcome"] == "found"
    name, params = db.calls[0]
    assert name == "load_generation_checkpoint"
    assert params["p_execution_token"] == "token-1"
