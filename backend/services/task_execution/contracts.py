"""Versioned logical-task facts. Model plans cannot edit user requirements."""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID
import hashlib

from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer

Key = Annotated[str, Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,99}$')]
Digest = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Count = Annotated[int, Field(strict=True, ge=0)]
ArtifactKind = Literal['text', 'file', 'image', 'data', 'receipt']
TaskStatus = Literal['active', 'waiting_input', 'waiting_approval', 'waiting_dependencies',
                     'paused', 'completed', 'partial', 'failed', 'cancelled']


class TaskError(ValueError):
    """Stable internal error code; never includes credentials or user content."""


class Fact(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')


class Requirement(Fact):
    """Created from trusted input adapters, never from update_task_plan arguments."""
    id: Key
    source_message_id: UUID
    description: str = Field(min_length=1, max_length=2000)
    artifact_kind: ArtifactKind
    minimum: Annotated[int, Field(strict=True, ge=1, le=100)] = 1
    validator: Key = 'artifact.exists'
    item_count: Annotated[int, Field(strict=True, ge=1, le=100)] | None = None

    @model_serializer(mode='wrap')
    def preserve_optional_metrics(self, handler):
        result = handler(self)
        if self.item_count is None:
            result.pop('item_count', None)
        return result


class ExecutionIdentity(Fact):
    """Frozen projection of the existing trusted ExecutionScope, not a grant."""
    workspace_owner_id: str = Field(min_length=1, max_length=300)
    context_scope: Literal['user', 'channel']
    personal_context_allowed: bool
    channel_scope_id: str | None = Field(default=None, max_length=300)

    @model_validator(mode='after')
    def channel_boundary(self):
        if self.context_scope == 'channel' and (self.personal_context_allowed or not self.channel_scope_id):
            raise TaskError('TASK_CHANNEL_SCOPE_INVALID')
        if self.context_scope == 'user' and self.channel_scope_id is not None:
            raise TaskError('TASK_USER_SCOPE_INVALID')
        return self


class PlanStep(Fact):
    id: Key
    title: str = Field(min_length=1, max_length=300)
    depends_on: tuple[Key, ...] = Field(default=(), max_length=32)
    requirement_ids: tuple[Key, ...] = Field(default=(), max_length=32,
        description='此步骤独立满足的完整交付要求。一项要求包含N张图片时，单张步骤不能在这里声明完成整项。')
    # A contribution produces >=1 verified item; task totals are checked later.
    contributes_to: tuple[Key, ...] = Field(default=(), max_length=32,
        description='此步骤向多项数量交付贡献部分成果。例如五张图片要求的逐图步骤仅在此引用图片要求，'
            '并填写image_item_index，不重复在requirement_ids声明整套完成。')
    capability: str | None = Field(default=None, max_length=150,
        description='仅实际业务工具执行步骤填写已披露的目录capability，不填工具名或方法名。'
            '纯文本规划留空。bind_task_image_sources等控制动作是执行准备，不另建占用相同图片交付槽位的业务步骤。')
    method: str | None = Field(default=None, max_length=150)
    image_item_index: Annotated[int, Field(strict=True, ge=1, le=100)] | None = Field(default=None,
        description='仅实际generate_image逐图执行步骤填写规划全局序号，从1开始。'
            '原图绑定是执行前控制，不另建具有相同序号/交付要求的生图步骤；整套汇总步骤不填。')

    @model_serializer(mode='wrap')
    def omit_unbound_image_index(self, handler):
        value = handler(self)
        if self.image_item_index is None:
            value.pop('image_item_index', None)
        return value


class WorkPlan(Fact):
    """Untrusted model proposal, with no permission or requirement-write fields."""
    expected_revision: Count
    steps: tuple[PlanStep, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode='after')
    def dag(self):
        by_id = {step.id: step for step in self.steps}
        if len(by_id) != len(self.steps):
            raise TaskError('TASK_DUPLICATE_STEP')
        visiting, visited = set(), set()

        def visit(key):
            if key in visiting:
                raise TaskError('TASK_DEPENDENCY_CYCLE')
            if key not in by_id:
                raise TaskError('TASK_DEPENDENCY_MISSING')
            if key in visited:
                return
            visiting.add(key)
            for dependency in by_id[key].depends_on:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)
        for key in by_id:
            visit(key)
        return self


