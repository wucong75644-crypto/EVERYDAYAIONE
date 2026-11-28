"""Pre-dispatch revision barrier for a saved image recipe and new size intent.

Only structured request fields are compared. The author revises the text; this
module never rewrites a prompt or substitutes a different historical source.
"""
from __future__ import annotations

import json

from . import content_objects as co
from .content_delivery import input_revision

KEY = '_saved_image_revision_v1'
CODE = 'CONTENT_IMAGE_REVISION_REQUIRED'
MAX_REJECTIONS = 2


def approves_revision(state, draft, frame):
    """A size change plus explicit execution of these exact waiting slots."""
    if state is None or frame is None or draft.base is None or not draft.continue_execution:
        return False
    media = frame.media
    if media.tool != 'generate_image' or not media.approved:
        return False
    try:
        artifact, record, _ = co._load_record(state, draft.base)
        return (media.artifact_id == artifact.id and media.revision == record.revision
            and record.confirmation_tool == media.tool
            and set(media.approved) <= set(record.confirmation_items)
            and set(media.approved) == {update.item_index for update in draft.updates}
            and (not media.confirmation_only or any(size_changes(record.document.items[index - 1].options,
                frame.size_for(index)) for index in media.approved)))
    except (ValueError, KeyError, IndexError):
        return False


def bind_approval(runtime, request, before, draft, artifact, candidate):
    """Bind this input's explicit approval to its successfully saved revision.

    Waiting conditions stay in the immutable records. Only this event's proof
    advances, and only after the preflight and normal save have both succeeded.
    """
    from services.handlers.chat.content_tools import semantic_context
    frame = semantic_context(request, runtime)
    if not approves_revision(before, draft, frame):
        return None
    _, record, _ = co._load_record(candidate, {'artifact_id': artifact.id, 'item_index': draft.base.item_index})
    proof = {'owner_task_id': str(runtime.task_id), 'input_revision': input_revision(runtime, request),
        'previous_artifact_id': draft.base.artifact_id, 'previous_revision': frame.media.revision,
        'artifact_id': artifact.id, 'revision': record.revision, 'approved_items': sorted(frame.media.approved)}
    journal = runtime.checkpoint_base.setdefault(KEY, {})
    journal.update(input_revision=proof['input_revision'], approval_revision=proof)
    # A rejected direct execution may already have recorded the old approval.
    # Keep the old proof in the revision journal, without creating two grants
    # for one authenticated event in the existing media ledger.
    for existing in (runtime.checkpoint_base.get('_media_decisions_v1') or {}).get('items', ()):
        if (existing.get('owner_task_id') == proof['owner_task_id']
                and existing.get('artifact_id') == proof['previous_artifact_id']
                and existing.get('revision') == proof['previous_revision']
                and existing.get('tool') == 'generate_image'
                and existing.get('approved_items') == proof['approved_items']):
            proof['previous_decision'] = dict(existing)
            existing.update(artifact_id=artifact.id, revision=record.revision)
    return {'tool': 'generate_image', 'artifact_id': artifact.id, 'revision': record.revision,
            'item_indices': proof['approved_items']}


def revised_approval(runtime, request, value):
    proof = ((getattr(runtime, 'checkpoint_base', {}) or {}).get(KEY) or {}).get('approval_revision') or {}
    if not proof:
        return value
    if (proof.get('owner_task_id') == str(runtime.task_id)
            and proof.get('input_revision') == input_revision(runtime, request)
            and value.tool == 'generate_image'
            and proof.get('previous_artifact_id') == value.artifact_id
            and proof.get('previous_revision') == value.revision
            and proof.get('approved_items') == sorted(value.approved)):
        # The requested revision has now been saved; the remaining action is
        # execution of precisely the version approved by this same input.
        return value.model_copy(update={'artifact_id': proof['artifact_id'], 'revision': proof['revision'],
                                       'confirmation_only': True})
    return value


