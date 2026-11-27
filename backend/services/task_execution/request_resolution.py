"""Bind semantic targets to real requests before restoring or dispatching work.

The model interprets language. This module compares identities and versions;
neither user words nor document titles are routing rules.
"""
from __future__ import annotations

import copy
import json
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictInt, field_validator, model_validator

from .contracts import Fact, TaskError, TaskState
from . import action_target_contract

CONTEXT = '_request_context_v1'
KEY = '_request_resolution_v1'
RESPOND = 'respond_without_execution'
SELECT = 'resolve_request_target'
RESPONSE_SCHEMA = {'type': 'function', 'function': {'name': RESPOND,
    'description': '缺少信息或目标有歧义时，单独提交本轮澄清问题或解释并结束本轮，不执行业务。'
                   '仅填写需要用户回答的问题或事实说明，不声称已受理/生成。写方案用保存正文工具。',
    'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['purpose', 'text'],
                   'properties': {'purpose': {'type': 'string', 'enum': ['clarification', 'explanation']},
                                  'text': {'type': 'string', 'minLength': 1, 'maxLength': 4000}}}}}


class Target(Fact):
    model_config = {**Fact.model_config, 'json_schema_extra': {'oneOf': [
        {'properties': {'kind': {'const': kind}}, 'required': ['kind', 'basis', *required]}
        for kind, required in (
            ('current', ()), ('request', ('source_message_id',)),
            ('content', ('artifact_id', 'revision', 'item_indices')),
            ('image', ('source_message_id', 'content_index')), ('ambiguous', ()))
    ]}}
    kind: Literal['current', 'request', 'content', 'image', 'ambiguous']
    basis: Literal['current_input', 'active_request', 'explicit_reference', 'ambiguous']
    source_message_id: str | None = Field(default=None, description='图片复制target_fields.source_message_id；稿件复制source_request.message_id。不是轮次数或文件ID。')
    artifact_id: str | None = Field(default=None, min_length=1, max_length=200)
    revision: int | None = Field(default=None, strict=True, ge=1)
    item_indices: tuple[StrictInt, ...] = Field(default=(), max_length=100)
    content_index: int | None = Field(default=None, strict=True, ge=0, description='复制target_fields.content_index，不自行按图片数量计数。')

    @field_validator('source_message_id')
    @classmethod
    def message_identity(cls, value):
        return str(UUID(value)) if value is not None else None

    @model_validator(mode='after')
    def identity(self):
        if self.kind in {'request', 'image'} and self.source_message_id is None:
            raise ValueError('REQUEST_TARGET_MESSAGE_REQUIRED')
        if self.kind == 'content':
            if not self.artifact_id or self.revision is None or not self.item_indices:
                raise ValueError('REQUEST_TARGET_CONTENT_REQUIRED')
        elif self.artifact_id is not None or self.revision is not None or self.item_indices:
            raise ValueError('REQUEST_TARGET_CONTENT_CONFLICT')
        if any(type(i) is not int or not 1 <= i <= 100 for i in self.item_indices) or len(set(self.item_indices)) != len(self.item_indices):
            raise ValueError('REQUEST_TARGET_ITEMS_INVALID')
        if (self.kind == 'image') != (self.content_index is not None):
            raise ValueError('REQUEST_TARGET_IMAGE_CONFLICT')
        if (self.kind == 'ambiguous') != (self.basis == 'ambiguous'):
            raise ValueError('REQUEST_TARGET_BASIS_INVALID')
        if self.kind == 'current' and self.basis != 'current_input':
            raise ValueError('REQUEST_TARGET_BASIS_INVALID')
        if self.kind not in {'current', 'image', 'ambiguous'} and self.basis == 'current_input':
            raise ValueError('REQUEST_TARGET_BASIS_INVALID')
        return self


