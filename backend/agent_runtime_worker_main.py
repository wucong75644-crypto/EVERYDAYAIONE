"""Dedicated production entrypoint; all durable ownership is gate-controlled."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from contextlib import suppress

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.db_scope import DatabaseAccessKind
from core.logging_config import setup_logging
from services.agent.runtime.composition import (
    build_authorization, build_projection, build_runtime, build_sandbox, scoped,
)
from services.agent.runtime.application.media_projection_readiness import (
    report_media_projection_readiness as _report_media_projection_readiness,
    set_media_owner_readiness as _set_media_owner_readiness,
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


class AgentRuntimeProcessSettings(BaseSettings):
    """Minimal Runtime-owner config that never reads the backend `.env`."""

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
    agent_runtime_drain_timeout_seconds: float = 3600.0
    agent_runtime_production_composition_enabled: bool = False
    agent_runtime_media_enabled: bool = False
    agent_runtime_media_provider_probe_passed: bool = False
    agent_runtime_media_production_ready: bool = False
    agent_runtime_agent_definition_id: str = "everydayai-default"
    agent_runtime_agent_definition_revision: str = "v1"
    sandbox_job_root: str
    sandbox_runtime_revision: str


class AuthorizationProcessSettings(BaseSettings):
    """Minimal Authorization worker config isolated from application secrets."""

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
    sentry_dsn: str | None = None
    environment: str = "production"


class ProjectionProcessSettings(AuthorizationProcessSettings):
    """Projection config plus its explicit Tool Confirmation Redis scope."""

    agent_runtime_scheduled_web_projection_enabled: bool = False
    agent_runtime_media_enabled: bool = False
    agent_runtime_media_provider_probe_passed: bool = False
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_password: SecretStr | None = None
    redis_db: int = 0
    redis_ssl: bool = False
    media_workspace_root: str = "/mnt/nas-workspace"
    media_cdn_domain: str | None = None
    media_result_allowed_hosts: str = ""


def _load_process_settings(role: str):
    if role == "agent_runtime":
        return AgentRuntimeProcessSettings()
    if role == "sandbox":
        return SandboxProcessSettings()
    if role == "projection":
        return ProjectionProcessSettings()
    if role == "authorization":
        return AuthorizationProcessSettings()
    raise RuntimeError("AGENT_RUNTIME_PROCESS_ROLE_INVALID")


def _configure_projection_redis(settings: ProjectionProcessSettings) -> None:
    """Bind every Projection Redis path before importing its composition."""
    from core.redis import RedisClient

    password = (
        settings.redis_password.get_secret_value()
        if settings.redis_password is not None else None
    )
    RedisClient.configure_explicit(
        host=settings.redis_host,
        port=settings.redis_port,
        password=password,
        db=settings.redis_db,
        ssl=settings.redis_ssl,
    )


def _initialize_sentry(settings) -> None:
    sentry_dsn = getattr(settings, "sentry_dsn", None)
    if not sentry_dsn:
        return
    import sentry_sdk
    sentry_sdk.init(
        dsn=sentry_dsn,
        environment=getattr(settings, "environment", "production"),
        release=settings.agent_runtime_release_revision,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.0,
    )


async def _run() -> None:
    role = os.environ.get("AGENT_RUNTIME_PROCESS_ROLE", "")
    settings = _load_process_settings(role)
    if role not in {"agent_runtime", "projection", "authorization", "sandbox"}:
        raise RuntimeError("AGENT_RUNTIME_PROCESS_ROLE_INVALID")
    if role == "sandbox" and settings.sandbox_worker_concurrency != 1:
        raise RuntimeError("SANDBOX_WORKER_CONCURRENCY_MUST_BE_ONE")
    from core.database import close_async_worker_db, get_async_worker_db
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
    state = {
        "liveness": True,
        "ready": False,
        "draining": False,
        "status": "unavailable",
        "reason": "STARTING",
    }
    last_heartbeat = 0.0
    loop = asyncio.get_running_loop()
    owner = None
    cycle = None
    owner_drained = False

    def drain_owner_once() -> None:
        nonlocal owner_drained
        if owner_drained:
            return
        owner_drained = True
        _drain_owner(owner)

    def request_drain() -> None:
        state.update(draining=True, ready=False, status="draining", reason="SIGTERM")
        stopping.set()
        drain_owner_once()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, request_drain)

    async def health(reader, writer):
        try:
            await reader.read(1024)
            payload = _health_payload(state, role, stopping.is_set())
            writer.write((json.dumps(payload) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    socket_path = settings.agent_runtime_health_socket
    if os.path.exists(socket_path):
        os.unlink(socket_path)
    server = await asyncio.start_unix_server(health, path=socket_path)
    os.chmod(socket_path, 0o660)
    try:
        try:
            owner, cycle = await _build_owner_and_cycle(role, raw, settings)
            state.update(status="disabled", reason="GATE_CLOSED")
        except Exception as error:
            state.update(status="unavailable", reason=_redacted_error(error))
            owner = None
            cycle = None
        while not stopping.is_set():
            enabled, control_reason = await _read_control(control_db, role)
            _apply_gate_state(
                state, _composition_ready(owner), enabled, control_reason,
            )
            now = time.monotonic()
            if now - last_heartbeat >= settings.agent_runtime_heartbeat_seconds:
                heartbeat_ok = await _report_heartbeat(
                    control_db, settings, role,
                    ready=bool(_composition_ready(owner) and enabled),
                    draining=bool(state["draining"]),
                    status_code=(
                        "accepting" if _composition_ready(owner) and enabled
                        else control_reason.lower()
                    ),
                )
                last_heartbeat = now
                _update_readiness(
                    state, heartbeat_ok, _composition_ready(owner), enabled,
                    control_reason,
                )
            if _can_run_cycle(owner, cycle, enabled, bool(state["ready"])):
                media_rpc_ok, media_ready = await _report_media_projection_readiness(
                    control_db, settings, role, ready=True, draining=False,
                )
                if media_rpc_ok and _set_media_owner_readiness(
                    owner, role, media_ready,
                ):
                    await cycle()
                else:
                    state.update(
                        ready=False, status="unavailable",
                        reason="MEDIA_READINESS_HEARTBEAT_FAILED",
                    )
            else:
                await asyncio.sleep(settings.agent_runtime_poll_interval_seconds)
    finally:
        state.update(ready=False, draining=True, status="draining", reason="SHUTDOWN")
        await _report_heartbeat(
            control_db, settings, role, ready=False, draining=True,
            status_code="draining",
        )
        await _shutdown(
            owner, server, socket_path, close_async_worker_db,
            owner_already_drained=owner_drained,
        )


async def _read_control(control_db, role: str) -> tuple[bool, str]:
    try:
        response = await control_db.rpc(
            "get_agent_runtime_worker_control",
            {"p_process_role": role},
        ).execute()
        enabled = bool(
            isinstance(response.data, dict) and response.data.get("enabled")
        )
        return enabled, "GATE_OPEN" if enabled else "GATE_CLOSED"
    except Exception as error:
        return False, _redacted_error(error)


def _update_readiness(
    state: dict[str, object], heartbeat_ok: bool, composition_ready: bool,
    gate_enabled: bool, control_reason: str,
) -> None:
    if not heartbeat_ok:
        state.update(ready=False, status="unavailable", reason="HEARTBEAT_FAILED")
    elif state["draining"]:
        state.update(ready=False, status="draining", reason="SIGTERM")
    elif not composition_ready:
        state.update(ready=False, status="unavailable")
    elif gate_enabled:
        state.update(ready=True, status="ready", reason="ACCEPTING")
    else:
        state.update(
            ready=False,
            status="disabled" if control_reason == "GATE_CLOSED" else "unavailable",
            reason=control_reason,
        )


def _apply_gate_state(
    state: dict[str, object], composition_ready: bool, gate_enabled: bool,
    control_reason: str,
) -> None:
    if state["draining"] or not composition_ready or gate_enabled:
        return
    state.update(
        ready=False,
        status="disabled" if control_reason == "GATE_CLOSED" else "unavailable",
        reason=control_reason,
    )


async def _build_owner_and_cycle(role, raw, settings):
    if role == "projection":
        _configure_projection_redis(settings)
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
        owner = build_projection(
            raw, settings.agent_runtime_worker_id, process_role=role,
            scheduled_web_projection_enabled=(
                settings.agent_runtime_scheduled_web_projection_enabled
            ),
            media_projection_enabled=settings.agent_runtime_media_enabled,
            media_workspace_root=settings.media_workspace_root,
            media_cdn_domain=settings.media_cdn_domain,
            media_result_allowed_hosts=tuple(
                host.strip() for host in settings.media_result_allowed_hosts.split(",")
                if host.strip()
            ),
        )
        cycle = owner.run_once
    elif role == "authorization":
        owner = build_authorization(
            raw, settings.agent_runtime_worker_id, process_role=role,
        )
        cycle = owner.run_once
    elif role == "sandbox":
        owner = build_sandbox(raw, settings, process_role=role)
        probe = owner.worker.probe()
        if not probe.ready:
            raise RuntimeError(f"SANDBOX_CAPABILITY_PROBE_FAILED:{probe.code}")

        async def cycle():
            execution = await owner.worker.run_once()
            if owner.worker.draining:
                return execution.worked
            recovery = await owner.worker.reconcile_next()
            owner.worker.cleanup_expired_partials(
                settings.sandbox_partial_retention_seconds,
            )
            return execution.worked or recovery.worked
    else:
        # build_runtime owns the production gate and the single code-owned
        # production factory call. No Worker setting can inject composition.
        owner = build_runtime(raw, settings, process_role=role)
        cycle = owner.run_once
    return owner, cycle


async def _report_heartbeat(
    control_db, settings, role, *, ready: bool, draining: bool,
    status_code: str,
) -> bool:
    try:
        await control_db.rpc(
            "report_agent_runtime_worker_heartbeat", {
            "p_process_role": role,
            "p_worker_id": settings.agent_runtime_worker_id,
            "p_release_revision": settings.agent_runtime_release_revision,
            "p_ready": ready,
            "p_draining": draining,
            "p_status_code": status_code,
            "p_details": {
                "ready": ready, "draining": draining,
                "media_projection_enabled": bool(
                    role == "projection"
                    and settings.agent_runtime_media_enabled
                ),
                "media_provider_probe_passed": bool(
                    role == "projection"
                    and settings.agent_runtime_media_provider_probe_passed
                ),
            },
            },
        ).execute()
        media_rpc_ok, _ = await _report_media_projection_readiness(
            control_db, settings, role, ready=ready, draining=draining,
        )
        if not media_rpc_ok:
            return False
    except Exception:
        return False
    return True


def _redacted_error(error: Exception) -> str:
    """Expose only stable class/code information to health consumers."""
    code = str(error).split(":", 1)[0].strip().upper()
    if not code or len(code) > 80 or not code.replace("_", "").isalnum():
        return type(error).__name__.upper()
    return code


def _health_payload(state: dict[str, object], role: str, stopping: bool) -> dict[str, object]:
    payload = dict(state)
    payload["ready"] = bool(
        state["ready"] and not state["draining"] and not stopping
    )
    payload["role"] = role
    return payload


def _can_run_cycle(owner, cycle, gate_enabled: bool, ready: bool) -> bool:
    return (
        owner is not None and _composition_ready(owner)
        and cycle is not None and gate_enabled and ready
    )


def _composition_ready(owner) -> bool:
    return owner is not None and bool(getattr(owner, "ready", True))


def _drain_owner(owner) -> None:
    if owner is None:
        return
    drain = getattr(owner, "drain", None)
    if callable(drain):
        drain()
        return
    stop = getattr(owner, "stop", None)
    if callable(stop):
        stop()
        return
    service = getattr(owner, "service", None)
    stop = getattr(service, "stop", None)
    if callable(stop):
        stop()


async def _shutdown(
    owner, server, socket_path, close_worker_db, *, owner_already_drained=False,
):
    try:
        server.close()
        await server.wait_closed()
    finally:
        try:
            if os.path.exists(socket_path):
                os.unlink(socket_path)
        finally:
            try:
                if not owner_already_drained:
                    _drain_owner(owner)
            finally:
                await close_worker_db()


def main() -> None:
    setup_logging()
    role = os.environ.get("AGENT_RUNTIME_PROCESS_ROLE", "")
    settings = _load_process_settings(role)
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
        if getattr(settings, "sentry_dsn", None):
            import sentry_sdk
            sentry_sdk.capture_exception(error)
            sentry_sdk.flush(timeout=5)
        raise


if __name__ == "__main__":
    main()
