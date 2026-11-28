"""Image-only completion scope; native effects and existing ledgers stay authoritative."""
from types import SimpleNamespace
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ImageCompletionScope(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, json_schema_extra={'examples': [
        {'after_images':'terminal','evidence':['第三个做成图']},
        {'after_images':'continue','evidence':['做好再比较一下']},
        {'after_images':'unknown','evidence':[]},
    ]})
    after_images: Literal['terminal', 'continue', 'unknown']
    evidence: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(default_factory=list, max_length=4)


SCOPE_GUIDANCE = (
    'image_completion_scope描述真实图片发布之后是否还有用户要求，不是完成状态或执行批准。'
    'after_images=terminal仅用于图片发布就是本轮最后交付；出完再分析、比较、做文件或视频用continue；'
    '不确定用unknown，缺少信息不能猜terminal。evidence逐字引用当前用户要求，不填写消息ID或图片短号。'
    '例如“第三个做成图”可为{"after_images":"terminal","evidence":["第三个做成图"]}；'
    '“做好再比较一下”用{"after_images":"continue","evidence":["做好再比较一下"]}；'
    '“视频等我确认”仍保留原等待条件，不因为此字段获得批准。'
    '此对象只含after_images、evidence，不添加uncertainty_reason等字段，也不把对象写成JSON字符串。'
    '缺失或格式不合法仅视为unknown，不能取消其他已校验的业务要求或批准。'
)


def advisory_scope(value):
    """Invalid optional metadata loses terminal authority, not valid business input.

    Never strip unknown keys and keep terminal, or parse nested JSON strings.
    The original business schema (sizes, targets and approvals) stays strict.
    """
    if value is None:
        return None
    try:
        return ImageCompletionScope.model_validate(value)
    except ValidationError:
        return ImageCompletionScope(after_images='unknown')


def user_text(request, runtime=None):
    parts = getattr(request, 'content', ()) or ()
    original = '\n'.join((p.get('text', '') if isinstance(p, dict) else getattr(p, 'text', ''))
                     for p in parts if (p.get('type') if isinstance(p, dict) else getattr(p, 'type', None)) == 'text')
    owner = str(getattr(runtime, 'task_id', ''))
    changes = getattr(getattr(runtime, 'task_user_changes', None), 'items', ())
    return '\n'.join([original, *(change.message for change in changes
        if change.delivered and str(change.owner_task_id) == owner)])


def verified_scope(scope, text, *, continued=False):
    if scope is None:
        return None
    value = ImageCompletionScope.model_validate(scope)
    if not value.evidence or any(not quote.strip() or len(quote) > 2000 or quote not in text for quote in value.evidence):
        return ImageCompletionScope(after_images='unknown')
    if continued:
        return value.model_copy(update={'after_images': 'continue'})
    return value


def completion_decision(source, candidate, checkpoint, task, children, failures):
    """Called only inside the authenticated native image continuation adapter.

    Unknown semantic coverage is never inferred from an empty plan or list.
    Conservatively retain the old model path for unsupported owners/windows.
    """
    from .direct_loop import end_candidate
    delivery = task.get('delivery_context') or {}
    if (not source.model_directed or delivery.get('channel') != 'web'
            or delivery.get('source_kind', 'image') != 'image'):
        return 'resume_model'
    if failures:
        return 'recover'
    if any(o.status in {'reserved', 'uncertain'} for o in candidate.operations):
        return 'recover'
    if any(o.status == 'accepted' for o in candidate.operations):
        return 'wait'
    action = checkpoint.get('_request_resolution_v1') or {}
    frame = (action.get('action') or {}).get('request_context') or {}
    scope = frame.get('image_completion_scope') or {}
    owner = delivery.get('source_owner_task_id')
    if (scope.get('after_images') != 'terminal' or not scope.get('evidence')
            or action.get('status') != 'resolved' or action.get('execution_task_id') != owner
            or str(action.get('input_message_id')) != str(task.get('input_message_id'))):
        return 'resume_model'
    # Reconstruct only the proven source input identity, never rebind an old
    # decision to the new continuation Actor's window.
    from .request_window import input_revision
    from .user_changes import restore
    snapshot = checkpoint.get('_request_context_v1') or {}
    runtime = SimpleNamespace(task_id=owner, checkpoint_base=checkpoint,
        task_user_changes=restore(checkpoint, root_task_id=source.root_task_id,
            owner_task_id=owner, inherited_root_task_id=source.root_task_id))
    request = SimpleNamespace(context_anchor=SimpleNamespace(input_message_id=action['input_message_id'],
        base_revision=snapshot.get('context_revision')))
    if action.get('input_revision') != input_revision(runtime, request):
        return 'resume_model'
    changes = getattr(runtime.task_user_changes, 'items', ())
    if any(not change.delivered for change in changes):
        return 'resume_model'
    from .content_delivery import KEY as content_delivery_key
    draft = checkpoint.get(content_delivery_key) or {}
    if draft and (draft.get('phase') not in {'committed', 'superseded'} or draft.get('delivery_complete') is False):
        return 'resume_model'
    if frame.get('continue_delivery') or frame.get('deliveries') or frame.get('confirmation'):
        return 'resume_model'
    media = frame.get('media') or {}
    if media.get('wait_all') or set(media.get('wait_for', ())) - set(media.get('approved', ())):
        return 'wait_input'
    if candidate.working_plan and any(s.status != 'completed' for s in candidate.working_plan.steps):
        return 'resume_model'
    from .pending_batch import pending_calls
    if pending_calls(checkpoint.get('messages', ()))[0]:
        return 'resume_model'
    selected_children = [c for c in children if c['origin'].get('parent_task_id') == owner]
    if not selected_children or any(c.get('status') != 'completed' or c.get('phase') != 'published' for c in selected_children):
        return 'resume_model'
    # Known counts and content selections remain host facts. All native steps
    # must also be complete; a semantic terminal hint cannot retire siblings.
    target = action.get('target') or {}
    expected = (len(target.get('item_indices', ())) if target.get('kind') == 'content'
                else 1 if target.get('kind') == 'image' else (action.get('action') or {}).get('image_count'))
    if expected is None or len(selected_children) != expected:
        return 'resume_model'
    return 'finish' if end_candidate(candidate).status == 'completed' else 'resume_model'