def selection_schema(runtime=None):
    from services.agent.image.ecommerce_planner.workflow import IMAGE_INTENTS, ImageRequestContext
    frame = ImageRequestContext.model_json_schema()
    definitions = frame.pop('$defs', {})
    if runtime is not None and (context(runtime) or {}).get('catalog_version') == 2:
        for branch in definitions['Target']['oneOf']:
            if branch['properties']['kind']['const'] == 'content':
                branch['required'].append('source_message_id')
        # This control repairs a target, not unrelated delivery authorization.
        # Unmentioned validated directives remain with the existing workflow.
        for key in ('media', 'deliveries', 'continue_delivery', 'platform_changed', 'platform'):
            frame['properties'].pop(key, None)
    frame['required'] = ['target', 'focus']
    return {'type': 'function', 'function': {'name': SELECT,
        'description': '核对最近请求或读取来源后，明确选择/纠正本次动作与目标。只单独调用，不执行业务；'
                       'request_context填写所选需求的规格、素材和target，不沿用无关旧方案。intent=explain时用image_action=analyze表示分析，display_existing表示实际向用户展示已有图；改成其他意图时image_action填null。未准备执行稿、未派发且没有接回旧任务时可纠正。'
                       + action_target_contract.SELECTION_GUIDANCE,
        'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['intent', 'request_context'],
                       'allOf': action_target_contract.schema_rules(), 'examples': action_target_contract.selection_examples(),
                       '$defs': definitions,
                       'properties': {'intent': {'type': 'string', 'enum': list(IMAGE_INTENTS)}, 'request_context': frame}}}}


async def select(runtime, request, prepared, args):
    from services.agent.image.ecommerce_planner.workflow import ImageRequestContext
    workflow = getattr(prepared, 'ecom_workflow', None)
    raw_frame = args['request_context']
    if (context(runtime) or {}).get('catalog_version') == 2 and workflow and workflow.value:
        previous = workflow.value.request_context
        merged = previous.model_dump(mode='json', exclude_none=True) if previous else {}
        old_target = Target.model_validate(merged['target']).model_dump(exclude={'basis'}) if merged.get('target') else None
        new_target = Target.model_validate(raw_frame['target']).model_dump(exclude={'basis'}) if raw_frame.get('target') else None
        if old_target != new_target:
            for field in ('size', 'size_rules', 'canvas_image', 'canvas_resource', 'materials', 'reference_usage'):
                merged.pop(field, None)
            # Approval belongs to an exact saved object, never to a corrected
            # image/request. Waiting conditions may survive an alias change
            # within the same request, but cannot cross request ownership.
            def source(target):
                if target and target['kind'] == 'current':
                    return context(runtime)['input_message_id']
                return (target or {}).get('source_message_id')
            media = merged.get('media') or {}
            old_source, new_source = source(old_target), source(new_target)
            if (media.get('artifact_id') or media.get('approved')
                    or old_source and new_source and old_source != new_source):
                merged.pop('media', None)
        raw_frame = {**merged, **raw_frame}
    if args['intent'] != 'explain':
        raw_frame = {**raw_frame, 'image_action': None}
    frame = ImageRequestContext.model_validate(raw_frame)
    if frame.target is None or frame.focus is None or workflow is None or workflow.value is None or workflow.value.kind != 'ordinary_image':
        raise TaskError('REQUEST_TARGET_CONTROL_UNAVAILABLE')
    state = getattr(runtime, 'task_execution_state', None)
    if state is not None:
        from .content_objects import objects
        if str(state.root_task_id) != str(runtime.task_id) or state.operations or objects(state):
            raise TaskError('REQUEST_TARGET_EXECUTION_ALREADY_BOUND')
    old = copy.deepcopy(binding(runtime))
    old_value = workflow.value
    try:
        value = freeze_frame(runtime, request, frame, args['intent'])
        workflow.value = workflow.value.model_copy(update={'intent': args['intent'], 'request_context': frame,
            'input_revision': value['input_revision'], 'image_count': None})
        await workflow.save()
        workflow._coordinated = False
    except BaseException:
        workflow.value = old_value
        if old is None:
            runtime.checkpoint_base.pop(KEY, None)
        else:
            runtime.checkpoint_base[KEY] = old
        raise
    return {'ok': True, 'accepted': False, 'status': 'target_selected',
            'resolution': model_context(runtime)['resolution']}


def context(runtime):
    return (getattr(runtime, 'checkpoint_base', {}) or {}).get(CONTEXT)


def binding(runtime):
    return (getattr(runtime, 'checkpoint_base', {}) or {}).get(KEY)


