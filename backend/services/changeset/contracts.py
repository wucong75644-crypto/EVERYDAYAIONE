"""ChangeSet 与业务适配器之间的稳定契约。

通用内核只保存候选变更、检查结果和事件，不直接理解或更新业务表。
适配器必须在 commit/restore 内部以业务自己的版本机制校验基线，并负责
业务事务的实际提交；内核只负责流程、授权边界、审计和状态投影。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence


CHANGESET_CONTRACT_VERSION = "changeset.v1"
CHANGESET_MIGRATION_ID = "248_change_sets.sql"


class ChangeSetStatus(str, Enum):
    DRAFT = "draft"
    RESOLVING = "resolving"
    PROPOSED = "proposed"
    VALIDATING = "validating"
    PREFLIGHTING = "preflighting"
    AWAITING_APPROVAL = "awaiting_approval"
    COMMITTING = "committing"
    APPLIED = "applied"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"
    EXPIRED = "expired"
    CONFLICTED = "conflicted"


class ChangeCheckType(str, Enum):
    VALIDATION = "validation"
    PREFLIGHT = "preflight"
    AUTHORIZATION = "authorization"
    APPROVAL = "approval"
    CONFLICT = "conflict"
    COMMIT = "commit"
    RESTORE = "restore"


class ChangeCheckStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ChangeSetContext:
    """适配器可见的完整候选快照；不包含数据库客户端。"""

    id: str
    org_id: str
    resource_type: str
    resource_id: str
    operation: str
    base_revision: str
    base_snapshot: Mapping[str, Any]
    proposed_snapshot: Mapping[str, Any]
    patch: Sequence[Mapping[str, Any]]
    diff: Mapping[str, Any]
    policy_snapshot: Mapping[str, Any]
    plan_snapshot: Mapping[str, Any] | None = None
    tool_policy_snapshot: Mapping[str, Any] | None = None
    check_summary: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ResolveRequest:
    context: ChangeSetContext
    intent: Mapping[str, Any]


@dataclass(frozen=True)
class ResolveResult:
    proposed_snapshot: Mapping[str, Any]
    operation: str | None = None
    plan_snapshot: Mapping[str, Any] | None = None
    tool_policy_snapshot: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizeRequest:
    context: ChangeSetContext
    proposed_snapshot: Mapping[str, Any]


@dataclass(frozen=True)
class NormalizeResult:
    proposed_snapshot: Mapping[str, Any]
    patch: Sequence[Mapping[str, Any]]
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthorizeRequest:
    context: ChangeSetContext
    actor_id: str
    actor_type: str


@dataclass(frozen=True)
class AuthorizationResult:
    allowed: bool
    policy_snapshot: Mapping[str, Any]
    reasons: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class ValidateRequest:
    context: ChangeSetContext


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    result: Mapping[str, Any]
    reasons: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class DiffRequest:
    context: ChangeSetContext


@dataclass(frozen=True)
class DiffResult:
    patch: Sequence[Mapping[str, Any]]
    diff: Mapping[str, Any]


@dataclass(frozen=True)
class PreflightRequest:
    context: ChangeSetContext


@dataclass(frozen=True)
class PreflightResult:
    passed: bool
    result: Mapping[str, Any]
    reasons: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class CommitRequest:
    """提交边界：适配器必须自行锁定并校验业务对象的 base_revision。"""

    context: ChangeSetContext
    idempotency_key: str


@dataclass(frozen=True)
class CommitResult:
    applied: bool
    new_revision: str | None
    receipt: Mapping[str, Any] = field(default_factory=dict)
    conflict: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class RestoreRequest:
    context: ChangeSetContext
    idempotency_key: str
    target_revision: str | None = None


@dataclass(frozen=True)
class RestoreResult:
    restored: bool
    revision: str | None
    receipt: Mapping[str, Any] = field(default_factory=dict)
    conflict: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class RenderRequest:
    context: ChangeSetContext
    locale: str = "zh-CN"


@dataclass(frozen=True)
class RenderResult:
    title: str
    summary: str
    sections: Sequence[Mapping[str, Any]] = field(default_factory=tuple)


class ChangeSetAdapter(Protocol):
    """业务适配器契约；禁止内核通过 JSON 任意写业务表。"""

    async def resolve(self, request: ResolveRequest) -> ResolveResult:
        """把 AI 意图解析为业务对象候选，不产生持久化副作用。"""

    async def normalize(self, request: NormalizeRequest) -> NormalizeResult:
        """按业务规则归一化候选值，并返回可审计 patch。"""

    async def authorize(self, request: AuthorizeRequest) -> AuthorizationResult:
        """检查当前主体对该业务对象和操作的权限。"""

    async def validate(self, request: ValidateRequest) -> ValidationResult:
        """执行确定性业务校验；失败时不得写业务表。"""

    async def diff(self, request: DiffRequest) -> DiffResult:
        """生成业务语义 Diff，而不是让内核猜测 JSON 字段含义。"""

    async def preflight(self, request: PreflightRequest) -> PreflightResult:
        """执行只读安全预检并返回结构化检查结果。"""

    async def commit(self, request: CommitRequest) -> CommitResult:
        """在业务事务内校验 base_revision 后提交候选变更。"""

    async def restore(self, request: RestoreRequest) -> RestoreResult:
        """在业务事务内按业务规则回滚或恢复，不能由内核直接更新业务表。"""

    async def render(self, request: RenderRequest) -> RenderResult:
        """为前端/聊天投影生成业务可读的摘要。"""