class CapabilityGate(Fact):
    """Host/method-owned prerequisites, never editable by a model work plan."""
    capability: str = Field(min_length=1, max_length=150)
    requirement_ids: tuple[Key, ...] = Field(min_length=1, max_length=32)


class MethodBinding(Fact):
    """Host-verified immutable method phase; no tools or grants in this fact."""
    profile_id: Key
    package_id: UUID
    skill_key: Key
    revision: str = Field(min_length=1, max_length=100)
    body_sha256: Digest
    requirement_ids: tuple[Key, ...] = Field(min_length=1, max_length=8)
    image_count: Annotated[int, Field(strict=True, ge=1, le=100)] = 1
    retire_on_complete: bool = False
    contract_source: Literal['reviewed', 'declared'] = 'reviewed'


class ReusedImage(Fact):
    requirement_id: Key
    item_index: Annotated[int, Field(strict=True, ge=1, le=100)]
    requirements_revision: Count
    planning_artifact_id: Key
    request_sha256: Digest


class Artifact(Fact):
    """Registered by a trusted result adapter, with opaque existing resource refs."""
    id: Key
    kind: ArtifactKind
    reference: str = Field(min_length=1, max_length=2000)
    sha256: Digest
    source_task_id: UUID
    delivery_task_id: UUID | None = None
    source_call_id: str = Field(min_length=1, max_length=300)
    step_id: Key
    # Names of domain checks actually passed, never assertions accepted from a model.
    validations: tuple[Key, ...] = Field(default=('artifact.exists',), max_length=32)
    inline_text: str | None = Field(default=None, min_length=1, max_length=50000)
    image_reuse: ReusedImage | None = None

    @model_serializer(mode='wrap')
    def preserve_old_artifact(self,handler):
        value=handler(self)
        if self.image_reuse is None:value.pop('image_reuse',None)
        return value

    @model_validator(mode='after')
    def text_integrity(self):
        if self.image_reuse is not None and (self.kind!='image' or not self.reference.startswith('task-image:')):
            raise TaskError('TASK_REUSED_IMAGE_INVALID')
        if self.inline_text is not None and (self.kind != 'text' or
                hashlib.sha256(self.inline_text.encode()).hexdigest() != self.sha256):
            raise TaskError('TASK_TEXT_ARTIFACT_INTEGRITY')
        return self


class StepResult(Fact):
    step_id: Key
    artifact_ids: tuple[Key, ...] = Field(min_length=1, max_length=100)
    completed_task_id: UUID | None = None


def verified_resources(requirement: Requirement, artifacts) -> set[str]:
    return {a.reference for a in artifacts if a.kind == requirement.artifact_kind
            and requirement.validator in a.validations}


class NativeAttempt(Fact):
    step_id: Key
    capability: str
    arguments_sha256: Digest
    owner_task_id: UUID
    invocation_id: str
    owner_turn_id: UUID | None = None
    tool_name: Key | None = None