def groups(runtime):
    from .content_objects import catalog
    from .direct_loop import followup_source
    result = []
    state = getattr(runtime, 'task_execution_state', None)
    source = followup_source(runtime)
    if state is None and source:
        state = TaskState.model_validate(source['state'])
    if state is not None:
        creators = {a.id: str(a.source_task_id) for a in state.artifacts}
        from .content_objects import ContentRecord, objects
        documents = {a.id: ContentRecord.model_validate_json(a.inline_text) for a in objects(state)}
        for group in catalog(state, for_model=True):
            record = documents[group['artifact_id']]
            inputs = [[m.model_dump(mode='json') for m in item.materials if m.usage == 'input']
                      for item in record.document.items]
            result.append({**group, 'root_task_id': str(state.root_task_id),
                           'creation_task_id': creators[group['artifact_id']], 'item_inputs': inputs})
    result.extend(copy.deepcopy(getattr(runtime, 'task_content_groups', ())))
    unique = {}
    for group in result:
        unique.setdefault((group['artifact_id'], group['revision']), group)
    return list(unique.values())


def active_request(snapshot):
    """Ordering only selects focus among recorded actions, never parses prose."""
    current = snapshot['input_message_id']
    for row in snapshot['requests']:
        if row['message_id'] == current:
            continue
        previous = row.get('resolution')
        focus = row.get('focus') or (previous or {}).get('focus')
        if focus == 'retain_previous':
            continue
        if focus == 'selected_target' and previous and previous.get('status') == 'resolved':
            if row.get('has_execution_state'):
                return row['message_id']
            return previous['target'].get('source_message_id', row['message_id'])
        # Legacy/unknown focus is visible as a recent-request barrier. It is
        # never silently skipped just because discussion did not create work.
        return row['message_id']
    return None


def native_context(runtime):
    snapshot = context(runtime)
    if snapshot is None:
        return None
    def public_resolution(value):
        if not value:
            return None
        return {'status': value['status'], 'focus': value.get('focus'),
                **({'rejection': copy.deepcopy(value['rejection'])} if value.get('rejection') else {}),
                'target': {key: val for key, val in value['target'].items() if key in Target.model_fields}}
    requests = []
    for row in snapshot['requests']:
        public = {key: copy.deepcopy(val) for key, val in row.items() if key not in {'source_sha256', 'resolution'}}
        public['resolution'] = public_resolution(row.get('resolution'))
        requests.append(public)
    result = {'input_message_id': snapshot['input_message_id'],
            'recent_requests': requests,
            'image_results': [{key: copy.deepcopy(val) for key, val in image.items() if key not in {'source_sha256','root_task_id'}}
                              for image in snapshot.get('image_results', [])],
            'active_request_message_id': active_request(snapshot),
            'resolution': public_resolution(binding(runtime)),
            'instruction': 'recent_requests含尚未提交的真实需求；未提交不是已受理或等待批准。'
                '先核对本次目标，再使用content_source。历史完成项不是当前任务的默认答案。'
                'text_requires_read=true的摘要不含完整要求，准备执行前用read_conversation_text读取原消息。'
                '目标不明确或缺少资料时，用respond_without_execution提交具体问题；该工具不执行业务。'
                '已读来源后需要纠正选择时，先单独调用resolve_request_target，不能直接换content_source。'
                '目标缺稿时依据真实需求准备新稿，不执行无关旧稿。'}
    if snapshot.get('catalog_version') == 2:
        from .request_catalog import project
        result['image_results'] = [{**image, 'target_fields': {'kind': 'image',
                                   'source_message_id': image['source_message_id'], 'content_index': image['content_index']}}
                                  for image in result['image_results']]
        public_snapshot = {**snapshot, 'requests': requests, 'image_results': result['image_results']}
        projected = project(public_snapshot, groups(runtime), result['resolution'])
        focused = next((r for r in projected['recent_requests'] if r['message_id'] == result['active_request_message_id']), None)
        selected_id = ((result['resolution'] or {}).get('target') or {}).get('source_message_id')
        selected = next((r for r in projected['recent_requests'] if r['message_id'] == selected_id), None)
        index = []
        for row in projected['recent_requests']:
            item = {key: copy.deepcopy(val) for key, val in row.items()
                    if key not in {'new_uploads', 'quoted_images', 'resolution'}}
            item['text_excerpt'] = (row.get('text_excerpt') or '')[:200]
            item['text_requires_read'] = row.get('text_requires_read', False) or len(row.get('text_excerpt') or '') > 200
            index.append(item)
        result.update(catalog_version=2, recent_requests=index,
                      current_turn=projected['current_turn'], focused_request=focused,
                      saved_groups=projected['saved_groups'])
        if selected and selected_id not in {snapshot['input_message_id'], result['active_request_message_id']}:
            result['selected_request'] = selected
        result['instruction'] += ('轮次为真实用户消息的来源编号，不是自动执行规则。new_uploads是当轮新增附件；'
            'quoted_images是客户端携带的历史引用，保留原始来源；selected_images是模型解析的使用关系，不能冒充用户明确指定。'
            'chat_turn_status只表示聊天回合，不表示图片任务完成。没有保存稿的需求仍是有效候选，不用旧稿补缺。'
            '保存稿标题属于历史记录，不解释为当前任务。content目标填写source_message_id，复制其source_request.message_id；'
            '图片目标复制目录中的图片选择字段，纠正和路由使用同一引用。'
            '新上传和历史参考可同时使用，没有上传不代表自动继承最近图片。')
        result['instruction'] += ('用户已经明确的约束应当保留，不自行提出删除/改写等新选项或反复确认；'
            '仅当执行确实缺少必要信息且无法从原话取得时澄清。目标已明确且未改变时无需重复调用目标纠正工具。')
    from .reference_boundary import NativeContext
    return NativeContext(result)


