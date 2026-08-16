"""Immutable tool catalog facts shared by Runtime and Authorization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, kw_only=True)
class RuntimeToolDefinition:
    canonical_name: str
    tool_group: str
    schema: Mapping[str, object]
    safety_level: str
    executor_type: str
    executor_revision: int
    capability_requirements: frozenset[str]
    side_effect: str
    authorization_requirement: str
    retry_semantics: str
    reconcile_semantics: str
    cancel_semantics: str
    result_schema_revision: int
    allowed_scope_kinds: frozenset[str] = frozenset({"user", "channel"})
    allowed_channels: frozenset[str] = frozenset({"web", "wecom"})
    schema_hash: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if not self.canonical_name or not self.executor_type:
            raise ValueError("tool identity is required")
        if not isinstance(self.description, str):
            raise ValueError("tool description must be text")
        schema_hash = hashlib.sha256(json.dumps(
            self.schema, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode()).hexdigest()
        if self.schema_hash and self.schema_hash != schema_hash:
            raise ValueError("tool schema hash mismatch")
        object.__setattr__(self, "schema_hash", schema_hash)

    @property
    def revision(self) -> str:
        return f"{self.executor_type}:{self.executor_revision}:{self.schema_hash}:{self.result_schema_revision}"

    def security_facts(self) -> dict[str, object]:
        return {
            "canonical_name": self.canonical_name,
            "tool_group": self.tool_group,
            "description": self.description,
            "schema": self.schema,
            "schema_hash": self.schema_hash,
            "safety_level": self.safety_level,
            "executor_type": self.executor_type,
            "executor_revision": self.executor_revision,
            "capability_requirements": sorted(self.capability_requirements),
            "allowed_scope_kinds": sorted(self.allowed_scope_kinds),
            "allowed_channels": sorted(self.allowed_channels),
            "side_effect": self.side_effect,
            "authorization_requirement": self.authorization_requirement,
            "retry_semantics": self.retry_semantics,
            "reconcile_semantics": self.reconcile_semantics,
            "cancel_semantics": self.cancel_semantics,
            "result_schema_revision": self.result_schema_revision,
        }
