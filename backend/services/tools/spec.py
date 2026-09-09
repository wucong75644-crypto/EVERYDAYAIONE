"""Immutable tool facts. This package is not wired into production execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


def freeze(value: Any) -> Any:
    """Take an immutable snapshot of JSON-shaped metadata."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError(f"Not snapshot metadata: {type(value).__name__}")


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    return value


class Exposure(str, Enum):
    PUBLIC = "public"
    LEGACY_INTERNAL = "legacy_internal"


@dataclass(frozen=True, kw_only=True)
class ToolAvailability:
    requires_org: bool = False
    requires_personal_context: bool = False
    feature_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("requires_org", "requires_personal_context"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"Invalid availability.{name}")
        if any(not isinstance(flag, str) or not flag.strip() for flag in self.feature_flags):
            raise ValueError("Invalid availability.feature_flags")
        object.__setattr__(self, "feature_flags", tuple(self.feature_flags))


@dataclass(frozen=True, kw_only=True)
class ToolSpec:
    name: str
    schema: Mapping[str, Any] | None
    domain: str
    availability: ToolAvailability
    risk_level: str
    parallelizable: bool
    cacheable: bool
    effects: tuple[str, ...]
    executor_type: str
    handler_key: str
    exposure: Exposure
    source: str
    definition_kind: str = "legacy"
    compatibility_notes: tuple[str, ...] = ()
    legacy_validation_schema: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for field in ("name", "handler_key", "source"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"ToolSpec missing/invalid {field}")
        if self.domain not in {"general", "erp", "shared"}:
            raise ValueError(f"Invalid domain: {self.domain}")
        if not isinstance(self.availability, ToolAvailability):
            raise ValueError("ToolSpec missing availability")
        if self.risk_level not in {"safe", "confirm", "dangerous"}:
            raise ValueError("ToolSpec missing/invalid risk_level")
        if self.executor_type != "legacy":
            raise ValueError("Only the existing legacy executor is supported")
        if not isinstance(self.exposure, Exposure):
            raise ValueError("ToolSpec missing exposure")
        if self.definition_kind not in {"explicit", "legacy"}:
            raise ValueError("Invalid definition_kind")
        if type(self.parallelizable) is not bool or type(self.cacheable) is not bool:
            raise ValueError("parallelizable and cacheable must be declared separately")
        if not self.effects or any(not isinstance(item, str) or not item.strip() for item in self.effects):
            raise ValueError("ToolSpec must declare effects (use 'unknown' if unreviewed)")
        object.__setattr__(self, "effects", tuple(self.effects))
        object.__setattr__(self, "compatibility_notes", tuple(self.compatibility_notes))
        if self.legacy_validation_schema is not None:
            if not isinstance(self.legacy_validation_schema, Mapping):
                raise ValueError("Invalid legacy_validation_schema")
            object.__setattr__(self, "legacy_validation_schema", freeze(self.legacy_validation_schema))
        if self.schema is None:
            if self.exposure is Exposure.PUBLIC:
                raise ValueError(f"Public tool {self.name} missing schema")
            return
        if not isinstance(self.schema, Mapping):
            raise ValueError(f"Invalid original schema: {self.name}")
        function = self.schema.get("function")
        if not isinstance(function, Mapping):
            raise ValueError(f"Invalid original schema function: {self.name}")
        parameters = function.get("parameters")
        if not isinstance(parameters, Mapping):
            raise ValueError(f"Invalid original schema parameters: {self.name}")
        if (self.schema.get("type") != "function" or function.get("name") != self.name
                or not isinstance(function.get("description"), str)
                or parameters.get("type") != "object"
                or not isinstance(parameters.get("properties"), Mapping)):
            raise ValueError(f"Invalid original schema: {self.name}")
        required = parameters.get("required", ())
        if not isinstance(required, (list, tuple)) or any(key not in parameters["properties"] for key in required):
            raise ValueError(f"Invalid schema required fields: {self.name}")
        object.__setattr__(self, "schema", freeze(self.schema))

    def to_schema(self) -> dict[str, Any] | None:
        """Return a fresh original schema, never the catalog's mutable storage."""
        return thaw(self.schema)

    def to_legacy_validation_schema(self) -> dict[str, Any] | None:
        """Preserve old partial validators separately from model-facing schema."""
        return thaw(self.legacy_validation_schema)
