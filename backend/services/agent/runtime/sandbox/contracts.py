"""Narrow contracts shared by Sandbox Job dispatch and execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


EMPTY_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)


@dataclass(frozen=True, kw_only=True)
class SandboxResourceLimits:
    timeout_seconds: int
    cpu_millis: int
    memory_bytes: int
    pids: int
    disk_bytes: int
    file_count: int

    @classmethod
    def from_request(
        cls, value: Mapping[str, object] | None,
    ) -> "SandboxResourceLimits":
        raw = dict(value or {})
        allowed = {
            "timeout_seconds", "cpu_millis", "memory_bytes",
            "pids", "disk_bytes", "file_count",
        }
        if raw.keys() - allowed:
            raise ValueError("SANDBOX_RESOURCE_LIMIT_UNKNOWN")
        limits = cls(
            timeout_seconds=_bounded(raw, "timeout_seconds", 120, 1, 120),
            cpu_millis=_bounded(raw, "cpu_millis", 800, 50, 800),
            memory_bytes=_bounded(
                raw, "memory_bytes", 512 * 1024 * 1024,
                64 * 1024 * 1024, 512 * 1024 * 1024,
            ),
            pids=_bounded(raw, "pids", 64, 1, 64),
            disk_bytes=_bounded(
                raw, "disk_bytes", 256 * 1024 * 1024,
                1024 * 1024, 256 * 1024 * 1024,
            ),
            file_count=_bounded(raw, "file_count", 100, 1, 100),
        )
        return limits

    def as_dict(self) -> dict[str, int]:
        return {
            "timeout_seconds": self.timeout_seconds,
            "cpu_millis": self.cpu_millis,
            "memory_bytes": self.memory_bytes,
            "pids": self.pids,
            "disk_bytes": self.disk_bytes,
            "file_count": self.file_count,
        }


def bounded_summary(value: bytes) -> tuple[str, int, str, bool]:
    """Persist no user-controlled output text; retain only length and hash."""
    import hashlib

    original_length = len(value)
    return (
        "",
        original_length,
        hashlib.sha256(value).hexdigest(),
        bool(value),
    )


def _bounded(
    raw: Mapping[str, object], field: str, default: int,
    minimum: int, maximum: int,
) -> int:
    value = raw.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"SANDBOX_{field.upper()}_INVALID")
    if value < minimum or value > maximum:
        raise ValueError(f"SANDBOX_{field.upper()}_OUT_OF_RANGE")
    return value
