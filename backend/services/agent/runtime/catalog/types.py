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
    schema_hash: str = ""

    def __post_init__(self) -> None:
        if not self.canonical_name or not self.executor_type:
            raise ValueError("tool identity is required")
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