class Operation(Fact):
    id: Digest
    step_id: Key
    capability: str = Field(min_length=1, max_length=150)
    effect_key: Key
    effect_version: Annotated[int, Field(strict=True, ge=1)]
    arguments_sha256: Digest
    owner_task_id: UUID
    invocation_id: str = Field(min_length=1, max_length=300)
    owner_turn_id: UUID | None = None
    tool_name: Key | None = None
    status: Literal['reserved', 'accepted', 'uncertain', 'completed', 'failed_before_dispatch', 'failed_after_dispatch']
    receipt_ref: str | None = Field(default=None, max_length=2000)
    previous_attempts: tuple[NativeAttempt, ...] = Field(default=(), max_length=8)

    @model_serializer(mode='wrap')
    def omit_empty_history(self, handler):
        value = handler(self)
        if not self.previous_attempts:
            value.pop('previous_attempts', None)
        return value

    @model_validator(mode='after')
    def receipt_required(self):
        if self.status in {'accepted', 'completed'} and not self.receipt_ref:
            raise TaskError('TASK_EFFECT_RECEIPT_REQUIRED')
        return self


class RequirementsRevision(Fact):
    number: Annotated[int, Field(strict=True, ge=1)]
    event_id: UUID
    source_owner_task_id: UUID
    message: str = Field(min_length=1, max_length=256000)
    message_sha256: Digest
    requirements: tuple[Requirement, ...]
    steps: tuple[PlanStep, ...]
    capability_gates: tuple[CapabilityGate, ...]
    method_bindings: tuple[MethodBinding, ...]
    applicable_method_ids: tuple[Key, ...]
    intent_action: str


class CallBinding(Fact):
    call_id: str = Field(min_length=1, max_length=200)
    tool_name: Key
    arguments_sha256: Digest
    step_id: Key
    input_sha256: Digest = '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'


class ResolvedCallBinding(Fact):
    call_id: str = Field(min_length=1,max_length=200)
    arguments_sha256: Digest


class WorkItem(Fact):
    step: str = Field(min_length=1, max_length=300)
    status: Literal['pending', 'in_progress', 'completed']


class WorkingPlan(Fact):
    """Model-owned notes, never dispatch, permission or completion evidence."""
    explanation: str = Field(default='', max_length=2000)
    steps: tuple[WorkItem, ...] = Field(max_length=32)


