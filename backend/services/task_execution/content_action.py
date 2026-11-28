"""Derived action view shared by tool availability and completion checks.

No phase is persisted here. Reads, immutable content, approvals and native
receipts stay authoritative in their existing ledgers.
"""
import copy
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActionView:
    reads: list = field(default_factory=list)
    revisions: list = field(default_factory=list)
    completion: dict | None = None
    items: tuple = ()


def evaluate(runtime, request):
    from .request_resolution import binding, context, current_image_effects
    from .content_delivery import input_revision, KEY
    from .content_revision import pending_reads, saved_reference
    from .saved_image_revision import pending_execution
    from . import content_objects as co
    from services.handlers.chat.content_tools import semantic_context, semantic_intent
    selected = binding(runtime)
    if not selected:
        return ActionView()
    valid = (context(runtime) is not None and selected.get('status') == 'resolved'
        and selected.get('input_revision') == input_revision(runtime, request)
        and getattr(runtime, 'task_image_completion', None) is None)
    intent, target = semantic_intent(request, runtime), selected['target']
    frame = semantic_context(request, runtime)
    if valid and intent == 'write' and target['kind'] != 'content':
        delivery = runtime.checkpoint_base.get(KEY) or {}
        artifact = next((a for a in co.objects(getattr(runtime, 'task_execution_state', None))
                         if a.id == delivery.get('artifact_id')), None)
        if (artifact and delivery.get('phase') == 'committed'
                and delivery.get('owner_task_id') == str(runtime.task_id)
                and delivery.get('input_revision') == input_revision(runtime, request)
                and delivery.get('delivery_complete') is not False):
            return ActionView()
        return ActionView(completion={'stage': 'author_required', 'next_tool': co.SAVE,
            'message': '本次要求的新方案尚未完整保存。用document保存整组及confirmation，不填写new_content。'})
    if target['kind'] != 'content':
        return ActionView()
    revised = saved_reference(runtime, request, target)
    reads = [] if revised else pending_reads(runtime, request)
    revisions = [] if revised else pending_execution(runtime, request)
    affected = {row['content_source']['item_index'] for row in revisions}
    unread = {row['content_source']['item_index'] for row in reads}
    items = tuple({'item_index': index, 'stage': ('read_required' if index in unread and index in affected else
        'revision_required' if index in affected else 'ready')} for index in target['item_indices'])
    if not valid or intent not in {'write', 'write_then_submit_images', 'execute_saved', 'repeat'}:
        return ActionView(reads, revisions, items=items)
    if revised is None and (intent in {'write', 'write_then_submit_images'} or revisions):
        return ActionView(reads, revisions, {'stage': 'read_required' if reads else 'revision_required',
            'next_tool': 'read_conversation_text' if reads else co.SAVE,
            'read_calls': [{'tool': 'read_conversation_text', 'arguments': args} for args in reads],
            'target': copy.deepcopy(target), 'items': revisions,
            'message': '所选旧稿尚未按本次要求保存新版本，不能声称已保存或已出图。'}, items)
    from .content_source import state_for
    from .item_confirmation import directive
    source = revised or {'artifact_id': target['artifact_id'], 'item_index': target['item_indices'][0]}
    state = state_for(runtime, source)
    waiting = set()
    if state is not None:
        group = next((g for g in co.catalog(state, for_model=True) if g['artifact_id'] == source['artifact_id']), {})
        waiting = {i['item_index'] for i in group.get('items', ()) if i.get('awaiting_confirmation')}
    approved = set(directive(request, runtime).approved)
    items = tuple({**item, 'stage': 'waiting_confirmation'} if item['stage'] == 'ready'
        and item['item_index'] in waiting - approved else item for item in items)
    if (intent == 'write' or waiting.intersection(target['item_indices']) - approved
            or frame and (frame.media.wait_all or frame.media.wait_for) and not frame.media.approved):
        return ActionView(reads, revisions, items=items)
    operations, reused = current_image_effects(runtime, request)
    if operations or reused:
        return ActionView(reads, revisions, items=items)
    return ActionView(reads, revisions, {'stage': 'execute_required', 'next_tool': 'generate_image',
        'content_source': revised or {'artifact_id': target['artifact_id'], 'item_index': target['item_indices'][0]},
        'target': copy.deepcopy(target),
        'message': '本次用户要求出图，但尚无本次执行回执；只读取或保存不能结束本次交付。'}, items)
