"""Pure decisions. Persistence, authentication and dispatch remain host-owned."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from uuid import UUID

from .contracts import Artifact, NativeAttempt, Operation, StepResult, TaskError, TaskState, WorkPlan, verified_resources


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _change(state: TaskState, **changes) -> TaskState:
    return TaskState.model_validate({**state.model_dump(), **changes, 'revision': state.revision + 1})


def _active(state):
    if state.status != 'active':
        raise TaskError('TASK_NOT_ACTIVE')


def revise_plan(state: TaskState, proposal: WorkPlan) -> TaskState:
    _active(state)
    if proposal.expected_revision != state.revision:
        raise TaskError('TASK_REVISION_STALE')
    requirements = {r.id for r in state.requirements}
    claimed = {r for step in proposal.steps for r in (*step.requirement_ids, *step.contributes_to)}
    if claimed != requirements:
        raise TaskError('TASK_REQUIREMENTS_NOT_COVERED')
    # Existing receipts/effects cannot be orphaned or rebound by renaming steps.
    locked = ({r.step_id for r in state.results} | {a.step_id for a in state.artifacts}
              | {op.step_id for op in state.operations}) & {s.id for s in state.steps}
    old = {step.id: step for step in state.steps}
    new = {step.id: step for step in proposal.steps}
    if set(new) & {s.id for v in state.requirements_history for s in v.steps}:
        raise TaskError('TASK_HISTORICAL_STEP_IMMUTABLE')
    if any(new.get(key) != old.get(key) for key in locked):
        raise TaskError('TASK_COMPLETED_STEP_IMMUTABLE')
    if proposal.steps == state.steps:
        return state
    return _change(state, steps=proposal.steps)


def ready_steps(state: TaskState):
    if state.status != 'active':
        return ()
    completed = {result.step_id for result in state.results}
    missing = set(missing_requirements(state))
    return tuple(step for step in state.steps if step.id not in completed
                 and set(step.depends_on) <= completed
                 and not any(missing & set(g.requirement_ids) for g in state.capability_gates
                             if g.capability == step.capability))


def register_artifacts(state: TaskState, artifacts: Iterable[Artifact]) -> TaskState:
    """Host-only: callers must resolve real resources and validate their scope."""
    additions = tuple(artifacts)
    by_id = {a.id: a for a in state.artifacts}
    steps = {s.id for s in state.steps} | {s.id for v in state.requirements_history for s in v.steps}
    for artifact in additions:
        if artifact.step_id not in steps:
            raise TaskError('TASK_ARTIFACT_STEP_UNKNOWN')
        previous = by_id.get(artifact.id)
        if previous is not None and previous != artifact:
            raise TaskError('TASK_ARTIFACT_ID_REUSED')
        by_id[artifact.id] = artifact
    if tuple(by_id.values()) == state.artifacts:
        return state
    return _change(state, artifacts=tuple(by_id.values()))


def complete_step(state: TaskState, step_id: str, artifact_ids: Iterable[str], *, owner_task_id=None) -> TaskState:
    _active(state)
    ids = tuple(dict.fromkeys(artifact_ids))
    existing = next((r for r in state.results if r.step_id == step_id), None)
    if existing is not None:
        if set(existing.artifact_ids) == set(ids):
            return state
        raise TaskError('TASK_COMPLETED_STEP_IMMUTABLE')
    if step_id not in {s.id for s in ready_steps(state)}:
        raise TaskError('TASK_STEP_NOT_READY')
    artifacts = {a.id: a for a in state.artifacts}
    if not ids or any(key not in artifacts or artifacts[key].step_id != step_id for key in ids):
        raise TaskError('TASK_EVIDENCE_MISSING')
    step = next(s for s in state.steps if s.id == step_id)
    for requirement in state.requirements:
        if requirement.id not in (*step.requirement_ids, *step.contributes_to):
            continue
        resources = verified_resources(requirement, (artifacts[key] for key in ids))
        minimum = requirement.minimum if requirement.id in step.requirement_ids else 1
        if len(resources) < minimum:
            raise TaskError('TASK_STEP_DELIVERY_INCOMPLETE')
    return _change(state, results=(*state.results, StepResult(step_id=step_id, artifact_ids=ids,
        completed_task_id=owner_task_id)))


def missing_requirements(state: TaskState) -> tuple[str, ...]:
    completed = {r.step_id: r for r in state.results}
    artifacts = {a.id: a for a in state.artifacts}
    missing = []
    for requirement in state.requirements:
        evidence = {key for step in state.steps if requirement.id in (*step.requirement_ids, *step.contributes_to)
                    and step.id in completed for key in completed[step.id].artifact_ids}
        # Distinct artifacts cannot inflate count by repeating the same resource.
        resources = verified_resources(requirement, (artifacts[key] for key in evidence if key in artifacts))
        if len(resources) < requirement.minimum:
            missing.append(requirement.id)
    return tuple(missing)


def finish(state: TaskState) -> TaskState:
    if state.status == 'completed':
        return state
    _active(state)
    if not state.steps or len([r for r in state.results if r.step_id in {s.id for s in state.steps}]) != len(state.steps) or missing_requirements(state):
        raise TaskError('TASK_DELIVERY_INCOMPLETE')
    if any(op.status in {'reserved', 'accepted', 'uncertain'} for op in state.operations):
        raise TaskError('TASK_EFFECT_UNRESOLVED')
    return _change(state, status='completed')


def reserve_operation(state: TaskState, *, expected_revision: int, step_id: str, capability: str, effect_key: str,
                      effect_version: int, arguments: dict, owner_task_id,
                      invocation_id: str) -> tuple[TaskState, Operation, str]:
    _active(state)
    if expected_revision != state.revision:
        raise TaskError('TASK_REVISION_STALE')
    step = next((step for step in state.steps if step.id == step_id), None)
    if step is None or step.capability != capability:
        raise TaskError('TASK_EFFECT_CAPABILITY_MISMATCH')
    identity = digest([str(state.root_task_id), effect_key, effect_version])
    arguments_hash = digest(arguments)
    existing = next((op for op in state.operations if op.id == identity), None)
    if existing:
        if ((existing.status != 'failed_before_dispatch' and existing.arguments_sha256 != arguments_hash) or existing.step_id != step_id
                or existing.capability != capability):
            raise TaskError('TASK_EFFECT_ARGUMENTS_CHANGED')
        if existing.status in {'accepted', 'completed'}:
            return state, existing, 'replay'
        if existing.status != 'failed_before_dispatch':
            return state, existing, 'reconcile'
    else:
        previous = [op for op in state.operations if op.effect_key == effect_key]
        if any(op.status in {'reserved', 'accepted', 'uncertain'} for op in previous):
            raise TaskError('TASK_EFFECT_UNRESOLVED')
        # Initial effects start at 1. Additional versions require a persisted,
        # trusted user-requirement revision; plan edits cannot supply that fact.
        maximum = max((op.effect_version for op in previous), default=0)
        if effect_version != maximum + 1 or effect_version > state.requirements_revision + 1:
            raise TaskError('TASK_EFFECT_REVISION_UNAUTHORIZED')
    if step_id not in {s.id for s in ready_steps(state)}:
        raise TaskError('TASK_STEP_NOT_READY')
    history = existing.previous_attempts if existing else ()
    if existing is not None:
        if len(history) >= 8:
            raise TaskError('TASK_EFFECT_RETRY_EXHAUSTED')
        history += (NativeAttempt.model_validate(existing.model_dump(include=set(NativeAttempt.model_fields))),)
    operation = Operation(id=identity, step_id=step_id, capability=capability,
                          effect_key=effect_key, effect_version=effect_version,
                          arguments_sha256=arguments_hash, owner_task_id=owner_task_id,
                          invocation_id=invocation_id, status='reserved', previous_attempts=history)
    operations = tuple(op for op in state.operations if op.id != identity) + (operation,)
    return _change(state, operations=operations), operation, 'reserve'


def record_operation(state: TaskState, operation_id: str, *, owner_task_id,
                     status: str, receipt_ref: str | None = None) -> TaskState:
    operation = next((op for op in state.operations if op.id == operation_id), None)
    if operation is None or operation.owner_task_id != UUID(str(owner_task_id)):
        raise TaskError('TASK_EFFECT_OWNER_MISMATCH')
    transitions = {'reserved': {'accepted', 'completed', 'uncertain', 'failed_before_dispatch'},
                   'accepted': {'completed', 'uncertain', 'failed_after_dispatch'},
                   'uncertain': {'completed', 'accepted', 'failed_after_dispatch'},
                   'completed': set(), 'failed_before_dispatch': set(), 'failed_after_dispatch': set()}
    if status == operation.status and receipt_ref == operation.receipt_ref:
        return state
    if status not in transitions[operation.status]:
        raise TaskError('TASK_EFFECT_TRANSITION_DENIED')
    if status in {'accepted', 'completed'} and not receipt_ref:
        raise TaskError('TASK_EFFECT_RECEIPT_REQUIRED')
    updated = Operation.model_validate({**operation.model_dump(), 'status': status,
                                       'receipt_ref': receipt_ref})
    return _change(state, operations=tuple(updated if op.id == operation_id else op
                                          for op in state.operations))


def consume_model_round(state: TaskState, *, correction=False) -> TaskState:
    _active(state)
    rounds = state.model_rounds_used + 1
    corrections = state.correction_count + int(correction)
    if rounds > state.model_round_limit or corrections > state.correction_limit:
        raise TaskError('TASK_BUDGET_EXHAUSTED')
    return _change(state, model_rounds_used=rounds, correction_count=corrections)


def effect_version(state, key):
    """A fresh slot starts at one; a new version requires a trusted revision."""
    if not state.requirements_history:return state.requirements_revision+1
    return max((op.effect_version for op in state.operations if op.effect_key==key), default=0) or 1