def size_changes(options, size):
    from services.adapters.kie.configs import IMAGE_MODEL_CONFIGS
    from services.handlers.chat_image_request import default_chat_image_model
    from services.handlers.image_size_requirements import normalize_approximate_pixels

    config = IMAGE_MODEL_CONFIGS[default_chat_image_model('image_to_image')]
    size = normalize_approximate_pixels(size, config)
    # Exact/unknown pixels retain the existing precision/unsupported-size gate.
    if 'width' in size or 'height' in size:
        return {}
    wanted = {key: size[key] for key in ('aspect_ratio', 'resolution') if key in size}
    if size.get('mode') == 'auto':
        wanted['aspect_ratio'] = 'auto'
    changes = {key: value for key, value in wanted.items()
               if options.get(key, '1K' if key == 'resolution' else 'auto') != value}
    if size.get('orientation') and 'aspect_ratio' not in wanted:
        try:
            w, h = map(int, options.get('aspect_ratio', '').split(':'))
        except (ValueError, TypeError):
            w = h = 0
        if (size['orientation'] == 'portrait' and not w < h
                or size['orientation'] == 'landscape' and not w > h):
            changes['orientation'] = size['orientation']
    return changes



def execution_requirement(runtime, request, reference, *, options, materials, revision):
    """One read-only decision for model availability and pre-dispatch validation."""
    from services.handlers.chat.content_tools import semantic_context, semantic_intent
    from .item_confirmation import directive
    frame = semantic_context(request, runtime)
    if frame is None or semantic_intent(request, runtime) not in {
            'execute_saved', 'repeat', 'edit', 'generate', 'write', 'write_then_submit_images'}:
        return None
    if semantic_intent(request, runtime) == 'generate' and (frame.target is None or frame.target.kind != 'content'):
        return None
    size = frame.size_for(reference.item_index)
    changes = size_changes(options, size)
    reason = '旧稿规格与本次要求不同，尚未提交图片。'
    target = frame.target
    if (semantic_intent(request, runtime) in {'write', 'write_then_submit_images', 'edit'}
            and target is not None and target.kind == 'content'
            and target.artifact_id == reference.artifact_id and target.revision == revision):
        changes['saved_revision'] = 'required'
        reason = '当前请求要求修订所选旧稿；先读取并保存修改，不能直接执行未修改的版本。'
    media = directive(request, runtime)
    if (media.tool == 'generate_image' and media.approved and not media.confirmation_only
            and reference.artifact_id == media.artifact_id and revision == media.revision
            and reference.item_index in media.approved):
        changes['saved_revision'] = 'required'
        reason = '本次已批准该条目，但要求先修订；不是等待用户确认。先读取并修订，保存成功后仅执行新版。'
    inputs = [co.Material.model_validate(m) if isinstance(m, dict) else m for m in materials]
    missing = [m.model_dump(mode='json', exclude_none=True) for m in (frame.materials or ())
        if m.usage == 'input' and m.reference not in [old.reference for old in inputs if old.usage == 'input']]
    if missing:
        changes['materials'] = missing
        reason = '旧稿未绑定本次明确选择的参考图，不能降为文生图；先读取并修订素材绑定。'
    return {'required_changes': changes, 'requested_size': size, 'message': reason} if changes else None


def pending_execution(runtime, request):
    """Plan only the resolved saved target; never select or approve another source."""
    from .request_resolution import binding, groups
    selected = binding(runtime) or {}
    target = selected.get('target') or {}
    if selected.get('status') != 'resolved' or target.get('kind') != 'content':
        return []
    group = next((g for g in groups(runtime) if g['artifact_id'] == target.get('artifact_id')
        and g['revision'] == target.get('revision')), None)
    if group is None:
        return []  # Unknown/stale targets retain the existing scope/version recovery.
    result = []
    for item in group['items']:
        index = item['item_index']
        if index not in target.get('item_indices', ()):
            continue
        reference = co.ContentReference(artifact_id=group['artifact_id'], item_index=index)
        inputs = group.get('item_inputs', ())
        requirement = execution_requirement(runtime, request, reference, options=item.get('options', {}),
            materials=inputs[index - 1] if len(inputs) >= index else (), revision=group['revision'])
        if requirement:
            result.append({'content_source': reference.model_dump(mode='json', exclude_none=True), **requirement})
    return result


