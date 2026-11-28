"""Server-observed reads before model revisions; no model-authored read claims."""
import copy
import json

from .methods import content_format_contract

def input_revision(runtime, request):
    from .request_window import input_revision as identity
    return identity(runtime, request)


KEY = '_content_revision_reads_v1'
READ_GUIDANCE = (
    '修改已有稿件必须先单独用read_conversation_text的content_source读取每个待修改条目。'
    '读取回执同时给出完整item和format_contract；按旧稿要求修订，不以当前是否激活Skill决定旧稿格式。'
    'content_source绑定所选稿件及版本，后台按该来源解析原任务；读取其他稿件不改变本次选择。'
    '后台核验本次真实读取记录；历史摘要、示例和模型声称已读取均不能代替读取回执。'
    '未修改字段省略，由后台保留；不能用概述代替原稿详细内容。'
    '保存后需要再次修改新版本时先读取新版本；直接执行新回执无需重复读取。'
)


def _journal(runtime):
    value = runtime.checkpoint_base.get(KEY, {})
    return value if value.get('owner_task_id') == str(runtime.task_id) else {}


def record_read(runtime, request, artifact, record, item_index):
    revision = input_revision(runtime, request)
    journal = _journal(runtime)
    if journal.get('input_revision') != revision:
        journal = {'version': 1, 'owner_task_id': str(runtime.task_id), 'input_revision': revision, 'items': {}}
    else:
        journal = copy.deepcopy(journal)
    key = f'{artifact.id}:{item_index}'
    fresh = key not in journal['items']
    journal['items'][key] = {
        'artifact_id': artifact.id, 'item_index': item_index, 'sha256': artifact.sha256,
        'revision': record.revision, 'object_key': record.object_key,
        'has_presentation': bool(record.document.items[item_index - 1].display_prefix),
        'format_contract': content_format_contract(record.presentation_profile)}
    runtime.checkpoint_base[KEY] = journal
    if fresh:
        _progress(runtime)


def _progress(runtime):
    # Reuse the existing bounded delivery correction. Only a new verified read
    # or committed-save candidate advances it; repeated calls cannot reset it.
    from .content_delivery import KEY as delivery_key
    delivery = runtime.checkpoint_base.get(delivery_key)
    if delivery and delivery.get('control_rejections'):
        runtime.checkpoint_base[delivery_key] = {**delivery, 'control_rejections': 0}


def record_save(runtime, request, value, artifact):
    """Written in the same checkpoint as the candidate; readers verify its hash."""
    if value.base is None:
        return
    revision = input_revision(runtime, request)
    journal = copy.deepcopy(_journal(runtime))
    if journal.get('input_revision') != revision:
        journal = {'version': 1, 'owner_task_id': str(runtime.task_id), 'input_revision': revision, 'items': {}}
    proof = {'base_artifact_id': value.base.artifact_id, 'artifact_id': artifact.id,
             'sha256': artifact.sha256, 'item_indices': [u.item_index for u in value.updates]}
    saved = journal.setdefault('saved', [])
    if proof not in saved:
        saved.append(proof)
        _progress(runtime)
    runtime.checkpoint_base[KEY] = journal


def selected_format(runtime):
    """Prefer the selected saved resource, independent of active authoring Skill."""
    from .content_objects import ContentRecord, objects
    from .request_resolution import binding
    target = (binding(runtime) or {}).get('target') or {}
    if target.get('kind') != 'content':
        return None
    artifact_id = target['artifact_id']
    from .content_source import state_for
    states = [state_for(runtime, {'artifact_id': artifact_id, 'item_index': target.get('item_indices', [1])[0]})]
    for state in states:
        if state is None:
            continue
        artifact = next((a for a in objects(state) if a.id == artifact_id), None)
        if artifact is not None:
            record = ContentRecord.model_validate_json(artifact.inline_text)
            return content_format_contract(record.presentation_profile)
    for proof in _journal(runtime).get('items', {}).values():
        if proof['artifact_id'] == artifact_id:
            return copy.deepcopy(proof['format_contract'])
    return None  # Unknown until scoped read; never infer a profile from titles.


