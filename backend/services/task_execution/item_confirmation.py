"""User-authored confirmation conditions for numbered content plans."""
import json
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class MediaDirective(BaseModel):
    """One semantic decision, later bound to a real saved item and user event."""
    model_config = ConfigDict(extra='forbid', strict=True)
    tool: Literal['generate_image', 'generate_video'] | None = Field(default=None, description='等待或批准的目标工具；不是对整个会话新增审批。')
    wait_for: list[Annotated[int, Field(ge=1, le=100)]] = Field(default_factory=list, max_length=100)
    wait_all: bool = Field(default=False, description='用户要求该工具的全部成果等后续确认才执行；即使本轮尚未写出方案也要填写。')
    approved: list[Annotated[int, Field(ge=1, le=100)]] = Field(default_factory=list, max_length=100,
        description='本轮同意的真实待确认条目；普通选择ready方案不是新的审批。')
    confirmation_only: bool = False
    artifact_id: str | None = Field(default=None, min_length=1, max_length=200)
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def bounded(self):
        if (self.wait_for or self.wait_all or self.approved) and self.tool is None:
            raise ValueError('CONTENT_CONFIRMATION_TOOL_REQUIRED')
        if len(set(self.wait_for)) != len(self.wait_for) or len(set(self.approved)) != len(self.approved):
            raise ValueError('CONTENT_CONFIRMATION_DUPLICATE_ITEM')
        return self


def directive(request, runtime):
    projection = getattr(runtime, 'workflow_projection', None) or (getattr(request, 'params', None) or {}).get('_ecom_workflow') or {}
    raw = (projection.get('request_context') or {}).get('media')
    return MediaDirective.model_validate(raw) if raw is not None else MediaDirective()


def record_decision(runtime, request, state, value):
    """Bind a validated semantic decision to an authenticated, delivered event."""
    from . import content_objects as content
    from .contracts import TaskError
    if not value.confirmation_only or not value.approved:
        return
    changes = getattr(getattr(runtime, 'task_user_changes', None), 'items', ())
    source = next((c for c in reversed(changes) if c.delivered and str(c.owner_task_id)==str(runtime.task_id)), None)
    if source is None or source.attachments:
        return
    pending = [row for row in content.catalog(state, for_model=True)
               if row.get('confirmation_tool')==value.tool
               and (value.artifact_id is None or row['artifact_id']==value.artifact_id)
               and all(any(i['item_index']==n and i.get('awaiting_confirmation') for i in row['items']) for n in value.approved)]
    if len(pending)!=1:
        raise TaskError('CONTENT_CONFIRMATION_AMBIGUOUS')
    artifact, record, _ = content._load_record(state, {'artifact_id':pending[0]['artifact_id'], 'item_index':value.approved[0]})
    if value.revision is not None and record.revision!=value.revision:
        raise TaskError('CONTENT_CONFIRMATION_AMBIGUOUS')
    proof = {'event_id':str(source.event_id), 'owner_task_id':str(source.owner_task_id),
        'message_sha256':source.message_sha256, 'artifact_id':artifact.id, 'object_key':record.object_key,
        'revision':record.revision, 'tool':value.tool, 'approved_items':sorted(value.approved)}
    ledger = runtime.checkpoint_base.setdefault('_media_decisions_v1', {'version':1, 'items':[]})
    previous = next((p for p in ledger['items'] if p['event_id']==proof['event_id']), None)
    if previous is not None and previous!=proof:
        raise TaskError('CONTENT_CONFIRMATION_DECISION_CHANGED')
    if previous is None:
        ledger['items'].append(proof)


