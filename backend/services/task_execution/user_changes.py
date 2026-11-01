"""Durable authenticated steer delivery; recording is not requirement approval."""
import hashlib
from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from .contracts import Fact, TaskError


class UserChange(Fact):
    event_id: UUID
    owner_task_id: UUID
    message: str = Field(min_length=1, max_length=20000)
    message_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    delivered: bool = False

    @model_validator(mode='after')
    def original_text(self):
        if not self.message.strip() or hashlib.sha256(self.message.encode()).hexdigest() != self.message_sha256:
            raise TaskError('TASK_USER_CHANGE_SOURCE_INVALID')
        return self


class UserChanges(Fact):
    version: Literal[1] = 1
    root_task_id: UUID
    items: tuple[UserChange, ...] = Field(default=(), max_length=32)

    @model_validator(mode='after')
    def bounded(self):
        if len({i.event_id for i in self.items}) != len(self.items) or sum(len(i.message.encode()) for i in self.items) > 256000:
            raise TaskError('TASK_USER_CHANGE_INBOX_INVALID')
        return self


def restore(payload, *, root_task_id, owner_task_id, inherited_root_task_id=None):
    raw = (payload or {}).get('_task_user_changes')
    if raw is None:
        return None
    value = UserChanges.model_validate(raw)
    if str(value.root_task_id) != str(root_task_id) or any(
        not i.delivered and str(i.owner_task_id) != str(owner_task_id)
        and str(inherited_root_task_id) != str(root_task_id) for i in value.items
    ):
        raise TaskError('TASK_USER_CHANGE_REPLAY_SCOPE_MISMATCH')
    return value


def merge(previous, captured, *, root_task_id):
    value = UserChanges.model_validate(captured)
    if str(value.root_task_id) != str(root_task_id):
        raise TaskError('TASK_USER_CHANGE_CAPTURE_SCOPE_MISMATCH')
    if previous is None:
        return value
    old = {i.event_id: i for i in previous.items}
    if previous.root_task_id != value.root_task_id or not set(old) <= {i.event_id for i in value.items}:
        raise TaskError('TASK_USER_CHANGE_CAPTURE_SOURCE_CHANGED')
    items = []
    for item in value.items:
        prior = old.get(item.event_id)
        if prior is not None:
            if prior.model_dump(exclude={'delivered'}) != item.model_dump(exclude={'delivered'}):
                raise TaskError('TASK_USER_CHANGE_CAPTURE_SOURCE_CHANGED')
            item = item.model_copy(update={'delivered': prior.delivered or item.delivered})
        items.append(item)
    return value.model_copy(update={'items': tuple(items)})


async def capture(db, *, actor_user_id, org_id, task_id, execution_token, event_id):
    from core.db_scope import AsyncScopedDatabaseClient, DatabaseScope, DatabaseAccessKind, database_scope_from_client
    from core.local_db import AsyncLocalDBClient
    expected = DatabaseScope(str(actor_user_id), str(org_id) if org_id else None, DatabaseAccessKind.RUNTIME)
    current = database_scope_from_client(db)
    if current is not None and current != expected:
        # Request IDs are metadata; authority must match exactly.
        if (current.actor_user_id, current.org_id, current.access_kind) != (expected.actor_user_id, expected.org_id, expected.access_kind):
            raise TaskError('TASK_USER_CHANGE_DATABASE_SCOPE_MISMATCH')
    if current is None and isinstance(db, AsyncLocalDBClient):
        db = AsyncScopedDatabaseClient(db, expected)
    response = await db.rpc('capture_agent_task_user_change', {
        'p_task_id': str(task_id), 'p_execution_token': str(execution_token), 'p_event_id': str(event_id),
    }).execute()
    result = response.data if response else None
    if not isinstance(result, dict) or result.get('outcome') != 'captured':
        raise TaskError('TASK_USER_CHANGE_CAPTURE_UNCONFIRMED')
    return result['inbox']


def activate_for_delivery(state, changes, *, unreviewed=False):
    """Do not skip undelivered input when old output has just completed.

    This opens the existing model window; it does not change any requirement,
    increase a budget or authorize another operation version.
    """
    if (state is not None and state.status == 'completed' and (unreviewed or (changes is not None
        and changes.root_task_id == state.root_task_id and any(not i.delivered for i in changes.items)))):
        from .contracts import TaskState
        return TaskState.model_validate({**state.model_dump(), 'revision': state.revision + 1, 'status': 'active'})
    return state
