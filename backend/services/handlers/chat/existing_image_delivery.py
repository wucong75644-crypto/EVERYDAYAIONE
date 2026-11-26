"""An explicit current-input display directive, never an implicit latest image."""
from __future__ import annotations

import hashlib
import json

from services.task_execution import request_resolution as resolution
from services.task_execution.content_delivery import input_revision

MISSING = '这次还没有展示图片。请明确要查看哪一张，例如哪一轮或哪一个方案的图片。'
FAILED = '这次未能展示所选图片，没有重新生成。所选图片暂时无法读取或展示条件未通过，请稍后重试。'


def action(runtime, request):
    projection = getattr(runtime, 'workflow_projection', None) or (getattr(request, 'params', None) or {}).get('_ecom_workflow') or {}
    if projection.get('intent') != 'explain':
        return None
    return (projection.get('request_context') or {}).get('image_action')


def references(runtime, request):
    """Use only the frozen, validated selection for this exact input."""
    if action(runtime, request) != 'display_existing':
        return []
    selected = resolution.binding(runtime)
    if (not selected or selected.get('status') != 'resolved'
            or selected.get('input_revision') != input_revision(runtime, request)):
        return []
    target = selected['target']
    if target['kind'] == 'image':
        return [{'message_id': target['source_message_id'], 'content_index': target['content_index']}]
    context = resolution.context(runtime) or {}
    if target['kind'] == 'request':
        row = next((r for r in context.get('requests', ()) if r['message_id'] == target['source_message_id']), {})
        # A request containing multiple images does not identify one of them.
        images = row.get('images', ())
        return [images[0]['reference']] if len(images) == 1 else []
    if target['kind'] == 'content':
        group = next((g for g in resolution.groups(runtime) if g['artifact_id'] == target['artifact_id']), {})
        if group.get('revision') != target['revision']:
            return []
        refs = []
        for index in target['item_indices']:
            item = next((i for i in group.get('items', ()) if i['item_index'] == index), {})
            results = [i for i in context.get('image_results', ()) if i.get('task_id') and i['task_id'] == item.get('image_task_id')]
            if len(results) != 1:
                return []
            ref = {'message_id': results[0]['source_message_id'], 'content_index': results[0]['content_index']}
            if ref not in refs:
                refs.append(ref)
        return refs
    return []


def complete(runtime, request, blocks, refs=None):
    """A receipt and the actual reply blocks must agree, including on recovery."""
    refs = references(runtime, request) if refs is None else refs
    record = (getattr(runtime, 'checkpoint_base', {}) or {}).get('_displayed_image_delivery_v1') or {}
    if (not refs or record.get('owner_task_id') != str(runtime.task_id)
            or record.get('input_revision') != input_revision(runtime, request)):
        return False
    delivered = record.get('images') or [{'message_id': record.get('source_message_id'),
                                        'content_index': record.get('source_content_index')}]
    return all(ref in delivered and any(
        b.get('type') == 'image' and b.get('url')
        and b.get('source_message_id') == ref['message_id']
        and b.get('source_content_index') == ref['content_index'] for b in blocks) for ref in refs)


async def deliver(runtime, request, prepared, blocks, *, execute, finish):
    """Reuse the original dispatcher, permission checks, checkpoint and sink."""
    from . import content_tools
    from services.task_execution.image_reference_catalog import for_runtime

    refs = references(runtime, request)
    if not refs or not prepared.content_controls_available or content_tools.READ not in prepared.content_visible_tool_names:
        return False
    if not complete(runtime, request, blocks, refs):
        encoded = for_runtime(runtime)
        calls = []
        for ref in refs:
            resolution.check_display(runtime, request, ref)
            args = {'show_image': ref}
            if encoded:
                args = encoded.encode(args)
            identity = json.dumps([input_revision(runtime, request), ref], sort_keys=True)
            calls.append({'name': content_tools.READ, 'arguments': json.dumps(args),
                          'id': 'display_' + hashlib.sha256(identity.encode()).hexdigest()[:24]})
        await execute(calls)
        if not complete(runtime, request, blocks, refs):
            await finish(FAILED)
            return True
    # Extra deliveries continue normally; a standalone re-display needs no
    # second sampling round that could claim success without doing the action.
    if not content_tools.requires_continued_delivery(request, runtime):
        return True
    return False
