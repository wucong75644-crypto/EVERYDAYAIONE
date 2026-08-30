"""通用 AI Planner 契约。

Planner 只产出受校验的候选计划和发布快照；它不持有数据库客户端，
也不提供任何业务写入能力。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from uuid import uuid4


PLANNER_CONTRACT_VERSION = "planner.v1"


@dataclass(frozen=True)
class CapabilityDescriptor:
    tool_name: str
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    read_attributes: Sequence[str] = field(default_factory=tuple)
    write_attributes: Sequence[str] = field(default_factory=tuple)
    risk_level: str = "low"
    required_permissions: Sequence[str] = field(default_factory=tuple)
    execution_modes: Sequence[str] = field(default_factory=lambda: ("interactive", "scheduled"))
    supports_readonly_preflight: bool = True
    version: str = "capability.v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "read_attributes": list(self.read_attributes),
            "write_attributes": list(self.write_attributes),
            "risk_level": self.risk_level,
            "required_permissions": list(self.required_permissions),
            "execution_modes": list(self.execution_modes),
            "supports_readonly_preflight": self.supports_readonly_preflight,
            "version": self.version,
        }


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    intent: str
    tools: Sequence[str]
    input: Mapping[str, Any] = field(default_factory=dict)
    required: bool = True
    verification: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.step_id,
            "intent": self.intent,
            "tools": list(self.tools),
            "input": dict(self.input),
            "required": self.required,
            "verify": self.verification,
        }


@dataclass(frozen=True)
class PlanCandidate:
    target: Mapping[str, Any]
    input_contract: Mapping[str, Any]
    output_contract: Mapping[str, Any]
    steps: Sequence[PlanStep]
    candidate_tools: Sequence[str]
    verification_conditions: Sequence[str] = field(default_factory=tuple)
    risk_info: Mapping[str, Any] = field(default_factory=dict)
    version: str = PLANNER_CONTRACT_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "target": dict(self.target),
            "input_contract": dict(self.input_contract),
            "output_contract": dict(self.output_contract),
            "steps": [step.as_dict() for step in self.steps],
            "candidate_tools": sorted(set(self.candidate_tools)),
            "verification_conditions": list(self.verification_conditions),
            "risk_info": dict(self.risk_info),
        }


@dataclass(frozen=True)
class PlanValidationResult:
    valid: bool
    errors: Sequence[str] = field(default_factory=tuple)
    warnings: Sequence[str] = field(default_factory=tuple)
    tool_policy: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "tool_policy": dict(self.tool_policy),
        }


@dataclass(frozen=True)
class PlanRelease:
    """候选计划经系统校验并在 ChangeSet 确认后使用的固化快照。"""

    release_id: str
    plan_version: str
    candidate: Mapping[str, Any]
    capability_names: Sequence[str]
    tool_policy: Mapping[str, Any]
    policy_version: str
    approved: bool = False
    approved_by: str | None = None
    released_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )

    @classmethod
    def create(
        cls,
        candidate: PlanCandidate,
        *,
        tool_policy: Mapping[str, Any],
        policy_version: str,
    ) -> "PlanRelease":
        return cls(
            release_id=str(uuid4()),
            plan_version=candidate.version,
            candidate=candidate.as_dict(),
            capability_names=sorted(set(candidate.candidate_tools)),
            tool_policy=dict(tool_policy),
            policy_version=policy_version,
        )

    def approve(self, actor_id: str) -> "PlanRelease":
        return PlanRelease(
            release_id=self.release_id,
            plan_version=self.plan_version,
            candidate=self.candidate,
            capability_names=self.capability_names,
            tool_policy=self.tool_policy,
            policy_version=self.policy_version,
            approved=True,
            approved_by=actor_id,
            released_at=self.released_at,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "plan_version": self.plan_version,
            "candidate": dict(self.candidate),
            "capability_names": list(self.capability_names),
            "tool_policy": dict(self.tool_policy),
            "policy_version": self.policy_version,
            "approved": self.approved,
            "approved_by": self.approved_by,
            "released_at": self.released_at,
        }