class TaskState(Fact):
    version: Literal[1] = 1
    image_binding_version: Literal[1,2,3] = 1
    model_directed: Annotated[bool, Field(strict=True)] = False
    call_bindings: tuple[CallBinding, ...] = Field(default=(), max_length=256)
    resolved_call_bindings: tuple[ResolvedCallBinding, ...] = Field(default=(),max_length=256)
    working_plan: WorkingPlan | None = None
    retired_call_steps: tuple[Key, ...] = Field(default=(),max_length=32)
    root_task_id: UUID
    conversation_id: UUID
    actor_user_id: UUID
    execution_identity: ExecutionIdentity
    org_id: UUID | None = None
    branch_id: UUID | None = None
    revision: Count = 0
    requirements_revision: Count = 0
    requirements_history: tuple[RequirementsRevision, ...] = Field(default=(), max_length=16)
    source_message_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    original_goal: str = Field(min_length=1, max_length=50000)
    intent_action: Literal['plan', 'execute_ready', 'execute_prompt', 'edit', 'explain', 'other'] = 'other'
    applicable_method_ids: tuple[Key, ...] = Field(default=(), max_length=4)
    status: TaskStatus = 'active'
    requirements: tuple[Requirement, ...] = Field(min_length=1, max_length=32)
    capability_gates: tuple[CapabilityGate, ...] = Field(default=(), max_length=32)
    method_bindings: tuple[MethodBinding, ...] = Field(default=(), max_length=4)
    steps: tuple[PlanStep, ...] = Field(default=(), max_length=32)
    artifacts: tuple[Artifact, ...] = Field(default=(), max_length=256)
    results: tuple[StepResult, ...] = Field(default=(), max_length=32)
    operations: tuple[Operation, ...] = Field(default=(), max_length=256)
    model_rounds_used: Count = 0
    correction_count: Count = 0
    model_round_limit: Annotated[int, Field(strict=True, ge=1, le=200)] = 30
    correction_limit: Annotated[int, Field(strict=True, ge=1, le=10)] = 3

    @model_serializer(mode='wrap')
    def preserve_legacy_image_protocol(self,handler):
        value=handler(self)
        if not self.model_directed:
            value.pop('model_directed', None)
            value.pop('call_bindings', None)
            value.pop('resolved_call_bindings',None)
            value.pop('working_plan', None)
            value.pop('retired_call_steps', None)
        elif self.working_plan is None:
            value.pop('working_plan', None)
        if not self.retired_call_steps:value.pop('retired_call_steps',None)
        if not self.resolved_call_bindings:value.pop('resolved_call_bindings',None)
        if self.image_binding_version==1:value.pop('image_binding_version',None)
        if not self.requirements_history:value.pop('requirements_history',None)
        return value

    @model_validator(mode='after')
    def unique_and_scoped(self):
        if len({v.call_id for v in self.call_bindings}) != len(self.call_bindings):
            raise TaskError('TASK_DIRECT_CALL_DUPLICATE')
        if (self.call_bindings or self.resolved_call_bindings or self.working_plan is not None or self.retired_call_steps) and not self.model_directed:
            raise TaskError('TASK_DIRECT_CALL_POLICY_MISMATCH')
        if (len({b.call_id for b in self.resolved_call_bindings})!=len(self.resolved_call_bindings)
                or not {b.call_id for b in self.resolved_call_bindings}<={b.call_id for b in self.call_bindings}):
            raise TaskError('TASK_DIRECT_RESOLVED_BINDING_INVALID')
        for values in (self.requirements, self.steps, self.artifacts, self.operations):
            if len({v.id for v in values}) != len(values):
                raise TaskError('TASK_DUPLICATE_FACT')
        if len({v.step_id for v in self.results}) != len(self.results):
            raise TaskError('TASK_DUPLICATE_RESULT')
        if any(r.source_message_id not in self.source_message_ids for r in self.requirements):
            raise TaskError('TASK_REQUIREMENT_SOURCE_MISMATCH')
        if (self.execution_identity.context_scope == 'user'
                and self.execution_identity.workspace_owner_id != str(self.actor_user_id)):
            raise TaskError('TASK_USER_SCOPE_INVALID')
        if self.steps:
            WorkPlan(expected_revision=self.revision, steps=self.steps)
        historical_steps = [s for v in self.requirements_history for s in v.steps]
        historical_requirements = [r for v in self.requirements_history for r in v.requirements]
        if (len(historical_steps) > 256 or len(historical_requirements) > 256
            or len({s.id for s in [*self.steps,*historical_steps]}) != len(self.steps)+len(historical_steps)
            or len({r.id for r in [*self.requirements,*historical_requirements]}) != len(self.requirements)+len(historical_requirements)):
            raise TaskError('TASK_REVISION_FACT_COLLISION')
        if self.requirements_history and [v.number for v in self.requirements_history] != list(range(1,self.requirements_revision+1)):
            raise TaskError('TASK_REQUIREMENT_REVISION_HISTORY_INVALID')
        steps = {s.id: s for s in [*self.steps,*historical_steps]}
        if any(v.step_id not in steps for v in self.call_bindings):
            raise TaskError('TASK_DIRECT_CALL_STEP_UNKNOWN')
        if self.model_directed:
            if (len(set(self.retired_call_steps))!=len(self.retired_call_steps)
                    or not set(self.retired_call_steps)<={s.id for s in self.steps}
                    or any(o.step_id in self.retired_call_steps and o.status!='failed_before_dispatch' for o in self.operations)):
                raise TaskError('TASK_DIRECT_RETIREMENT_INVALID')
            by_step = {}
            for binding in self.call_bindings:
                by_step.setdefault(binding.step_id, set()).add((binding.tool_name, binding.arguments_sha256))
            if not self.call_bindings or any(len(values) != 1 for values in by_step.values()):
                raise TaskError('TASK_DIRECT_CALL_BINDING_MISMATCH')
            for step in self.steps:
                if (step.id not in by_step or not step.id.startswith('host.call.')
                        or step.requirement_ids != (step.id.replace('host.call.', 'host.result.', 1),)
                        or step.depends_on or step.contributes_to or not step.capability):
                    raise TaskError('TASK_DIRECT_CALL_BINDING_MISMATCH')
        requirements = {r.id for r in self.requirements}
        if any(not (set(s.requirement_ids) | set(s.contributes_to)) <= requirements for s in self.steps):
            raise TaskError('TASK_REQUIREMENT_UNKNOWN')
        if any(not set(g.requirement_ids) <= requirements for g in self.capability_gates):
            raise TaskError('TASK_REQUIREMENT_UNKNOWN')
        if (len({m.skill_key for m in self.method_bindings}) != len(self.method_bindings)
                or any(not set(m.requirement_ids) <= requirements or m.skill_key not in self.applicable_method_ids
                       for m in self.method_bindings)):
            raise TaskError('TASK_METHOD_BINDING_INVALID')
        artifacts = {a.id: a for a in self.artifacts}
        completed = {r.step_id for r in self.results}
        for result in self.results:
            if (result.step_id not in steps or not set(steps[result.step_id].depends_on) <= completed
                    or any(key not in artifacts or artifacts[key].step_id != result.step_id
                           for key in result.artifact_ids)):
                raise TaskError('TASK_RESULT_INCONSISTENT')
            step = steps[result.step_id]
            for requirement in [*self.requirements,*historical_requirements]:
                if requirement.id in (*step.requirement_ids, *step.contributes_to):
                    minimum = requirement.minimum if requirement.id in step.requirement_ids else 1
                    if len(verified_resources(requirement, (artifacts[k] for k in result.artifact_ids))) < minimum:
                        raise TaskError('TASK_RESULT_DELIVERY_INCONSISTENT')
        if any(op.step_id not in steps for op in self.operations):
            raise TaskError('TASK_EFFECT_STEP_UNKNOWN')
        if any(a.step_id not in steps for a in self.artifacts):
            raise TaskError('TASK_ARTIFACT_STEP_UNKNOWN')
        if sum(len((a.inline_text or '').encode()) for a in self.artifacts) > 256_000:
            raise TaskError('TASK_INLINE_ARTIFACT_BUDGET')
        if self.status == 'completed':
            effective_steps=[s for s in self.steps if s.id not in self.retired_call_steps]
            if not self.steps or len([r for r in self.results if r.step_id in {s.id for s in effective_steps}]) != len(effective_steps):
                raise TaskError('TASK_DELIVERY_INCOMPLETE')
            for requirement in self.requirements:
                if self.model_directed and any(requirement.id in s.requirement_ids for s in self.steps if s.id in self.retired_call_steps):continue
                ids = {key for s in self.steps if requirement.id in (*s.requirement_ids, *s.contributes_to)
                       for r in self.results if r.step_id == s.id for key in r.artifact_ids}
                if len(verified_resources(requirement, (artifacts[k] for k in ids))) < requirement.minimum:
                    raise TaskError('TASK_DELIVERY_INCOMPLETE')
            if any(op.status in {'reserved', 'accepted', 'uncertain'} for op in self.operations):
                raise TaskError('TASK_EFFECT_UNRESOLVED')
        return self


class ContinuationInput(Fact):
    """Server-only input; never substitutes for a user ContextAnchor."""
    kind: Literal['continuation'] = 'continuation'
    version: Literal[1] = 1
    logical_task_id: UUID
    event_id: UUID
    expected_revision: Count
    conversation_id: UUID
    actor_user_id: UUID
    execution_identity: ExecutionIdentity
    org_id: UUID | None = None
    branch_id: UUID | None = None
    source_message_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    base_context_revision: Count
    through_message_id: UUID | None = None
    event_artifact_ids: tuple[Key, ...] = Field(default=(), max_length=100)