def model_context(runtime):
    """Explicit transport projection; native consumers use native_context."""
    result = native_context(runtime)
    if result is None:
        return None
    from .reference_boundary import for_runtime
    boundary = for_runtime(runtime)
    projected = boundary.context(result)

    return projected


def freeze_frame(runtime, request, frame, intent):
    """One admission contract for router choices and later model corrections."""
    raw = frame.target.model_dump(mode='json', exclude_none=True) if frame.target else None
    if raw:
        action_target_contract.validate(intent, raw['kind'])
    if (context(runtime) or {}).get('catalog_version') == 2 and raw:
        if raw['kind'] == 'content' and intent in {'execute_saved', 'repeat'} and frame.focus == 'current_request':
            raise TaskError('REQUEST_TARGET_FOCUS_CONFLICT')
        if raw['kind'] == 'content' and intent in {'execute_saved', 'repeat'}:
            selected = next((g for g in groups(runtime) if g['artifact_id'] == raw['artifact_id'] and g['revision'] == raw['revision']), None)
            if (selected
                    and any(item['item_index'] in raw['item_indices']
                            and frame.size_for(item['item_index']).get('mode') == 'inherit_reference'
                            and item.get('options', {}).get('aspect_ratio') == 'auto'
                            for item in selected['items'])):
                # Do not adopt the old task and then discover this known frozen
                # size conflict at native dispatch. Preserve the recipe or
                # author a new version; never silently drop user constraints.
                raise TaskError('REQUEST_TARGET_SAVED_SIZE_CHANGED')
        if raw['kind'] == 'content' and intent in {'execute_saved', 'repeat'} and frame.materials:
            selected = next((g for g in groups(runtime) if g['artifact_id'] == raw['artifact_id'] and g['revision'] == raw['revision']), None)
            if selected is not None and 'item_inputs' in selected:
                inputs = [selected['item_inputs'][i - 1] for i in raw['item_indices'] if i <= len(selected['item_inputs'])]
                allowed = {(m['reference'].get('message_id'), m['reference'].get('content_index'))
                           for item in inputs for m in item}
                if inputs and all(inputs) and any(m.usage == 'input' and (
                        m.reference.message_id, m.reference.content_index) not in allowed for m in frame.materials):
                    raise TaskError('REQUEST_TARGET_SAVED_INPUTS_CHANGED')
    return freeze(runtime, request, raw, focus=frame.focus)


