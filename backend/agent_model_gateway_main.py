"""Flags-off entrypoint for the isolated Agent Model Gateway BG3 harness."""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from core.db_scope import (
    AsyncScopedDatabaseClient,
    DatabaseAccessKind,
    DatabaseScope,
)
from services.agent.runtime.infrastructure.postgres.model_gateway import (
    PostgresModelGatewayRepository,
)
from services.agent.runtime.model_gateway.configuration import (
    GatewaySecretBundleConsumer,
    build_gateway_secret_consumer,
)
from services.agent.runtime.model_gateway.provider import (
    GatewayProviderExecutor,
    provider_registry_available,
)
from services.agent.runtime.model_gateway.server import (
    FakeModelGatewayServer,
    LinuxPeerCredentialVerifier,
)
from services.agent.runtime.model_gateway.service import ModelGatewayService


PRODUCTION_READY = False
_RELEASE_RE = re.compile(r"^[0-9a-f]{40,64}$")


@dataclass(frozen=True)
class GatewayProcessSettings:
    database_url: str
    worker_id: str
    release: str
    socket_path: str
    health_socket_path: str
    runtime_uid: int
    drain_timeout_seconds: float = 130.0
    isolated_harness_enabled: bool = False
    production_enabled: bool = False

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None,
    ) -> "GatewayProcessSettings":
        values = os.environ if environ is None else environ
        try:
            settings = cls(
                database_url=_required(values, "AGENT_MODEL_GATEWAY_DATABASE_URL"),
                worker_id=_required(values, "AGENT_MODEL_GATEWAY_WORKER_ID"),
                release=_required(values, "AGENT_MODEL_GATEWAY_RELEASE_REVISION"),
                socket_path=_required(values, "AGENT_MODEL_GATEWAY_SOCKET"),
                health_socket_path=_required(
                    values, "AGENT_MODEL_GATEWAY_HEALTH_SOCKET",
                ),
                runtime_uid=int(_required(values, "AGENT_MODEL_GATEWAY_RUNTIME_UID")),
                drain_timeout_seconds=float(values.get(
                    "AGENT_MODEL_GATEWAY_DRAIN_TIMEOUT_SECONDS", "130",
                )),
                isolated_harness_enabled=_boolean(
                    values.get("AGENT_MODEL_GATEWAY_ISOLATED_HARNESS_ENABLED", "false")
                ),
                production_enabled=_boolean(
                    values.get("AGENT_MODEL_GATEWAY_PRODUCTION_ENABLED", "false")
                ),
            )
        except (TypeError, ValueError):
            raise RuntimeError("GATEWAY_PROCESS_CONFIGURATION_INVALID") from None
        settings.validate()
        return settings

    def validate(self) -> None:
        paths = (Path(self.socket_path), Path(self.health_socket_path))
        if (
            self.production_enabled
            or not self.isolated_harness_enabled
            or not _RELEASE_RE.fullmatch(self.release)
            or self.runtime_uid < 0
            or not 1 <= self.drain_timeout_seconds <= 130
            or any(not path.is_absolute() for path in paths)
            or paths[0] == paths[1]
        ):
            raise RuntimeError("GATEWAY_PROCESS_CONFIGURATION_INVALID")


async def _run() -> None:
    settings = GatewayProcessSettings.from_environment()
    if not provider_registry_available():
        raise RuntimeError("GATEWAY_PROVIDER_REGISTRY_UNAVAILABLE")
    secret_consumer = build_gateway_secret_consumer()
    from core.database import close_async_worker_db, get_async_worker_db

    raw_database = await get_async_worker_db(settings.database_url)
    try:
        await _serve(settings, raw_database, secret_consumer)
    finally:
        await close_async_worker_db()


async def _serve(
    settings: GatewayProcessSettings,
    raw_database: object,
    secret_consumer: GatewaySecretBundleConsumer,
) -> None:
    database = AsyncScopedDatabaseClient(raw_database, DatabaseScope(
        actor_user_id=None,
        org_id=None,
        access_kind=DatabaseAccessKind.AGENT_MODEL_GATEWAY,
        request_id=settings.worker_id[:128],
    ))
    repository = PostgresModelGatewayRepository(database)
    recovery = await repository.recover(gateway_worker_id=settings.worker_id)
    if recovery.get("outcome") != "recovered":
        raise RuntimeError("GATEWAY_DATABASE_UNAVAILABLE")
    service = ModelGatewayService(
        repository,
        secret_consumer,
        GatewayProviderExecutor(),
        worker_id=settings.worker_id,
        release=settings.release,
    )
    dependencies = {
        "db": "available",
        "kek": "available",
        "provider_registry": "available",
        "socket": "unavailable",
    }
    gateway = FakeModelGatewayServer(
        settings.socket_path,
        service.complete,
        LinuxPeerCredentialVerifier(settings.runtime_uid),
    )
    health_server = None
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_drain() -> None:
        service.drain()
        stopping.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, request_drain)
    try:
        await gateway.start()
        dependencies["socket"] = "available"
        health_server = await _start_health_server(
            settings.health_socket_path, service, dependencies,
        )
        await stopping.wait()
        await gateway.close()
        async with asyncio.timeout(settings.drain_timeout_seconds):
            while service.in_flight:
                await asyncio.sleep(0.01)
    finally:
        service.drain()
        await gateway.close()
        if health_server is not None:
            health_server.close()
            await health_server.wait_closed()
        _unlink_socket(settings.health_socket_path)


async def _start_health_server(
    socket_path: str,
    service: ModelGatewayService,
    dependencies: Mapping[str, str],
) -> asyncio.AbstractServer:
    path = Path(socket_path)
    if not path.parent.is_dir() or path.is_symlink():
        raise RuntimeError("GATEWAY_HEALTH_SOCKET_INVALID")
    _unlink_socket(socket_path)

    async def health(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await reader.read(1024)
            writer.write((json.dumps(
                service.health(dependencies), separators=(",", ":"),
            ) + "\n").encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    server = await asyncio.start_unix_server(health, path=socket_path)
    os.chmod(socket_path, 0o660)
    return server


def _unlink_socket(socket_path: str) -> None:
    path = Path(socket_path)
    try:
        if path.is_socket():
            path.unlink()
        elif path.exists() or path.is_symlink():
            raise RuntimeError("GATEWAY_SOCKET_PATH_UNSAFE")
    except OSError:
        raise RuntimeError("GATEWAY_SOCKET_PATH_UNSAFE") from None


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise RuntimeError("GATEWAY_PROCESS_CONFIGURATION_INVALID")
    return value


def _boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError("invalid boolean")
    return normalized == "true"


if __name__ == "__main__":
    asyncio.run(_run())
