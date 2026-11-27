"""An explicit current-input display directive, never an implicit latest image."""
from __future__ import annotations

import copy
import hashlib
import json

from services.task_execution import request_resolution as resolution
from services.task_execution.content_delivery import input_revision

MISSING = '这次还没有完成旧图的定位和展示，没有重新生成图片。'
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
    from services.task_execution.reference_boundary import NativeArguments, NativeToolCall

    refs = references(runtime, request)
    if not refs or not prepared.content_controls_available or content_tools.READ not in prepared.content_visible_tool_names:
        return False
    if not complete(runtime, request, blocks, refs):
        calls = []
        for ref in refs:
            resolution.check_display(runtime, request, ref)
            args = {'show_image': ref}
            identity = json.dumps([input_revision(runtime, request), ref], sort_keys=True)
            calls.append(NativeToolCall(name=content_tools.READ, arguments=NativeArguments(args),
                          call_id='display_' + hashlib.sha256(identity.encode()).hexdigest()[:24]))
        await execute(calls)
        if not complete(runtime, request, blocks, refs):
            await finish(FAILED)
            return True
    # Extra deliveries continue normally; a standalone re-display needs no
    # second sampling round that could claim success without doing the action.
    if not content_tools.requires_continued_delivery(request, runtime):
        return True
    return False


def selection_arguments(reference):
    return {'intent': 'explain', 'request_context': {'focus': 'selected_target',
        'image_action': 'display_existing', 'target': {'kind': 'image', 'basis': 'explicit_reference',
            'source_message_id': reference['message_id'], 'content_index': reference['content_index']}}}


def selection_rejection(runtime, request, reference):
    if action(runtime, request) != 'display_existing' or resolution.context(runtime) is None:
        return None
    if (not isinstance(reference, dict) or not isinstance(reference.get('message_id'), str)
            or type(reference.get('content_index')) is not int):
        return None
    selected = resolution.binding(runtime) or {}
    if (selected.get('status') == 'resolved'
            and selected.get('input_revision') == input_revision(runtime, request)):
        return None
    from services.task_execution.reference_boundary import for_runtime, NativeContext
    args = for_runtime(runtime).context(NativeContext(selection_arguments(reference)))
    return {'ok': False, 'accepted': False, 'code': 'REQUEST_TARGET_SELECTION_REQUIRED',
        'message': '已确认用户要求展示旧图，但尚未绑定具体图片，本次未展示。'
                   '核实候选后先单独选择目标；需要看图时先view_image。不要重新生成或换成其他图。',
        'recovery': {'tool': resolution.SELECT, 'arguments': args}}


def stage(runtime, request, blocks):
    if complete(runtime, request, blocks):
        return 'delivered'
    return 'ready_to_deliver' if references(runtime, request) else 'needs_selection'


def recovery_context(runtime, request, blocks):
    from services.task_execution.image_history import recovered, requires_view
    from services.task_execution.reference_boundary import for_runtime, NativeContext
    candidates = recovered(runtime)
    examples = [{'reference': image['reference'], 'requires_view_before_selection': requires_view(runtime, image['reference']),
                 'selection_arguments': selection_arguments(image['reference'])}
                for image in candidates[:20]]
    retry = runtime.checkpoint_base.get('_existing_image_recovery_v1', {})
    attempts = retry.get('attempts', 0) if retry.get('input_revision') == input_revision(runtime, request) else 0
    facts = {'continuation_attempt': attempts, 'previous_narrative_is_not_delivery': bool(attempts), 'intent': 'explain', 'image_action': 'display_existing', 'stage': stage(runtime, request, blocks),
        'candidates': examples, 'instruction':
            '本次要求实际展示已有图，尚未完成。查询、看图和口头承诺都不是交付。'
            '尚未找到候选用list_conversation_images，has_more=true可继续翻页；'
            '需要核图时用read_conversation_text(view_image)，再单独resolve_request_target选择用户指向的图片。'
            '候选示例不是默认选择，不因只有一张就省略用户语义核对。'
            '目标明确后后台按已校验目标展示；不要重新生成。'
            '确实需要用户选择时单独用respond_without_execution(purpose=clarification)提出具体问题；'
            '查询无结果、权限不足或文件不可用时如实说明，不能只说稍后发图。'}
    return '[Existing image delivery]\n' + json.dumps(for_runtime(runtime).context(NativeContext(facts)), ensure_ascii=False)


def offer(runtime, request, tools):
    mode = action(runtime, request)
    if mode not in {'display_existing', 'analyze'} or resolution.context(runtime) is None:
        return tools
    ready = mode == 'display_existing' and bool(references(runtime, request))
    result = []
    for original in tools:
        if original.get('function', {}).get('name') != 'read_conversation_text':
            result.append(original)
            continue
        tool = copy.deepcopy(original)
        params = tool['function']['parameters']
        if not ready:
            params['properties'].pop('show_image', None)
            params['oneOf'] = [form for form in params.get('oneOf', []) if 'show_image' not in form.get('required', [])]
            tool['function']['description'] += (' 本次仅分析，不提供show_image，使用view_image核图。' if mode == 'analyze' else ' 当前目标尚未绑定，show_image暂不提供。')
            tool['function']['description'] += (' 对需要展示的目标，'
                '先查询、必要时view_image核图，再单独resolve_request_target。绑定成功后开放展示或后台自动交付。')
        result.append(tool)
    return result


def can_continue(runtime, request, prepared):
    if runtime is None:
        return False
    journal = runtime.checkpoint_base.get('_existing_image_recovery_v1', {})
    count = journal.get('attempts', 0) if journal.get('input_revision') == input_revision(runtime, request) else 0
    state = getattr(runtime, 'task_execution_state', None)
    limit = min(2, state.correction_limit) if state else 2
    return (count < limit and not prepared.budget.stop_reason
            and (state is None or state.budget_rounds_used < state.model_round_limit))


def request_continuation(runtime, request, prepared, blocks):
    """Bounded, checkpointed retries when a model returns prose before delivery."""
    if not can_continue(runtime, request, prepared):
        return False
    key = '_existing_image_recovery_v1'
    revision = input_revision(runtime, request)
    journal = runtime.checkpoint_base.get(key, {})
    count = journal.get('attempts', 0) if journal.get('input_revision') == revision else 0
    runtime.checkpoint_base[key] = {'input_revision': revision, 'attempts': count + 1}
    draft = getattr(prepared, 'pending_display_narrative', None)
    if draft:
        prepared.messages.append({'role': 'assistant', 'content': draft})
    prepared.pending_display_narrative = None
    prepared.messages.append({'role': 'system', 'content': recovery_context(runtime, request, blocks)})
    return True
