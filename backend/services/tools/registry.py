"""Catalog eligibility and model advertisement, with one permission-policy seam."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping, Protocol

from .context import ToolContext
from .spec import Exposure, ToolSpec


@dataclass(frozen=True)
class ToolAccessDecision:
    """Name-level eligibility only; allow is never approval of call arguments."""

    allowed: bool
    reason: str

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool or not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("Access decision requires a boolean and reason")


class ToolAccessPolicy(Protocol):
    def resolve_access(self, spec: ToolSpec, context: ToolContext) -> ToolAccessDecision:
        """Block 02 owns mode, scenario, authorization and business decisions.

        Receive the complete trusted context on every resolution. No cached user
        state, UI confirmation, Handler execution or argument approval here.
        Exceptions propagate: there is no allow-on-error or legacy fallback.
        """
        ...


class ToolAdvertisement(Protocol):
    def names(self, context: ToolContext, discovered_names: frozenset[str]) -> Iterable[str]: ...


@dataclass(frozen=True)
class ResolvedTools:
    allowed: Mapping[str, ToolSpec]
    advertised: Mapping[str, ToolSpec]
    denied: Mapping[str, str]

    def advertised_schemas(self) -> list[dict]:
        return [spec.to_schema() for spec in self.advertised.values()]


class ToolRegistry:
    """Holds definitions only. Every request must supply its own context/policy."""

    def __init__(self, specs: Iterable[ToolSpec] = ()) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if not isinstance(spec, ToolSpec):
            raise TypeError("Registry requires a validated ToolSpec")
        if spec.name in self._specs:
            raise ValueError(f"Duplicate tool registration: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def require(self, name: str) -> ToolSpec:
        if name not in self._specs:
            raise KeyError(f"Unknown tool: {name}")
        return self._specs[name]

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs.values())

    def check_access(
        self, name: str, context: ToolContext, *, policy: ToolAccessPolicy,
    ) -> ToolAccessDecision:
        """Single eligibility path for advertisement AND call-time decisions."""
        spec = self.get(name)
        if spec is None:
            return ToolAccessDecision(False, "unknown_tool")
        reason = self._unavailable_reason(spec, context)
        if reason is not None:
            return ToolAccessDecision(False, reason)
        decision = policy.resolve_access(spec, context)
        if not isinstance(decision, ToolAccessDecision):
            raise TypeError("Policy must return ToolAccessDecision")
        return decision

    def resolve(
        self, context: ToolContext, *, policy: ToolAccessPolicy,
        advertisement: ToolAdvertisement, discovered_names: Iterable[str] = (),
    ) -> ResolvedTools:
        allowed: dict[str, ToolSpec] = {}
        denied: dict[str, str] = {}
        for name, spec in self._specs.items():
            decision = self.check_access(name, context, policy=policy)
            if decision.allowed:
                allowed[name] = spec
            else:
                denied[name] = decision.reason
        selected = frozenset(advertisement.names(context, frozenset(discovered_names)))
        advertised = {
            name: spec for name, spec in sorted(allowed.items())
            if name in selected and spec.exposure is Exposure.PUBLIC
        }
        return ResolvedTools(
            MappingProxyType(allowed), MappingProxyType(advertised), MappingProxyType(denied),
        )

    @staticmethod
    def _unavailable_reason(spec: ToolSpec, context: ToolContext) -> str | None:
        if spec.exposure is Exposure.LEGACY_INTERNAL and context.entrypoint != "legacy_internal":
            return "legacy_internal_only"
        if spec.domain not in {"shared", context.agent_domain}:
            return "domain_mismatch"
        availability = spec.availability
        if availability.requires_org and context.org_id is None:
            return "organization_required"
        if availability.requires_personal_context and not context.personal_context_allowed:
            return "personal_context_required"
        for flag in availability.feature_flags:
            if context.feature_flags.get(flag) is not True:
                return f"feature_unavailable:{flag}"
        if context.authorized_tool_names is not None and spec.name not in context.authorized_tool_names:
            return "outside_authorized_scope"
        return None
