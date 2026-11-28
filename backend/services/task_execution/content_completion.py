"""Current-input completion evidence for saved drafts, including before adoption.

The model chooses and authors. Only committed records and native receipts can
satisfy the already selected action; this module never selects or dispatches.
"""
import copy

from . import content_objects as co, content_revision


def saved_revision(runtime, request, target):
    journal = content_revision._journal(runtime)
    if journal.get('input_revision') != content_revision.input_revision(runtime, request):
        return None
    state = getattr(runtime, 'task_execution_state', None)
    artifacts = {a.id: a for a in co.objects(state)}
    if target['artifact_id'] not in artifacts:
        return None
    from .saved_image_revision import execution_requirement
    _, record, _ = co._load_record(state, {'artifact_id': target['artifact_id'],
                                         'item_index': target['item_indices'][0]})
    required = {i for i in target['item_indices'] if execution_requirement(runtime, request,
        co.ContentReference(artifact_id=target['artifact_id'], item_index=i),
        options=record.document.items[i - 1].options, materials=record.document.items[i - 1].materials,
        revision=record.revision)}
    identity, covered = target['artifact_id'], set()
    for proof in journal.get('saved', ()):
        if proof['base_artifact_id'] != identity:
            continue
        artifact = artifacts.get(proof['artifact_id'])
        if artifact and artifact.sha256 == proof['sha256']:
            identity = artifact.id
            covered.update(proof['item_indices'])
    if identity != target['artifact_id'] and required <= covered and co.is_current(state, identity):
        return {'artifact_id': identity, 'item_index': target['item_indices'][0]}
    return None


def phase(runtime, request):
    from services.handlers.chat.content_tools import semantic_context, semantic_intent
    from .request_resolution import binding, context
    from .content_delivery import input_revision
    from .saved_image_revision import pending_execution
    selected = binding(runtime)
    if (context(runtime) is None or not selected or selected.get('status') != 'resolved'
            or selected.get('input_revision') != input_revision(runtime, request)
            or getattr(runtime, 'task_image_completion', None) is not None):
        return None
    intent = semantic_intent(request, runtime)
    target = selected['target']
    frame = semantic_context(request, runtime)
    if (intent == 'write' and target['kind'] != 'content' and frame
            and (frame.media.wait_all or frame.media.wait_for)):
        from .content_delivery import KEY
        delivery = runtime.checkpoint_base.get(KEY) or {}
        artifact = next((a for a in co.objects(getattr(runtime, 'task_execution_state', None))
                         if a.id == delivery.get('artifact_id')), None)
        if (artifact and delivery.get('phase') == 'committed'
                and delivery.get('owner_task_id') == str(runtime.task_id)
                and delivery.get('input_revision') == input_revision(runtime, request)
                and delivery.get('delivery_complete') is not False):
            return None
        return {'stage': 'author_required', 'next_tool': co.SAVE,
                'message': '本次要求的新方案尚未完整保存。用document保存整组及confirmation，不填写new_content。'}
    if target['kind'] != 'content' or intent not in {'write', 'write_then_submit_images', 'execute_saved', 'repeat'}:
        return None
    revised = saved_revision(runtime, request, target)
    needs_revision = intent in {'write', 'write_then_submit_images'} or bool(pending_execution(runtime, request))
    if revised is None and needs_revision:
        reads = content_revision.pending_reads(runtime, request)
        return {'stage': 'read_required' if reads else 'revision_required',
                'next_tool': 'read_conversation_text' if reads else co.SAVE,
                'read_calls': [{'tool': 'read_conversation_text', 'arguments': args} for args in reads],
                'target': copy.deepcopy(target), 'items': pending_execution(runtime, request),
                'message': '所选旧稿尚未按本次要求保存新版本，不能声称已保存或已出图。'}
    if intent == 'write':
        return None
    if frame and (frame.media.wait_all or frame.media.wait_for) and not frame.media.approved:
        return None  # Saving does not grant execution approval.
    from .request_resolution import current_image_effects
    operations, reused = current_image_effects(runtime, request)
    if operations or reused:
        return None  # Native accepted/unknown/failed statuses own truthful delivery.
    source = revised or {'artifact_id': target['artifact_id'], 'item_index': target['item_indices'][0]}
    return {'stage': 'execute_required', 'next_tool': 'generate_image',
            'content_source': source, 'target': copy.deepcopy(target),
            'message': '本次用户要求出图，但尚无本次执行回执；只读取或保存不能结束本次交付。'}


def incomplete_text(pending):
    if pending['stage'] == 'execute_required':
        return '本次要求的图片尚未提交，已保存的稿件仍保留。'
    if pending['stage'] == 'author_required':
        return '本次方案尚未完整保存，未提交图片任务。'
    return '本次要求的稿件修订尚未保存成功，没有提交新的图片任务。'