def check_call(runtime, request, call):
    """Before adoption and before saving; original idempotent results stay replayable."""
    from .content_objects import SAVE, SaveContent, _load_record, is_current, catalog
    if call['name'] != SAVE:
        return None
    try:
        value = SaveContent.model_validate_json(call['arguments'])
    except (ValueError, TypeError):
        return None  # Existing argument validation owns malformed JSON.
    if value.base is None:
        return None
    from .content_source import state_for
    state = state_for(runtime, value.base)
    if state and any(b.call_id == call['id'] for b in state.call_bindings):
        return None  # save() verifies the original call hash, never saves anew.
    if state is not None:
        try:
            _, record, _ = _load_record(state, value.base)
        except ValueError:
            record = None  # The native loader reports invalid identities.
        if record is not None and not is_current(state, value.base.artifact_id):
            latest = next((row for row in catalog(state) if row['object_key'] == record.object_key), None)
            return {'ok': False, 'accepted': False, 'code': 'CONTENT_BASE_VERSION_CHANGED',
                'message': '所选稿件已有新版本，本次未修改。核对用户指定版本和当前目录后重新选择、读取；不自动换成最新版本。',
                'recovery': {'action': 'verify_version_then_read', 'tool': 'read_conversation_text',
                    'available_content_sources': ([{'artifact_id': latest['artifact_id'], 'item_index': u.item_index}
                        for u in value.updates if u.item_index <= len(latest['items'])] if latest else [])}}
    needed = missing_reads(runtime, request, value.base, [u.item_index for u in value.updates])
    if not needed:
        return None
    return {'ok': False, 'accepted': False, 'code': 'CONTENT_READ_REQUIRED',
        'message': '尚未保存、接回任务或提交生成。' + READ_GUIDANCE,
        'issues': [{'path': '$.base', 'reason': '缺少当前输入下所选版本及修改条目的成功读取记录'}],
        'recovery': {'action': 'read_before_revision', 'tool': 'read_conversation_text', 'calls': needed}}


def missing_reads(runtime, request, reference, indices):
    from .content_objects import _load_record
    from .content_source import state_for
    state = state_for(runtime, reference)
    journal = _journal(runtime)
    proofs = journal.get('items', {}) if journal.get('input_revision') == input_revision(runtime, request) else {}
    needed = []
    for index in indices:
        proof = proofs.get(f'{reference.artifact_id}:{index}')
        valid = proof is not None
        if valid and reference.sha256 is not None:
            valid = reference.sha256 == proof['sha256']
        if valid and state is not None:
            try:
                artifact, record, _ = _load_record(state, {
                    'artifact_id': reference.artifact_id, 'item_index': index})
                valid = (artifact.sha256 == proof['sha256'] and record.revision == proof['revision'])
            except ValueError:
                valid = False
        if not valid:
            needed.append({'content_source': {'artifact_id': reference.artifact_id, 'item_index': index}})
    return needed


def pending_reads(runtime, request):
    from .request_resolution import binding
    from .content_objects import ContentReference
    target = (binding(runtime) or {}).get('target') or {}
    if target.get('kind') != 'content' or not target.get('item_indices'):
        return []
    return missing_reads(runtime, request, ContentReference(
        artifact_id=target['artifact_id'], item_index=target['item_indices'][0]), target['item_indices'])


def public_fields(runtime, value):
    """Project diagnostic metadata only; never rewrite authored text or IDs."""
    public = copy.deepcopy(value)
    if (selected_format(runtime) or {}).get('profile_id') == 'reference-prompts-v1':
        def visit(node):
            if isinstance(node, list):
                for item in node:
                    visit(item)
            elif isinstance(node, dict):
                if 'required_update_fields' in node:
                    node['required_update_fields'] = [{'body': 'prompt', 'display_prefix': 'presentation'}.get(key, key)
                        for key in node['required_update_fields']]
                for key in ('items', 'pending_action', 'recovery'):
                    if key in node:
                        visit(node[key])
        visit(public)
    return public


def model_view(runtime, value):
    """One public field projection for stage messages and tool contracts."""
    from .image_reference_catalog import for_runtime
    from .reference_boundary import ModelReferenceBoundary
    return ModelReferenceBoundary(for_runtime(runtime)).value(public_fields(runtime, value))