def record_slot(runtime, state, call, reference):
    from . import content_objects as content
    from .contracts import TaskError
    ledger = runtime.checkpoint_base.get('_media_decisions_v1')
    if ledger is None:
        return
    artifact, record, _ = content._load_record(state, {k:reference[k] for k in ('artifact_id','item_index','sha256') if k in reference})
    proof = {'artifact_id':artifact.id, 'object_key':record.object_key, 'revision':record.revision,
             'item_index':reference['item_index'], 'tool':call['name']}
    bindings = ledger.setdefault('bindings', {})
    previous = bindings.get(call['id'])
    if previous is not None and previous!=proof:
        raise TaskError('CONTENT_CONFIRMATION_CALL_CHANGED')
    bindings[call['id']] = proof


def _check_directive(call, state, value, *, allow_new=False):
    from . import content_objects as content
    approval = set(value.approved) if value.confirmation_only and value.tool==call['name'] else set()
    current_wait = value.tool==call['name'] and bool(value.wait_all or value.wait_for)
    catalog = content.catalog(state, for_model=True)
    if not current_wait and not approval and not any(row.get('confirmation_tool') for row in catalog):
        return None  # Ordinary source validation/recovery belongs to the content protocol.
    pending = [row for row in catalog if row.get('confirmation_tool')==call['name']
               and any(item.get('awaiting_confirmation') for item in row['items'])]
    try:
        arguments = json.loads(call.get('arguments') or '{}')
        reference = arguments.get('content_source')
        if not isinstance(reference, dict):
            if call['name']=='generate_image' and isinstance(arguments.get('plan_source'),dict) and not pending:
                return None  # Native plan identity and its item conditions are checked at preparation.
            if pending and not allow_new or current_wait or approval:
                return ('CONTENT_CONFIRMATION_REFERENCE_REQUIRED', '确认必须绑定真实已保存条目。')
            return None
        artifact, record, _ = content._load_record(state, {k: reference[k] for k in ('artifact_id','item_index','sha256') if k in reference})
    except (ValueError, KeyError, TypeError, AttributeError):
        return ('CONTENT_CONFIRMATION_REFERENCE_REQUIRED', '请读取真实已保存条目。')
    index = reference['item_index']
    if record.confirmation_tool and record.confirmation_tool != call['name']:
        return ('CONTENT_CONFIRMATION_TOOL_MISMATCH', '该条目的交付工具与原确认条件不一致。')
    if approval:
        if value.tool != call['name'] or index not in approval:
            return ('CONTENT_CONFIRMATION_DIFFERENT_ITEM', '本轮没有确认该条目。')
        peers = [row for row in pending if all(any(i['item_index']==n and i.get('awaiting_confirmation') for i in row['items']) for n in approval)
                 and (value.artifact_id is None or row['artifact_id']==value.artifact_id)]
        if len(peers)!=1 or peers[0]['artifact_id']!=artifact.id or value.revision is not None and record.revision!=value.revision:
            return ('CONTENT_CONFIRMATION_AMBIGUOUS', '确认对象或版本不能唯一匹配当前待确认方案。')
    if (value.tool==call['name'] and (value.wait_all or index in value.wait_for) or index in record.confirmation_items) and index not in approval:
        return ('CHAT_MEDIA_AWAITING_USER', f'第 {index} 项在等待用户确认，本次未派发。')
    return None

MEDIA = frozenset({'generate_image', 'generate_video'})
def text(request):
    return '\n'.join(part.text for part in request.content if getattr(part, 'type', None) == 'text')


def slot(state, tool, reference):
    from . import content_objects as content

    if tool not in MEDIA:
        return None
    selection = {key: reference[key] for key in ('artifact_id', 'item_index', 'sha256') if key in reference}
    _, record, _ = content._load_record(state, selection)
    if record.confirmation_items and record.confirmation_tool == tool:
        return f"{record.object_key}:{selection['item_index']}"
    return None


def check(call, state, user_text, *, latest_directive=None, semantic=None, allow_new=False):
    if call['name'] not in MEDIA:
        return None
    # Missing semantics cannot approve anything. Previously committed conditions
    # still apply; the normal semantic/model path can identify their real target.
    return _check_directive(call, state, semantic or MediaDirective(), allow_new=allow_new)