def freeze(runtime, request, raw, *, focus=None):
    """Validate a router selection and freeze it to this authenticated input."""
    from .content_delivery import input_revision
    snapshot = context(runtime)
    if snapshot is None:
        return None  # Old checkpoints finish under their original contracts.
    target = Target.model_validate(raw or {'kind': 'ambiguous', 'basis': 'ambiguous'})
    value = target.model_dump(mode='json', exclude_none=True)
    source = next((r for r in snapshot['requests'] if r['message_id'] == target.source_message_id), None)
    if target.kind == 'current':
        value['source_message_id'] = snapshot['input_message_id']
        source = next((r for r in snapshot['requests'] if r['message_id'] == value['source_message_id']), None)
    elif target.kind in {'request', 'image'}:
        if target.kind == 'request' and source is None:
            raise TaskError('REQUEST_TARGET_NOT_VISIBLE')
        if target.kind == 'image':
            if target.basis == 'current_input' and target.source_message_id != snapshot['input_message_id']:
                if target.source_message_id != active_request(snapshot):
                    raise TaskError('REQUEST_TARGET_BASIS_INVALID')
                value['basis'] = 'active_request'  # Same focused source, canonical relation.
            actual = next((i for i in snapshot.get('image_results', ())
                           if (i['source_message_id'], i['content_index']) == (target.source_message_id, target.content_index)), None)
            if actual is None:
                from .image_history import recovered, was_viewed
                actual = next((i for i in recovered(runtime)
                    if (i['source_message_id'], i['content_index']) == (target.source_message_id, target.content_index)), None)
                if actual and not was_viewed(runtime, actual['reference']):
                    raise TaskError('REQUEST_TARGET_IMAGE_REQUIRES_VIEW')
            if not actual and not (source and any(i['reference']['content_index'] == target.content_index for i in source.get('images', ()))):
                raise TaskError('REQUEST_TARGET_IMAGE_NOT_FOUND')
            if actual:
                value['source_sha256'] = actual['source_sha256']
                if actual.get('root_task_id'):
                    value['root_task_id'] = actual['root_task_id']
    elif target.kind == 'content':
        value.pop('source_message_id', None)
        matches = [g for g in groups(runtime) if g['artifact_id'] == target.artifact_id and g['revision'] == target.revision
                   and set(target.item_indices) <= {i['item_index'] for i in g['items']}]
        if len(matches) != 1:
            raise TaskError('REQUEST_TARGET_CONTENT_CHANGED')
        value['root_task_id'] = matches[0]['root_task_id']
        from .request_catalog import source_for
        source = source_for(matches[0], snapshot['requests'])
        if snapshot.get('catalog_version') == 2 and source:
            if target.source_message_id != source['message_id']:
                raise TaskError('REQUEST_TARGET_CONTENT_OWNER_MISMATCH')
        if source:
            value['source_message_id'] = source['message_id']
    if target.basis == 'active_request':
        active = active_request(snapshot)
        focus_row = next((r for r in snapshot['requests'] if r['message_id'] == active), None)
        focus_root = focus_row['root_task_id'] if focus_row else next((i.get('root_task_id') for i in snapshot.get('image_results', ())
                                                                     if i['source_message_id'] == active), None)
        same_root = target.kind in {'content', 'image'} and focus_root and focus_root == value.get('root_task_id')
        if not active or value.get('source_message_id') != active and not same_root:
            raise TaskError('REQUEST_TARGET_FOCUS_MISMATCH')
    if source:
        value['source_sha256'] = source['source_sha256']
    result = {'version': 1, 'input_message_id': snapshot['input_message_id'],
              'focus': focus or ('current_request' if target.kind in {'current', 'ambiguous'} else 'selected_target'),
              'execution_task_id': str(runtime.task_id),
              'input_revision': input_revision(runtime, request), 'target': value,
              'status': 'ambiguous' if target.kind == 'ambiguous' else 'resolved'}
    runtime.checkpoint_base[KEY] = result
    return result


