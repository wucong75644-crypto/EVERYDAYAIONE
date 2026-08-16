"""Production heartbeat bridge for the persisted media Projection owner gate."""

from __future__ import annotations

from loguru import logger

async def report_media_projection_readiness(
    control_db, settings, role: str, *, ready: bool, draining: bool,
) -> tuple[bool, bool]:
    """Record readiness and return ``(rpc_ok, persisted_ready)``."""
    if role != "projection":
        return True, False
    effective_ready = bool(
        ready and not draining and settings.agent_runtime_media_enabled
        and settings.agent_runtime_media_provider_probe_passed
    )
    ttl_seconds = max(
        5, min(300, int(settings.agent_runtime_heartbeat_seconds * 3)),
    )
    try:
        response = await control_db.rpc(
            "record_agent_runtime_media_projection_readiness_v1", {
                "p_worker_id": settings.agent_runtime_worker_id,
                "p_projection_revision": settings.agent_runtime_release_revision,
                "p_ready": effective_ready,
                "p_heartbeat_ttl_seconds": ttl_seconds,
            },
        ).execute()
    except Exception as error:
        logger.warning(
            "runtime_media_projection_readiness_rpc_failed role={} error_type={}",
            role, type(error).__name__,
        )
        return False, False
    result = response.data if response is not None else None
    if not isinstance(result, dict) or not isinstance(result.get("ready"), bool):
        logger.warning(
            "runtime_media_projection_readiness_invalid_result role={} result_type={}",
            role, type(result).__name__,
        )
        return False, False
    projection_ready = result.get(
        "projection_owner_ready", result.get("ready"),
    )
    heartbeat_fresh = result.get("projection_heartbeat_fresh", True)
    if not isinstance(projection_ready, bool) or not isinstance(heartbeat_fresh, bool):
        logger.warning(
            "runtime_media_projection_readiness_invalid_projection_state role={}",
            role,
        )
        return False, False
    logger.info(
        "runtime_media_projection_readiness role={} effective_ready={} "
        "projection_ready={} heartbeat_fresh={} global_ready={}",
        role, effective_ready, projection_ready, heartbeat_fresh, result["ready"],
    )
    return True, projection_ready and heartbeat_fresh


def set_media_owner_readiness(owner, role: str, ready: bool) -> bool:
    """Apply the persisted gate to the in-process media worker only."""
    if role != "projection":
        return True
    setter = getattr(owner, "set_media_readiness", None)
    if not callable(setter):
        return False
    setter(ready)
    return True


__all__ = ["report_media_projection_readiness", "set_media_owner_readiness"]
