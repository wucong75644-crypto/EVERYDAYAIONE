"""Structured logging helper for confirmation-gated tool paths."""

from typing import Any

from loguru import logger


def log_agent_event(
    level: str, event: str, agent: Any, tool_name: str,
    error_code: str = "", exception_type: str = "",
) -> None:
    getattr(logger, level)(
        "{} | user_id={} | org_id={} | task_id={} | tool={} | "
        "error_code={} | exception_type={}",
        event,
        getattr(agent, "user_id", None) or getattr(agent, "_user_id", None),
        getattr(agent, "org_id", None), getattr(agent, "task_id", None),
        tool_name, error_code, exception_type,
    )
