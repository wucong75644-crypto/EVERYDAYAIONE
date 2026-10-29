"""Deterministic composition. Declarations never grant tool or business authority."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import Field, model_validator

from .contracts import Contract, CoordinationContract, SkillAction, SkillError, SkillKey

POLICY_VERSION = 'skill-composition-v1'


class ActionContext(Contract):
    action: SkillAction = 'other'
    stage: str = Field(default='current', max_length=100)
    source: Literal['host', 'workflow', 'checkpoint', 'media', 'scheduled'] = 'host'
    plan_id: str | None = Field(default=None, max_length=64)


class Participant(Contract):
    package_id: str
    skill_key: SkillKey
    revision: str
    body_sha256: str
    sources: tuple[Literal['user', 'session', 'model', 'scheduled'], ...]
    contract: CoordinationContract | None = None


class Composition(Contract):
    version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    policy_version: Literal['skill-composition-v1'] = POLICY_VERSION
    turn_id: str
    action_context: ActionContext = Field(default_factory=ActionContext)
    participants: tuple[Participant, ...] = Field(default=(), max_length=4)
    owners: dict[str, str] = Field(default_factory=dict)
    workflow_links: dict[str, Any] = Field(default_factory=dict)
    effective_allowed_tool_names: tuple[str, ...] = ()

    @model_validator(mode='after')
    def consistent(self):
        expected = owners_for(self.participants, self.action_context)
        if self.owners != expected or len({p.skill_key for p in self.participants}) != len(self.participants):
            raise ValueError('SKILL_COMPOSITION_INCONSISTENT')
        return self


class CompositionConflict(SkillError):
    def __init__(self, keys, code='SKILL_WORKFLOW_CONFLICT'):
        self.keys = tuple(sorted(set(keys)))
        super().__init__(code)


def owners_for(participants, action: ActionContext):
    owners = {}
    keys = {p.skill_key for p in participants}
    for p in participants:
        contract = p.contract
        if contract is None:
            continue  # Unclassified legacy content retains its existing ceiling.
        if not set(contract.requires) <= keys:
            raise CompositionConflict([p.skill_key, *contract.requires], 'SKILL_DEPENDENCY_UNAVAILABLE')
        for claim in contract.claims:
            if action.action not in claim.actions:
                continue
            previous = owners.get(claim.scope)
            if previous is not None and previous != p.skill_key:
                raise CompositionConflict([previous, p.skill_key])
            owners[claim.scope] = p.skill_key
    return owners


def method_for(composition: Composition | None, scope: str):
    if composition is None:
        return None
    key = composition.owners.get(scope)
    for p in composition.participants:
        if p.skill_key == key and p.contract:
            return next(c.method for c in p.contract.claims
                        if c.scope == scope and composition.action_context.action in c.actions)
    return None


def build_composition(*, turn_id, action, participants, tools, previous=None, workflow_links=None):
    owners = owners_for(participants, action)
    values = dict(turn_id=turn_id, action_context=action, participants=tuple(participants),
                  owners=owners, effective_allowed_tool_names=tuple(sorted(tools)),
                  workflow_links=(workflow_links if workflow_links is not None else
                                  previous.workflow_links if previous else {}))
    candidate = Composition(**values)
    if previous:
        if candidate.model_dump(exclude={'revision'}) == previous.model_dump(exclude={'revision'}):
            return previous
        candidate = candidate.model_copy(update={'revision': previous.revision + 1})
    elif participants or action.action != 'other':
        candidate = candidate.model_copy(update={'revision': 1})
    return candidate


def validate_configuration(participants):
    """A fixed set must support every action it declares without two owners."""
    for action in ('plan', 'execute_ready', 'execute_prompt', 'edit', 'explain', 'other'):
        owners_for(participants, ActionContext(action=action))
