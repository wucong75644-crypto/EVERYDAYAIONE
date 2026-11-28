"""Server-observed reads before model revisions; no model-authored read claims."""
import copy
import json

from .contracts import TaskState
from .methods import content_format_contract

def input_revision(runtime, request):
    from .reducer import digest
    from .request_resolution import context
    checkpoint = runtime.checkpoint_base
    changes = getattr(getattr(runtime, 'task_user_changes', None), 'items', ())
    current = [item for item in changes if str(item.owner_task_id) == str(runtime.task_id)]
    followup = checkpoint.get('_task_followup') or {}
    original = (checkpoint.get('_task_followup_input_checkpoint') or {}).get('_task_user_changes') or {}
    prior_ids = {item['event_id'] for item in original.get('items', ())}
    # Adoption records the already-authenticated input as one new user-change
    # event. It is the same input, not a new steer invalidating its own read.
    synthetic = next((item.event_id for item in current
        if str(item.event_id) not in prior_ids and item.message_sha256 == followup.get('input_sha256')), None)
    anchor = getattr(request, 'context_anchor', None)
    return digest([str(getattr(anchor, 'input_message_id', '') or ''), context(runtime),
        [(str(item.event_id), item.message_sha256) for item in current if item.event_id != synthetic]])


KEY = '_content_revision_reads_v1'
READ_GUIDANCE = (
    '修改已有稿件必须先单独用read_conversation_text的content_source读取每个待修改条目。'
    '读取回执同时给出完整item和format_contract；按旧稿要求修订，不以当前是否激活Skill决定旧稿格式。'
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
    journal['items'][f'{artifact.id}:{item_index}'] = {
        'artifact_id': artifact.id, 'item_index': item_index, 'sha256': artifact.sha256,
        'revision': record.revision, 'object_key': record.object_key,
        'format_contract': content_format_contract(record.presentation_profile)}
    runtime.checkpoint_base[KEY] = journal


def selected_format(runtime):
    """Prefer the selected saved resource, independent of active authoring Skill."""
    from .content_objects import ContentRecord, objects
    from .request_resolution import binding
    target = (binding(runtime) or {}).get('target') or {}
    if target.get('kind') != 'content':
        return None
    artifact_id = target['artifact_id']
    states = [getattr(runtime, 'task_execution_state', None)]
    candidate = getattr(runtime, 'task_followup_candidate', None)
    if candidate and candidate.get('state'):
        states.append(TaskState.model_validate(candidate['state']))
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
    state = getattr(runtime, 'task_execution_state', None)
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
    state = getattr(runtime, 'task_execution_state', None)
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


def offer(runtime, request, tools):
    """Expose saved-text actions only after their actual prerequisites are met."""
    from .content_objects import SAVE
    from .saved_image_revision import pending_execution
    from .image_reference_catalog import for_runtime
    from .reference_boundary import ModelReferenceBoundary
    needed = pending_reads(runtime, request)
    revisions = pending_execution(runtime, request)
    if not needed and not revisions:
        return tools
    boundary = ModelReferenceBoundary(for_runtime(runtime))
    workflow = {'stage': 'read_required' if needed else 'revision_required',
        'read_required_for_revision': bool(needed),
        'read_calls': [{'tool': 'read_conversation_text', 'arguments': args} for args in needed],
        'items': revisions, 'next_tool': 'read_conversation_text' if needed else SAVE,
        'steps': ['read_saved_item', 'save_revision', 'execute_saved_revision']}
    # Convert the complete object, not just field names embedded in prose.
    public = boundary.value(workflow)
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
        if name in {'read_conversation_text', SAVE}:
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
    from .direct_loop import content_followup_state
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
    state = runtime.task_execution_state or content_followup_state(runtime)
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
