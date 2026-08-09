from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import time

import psycopg
import pytest

from core.db_scope import AsyncScopedDatabaseClient, DatabaseAccessKind, DatabaseScope
from core.local_db import AsyncLocalDBClient
from services.agent.runtime.infrastructure.postgres.model_gateway import (
    PostgresModelGatewayRepository,
)
from services.agent.runtime.model_gateway.client import IsolatedModelGatewayClient
from services.agent.runtime.model_gateway.configuration import GatewaySecretBundleConsumer
from services.agent.runtime.model_gateway.provider import GatewayProviderExecutor
from services.agent.runtime.model_gateway.protocol import GatewayProtocolError
from services.agent.runtime.model_gateway.server import FakeModelGatewayServer
from services.agent.runtime.model_gateway.service import ModelGatewayService
from services.configuration.envelope import LocalKEKProvider
from services.configuration.material_service import SecretMaterialService
from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_model_gateway_e2e_postgres_external import (
    SECRET,
    _Peer,
    _apply,
    _prepare_attempt,
)
from tests.test_agent_runtime_ar18_b4_model_cancel_postgres_external import (
    _install_cancel_facade,
)


pytestmark = pytest.mark.external
MIGRATION = "227_25_agent_runtime_model_gateway_cancel_fence.sql"


class _BlockedAdapter:
    def __init__(self, api_key: str, started: asyncio.Event, closed: asyncio.Event) -> None:
        self.api_key = api_key
        self.started = started
        self.closed = closed
        self.calls = 0

    async def stream_chat(self, **_kwargs: object):
        assert self.api_key == SECRET
        self.calls += 1
        self.started.set()
        await asyncio.Event().wait()
        if False:
            yield None

    async def close(self) -> None:
        self.api_key = ""
        self.closed.set()


def _cancel(url: str, run_id: object) -> dict[str, object]:
    role_url = url.replace("postgres@", "everydayai_runtime@")
    with psycopg.connect(role_url) as connection:
        connection.execute("SELECT set_config('app.access_kind','runtime',false)")
        connection.execute(
            "SELECT set_config('app.actor_user_id',%s,false)",
            ("44444444-4444-4444-4444-444444444444",),
        )
        connection.execute(
            "SELECT set_config('app.org_id',%s,false)",
            ("22222222-2222-2222-2222-222222222222",),
        )
        result = connection.execute(
            "SELECT test_b4_cancel_agent_run(%s,0,'task_cancel_requested')", (run_id,),
        ).fetchone()[0]
        connection.commit()
        return result


@pytest.mark.asyncio
async def test_b4_real_uds_cancel_closes_provider_within_poll_interval(
    database: str,
) -> None:
    ids, request = _prepare_attempt(database)
    _apply(database, MIGRATION)
    _install_cancel_facade(database)
    role_url = database.replace("postgres@", "everydayai_agent_model_gateway@")
    raw = AsyncLocalDBClient(role_url, min_size=1, max_size=2)
    await raw.open()
    scoped = AsyncScopedDatabaseClient(raw, DatabaseScope(
        actor_user_id=None, org_id=None,
        access_kind=DatabaseAccessKind.AGENT_MODEL_GATEWAY,
        request_id="gateway-cancel-e2e",
    ))
    repository = PostgresModelGatewayRepository(scoped)
    started, closed = asyncio.Event(), asyncio.Event()
    adapters: list[_BlockedAdapter] = []

    def build(_model_id: str, *, api_key: str, **_kwargs: object) -> _BlockedAdapter:
        adapter = _BlockedAdapter(api_key, started, closed)
        adapters.append(adapter)
        return adapter

    service = ModelGatewayService(
        repository,
        GatewaySecretBundleConsumer(SecretMaterialService(LocalKEKProvider(
            current_version="v1", keyring={"v1": bytes([7]) * 32},
        ))),
        GatewayProviderExecutor(build), worker_id="gateway-cancel-e2e",
        release="b" * 40, renew_interval_seconds=0.02,
    )
    frames: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="b4-cancel-", dir="/private/tmp") as directory:
        socket_path = Path(directory) / "gateway.sock"
        server = FakeModelGatewayServer(str(socket_path), service.complete, _Peer())
        await server.start()

        async def consume() -> None:
            client = IsolatedModelGatewayClient(str(socket_path))
            try:
                async for frame in client.complete(request):
                    frames.append(frame)
            except GatewayProtocolError:
                pass

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            cancelled_at = time.monotonic()
            cancelled = await asyncio.to_thread(_cancel, database, ids["run"])
            assert cancelled["outcome"] == "cancelled"
            await asyncio.wait_for(closed.wait(), timeout=0.5)
            assert time.monotonic() - cancelled_at < 0.5
            await asyncio.wait_for(consumer, timeout=0.5)

            replay = [frame async for frame in
                      IsolatedModelGatewayClient(str(socket_path)).complete(request)]
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await server.close()
            await raw.close()

    assert replay[-1]["type"] == "failed"
    assert replay[-1]["error_code"] == "GATEWAY_OPERATION_FENCED"
    assert len(adapters) == 1 and adapters[0].calls == 1
    assert adapters[0].closed.is_set() and adapters[0].api_key == ""
    assert SECRET not in json.dumps(frames + replay)
    with psycopg.connect(database) as connection:
        operation = connection.execute(
            "SELECT status,ambiguity_code,lease_token FROM "
            "agent_runtime_model_gateway_operations WHERE request_id=%s",
            (ids["request"],),
        ).fetchone()
    assert operation == (
        "unknown", "GATEWAY_PARENT_RUN_CANCELLED_AFTER_DISPATCH", None,
    )
