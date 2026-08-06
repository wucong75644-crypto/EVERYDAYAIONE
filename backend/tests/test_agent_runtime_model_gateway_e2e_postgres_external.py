"""BG5 local UDS + disposable PostgreSQL + mock Provider acceptance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

import psycopg
import pytest

from core.db_scope import AsyncScopedDatabaseClient, DatabaseAccessKind, DatabaseScope
from core.local_db import AsyncLocalDBClient
from services.adapters.types import StreamChunk
from services.agent.runtime.infrastructure.model.projection import resolve_model_revision
from services.agent.runtime.infrastructure.postgres.model_gateway import PostgresModelGatewayRepository
from services.agent.runtime.model_gateway.client import IsolatedModelGatewayClient
from services.agent.runtime.model_gateway.configuration import GatewaySecretBundleConsumer
from services.agent.runtime.model_gateway.provider import GatewayProviderExecutor
from services.agent.runtime.model_gateway.server import FakeModelGatewayServer
from services.agent.runtime.model_gateway.service import ModelGatewayService
from services.configuration.envelope import LocalKEKProvider
from services.configuration.material_service import SecretMaterialService
from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_model_gateway_postgres_external import (
    HASH, ORG, ORG_USER, REVISION, _call, _prepare_schema, _seed,
)

pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
SECRET = "disposable-provider-secret"


class _Peer:
    def verify(self, _writer: object) -> bool:
        return True


class _Adapter:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.closed = False

    async def stream_chat(self, **_kwargs: object):
        assert self.api_key == SECRET
        chunk = StreamChunk(content="gateway-e2e", prompt_tokens=4, completion_tokens=2)
        chunk.provider_request_id = "mock-provider-request"
        yield chunk

    async def close(self) -> None:
        self.api_key = ""
        self.closed = True


def _apply(url: str, name: str) -> None:
    with psycopg.connect(url) as connection, connection.transaction():
        connection.execute((ROOT / "migrations" / name).read_text(encoding="utf-8"))


def _prepare_attempt(url: str) -> tuple[dict[str, object], dict[str, object]]:
    _prepare_schema(url)
    _apply(url, "227_19_agent_runtime_model_gateway_predispatch_failure.sql")
    _apply(url, "227_20_agent_runtime_model_gateway_dispatch_binding.sql")
    material = SecretMaterialService(LocalKEKProvider(
        current_version="v1", keyring={"v1": bytes([7]) * 32},
    ))
    envelope = material.encrypt_payload(
        scope_kind="platform", scope_id=None,
        secret_name="ai.dashscope_api_key", payload_version=1,
        payload={"api_key": SECRET},
    )
    ids = _seed(url, org_id=ORG, user_id=ORG_USER)
    messages = [{"role": "user", "content": "disposable prompt"}]
    tools: list[dict[str, object]] = []
    prefix_hash = hashlib.sha256(json.dumps(
        {"messages": messages, "tools": tools}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    model_id = "qwen3.5-plus"
    model_revision = resolve_model_revision(model_id)
    receipt = {
        "receipt_hash": "b" * 64, "context_plan_hash": "c" * 64,
        "prefix_hash": prefix_hash, "message_count": 1, "tool_count": 0,
        "credential_provider": "dashscope", "credential_revision": REVISION,
        "credential_purpose": "model.invoke", "schema_version": 1,
    }
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "UPDATE secret_records SET payload_ciphertext=%s,wrapped_dek=%s,"
            "kek_version=%s,payload_version=%s WHERE secret_name='ai.dashscope_api_key'",
            (envelope.payload_ciphertext, envelope.wrapped_dek,
             envelope.kek_version, envelope.payload_version),
        )
        connection.execute(
            "UPDATE agent_model_steps SET model_id=%s,model_revision=%s WHERE id=%s",
            (model_id, model_revision, ids["step"]),
        )
        connection.execute(
            "UPDATE agent_model_attempts SET request_receipt=%s::jsonb WHERE id=%s",
            (json.dumps(receipt), ids["attempt"]),
        )
        connection.commit()
    started = _call(url, "everydayai_agent_runtime_worker",
                    "start_agent_runtime_model_gateway_dispatch", (
        ids["request"], ids["session"], ids["run"], ids["step"], ids["attempt"],
        ids["token"], HASH, 0, model_id, "dashscope", REVISION,
        model_revision, "model.invoke",
    ))
    assert started["outcome"] == "dispatching"
    request = {
        "version": "agent-model-gateway.v1", "type": "request",
        "operation": "model.complete", "request_id": str(ids["request"]),
        "org_id": str(ORG), "user_id": str(ORG_USER), "run_id": str(ids["run"]),
        "model_step_id": str(ids["step"]), "model_attempt_id": str(ids["attempt"]),
        "worker_id": "runtime-worker", "execution_token": str(ids["token"]),
        "request_hash": HASH, "state_version": started["state_version"],
        "model_id": model_id, "provider": "dashscope",
        "model_revision": model_revision, "purpose": "model.invoke",
        "tenant_kill_epoch": 0, "provider_kill_epoch": 0,
        "capability_kill_epoch": 0, "deadline_ms": 30_000,
        "input": {"messages": messages, "tools": tools,
                  "options": {"timeout_seconds": 5},
                  "context_receipt_hash": prefix_hash},
    }
    return ids, request


@pytest.mark.asyncio
async def test_full_gateway_lane_uses_local_uds_postgres_and_mock_provider(
    database: str,
) -> None:
    ids, request = _prepare_attempt(database)
    role_url = database.replace("postgres@", "everydayai_agent_model_gateway@")
    raw = AsyncLocalDBClient(role_url, min_size=1, max_size=2)
    await raw.open()
    adapters: list[_Adapter] = []

    def build(_model_id: str, *, api_key: str, **_kwargs: object) -> _Adapter:
        adapter = _Adapter(api_key)
        adapters.append(adapter)
        return adapter

    scoped = AsyncScopedDatabaseClient(raw, DatabaseScope(
        actor_user_id=None, org_id=None,
        access_kind=DatabaseAccessKind.AGENT_MODEL_GATEWAY,
        request_id="gateway-e2e",
    ))
    repository = PostgresModelGatewayRepository(scoped)
    service = ModelGatewayService(
        repository,
        GatewaySecretBundleConsumer(SecretMaterialService(LocalKEKProvider(
            current_version="v1", keyring={"v1": bytes([7]) * 32},
        ))),
        GatewayProviderExecutor(build), worker_id="gateway-e2e", release="a" * 40,
    )
    with tempfile.TemporaryDirectory(prefix="bg5-", dir="/private/tmp") as directory:
        socket_path = Path(directory) / "gateway.sock"
        server = FakeModelGatewayServer(str(socket_path), service.complete, _Peer())
        try:
            await server.start()
            frames = [frame async for frame in
                      IsolatedModelGatewayClient(str(socket_path)).complete(request)]
        finally:
            await server.close()
            await raw.close()
    assert frames[-1]["type"] == "completed"
    assert frames[-1]["text"] == "gateway-e2e"
    assert SECRET not in json.dumps(frames)
    assert adapters and adapters[0].closed and adapters[0].api_key == ""
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT status FROM agent_runtime_model_gateway_operations WHERE request_id=%s",
            (ids["request"],),
        ).fetchone()[0] == "completed"