def offer(runtime, request, tools):
    """Expose saved-text actions only after their actual prerequisites are met."""
    from .content_objects import SAVE
    from .content_action import evaluate
    view = evaluate(runtime, request)
    needed = view.reads
    source_error = getattr(runtime, '_content_source_error', None)
    if source_error is not None:
        result = []
        for original in tools:
            name = original.get('function', {}).get('name')
            if name in {SAVE, 'generate_image'}:
                continue
            tool = copy.deepcopy(original)
            if name == 'read_conversation_text':
                tool['function']['description'] += ' 当前指定稿件来源尚未核实：' + json.dumps(source_error.repair(), ensure_ascii=False)
            result.append(tool)
        return result
    revisions = view.revisions
    if not needed and not revisions:
        return tools
    workflow = {'stage': 'read_required' if needed else 'revision_required',
        'read_required_for_revision': bool(needed),
        'read_calls': [{'tool': 'read_conversation_text', 'arguments': args} for args in needed],
        'items': revisions, 'next_tool': 'read_conversation_text' if needed else SAVE,
        'steps': ['read_saved_item', 'save_revision', 'execute_saved_revision']}
    # Convert the complete object, not just field names embedded in prose.
    public = model_view(runtime, workflow)
    from services.handlers.chat.content_tools import semantic_intent
    public['continue_execution'] = semantic_intent(request, runtime) != 'write'
    from .request_resolution import binding
    selected_indices = set(((binding(runtime) or {}).get('target') or {}).get('item_indices', ()))
    blocked_indices = {row['content_source']['item_index'] for row in revisions}
    result = []
    for original in tools:
        function = original.get('function', {})
        name = function.get('name')
        if needed and name == SAVE:
            continue
        if revisions and name == 'generate_image' and selected_indices <= blocked_indices:
            continue
        tool = copy.deepcopy(original)
        if revisions and name == 'generate_image':
            # Mixed selections can still execute their already-ready items.
            source = tool['function']['parameters']['properties']['content_source']
            source['not'] = {'required': ['item_index'],
                'properties': {'item_index': {'enum': sorted(blocked_indices)}}}
        if name == SAVE:
            tool['function']['_content_workflow'] = {'revision_required': bool(revisions), 'state': public}
        if name == 'read_conversation_text':
            if revisions:
                tool['function']['description'] += (
                    ' 当前已选稿件的修订流程（后台核验状态，不是新的选图建议）：'
                    + json.dumps(public, ensure_ascii=False)
                    + '。read_calls里的arguments可直接调用；成功读取后按本工具JSON结构修订，'
                    '是否生成仍以本次用户要求为准；要求生成时仅用保存成功的新回执，不调用旧版本。')
            else:
                tool['function']['description'] += (
                    ' 仅在本次需要修订所选旧稿时，先按以下已核验参数读取：'
                    + json.dumps(public['read_calls'], ensure_ascii=False)
                    + '。原样执行旧稿无需额外读取或保存；仅查看原文不要求修订或生成。')
        result.append(tool)
    return result


def materialize_sections(runtime, request, call):
    """Merge section deltas only after a server-observed read of this version."""
    from . import content_objects as co
    from .contracts import TaskError
    from .reference_prompt_format import PROFILE, encode_item, decode_save
    from .reference_boundary import NativeArguments, NativeToolCall
    if call['name'] != co.SAVE:
        return call
    try:
        raw = json.loads(call['arguments'])
    except (ValueError, TypeError):
        return call  # Preserve the normal malformed-JSON repair contract.
    if not isinstance(raw, dict) or not any(isinstance(u, dict) and isinstance(u.get('item'), dict)
            and 'reference_sections' in u['item'] for u in (raw.get('updates') if isinstance(raw.get('updates'), list) else ())):
        return call
    value = co.SaveContent.model_validate(raw)
    rejection = check_call(runtime, request, call)
    if rejection is not None:
        raise TaskError(rejection['code'])
    from .content_source import state_for
    state = state_for(runtime, value.base)
    artifact, record, _ = co._load_record(state, value.base)
    if not co.is_current(state, artifact.id):
        raise TaskError('CONTENT_BASE_VERSION_CHANGED')
    if record.presentation_profile != PROFILE:
        raise TaskError('CONTENT_SECTION_PROFILE_MISMATCH')
    arguments = json.loads(call['arguments'])
    for update, raw in zip(value.updates, arguments['updates']):
        patch = update.item.reference_sections
        if patch is None:
            continue
        if update.item_index > len(record.document.items):
            raise TaskError('CONTENT_UPDATE_INVALID')
        old = record.document.items[update.item_index - 1]
        original = encode_item(old.model_dump(mode='json'))
        delta = raw['item'].copy()
        delta.pop('reference_sections')
        for name in patch.model_fields_set:
            delta[name] = {**original[name], **getattr(patch, name)}
        normalized = decode_save({'base': arguments['base'], 'updates': [
            {'item_index': update.item_index, 'item': delta}]}, lambda *_: old.title)
        raw['item'] = normalized['updates'][0]['item']
    return NativeToolCall(name=call['name'], arguments=NativeArguments(arguments), call_id=call['id'])


def saved_reference(runtime, request, target):
    from . import content_objects as co
    journal = _journal(runtime)
    if journal.get('input_revision') != input_revision(runtime, request):
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
        if (artifact and artifact.sha256 == proof['sha256']
                and co.ContentRecord.model_validate_json(artifact.inline_text).base_artifact_id == identity
                and co.ContentRecord.model_validate_json(artifact.inline_text).object_key == record.object_key):
            identity = artifact.id
            covered.update(proof['item_indices'])
    if identity != target['artifact_id'] and required <= covered and co.is_current(state, identity):
        return {'artifact_id': identity, 'item_index': target['item_indices'][0]}
    return None