def check(runtime, request, call, *, root_task_id=None):
    """Return a correctable rejection before adoption, reuse or dispatch."""
    from .content_delivery import input_revision
    value = binding(runtime)
    if context(runtime) is None:
        return None
    if call['name'] not in {'generate_image', 'generate_video', 'code_execute', 'save_task_text', 'resume_task_execution', 'read_task_result'}:
        return None
    # Registering a freshly authored document is preparation, not an effect.
    try:
        args = json.loads(call.get('arguments') or '{}')
    except (ValueError, TypeError):
        return None  # Original argument validation owns malformed payloads.
    if not isinstance(args, dict):
        return None
    if call['name'] == 'generate_image' and 'previous_result_edit' in args and not {
            'source_message_id', 'source_content_index'} <= args.keys():
        return ('REQUEST_TARGET_IMAGE_REFERENCE_REQUIRED', '本次已绑定明确目标。请填写该图的 source_message_id、source_content_index 和 edit_instruction，不以旧的上一轮默认选图代替。')
    if call['name'] == 'read_task_result':
        state = getattr(runtime, 'task_execution_state', None)
        image = next((a for a in state.artifacts if a.id == args.get('artifact_id') and a.kind == 'image'), None) if state else None
        if image is None:
            return None
        from services.handlers.chat.content_tools import semantic_intent
        from .reducer import digest
        current_steps = {b.step_id for b in state.call_bindings if b.tool_name == 'generate_image'
                         and b.input_sha256 == digest(sorted(getattr(runtime, 'task_direct_seen', ())))}
        if (value and value['input_revision'] == input_revision(runtime, request)
                and str(image.delivery_task_id) == value['execution_task_id'] and image.step_id in current_steps):
            return None  # Delivery of this input's verified result is legitimate.
        if semantic_intent(request, runtime) != 'explain':
            return ('REQUEST_TARGET_EXISTING_RESULT_NOT_REQUESTED', '本次要求实际执行，不能用读取旧图片替代本次交付。查看输入用图片查看接口，执行仍需真实回执。')
    if (call['name'] == 'save_task_text' and 'document' in args and not args.get('base')
            and context(runtime).get('catalog_version') != 2):
        return None
    if value is None or value['status'] != 'resolved' or value['input_revision'] != input_revision(runtime, request):
        return ('REQUEST_TARGET_UNRESOLVED', '本次目标尚未明确或输入已变化。核对最近需求与方案，不派发历史旧稿；有歧义时询问具体目标。')
    target = value['target']
    from services.handlers.chat.content_tools import semantic_intent
    try:
        action_target_contract.validate(semantic_intent(request, runtime), target['kind'])
    except TaskError:
        return (action_target_contract.CODE, action_target_contract.GUIDANCE)
    if (call['name'] == 'save_task_text' and target['kind'] == 'content'
            and ('document' in args or 'message_source' in args)):
        from .content_objects import REVISION_USAGE
        return ('REQUEST_TARGET_REVISION_REQUIRED',
            '本次已选定真实保存稿。先用read_conversation_text读取所选完整条目，'
            '再用save_task_text的base和updates修订原稿；不要重新登记整组document或message_source。'
            '仅更新用户指定的条目和字段，保留其他条目、未改规格与素材。'
            '用户只要求修改方案、不出图时continue_execution=false；修改并出图才设为true。' + REVISION_USAGE)
    if call['name'] == 'save_task_text' and target['kind'] == 'content':
        updates = args.get('updates')
        if (isinstance(updates, list) and all(isinstance(u, dict) and type(u.get('item_index')) is int for u in updates)
                and not {u['item_index'] for u in updates} <= set(target['item_indices'])):
            return ('REQUEST_TARGET_ITEM_SELECTION_MISMATCH',
                '本次仅选择了条目' + str(target['item_indices']) + '，未保存任何修改。'
                'updates只提交这些条目；其他条目由后台原样保留，不重新填写。')
    if call['name'] == 'read_task_result':
        if target['kind'] == 'content':
            from .content_objects import content_image_receipts
            receipts = content_image_receipts(state)
            tasks = {receipts.get((target['artifact_id'], i), {}).get('image_task_id') for i in target['item_indices']}
            if str(image.source_task_id) not in tasks:
                return ('REQUEST_TARGET_IMAGE_MISMATCH', '已有图片不是本次所选方案条目的结果。')
        elif target['kind'] == 'image':
            actual = next((i for i in context(runtime).get('image_results', ())
                           if (i['source_message_id'], i['content_index']) == (target['source_message_id'], target['content_index'])), None)
            if not actual or actual.get('task_id') != str(image.source_task_id):
                return ('REQUEST_TARGET_IMAGE_MISMATCH', '已有图片不是本次所选结果。')
        elif target['kind'] != 'current':
            return ('REQUEST_TARGET_IMAGE_MISMATCH', '请读取本次明确指定的图片结果。')
        return None
    if target['kind'] == 'request' and call['name'] in {'generate_image', 'generate_video', 'code_execute'}:
        row = next((r for r in context(runtime)['requests'] if r['message_id'] == target['source_message_id']), None)
        observed = runtime.checkpoint_base.get('_chat_content_tools', {}).get('observed_history', {})
        if row and row.get('text_requires_read') and not all(
                observed.get(f"{row['message_id']}:{index}", {}).get('context_revision') == row['context_revision']
                for index in row['text_part_indices']):
            return ('REQUEST_TARGET_FULL_TEXT_REQUIRED', '所选需求摘要不完整。先读取该消息的完整文字，不能按截断摘要生成。')
    if root_task_id is not None:
        if target['kind'] != 'content' or target.get('root_task_id') != str(root_task_id):
            return ('REQUEST_TARGET_OWNER_MISMATCH', '所选旧任务不属于本次目标。请按最近需求准备新稿，或明确选择用户指定的历史方案。')
        return None
    ref = args.get('base') if call['name'] == 'save_task_text' else args.get('content_source')
    if ref is not None:
        if not isinstance(ref, dict):
            return None
        state = getattr(runtime, 'task_execution_state', None)
        from .content_objects import objects
        fresh = state and any(a.id == ref.get('artifact_id') and str(a.source_task_id) == value['execution_task_id'] for a in objects(state))
        if fresh:
            return None
        if (target['kind'] != 'content' or ref.get('artifact_id') != target.get('artifact_id')
                or ref.get('item_index') not in target.get('item_indices', ())):
            return ('REQUEST_TARGET_CONTENT_MISMATCH', '调用引用的方案与本次目标不一致。本次未执行，请使用目标版本，不能改用旧成品。')
    elif call['name'] == 'resume_task_execution':
        source = getattr(runtime, 'task_followup_candidate', None)
        return check(runtime, request, call, root_task_id=(source or {}).get('state', {}).get('root_task_id', ''))
    elif target['kind'] == 'image' and call['name'] == 'generate_image':
        selected = (target['source_message_id'], target['content_index'])
        draft = args.get('draft')
        if isinstance(draft, dict) and not isinstance(draft.get('materials', ()), (list, tuple)):
            return None
        # An inline draft is another existing public form of a single image
        # request. Validate its actual first supplier input, not the absence of
        # the edit-only shorthand fields. Native draft validation still owns
        # field shapes, permissions, fingerprints and subsequent references.
        inputs = [m.get('reference', {}) for m in draft.get('materials', ())
                  if isinstance(m, dict) and m.get('usage', 'input') == 'input'] if isinstance(draft, dict) else []
        reference = inputs[0] if inputs and isinstance(inputs[0], dict) else {}
        actual = ((reference.get('message_id'), reference.get('content_index')) if isinstance(draft, dict)
                  else (args.get('source_message_id'), args.get('source_content_index')))
        if actual != selected:
            return ('REQUEST_TARGET_IMAGE_MISMATCH', '编辑底图与本次选定图片不同；请复制目标图片引用和本轮编辑要求。')
    elif target['kind'] == 'request' and call['name'] == 'generate_image' and {'edit_instruction', 'previous_result_edit'} & args.keys():
        row = next((r for r in context(runtime)['requests'] if r['message_id'] == target['source_message_id']), None)
        refs = {(i['reference']['message_id'], i['reference']['content_index']) for i in (row or {}).get('images', ())}
        if refs and (args.get('source_message_id'), args.get('source_content_index')) not in refs:
            return ('REQUEST_TARGET_IMAGE_MISMATCH', '编辑底图不属于本次承接的需求；核对该需求的真实图片引用。')
    elif target['kind'] == 'content' and call['name'] in {'generate_image', 'generate_video'}:
        return ('REQUEST_TARGET_CONTENT_REQUIRED', '本次执行已保存方案，请填写绑定版本的 content_source，不另写一份替代稿。')
    return None


