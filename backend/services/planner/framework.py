"""Planner Framework 门面。"""

from __future__ import annotations

from services.planner.contracts import PlanCandidate, PlanRelease
from services.planner.registry import CapabilityRegistry
from services.planner.validator import PlanValidator


class PlannerFramework:
    def __init__(self, registry: CapabilityRegistry, *, policy_version: str = "policy.v1") -> None:
        self.registry = registry
        self.policy_version = policy_version
        self.validator = PlanValidator(registry)

    def release(
        self,
        candidate: PlanCandidate,
        *,
        execution_mode: str = "scheduled",
        parameters: dict | None = None,
    ) -> PlanRelease:
        validation = self.validator.validate(
            candidate, execution_mode=execution_mode, parameters=parameters,
        )
        if not validation.valid:
            raise ValueError("计划校验失败: " + "; ".join(validation.errors))
        return PlanRelease.create(
            candidate,
            tool_policy=validation.tool_policy,
            policy_version=self.policy_version,
        )
