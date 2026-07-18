"""一次 Agent Run 的不可变交付合同。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from services.agent.runtime.artifact_ledger import ArtifactKind


class CapabilityKind(StrEnum):
    QUERY = "query"
    COMPUTE = "compute"
    GENERATE = "generate"
    EXPORT = "export"


class ContractSource(StrEnum):
    EMPTY = "empty"
    CALLER = "caller"
    ROUTE = "route"
    USER_RULE = "user_rule"


@dataclass(frozen=True)
class RunContract:
    """描述本轮必须交付的能力和产物；空合同保持原完成语义。"""

    required_artifacts: frozenset[ArtifactKind] = frozenset()
    optional_artifacts: frozenset[ArtifactKind] = frozenset()
    forbidden_artifacts: frozenset[ArtifactKind] = frozenset()
    required_capabilities: frozenset[CapabilityKind] = frozenset()
    policy_ids: tuple[str, ...] = ()
    source: ContractSource = ContractSource.EMPTY
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        overlap = self.required_artifacts & self.forbidden_artifacts
        if overlap:
            raise ValueError("required artifacts cannot be forbidden")

    @property
    def enabled(self) -> bool:
        return bool(self.required_artifacts or self.required_capabilities)

    @classmethod
    def empty(cls) -> "RunContract":
        return cls()


def build_run_contract(params: object) -> RunContract:
    """仅接纳调用方显式合同；用户文本和模型输出不能自行授权合同。"""
    if not isinstance(params, dict):
        return RunContract.empty()
    raw = params.get("_run_contract")
    if not isinstance(raw, dict):
        return RunContract.empty()
    required = _artifact_set(raw.get("required_artifacts"))
    optional = _artifact_set(raw.get("optional_artifacts"))
    forbidden = _artifact_set(raw.get("forbidden_artifacts"))
    policies = raw.get("policy_ids")
    policy_ids = (
        tuple(str(item) for item in policies)
        if isinstance(policies, list) else ()
    )
    return RunContract(
        required_artifacts=required,
        optional_artifacts=optional,
        forbidden_artifacts=forbidden,
        policy_ids=policy_ids,
        source=ContractSource.CALLER,
        confidence=1.0,
    )


def _artifact_set(value: object) -> frozenset[ArtifactKind]:
    if not isinstance(value, list):
        return frozenset()
    try:
        return frozenset(ArtifactKind(str(item)) for item in value)
    except ValueError as error:
        raise ValueError(f"unknown artifact kind: {error}") from error
