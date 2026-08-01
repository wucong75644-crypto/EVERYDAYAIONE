"""Versioned, immutable agent definitions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True, kw_only=True)
class AgentDefinition:
    canonical_key: str
    revision: str
    prompt_revision: str
    requested_tool_groups: frozenset[str] = field(default_factory=frozenset)
    model_policy: Mapping[str, object] = field(default_factory=dict)
    context_policy: Mapping[str, object] = field(default_factory=dict)
    channel_restrictions: frozenset[str] = field(default_factory=frozenset)
    definition_hash: str = ""

    def __post_init__(self) -> None:
        for value in (self.canonical_key, self.revision, self.prompt_revision):
            if not value or value.strip() != value:
                raise ValueError("agent definition identity is invalid")
        canonical = self._canonical_hash()
        if self.definition_hash and self.definition_hash != canonical:
            raise ValueError("agent definition hash mismatch")
        object.__setattr__(self, "definition_hash", canonical)

    def _canonical_hash(self) -> str:
        facts = {
            "canonical_key": self.canonical_key,
            "revision": self.revision,
            "prompt_revision": self.prompt_revision,
            "requested_tool_groups": sorted(self.requested_tool_groups),
            "model_policy": self.model_policy,
            "context_policy": self.context_policy,
            "channel_restrictions": sorted(self.channel_restrictions),
        }
        return hashlib.sha256(json.dumps(
            facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()


class AgentDefinitionRegistry:
    """A process-local read-only registry; active runs carry its hash."""

    def __init__(self, definitions: tuple[AgentDefinition, ...] = ()) -> None:
        self._definitions: dict[tuple[str, str], AgentDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: AgentDefinition) -> None:
        key = (definition.canonical_key, definition.revision)
        if key in self._definitions:
            raise ValueError("duplicate agent definition")
        self._definitions[key] = definition

    def resolve(self, key: str, revision: str) -> AgentDefinition:
        try:
            return self._definitions[(key, revision)]
        except KeyError as exc:
            raise LookupError("agent definition is not registered") from exc

    def definitions(self) -> tuple[AgentDefinition, ...]:
        return tuple(self._definitions[key] for key in sorted(self._definitions))
