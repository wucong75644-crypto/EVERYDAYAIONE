"""One authenticated request decision; workflow values are read-only projections.

Catalog snapshots, native call fingerprints and effect identities remain separate.
"""
import copy
from .contracts import Fact


class Action(Fact):
    intent: str | None = None
    request_context: dict
    image_count: int | None = None


def input_revision(runtime, request):
    from .reducer import digest
    checkpoint = getattr(runtime, 'checkpoint_base', {})
    anchor = getattr(request, 'context_anchor', None)
    changes = getattr(getattr(runtime, 'task_user_changes', None), 'items', ())
    owner = str(getattr(runtime, 'task_id', None) or getattr(request, 'task_id', ''))
    current = [item for item in changes if str(getattr(item, 'owner_task_id', owner)) == owner]
    followup = checkpoint.get('_task_followup') or {}
    original = (checkpoint.get('_task_followup_input_checkpoint') or {}).get('_task_user_changes') or {}
    prior = {item['event_id'] for item in original.get('items', ())}
    synthetic = next((item.event_id for item in current if str(item.event_id) not in prior
        and item.message_sha256 == followup.get('input_sha256')), None)
    snapshot = checkpoint.get('_request_context_v1') or {}
    return digest([owner, str(getattr(anchor, 'input_message_id', '') or snapshot.get('input_message_id', '')),
        str(getattr(anchor, 'base_revision', '') or ''),
        [(str(item.event_id), item.message_sha256) for item in current if item.event_id != synthetic]])


def action(runtime, request=None):
    saved = (getattr(runtime, 'checkpoint_base', {}) or {}).get('_request_resolution_v1') or {}
    raw = saved.get('action')
    if raw is None:
        return None  # Older checkpoints use their original projection, not a new decision.
    if request is not None and saved.get('input_revision') != input_revision(runtime, request):
        return Action(request_context={})  # A stale decision cannot fall back to an older workflow.
    value = Action.model_validate(raw)
    from .request_resolution import Target
    frame = copy.deepcopy(value.request_context)
    frame.update(target={k: v for k, v in saved['target'].items() if k in Target.model_fields},
                 focus=saved.get('focus'))
    if saved['target']['kind'] != 'content' and frame.get('confirmation'):
        # Legacy read-only consumers receive the same future conditions as a
        # projection; only confirmation is persisted as their authority.
        frame['media'] = {**frame.get('media', {}), **frame['confirmation']}
    return value.model_copy(update={'request_context': frame})


def projection(runtime, request=None):
    raw = (getattr(runtime, 'workflow_projection', None)
        or (getattr(request, 'params', None) or {}).get('_ecom_workflow') or {})
    value = copy.deepcopy(raw)
    selected = action(runtime, request)
    if selected is not None:
        value.update(intent=selected.intent, request_context=copy.deepcopy(selected.request_context),
                     input_revision=input_revision(runtime, request))
    return value


def normalize_media(frame, selected):
    """The selected saved record owns waiting slots; ready execution isn't approval.

    New waiting conditions still use explicit save.confirmation. A route's
    approval of a ready item must never turn into a new saved waiting slot.
    """
    if selected is None:
        if frame.confirmation is None and (frame.media.wait_all or frame.media.wait_for):
            from .item_confirmation import WaitingConditions
            frame.confirmation = WaitingConditions(tool=frame.media.tool, wait_all=frame.media.wait_all,
                wait_for=frame.media.wait_for)
        return frame
    from .item_confirmation import MediaDirective
    indices = set(frame.target.item_indices)
    waiting = {item['item_index'] for item in selected['items'] if item.get('awaiting_confirmation')}
    if set(frame.media.approved) - indices:
        from .contracts import TaskError
        raise TaskError('CONTENT_CONFIRMATION_DIFFERENT_ITEM')
    approved = set(frame.media.approved) & waiting & indices
    values = dict(tool=selected.get('confirmation_tool') if waiting else None,
        wait_for=sorted(waiting & indices), wait_all=False, approved=sorted(approved),
        confirmation_only=frame.media.confirmation_only if approved else False,
        artifact_id=selected['artifact_id'] if approved else None,
        revision=selected['revision'] if approved else None)
    # An explicit mismatched approval is not silently redirected to this draft.
    if approved and (frame.media.artifact_id not in (None, selected['artifact_id'])
            or frame.media.revision not in (None, selected['revision'])
            or frame.media.tool != selected.get('confirmation_tool')):
        from .contracts import TaskError
        raise TaskError('CONTENT_CONFIRMATION_AMBIGUOUS')
    frame.media = MediaDirective.model_validate(values)
    return frame


def capture_projection(runtime, prepared, request):
    workflow = getattr(prepared, 'ecom_workflow', None)
    return (copy.deepcopy(getattr(runtime, 'workflow_projection', None)),
        copy.deepcopy((getattr(request, 'params', None) or {}).get('_ecom_workflow')),
        {name: copy.deepcopy(getattr(workflow, name)) for name in ('value', 'route', '_coordinated', 'ready_execution')
         if workflow is not None and hasattr(workflow, name)})


def restore_projection(runtime, prepared, request, before):
    runtime.workflow_projection, params, fields = before
    if getattr(request, 'params', None) is not None:
        if params is None:
            request.params.pop('_ecom_workflow', None)
        else:
            request.params['_ecom_workflow'] = params
    workflow = getattr(prepared, 'ecom_workflow', None)
    if workflow is not None:
        for name, value in fields.items():
            setattr(workflow, name, value)
