"""Dedicated production entrypoint; all durable ownership is gate-controlled."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time

from pydantic_settings import BaseSettings, SettingsConfigDict

from core.config import get_settings
from core.database import close_async_worker_db, get_async_worker_db
from core.db_scope import DatabaseAccessKind
from core.logging_config import setup_logging
from services.agent.runtime.composition import (
    build_authorization, build_projection, build_runtime, build_sandbox, scoped,
)


class SandboxProcessSettings(BaseSettings):
    """Sandbox worker config that never reads the backend `.env`."""

    model_config = SettingsConfigDict(
        env_file=None, case_sensitive=False, extra="ignore",
    )
    worker_database_url: str
    agent_runtime_process_role: str
    agent_runtime_worker_id: str
    agent_runtime_release_revision: str
    agent_runtime_health_socket: str
    agent_runtime_poll_interval_seconds: float = 1.0
    agent_runtime_heartbeat_seconds: float = 10.0
    sandbox_job_root: str
    sandbox_rootfs: str
    sandbox_rootfs_manifest: str
    sandbox_rootfs_sha256: str
    sandbox_nsjail_path: str
    sandbox_nsjail_sha256: str
    sandbox_python_path: str
    sandbox_seccomp_policy: str
    sandbox_seccomp_sha256: str
    sandbox_cgroup_v2_mount: str
    sandbox_worker_concurrency: int = 1
    sandbox_partial_retention_seconds: int = 86400
    sandbox_runtime_revision: str
    sentry_dsn: str | None = None
    environment: str = "production"

def _initialize_sentry(settings) -> None:
    if not settings.sentry_dsn:
        return
    import sentry_sdk
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=settings.agent_runtime_release_revision,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.0,
    )


async def _run() -> None:
    role = os.environ.get("AGENT_RUNTIME_PROCESS_ROLE", "")
    settings = (
        SandboxProcessSettings()
        if role == "sandbox" else get_settings()
    )
    if role not in {"agent_runtime", "projection", "authorization", "sandbox"}:
        raise RuntimeError("AGENT_RUNTIME_PROCESS_ROLE_INVALID")
    if role == "sandbox" and settings.sandbox_worker_concurrency != 1:
        raise RuntimeError("SANDBOX_WORKER_CONCURRENCY_MUST_BE_ONE")
    raw = await get_async_worker_db(
        settings.worker_database_url,
        min_size=1 if role == "sandbox" else None,
        max_size=2 if role == "sandbox" else None,
    )
    kind = {
        "agent_runtime": DatabaseAccessKind.AGENT_RUNTIME,
        "projection": DatabaseAccessKind.PROJECTION,
        "authorization": DatabaseAccessKind.AUTHORIZATION,
        "sandbox": DatabaseAccessKind.SANDBOX_WORKER,
    }[role]
    control_db = scoped(raw, kind, settings.agent_runtime_worker_id)
    stopping = asyncio.Event()
    draining = False
    ready = False
    last_heartbeat = 0.0
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopping.set)
    owner, cycle = await _build_owner_and_cycle(role, raw, settings)
    async def health(reader, writer):
        await reader.read(1024)
        writer.write((json.dumps({
            "ready": ready and not draining and not stopping.is_set(),
            "draining": draining or stopping.is_set(),
            "role": role,
        }) + "\n").encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    socket_path = settings.agent_runtime_health_socket
    if os.path.exists(socket_path):
        os.unlink(socket_path)
    server = await asyncio.start_unix_server(health, path=socket_path)
    os.chmod(socket_path, 0o660)
    ready = True
    try:
        while not stopping.is_set():
            response = await control_db.rpc(
                "get_agent_runtime_worker_control",
                {"p_process_role": role},
            ).execute()
            enabled = isinstance(response.data, dict) and response.data.get("enabled")
            now = time.monotonic()
            if now - last_heartbeat >= settings.agent_runtime_heartbeat_seconds:
                await _report_heartbeat(
                    control_db, settings, role, bool(enabled),
                )
                last_heartbeat = now
            if enabled:
                await cycle()
            else:
                await asyncio.sleep(settings.agent_runtime_poll_interval_seconds)
    finally:
        await _shutdown(
            role, owner, control_db, settings, server, socket_path,
        )


async def _build_owner_and_cycle(role, raw, settings):
    if role == "projection":
        from services.tool_confirmation import tool_confirmation_service
        from services.tool_confirmation.capability_probe import (
            probe_tool_confirmation_redis,
        )
        redis_probe = await probe_tool_confirmation_redis()
        tool_confirmation_service.set_available(redis_probe.ready)
        if not redis_probe.ready:
            raise RuntimeError(
                f"TOOL_CONFIRMATION_REDIS_PROBE_FAILED:{redis_probe.code}",
            )
        owner = build_projection(raw, settings.agent_runtime_worker_id)
        cycle = owner.run_once
    elif role == "authorization":
        owner = build_authorization(raw, settings.agent_runtime_worker_id)
        cycle = owner.run_once
    elif role == "sandbox":
        owner = build_sandbox(raw, settings)
        if not owner.worker.probe().ready:
            raise RuntimeError("SANDBOX_CAPABILITY_PROBE_FAILED")

        async def cycle():
            execution = await owner.worker.run_once()
            recovery = await owner.worker.reconcile_next()
            owner.worker.cleanup_expired_partials(
                settings.sandbox_partial_retention_seconds,
            )
            return execution.worked or recovery.worked
    else:
        owner = build_runtime(raw, settings)
        cycle = owner.run_once
    return owner, cycle


async def _report_heartbeat(control_db, settings, role, enabled) -> None:
    await control_db.rpc(
        "report_agent_runtime_worker_heartbeat", {
            "p_process_role": role,
            "p_worker_id": settings.agent_runtime_worker_id,
            "p_release_revision": settings.agent_runtime_release_revision,
            "p_ready": True,
            "p_draining": False,
            "p_status_code": "accepting" if enabled else "gate_closed",
            "p_details": {"gate_enabled": enabled},
        },
    ).execute()


async def _shutdown(role, owner, control_db, settings, server, socket_path):
    try:
        await control_db.rpc(
            "report_agent_runtime_worker_heartbeat", {
                "p_process_role": role,
                "p_worker_id": settings.agent_runtime_worker_id,
                "p_release_revision": settings.agent_runtime_release_revision,
                "p_ready": False,
                "p_draining": True,
                "p_status_code": "draining",
                "p_details": {},
            },
        ).execute()
    except Exception:
        pass
    server.close()
    await server.wait_closed()
    if os.path.exists(socket_path):
        os.unlink(socket_path)
    if role == "sandbox":
        owner.service.stop()
    elif role == "agent_runtime":
        owner.stop()
    await close_async_worker_db()


def main() -> None:
    setup_logging()
    role = os.environ.get("AGENT_RUNTIME_PROCESS_ROLE", "")
    settings = (
        SandboxProcessSettings()
        if role == "sandbox" else get_settings()
    )
    _initialize_sentry(settings)
    try:
        asyncio.run(_run())
    except BaseException as error:
        from loguru import logger
        logger.exception(
            "agent_runtime_process_fatal | role={} | error={}",
            settings.agent_runtime_process_role,
            type(error).__name__,
        )
        if settings.sentry_dsn:
            import sentry_sdk
            sentry_sdk.capture_exception(error)
            sentry_sdk.flush(timeout=5)
        raise


if __name__ == "__main__":
    main()