def check(runtime, request, calls, state):
    """Reject an unchanged execution or incomplete revision before adoption/IO."""
    from services.handlers.chat.content_tools import semantic_context, semantic_intent

    frame = semantic_context(request, runtime)
    if state is None or frame is None or semantic_intent(request, runtime) not in {
            'execute_saved', 'repeat', 'edit', 'generate', 'write', 'write_then_submit_images'}:
        return {}
    if semantic_intent(request, runtime) == 'generate' and (frame.target is None or frame.target.kind != 'content'):
        return {}
    size = frame.size_for()
    errors = {}
    for call in calls:
        try:
            args = json.loads(call['arguments'])
            if call['name'] == 'generate_image' and isinstance(args.get('content_source'), dict):
                reference = co.ContentReference.model_validate({key: args['content_source'][key]
                    for key in ('artifact_id', 'item_index', 'sha256') if key in args['content_source']})
                _, record, item = co._load_record(state, reference)
                requirement = execution_requirement(runtime, request, reference, options=item.options,
                    materials=item.materials, revision=record.revision)
                if requirement is None:
                    continue
                size = requirement['requested_size']
                changes = requirement['required_changes']
                reason = requirement['message']
            elif call['name'] == co.SAVE and args.get('base'):
                draft = co.SaveContent.model_validate(args)
                _, document, _ = co.load(state, draft.base)
                for update in draft.updates:
                    if update.item_index > len(document.items):
                        continue  # Existing save validation owns invalid indices.
                    item = document.items[update.item_index - 1]
                    size = frame.size_for(update.item_index)
                    changes = size_changes(item.options, size)
                    patch = update.item
                    revised_materials = patch.materials if patch.materials is not None else item.materials
                    missing = [material.model_dump(mode='json', exclude_none=True) for material in (frame.materials or ())
                        if material.usage == 'input' and material.reference not in [m.reference for m in revised_materials]]
                    if missing:
                        changes['materials'] = missing
                        reference = draft.base.model_copy(update={'item_index': update.item_index})
                        reason = '修订尚未绑定本次明确选择的参考图；在materials按真实引用填写input素材。'
                        break
                    if not changes:
                        continue
                    revised = patch.options if patch.options is not None else item.options
                    changed_keys = set(changes) | ({'aspect_ratio'} if 'orientation' in changes else set())
                    unchanged = {key: value for key, value in item.options.items() if key not in changed_keys}
                    if (size_changes(revised, size)
                            or any(revised.get(key) != value for key, value in unchanged.items())
                            or patch.body is None or patch.body == item.body
                            or item.display_prefix and patch.display_prefix is None):
                        reference = draft.base.model_copy(update={'item_index': update.item_index})
                        reason = '修订尚未完整：保留未修改规格，并同步修改正文及原有展示说明中的画布要求。'
                        break
                else:
                    continue
            else:
                continue
            if not changes:
                continue
        except (ValueError, TypeError, KeyError):
            continue  # Shape, source scope and version errors keep their owners.
        errors[call['id']] = {'ok': False, 'accepted': False, 'code': CODE,
            'message': reason + co.IMAGE_REVISION_GUIDANCE,
            'content_source': reference.model_dump(mode='json', exclude_none=True),
            'saved_options': item.options, 'requested_size': size,
            'required_changes': changes,
            'recovery': {'steps': ['read_saved_item', 'save_revision', 'execute_saved_revision'],
                'read': {'tool': 'read_conversation_text', 'arguments': {
                    'content_source': reference.model_dump(mode='json', exclude_none=True)}},
                'save': {'tool': co.SAVE, 'base': reference.model_dump(mode='json', exclude_none=True)},
                'execute': {'tool': 'generate_image', 'source': 'successful_save_receipt'}}}
    if errors:
        previous = runtime.checkpoint_base.get(KEY) or {}
        revision = input_revision(runtime, request)
        count = (previous.get('rejections', 0) if previous.get('input_revision') == revision else 0) + 1
        runtime.checkpoint_base[KEY] = {**(previous if previous.get('input_revision') == revision else {}),
                                      'input_revision': revision, 'rejections': count}
        if count > MAX_REJECTIONS:
            for result in errors.values():
                result.update(code='CONTENT_IMAGE_REVISION_STOPPED', retryable=False,
                    message='旧稿规格自动修订未完成，本次未提交图片。请核对方案修订结果后继续。')
    return errors