def check_display(runtime, request, reference):
    """Viewing source bytes is read-only; delivering an old result has semantics."""
    if context(runtime) is None:
        return
    from services.handlers.chat.content_tools import semantic_intent
    from .content_delivery import input_revision
    value = binding(runtime)
    from services.handlers.chat.existing_image_delivery import action
    if semantic_intent(request, runtime) != 'explain':
        raise TaskError('REQUEST_TARGET_EXISTING_RESULT_NOT_REQUESTED')
    if (value is None or value['status'] != 'resolved'
            or value['input_revision'] != input_revision(runtime, request)):
        raise TaskError('REQUEST_TARGET_SELECTION_REQUIRED')
    if action(runtime, request) == 'analyze':
        raise TaskError('REQUEST_TARGET_EXISTING_RESULT_NOT_REQUESTED')
    target = value['target']
    if target['kind'] == 'image' and (reference.get('message_id'), reference.get('content_index')) != (
            target['source_message_id'], target['content_index']):
        raise TaskError('REQUEST_TARGET_IMAGE_MISMATCH')
    if target['kind'] == 'request':
        row = next((r for r in context(runtime)['requests'] if r['message_id'] == target['source_message_id']), None)
        if action(runtime, request) == 'display_existing' and len((row or {}).get('images', ())) != 1:
            raise TaskError('REQUEST_TARGET_IMAGE_SELECTION_REQUIRED')
        if not any(image['reference'] == reference for image in (row or {}).get('images', ())):
            raise TaskError('REQUEST_TARGET_IMAGE_MISMATCH')

    if target['kind'] == 'content':
        selected = next((g for g in groups(runtime) if g['artifact_id'] == target['artifact_id']), None)
        if action(runtime, request) == 'display_existing' and (selected or {}).get('revision') != target['revision']:
            raise TaskError('REQUEST_TARGET_CONTENT_CHANGED')
        task_ids = {i.get('image_task_id') for i in (selected or {}).get('items', ()) if i['item_index'] in target['item_indices']}
        if not any(i.get('task_id') in task_ids and i.get('task_id') is not None
                   and (i['source_message_id'], i['content_index']) == (reference.get('message_id'), reference.get('content_index'))
                   for i in context(runtime).get('image_results', ())):
            raise TaskError('REQUEST_TARGET_IMAGE_MISMATCH')


async def recover_display_selection(runtime, request, prepared, reference):
    """A main-model read-only choice can recover a failed advisory router.

    Not a fallback to the latest image: the model must name an exact image and
    the same selection, scope and save guards still apply. Genuine ambiguity
    and existing resolved generation choices are never overridden here.
    """
    from .content_delivery import input_revision
    value = binding(runtime)
    if (not value or value['status'] != 'ambiguous'
            or value.get('rejection', {}).get('code') != 'IMAGE_WORKFLOW_UNAVAILABLE'
            or value['input_revision'] != input_revision(runtime, request)):
        return
    await select(runtime, request, prepared, {'intent': 'explain', 'request_context': {
        'focus': 'selected_target', 'target': {'kind': 'image', 'basis': 'explicit_reference',
            'source_message_id': reference['message_id'], 'content_index': reference['content_index']}}})


def image_status_reply(runtime, request):
    """Describe only this input's effects, or the explicitly selected old slot."""
    from .content_delivery import input_revision
    from .reducer import digest
    from services.handlers.chat.content_tools import IMAGE_STATUS_NOT_SUBMITTED
    value = binding(runtime)
    if value is None or value['input_revision'] != input_revision(runtime, request):
        return IMAGE_STATUS_NOT_SUBMITTED
    state = getattr(runtime, 'task_execution_state', None)
    steps = {b.step_id for b in state.call_bindings if b.tool_name == 'generate_image'
             and b.input_sha256 == digest(sorted(getattr(runtime, 'task_direct_seen', ())))} if state else set()
    operations = [op for op in state.operations if op.step_id in steps
                  and str(op.owner_task_id) == value['execution_task_id']] if state else []
    statuses = {op.status for op in operations}
    if statuses & {'reserved', 'uncertain'}:
        return '图片请求的受理状态尚未确认，本轮不会重复提交。'
    if 'accepted' in statuses:
        return '图片任务已受理，正在后台处理中。'
    if statuses & {'failed_before_dispatch', 'failed_after_dispatch'}:
        return '本次图片请求未全部成功，请查看具体错误。'
    if statuses and statuses <= {'completed'}:
        return '本次图片任务已完成。'
    if state and any(a.kind == 'image' and a.image_reuse is not None and a.step_id in steps
                     and str(a.delivery_task_id) == value['execution_task_id'] for a in state.artifacts):
        return '已复用所选方案的已有图片，本轮没有新增生成。'
    target = value['target']
    if target['kind'] == 'content':
        from .content_objects import content_image_receipts
        receipts = content_image_receipts(state)
        selected = [receipts.get((target['artifact_id'], index), {}) for index in target['item_indices']]
        if any(r.get('status') == 'accepted' for r in selected):
            return '所选图片任务仍在处理中，本轮没有新增生成。'
        if selected and all(r.get('status') == 'completed' for r in selected):
            return '所选方案已有完成结果，但本轮尚未提交新的图片任务。'
    return IMAGE_STATUS_NOT_SUBMITTED
