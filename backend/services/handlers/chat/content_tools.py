"""Private discovery and read-only history; existing checkpoint verification."""
import copy
import hashlib
import json
import re
from uuid import UUID

from services.tools.catalog import build_tool_catalog
from services.tools.policy import ToolPolicy
from services.tools.spec import Exposure
from services.tools.runtime_context import _check_identity


DISCOVER = 'find_available_tools'
READ = 'read_conversation_text'
CONTROLS = frozenset({DISCOVER, READ})
INPUT_TOOLS = frozenset({'generate_video', 'generate_image', 'code_execute'})
MEDIA_TOOLS = frozenset({'generate_video', 'generate_image'})
STATE = '_chat_content_tools'
MISSING_IMAGE_PROMPT_QUESTION = (
    '这条对话里还没有你提到的原提示词。可以发给我原稿，或告诉我按当前素材重新写一版再生成吗？'
)

def semantic_intent(request, runtime=None):
    """One frozen workflow decision; consumers never reclassify its user words."""
    projection = getattr(runtime, 'workflow_projection', None) or (getattr(request, 'params', None) or {}).get('_ecom_workflow')
    if not isinstance(projection, dict):
        return None
    return projection.get('intent')


def semantic_runtime(runtime):
    return getattr(runtime, 'skill_coordination', False) is True








def requests_generated_image_edit(request, *, allow_named_output=False, runtime=None):
    """The existing semantic decision owns the action, including implicit edits."""
    return semantic_intent(request, runtime) == 'edit'


def is_single_generated_image_edit(request, runtime=None):
    return semantic_intent(request, runtime) == 'edit' and not any(
        part.type != 'text' for part in request.content)


def repeats_saved_image(request, runtime=None):
    return semantic_intent(request, runtime) == 'repeat'


def image_regeneration_variant(request, evidence=None, runtime=None):
    """Bind a semantic repeat to the authenticated input, never to matching words."""
    text = ' '.join(part.text for part in request.content if part.type == 'text').strip()
    if evidence is not None and (not isinstance(evidence, str) or evidence.strip() != text
                                 or not repeats_saved_image(request, runtime)):
        raise ValueError('CONTENT_IMAGE_REGENERATION_UNCONFIRMED')
    if not repeats_saved_image(request, runtime):
        return None
    try:
        message_id = UUID(str(image_input_id(request)))
    except ValueError:
        if evidence is not None:
            raise ValueError('CONTENT_IMAGE_REGENERATION_UNCONFIRMED') from None
        return None
    return f'regenerate:{message_id}'


IMAGE_NOT_SUBMITTED = '本轮没有提交图片任务。你可以重新发送生成要求。'


IMAGE_CALL_REPAIR = (
    '[Current image action correction] 用户本轮要求执行已存在的图片方案。'
    '检查当前目录和原文，目标明确时调用本轮提供的图片工具。'
    '用户明确要求再生成时填写 regenerate 和原条目引用；如果确有多个候选，询问用户选择。'
    '仅在实际受理后说明任务已提交。'
)


def image_input_id(request):
    return str(getattr(getattr(request, 'context_anchor', None), 'input_message_id', None) or '')


def has_newer_image_input(runtime):
    changes = getattr(runtime.task_user_changes, 'items', ())
    followup = runtime.checkpoint_base.get('_task_followup') or {}
    current = [item for item in changes if str(item.owner_task_id) == str(runtime.task_id)]
    # Adopting a saved plan delivers the initiating message as a steer. Any
    # additional input requires a new authorization rather than using that text.
    return bool(current and (len(current) != 1 or not current[0].delivered
                             or current[0].message_sha256 != followup.get('input_sha256')))


def bind_image_selection(runtime, request, state, reference):
    from services.task_execution import content_objects

    selection = content_objects.ImageContentReference.model_validate(reference)
    evidence = selection.regenerate
    if getattr(runtime, 'task_image_completion', None) is not None or has_newer_image_input(runtime):
        if evidence is not None:
            raise ValueError('CONTENT_IMAGE_REGENERATION_UNCONFIRMED')
        return reference, {}
    variant = image_regeneration_variant(request, evidence, runtime)
    if variant is None:
        return reference, {}
    selected = selection.model_dump(mode='json', exclude={'regenerate'}, exclude_none=True)
    return content_objects.repeat_image_reference(state, selected), {'variant_id': variant}






def image_execution_request(request, runtime=None):
    """Hold execution narration until a receipt; this never starts a task."""
    return semantic_intent(request, runtime) in {
        'write_then_submit_images', 'execute_saved', 'generate', 'edit', 'repeat'}


def image_writing_only_request(request, runtime=None):
    return semantic_intent(request, runtime) in {'write', 'explain'}


def requested_delivery_tools(request, runtime=None):
    projection = getattr(runtime, 'workflow_projection', None) or (getattr(request, 'params', None) or {}).get('_ecom_workflow') or {}
    context = projection.get('request_context') or {}
    labels = {'generate_video': '视频', 'code_execute': '文件'}
    return {tool: labels[tool] for tool in context.get('deliveries', ()) if tool in labels}


def requires_continued_delivery(request, runtime=None):
    projection = getattr(runtime, 'workflow_projection', None) or (getattr(request, 'params', None) or {}).get('_ecom_workflow') or {}
    context = projection.get('request_context') or {}
    return bool(context.get('continue_delivery') or requested_delivery_tools(request, runtime))



def waits_for_video_confirmation(request, runtime=None):
    projection = getattr(runtime, 'workflow_projection', None) or (getattr(request, 'params', None) or {}).get('_ecom_workflow') or {}
    media = (projection.get('request_context') or {}).get('media') or {}
    return media.get('tool') == 'generate_video' and bool(media.get('wait_for') or media.get('wait_all'))


def delivery_status(runtime, prepared, blocks, tool):
    artifact_type = {'code_execute': 'file', 'generate_video': 'video'}.get(tool)
    if artifact_type and any(block.get('type') == artifact_type and block.get('url') and not block.get('failed')
                             for block in blocks):
        return 'published'
    if tool == 'code_execute':
        return None
    state = getattr(runtime, 'task_execution_state', None)
    statuses = {op.status for op in state.operations
                if op.tool_name == tool and str(op.owner_task_id) == str(runtime.task_id) and op.receipt_ref} if state else set()
    if 'completed' in statuses:
        return 'published'
    if 'accepted' in statuses or tool in getattr(prepared, 'delivery_receipts', ()):
        return 'accepted'
    return None


def video_receipt_kind(result):
    metadata = getattr(result, 'metadata', {})
    if metadata.get('accepted') is not True or not metadata.get('task_id'):
        return None
    if getattr(getattr(result, 'execution', None), 'replayed', False) or metadata.get('outcome') == 'replay':
        return 'existing'
    return 'new' if metadata.get('outcome') == 'accepted' else None




def delivery_notice(deliveries, attempted):
    return ''.join(
        f'本轮尚未核实{label}的交付状态。' if tool in attempted
        else '本轮未提交视频任务。' if tool == 'generate_video' else '本轮未交付文件。'
        for tool, label in deliveries.items()
    )


def visual_author_request(request, indexed, runtime=None):
    text = '\n'.join(part.text for part in request.content if part.type == 'text')
    return text if indexed or semantic_intent(request, runtime) in {
        'write', 'write_then_submit_images', 'generate', 'edit'} else None


def authored_text_request(request, runtime=None):
    """Ordinary numbered answers are not execution artifacts."""
    return semantic_intent(request, runtime) in {'write', 'write_then_submit_images'}


REFERENCE_TEXT_INSTRUCTION = (
    '[Reference prompt delivery]\n'
    '直接按已激活方法要求流式输出完整 Markdown 正文。保留参考图判断、各方案的方案内容、'
    '视觉设定、参考图使用方式，以及完整生图提示词和负面提示词；保留原版编号和全部限制。'
    '后台在正文完成后校验、保存并生成可引用编号，本轮无需调用保存工具，也不要把正文改写成保存参数。'
    '例外：用户对新稿设置后续确认条件时，用save_task_text的confirmation结构化保存整组及条件，不适用此正文快路径。'
)

PLAIN_IMAGE_TEXT_INSTRUCTION = (
    '[Image prompt delivery]\n'
    '按用户要求交付方案，用同级编号标题分开各项。每项最终生图提示词及其全部执行限制'
    '写在一个独立的 ```prompt 代码块中；方案说明放在块外。整组比较、总结或追问放在'
    '单独同级的无编号标题下，避免混入最后一项提示词。后台保存正文和原编号，无需调用保存工具。'
    '若用户设置了后续确认条件，改用save_task_text的confirmation保存整组，不适用此正文快路径。'
)


def prefers_reference_text(request, runtime, *, saved_source_available=False, server_image_plan=False):
    """Only a verified, fresh writing phase may use host text registration."""
    from services.task_execution.direct_loop import active_image_methods
    from services.task_execution.methods import REFERENCE_PROMPTS_PROFILE

    if (server_image_plan or saved_source_available or image_execution_request(request, runtime)
            or requires_continued_delivery(request, runtime) or getattr(runtime, 'task_image_completion', None) is not None
            or getattr(getattr(runtime, 'task_user_changes', None), 'items', ())
            or getattr(runtime, '_steer_messages', ())):
        return False
    methods = active_image_methods(runtime)
    return len(methods) == 1 and next(iter(methods.values()))[0] == REFERENCE_PROMPTS_PROFILE


def prefers_plain_image_text(request, runtime, *, saved_source_available=False, server_image_plan=False):
    """Fresh prompt writing is host-registered using the frozen semantic intent."""
    from services.task_execution.direct_loop import active_image_methods
    from services.task_execution import item_confirmation
    if (server_image_plan or saved_source_available or selects_previous_item(request, runtime) or active_image_methods(runtime)
            or requires_continued_delivery(request, runtime)
            or getattr(runtime, 'task_image_completion', None) is not None):
        return False
    media = item_confirmation.directive(request, runtime)
    return semantic_intent(request, runtime) == 'write' and not (media.wait_for or media.wait_all)






def selects_previous_item(request, runtime=None):
    return semantic_intent(request, runtime) in {'execute_saved', 'repeat'}




def unconfirmed_image_reply(text, has_calls):
    if has_calls:
        return ''
    # Preserve authored content independently of punctuation. Acceptance is a
    # server receipt, not a claim inferred from the model's prose.
    return text + '\n\n' + IMAGE_NOT_SUBMITTED if text.strip() else IMAGE_NOT_SUBMITTED


def checkpoint_state(runtime):
    task_id = str(runtime.task_id)
    checkpoint = runtime.checkpoint_base
    state = checkpoint.get(STATE) or {}
    if state.get('task_id') == task_id:
        return state

    # Followup adoption preserves the previous owner's top-level state and
    # stores this turn's discoveries in the authenticated input checkpoint.
    if checkpoint.get('_task_followup'):
        initial = checkpoint.get('_task_followup_input_checkpoint') or {}
        state = initial.get(STATE) or {}
        if state.get('task_id') == task_id:
            return state
    return {'task_id': task_id}


def enabled(runtime, context):
    from services.task_execution.model_directed import enabled as model_directed
    return bool(model_directed(runtime) and context.execution_mode == 'interactive'
                and context.context_scope == 'user' and context.personal_context_allowed)


def uses_server_image_plan(prepared):
    workflow = getattr(prepared, 'ecom_workflow', None)
    return (workflow is not None and workflow.value is not None
            and workflow.value.kind == 'main_images' and workflow.server_selected())


def validate_model_input(name, arguments, *, server_image_plan=False):
    legacy_fields = {'chat_input', 'chat_text_source', 'source_prompt'}
    if name == 'image_agent' or (isinstance(arguments, dict) and legacy_fields.intersection(arguments)):
        raise ValueError('CONTENT_INPUT_PATH_RETIRED')
    if name not in MEDIA_TOOLS:
        return
    if name == 'generate_image' and server_image_plan:
        if not isinstance(arguments, dict) or set(arguments) != {'plan_source'}:
            raise ValueError('ECOM_PLAN_SOURCE_REQUIRED')
        return
    if isinstance(arguments, dict) and 'plan_source' in arguments:
        raise ValueError('CONTENT_INPUT_PATH_RETIRED')
    if not isinstance(arguments, dict) or 'content_source' not in arguments:
        raise ValueError('CONTENT_SOURCE_REQUIRED')
    if set(arguments) != {'content_source'}:
        raise ValueError('CONTENT_ARGUMENT_CONFLICT')


def schema(name, description, properties, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': {'type': 'object', 'additionalProperties': False,
                       'properties': properties, 'required': list(required)}}}


def material_candidates(images):
    return [image.reference.model_dump(mode='json', exclude_none=True) for image in images[:16]]


READ_MODE_GUIDANCE = (
    '每次只选一种读取方式。content_source、view_image、show_image、image_task_id、image_task_status 各自单独传。'
    '按消息读取时可传 message_id，并在 list_images=true、text_exact、text_exacts 中至多选一个。'
)


READ_SCHEMA = schema(READ, '在当前会话固定历史中读取资料。修订已保存提示词时用content_source读取指定版本的完整条目。'
                 '按原图重做用image_task_id读取所选成图的原始输入、实际提示词和规格。查询当前任务内已登记的图片受理或结果用image_task_status。'
                 '用message_id查看指定消息。定位历史图时设list_images=true，仅返回图片分组和定位。图片序号只在所属消息或成果组内有效，仍有多个候选时请用户选择。'
                 '模型需要看图分析时用view_image。用户要看旧图时用show_image交付，两个参数都复制所选图片的reference。只读动作不会提交生成。'
                 'text_exact/text_exacts读取逐字片段。'
                 '执行已登记稿件时把其content_source交给目标工具。' + READ_MODE_GUIDANCE,
           {'content_source': {'type': 'object', 'additionalProperties': False,
                               'required': ['artifact_id', 'item_index'],
                               'properties': {'artifact_id': {'type': 'string'}, 'item_index': {'type': 'integer'},
                                              'sha256': {'type': 'string'}}},
            'view_image': {'type': 'object', 'additionalProperties': False,
                           'required': ['message_id', 'content_index'],
                           'properties': {'message_id': {'type': 'string'}, 'content_index': {'type': 'integer'}}},
            'show_image': {'type': 'object', 'additionalProperties': False,
                           'required': ['message_id', 'content_index'],
                           'properties': {'message_id': {'type': 'string'}, 'content_index': {'type': 'integer'}}},
            'image_task_id': {'type': 'string'}, 'image_task_status': {'type': 'string'}, 'list_images': {'type': 'boolean'}, 'message_id': {'type': 'string'}, 'text_exact': {'type': 'string', 'minLength': 1, 'maxLength': 20000},
            'text_exacts': {'type': 'array', 'minItems': 1, 'maxItems': 100, 'uniqueItems': True,
                            'items': {'type': 'string', 'minLength': 1, 'maxLength': 20000}},
})
READ_PARAMETERS = READ_SCHEMA['function']['parameters']
READ_PARAMETERS['oneOf'] = [
    {'type': 'object', 'required': [key], 'additionalProperties': False,
     'properties': {key: {}}}
    for key in ('content_source', 'view_image', 'show_image', 'image_task_id', 'image_task_status')
] + [
    {'type': 'object', 'required': [key], 'additionalProperties': False,
     'properties': {'message_id': {}, key: {'const': True} if key == 'list_images' else {}}}
    for key in ('list_images', 'text_exact', 'text_exacts')
] + [
    {'type': 'object', 'additionalProperties': False, 'properties': {'message_id': {}}},
]


SCHEMAS = (
    schema(DISCOVER, '查找当前可用工具。用精确工具名或简短目标查找，只返回当前权限范围内的匹配项。后续按实际提供的定义调用；没有匹配时不要猜工具名。',
           {'query': {'type': 'string', 'minLength': 1, 'maxLength': 160}}, ('query',)),
    READ_SCHEMA,
)

def model_tools(tools, *, selectable_source_images=False, saved_text_available=False, server_image_plan=False, reuse_source=False, first_delivery=False, current_images=(), inline_image=False, generated_image_edit=False, single_image_edit=False, inline_only=False, previous_result=False, planned_image_count=None):
    """Project the one entry contract selected by the current workflow."""
    from services.task_execution.content_objects import ContentReference, ImageContentReference, selection_parameters
    from services.handlers.image_size_requirements import SIZE_GUIDANCE
    result=[]
    for original in tools:
        name=original.get('function',{}).get('name')
        if name == 'plan_ecommerce_images':
            tool = copy.deepcopy(original)
            parameters = tool['function']['parameters']
            if planned_image_count is not None:
                parameters['properties'].pop('image_count', None)
                tool['function']['description'] += f' 本轮逐图方案项总数已由后台按用户要求确定为{planned_image_count}，不要重复填写image_count。'
            else:
                parameters['oneOf'] = [{**form, 'required': [*form['required'], 'image_count']}
                    if 'references' in form.get('required', ()) else form for form in parameters['oneOf']]
            result.append(tool)
            continue
        if server_image_plan and name == 'save_task_text':
            # This workflow's native planner owns authoring and publication.
            continue
        if inline_only and name == 'save_task_text':
            # The inline entry already saves its draft atomically.
            continue
        if name == 'save_task_text' and not server_image_plan and (reuse_source or first_delivery or current_images):
            # Project the same backend action. The first turn has no historical
            # source to recover; an explicit follow-up already has its source.
            tool = copy.deepcopy(original)
            function = tool['function']
            parameters = function['parameters']
            excluded = {'document', 'new_content'} if reuse_source else {'base', 'updates', 'message_source', 'new_content'}
            for key in excluded if reuse_source or first_delivery else ():
                parameters['properties'].pop(key, None)
            forms = [form for form in parameters.pop('oneOf')
                     if not (reuse_source or first_delivery) or excluded.isdisjoint(form['required'])]
            if len(forms) == 1:
                parameters['required'] = forms[0]['required']
            else:
                parameters['oneOf'] = forms
            if reuse_source:
                function['description'] = ('执行已保存的条目直接调用目标工具。仅恢复历史正文时用message_source和'
                                           'continue_execution=true；修订用base和updates。')
            elif first_delivery:
                # Preserve authoring Skill presentation requirements in the prefix.
                from services.task_execution.content_objects import SCHEMA, MIXED_TEXT_GUIDANCE
                description = function['description'].removesuffix(SCHEMA['function']['description'])
                function['description'] = description + MIXED_TEXT_GUIDANCE + (
                    '普通新写方案、提示词或脚本可直接用编号标题及完整正文回复，由后台登记。'
                    '用户设置后续确认条件时，必须通过本工具一次交付document和confirmation（tool及wait_for或wait_all），'
                    '方案说明可放document.display_prefix，整组正文放items，不把等待条件当成批准。'
                    '结构化保存优先一次document，每条body保留完整原文和限制；分次写稿时前几次delivery_complete=false，最后一次才为true，'
                    '只交付文字时页面直接展示正文，后台保留当前原图待选。还要求执行时设continue_execution=true，'
                    '有上传图片则在每条的materials中绑定所用图片，保存后再调用目标工具。')
            if current_images and not reuse_source:
                refs = json.dumps(material_candidates(current_images), ensure_ascii=False)
                function['description'] += (
                    ' 本轮上传图的可选 materials[].reference（仅按用户意图选择）：' + refs
                )
            # Definitions for other forms can include long reference/patch schemas.
            # Only publish definitions reachable from the advertised arguments.
            definitions = parameters.pop('$defs', {})
            pending = [parameters]
            reachable = {}
            while pending:
                node = pending.pop()
                if isinstance(node, dict):
                    ref = node.get('$ref', '').removeprefix('#/$defs/')
                    if ref in definitions and ref not in reachable:
                        reachable[ref] = definitions[ref]
                        pending.append(definitions[ref])
                    pending.extend(node.values())
                elif isinstance(node, list):
                    pending.extend(node)
            if reachable:
                parameters['$defs'] = reachable
            result.append(tool)
            continue
        if name=='image_agent':continue
        if name not in INPUT_TOOLS:
            result.append(original);continue
        if name == 'code_execute' and not saved_text_available:
            result.append(original);continue
        tool=copy.deepcopy(original);parameters=tool['function']['parameters']
        if name == 'generate_image' and server_image_plan:
            plan = parameters['properties'].get('plan_source')
            if plan is not None:
                tool['function']['parameters'] = {'type': 'object', 'additionalProperties': False,
                    'properties': {'plan_source': plan}, 'required': ['plan_source']}
                tool['function']['description'] = (
                    '使用已保存的服务器主图策划的 plan_source 异步生成一张图片。'
                    '受理后由页面展示进度和结果。'
                )
                result.append(tool)
            continue
        source = ImageContentReference if name == 'generate_image' else ContentReference
        selection = selection_parameters(source)
        if source is ImageContentReference and not selectable_source_images:
            selection['properties'].pop('source_image_indices')
            selection['properties'].pop('without_reference')
        parameters.setdefault('properties', {})['content_source'] = selection
        # Old source locators are read-only recovery proofs, never a new model input.
        parameters['properties'].pop('source_prompt',None)
        constraints={key:parameters.pop(key) for key in ('required','anyOf','oneOf','allOf','not','dependentRequired') if key in parameters}
        if name in MEDIA_TOOLS:
            tool['function']['parameters'] = {'type': 'object', 'additionalProperties': False,
                'properties': {'content_source': parameters['properties']['content_source']}, 'required': ['content_source']}
            media = '一张图片' if name == 'generate_image' else '一段短视频'
            tool['function']['description'] = (
                f'异步生成{media}。执行已有稿件时，按标题、序号或最近修订从成果目录选中条目，'
                '直接把该条的content_source作为同名参数提交，无需重新保存。'
                '后台逐字读取选中条目的原文及已确定的执行规格；受理后由页面展示进度和结果。'
                + (' 已绑定的图自动复用。继续其余条目时跳过已有受理或结果的项；用户明确换图时只替换此次参考图。'
                   '用户明确要求按同一方案再生成时，使用同一 content_source，并填写 regenerate 为本轮完整原话。'
                   '每次调用处理一张图。多张已选且相互独立的图片按条目顺序在同一轮提交各自的调用。' + SIZE_GUIDANCE
                   if name == 'generate_image' else ' 仅接受文字脚本，不传图片素材；不支持视频剪辑。')
            )
            if name == 'generate_image' and reuse_source:
                tool['function']['description'] += (
                    ' 这轮要沿用已有原稿。目录未定位时先查会话原文；'
                    '找不到或无法唯一确定时请用户提供或选择，不另写一份替代。'
                    '用户指定当前上传图片时，从本轮图片引用中绑定该图。'
                )
            if name == 'generate_image' and inline_image:
                from services.task_execution.inline_image_action import InlineRequest, SingleImageEdit

                if single_image_edit:
                    tool['function']['parameters'] = SingleImageEdit.model_json_schema()
                    tool['function']['description'] = (
                        '编辑用户明确指定的图片（本轮附件底图或已有成图）。按用户指代找到确切的图片，'
                        '从该图的 reference 原样复制 message_id 和 content_index 到对应字段。'
                        '第一张指会话中的成图顺序。content_index 是消息内的位置。'
                        '定位缺失时先读取会话图片，定位不唯一时请用户选择。'
                        'edit_instruction 写明本轮修改和保留的要求，画布规格由后台依据用户消息与底图确定。'
                        '“刚才生成的图”“再改”沿用最近已完成的相关结果；明确说原图或指定较早图片时按该指代选图，不混用上一轮输入和输出。'
                        '本轮明确指定附件底图时，可整理为直接执行的修改指令，不凭空补商品身份、材质、功能或营销参数；后台核验图片绑定，不覆盖你的编辑指令。'
                        '本工具保存编辑要求并提交一次。'
                    )
                    if previous_result:
                        from services.task_execution.inline_image_action import PreviousResultEdit
                        parameters = tool['function']['parameters']
                        selected_required = parameters.pop('required')
                        parameters['properties']['previous_result_edit'] = PreviousResultEdit.model_json_schema()['properties']['previous_result_edit']
                        parameters['oneOf'] = [{'required': selected_required}, {'required': ['previous_result_edit']}]
                        tool['function']['description'] += (' 继续上一轮唯一图片结果时，只填previous_result_edit，后台绑定真实输出；'
                            '用户明确指向其他成图时才填写该图的source_message_id、source_content_index和edit_instruction。')
                    result.append(tool)
                    continue
                projected = tool['function']['parameters']
                draft, definitions = InlineRequest.parameters(parameters['properties'])
                if generated_image_edit:
                    projected['properties'] = {'draft': draft}
                    projected['required'] = ['draft']
                    tool['function']['description'] = (
                        '编辑本轮用户指定的图片。使用 draft.body 写清本轮修改、保留要求及每张输入图的作用，'
                        '在 draft.materials 按输入顺序以 usage=input 绑定实际使用的图片。'
                        '直接复制会话中真实的 message_id 与 content_index；定位缺失时先查会话图片。'
                        '本工具保存编辑稿并提交一次。' + SIZE_GUIDANCE
                    )
                else:
                    projected.pop('required')
                    projected['properties']['draft'] = draft
                    projected['oneOf'] = [{'required': ['content_source']}, {'required': ['draft']}]
                if definitions:
                    projected['$defs'] = definitions
                if not generated_image_edit:
                    tool['function']['description'] += (
                        ' 用户要求创作单张新图或编辑一张已选图时，单独调用本工具传draft。'
                        '用户指定已有提示词时先定位原稿，找不到就请用户补充。'
                        '填写完整编辑要求body，并按draft.materials的字段说明绑定所用图片。'
                        '画布与分辨率由后台依据用户明确要求、编辑底图和默认规则确定；'
                        '用户未明确规格时，不在body或options自行声明比例、像素或分辨率。'
                        '系统保存原稿并提交，不需要先调用save_task_text。'
                        '只改提示词时使用保存工具；多张图或已有策划继续用content_source。'
                    )
                if previous_result:
                    from services.task_execution.inline_image_action import PreviousResultEdit
                    projected['properties']['previous_result_edit'] = PreviousResultEdit.model_json_schema()['properties']['previous_result_edit']
                    forms = projected.pop('oneOf', None)
                    if forms is None:
                        forms = [{'required': projected.pop('required', ['draft'])}]
                    projected['oneOf'] = [*forms, {'required': ['previous_result_edit']}]
                    tool['function']['description'] += (
                        ' 继续编辑上一轮唯一图片结果时，传previous_result_edit，仅写修改与保留要求，后台绑定实际成图。'
                        '更换颜色等画面修改属于编辑，不能用content_source.regenerate重跑原稿。')
        else:
            if constraints:
                parameters['anyOf']=[constraints,{'required':['content_source',*[key for key in constraints.get('required',[]) if key!='prompt']]}]
            tool['function']['description'] += '使用已保存正文时传 content_source，并在代码中读取 chat_source_text。'
        result.append(tool)
    return result


def discover(context, query, blocked=()):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 160:
        raise ValueError('CHAT_TOOL_QUERY_INVALID')
    registry = build_tool_catalog()
    policy = ToolPolicy(registry)
    query = query.casefold().strip()
    words = set(re.findall(r'[a-z0-9]+', query))
    aliases = {
        'generate_video': ('视频', '短片', 'video'),
        'generate_image': ('生图', '图片', 'image', '换背景', '改图', '修图', '抠图', '做图'),
        'file_delete': ('删除文件', '删文件', '删除一个文件', '删除这个文件'),
        'restore_file': ('恢复文件', '找回文件', '撤销删除'),
        'file_search': ('找文件', '搜索文件', '查找文件'),
        'file_analyze': ('分析文件', '读取文件', '查看文件'),
        'code_execute': ('word', 'docx', '沙盒', '导出报告'),
        'web_search': ('搜索网页', '上网查', '网上找', '搜索互联网'),
        'manage_scheduled_task': ('定时任务', '定时提醒', '设置提醒'),
    }
    matches = []
    exact_matches = []
    for spec in registry.specs():
        # Existing domain-specific entrypoints retain their own routing prerequisites.
        if (spec.exposure is not Exposure.PUBLIC or spec.name in blocked
                or spec.name in {'plan_ecommerce_images', 'get_conversation_context', 'image_agent'}
                or not registry.check_access(spec.name, context, policy=policy).allowed):
            continue
        description = spec.to_schema()['function'].get('description') or spec.name
        exact = query in (spec.name, spec.capability)
        if exact:
            exact_matches.append({'name': spec.name, 'description': description.splitlines()[0][:240]})
            continue
        name_words = set(spec.name.split('_'))
        score = len(words & name_words)
        score += 4 * sum(alias in query for alias in aliases.get(spec.name, ()))
        if len(query) >= 2 and query in description.casefold():
            score += 2
        if score:
            matches.append((score, spec.name, description.splitlines()[0][:240]))
    if exact_matches or re.fullmatch(r'[a-z0-9]+(?:[_.][a-z0-9]+)+', query):
        return exact_matches[:5]
    return [{'name': name, 'description': description}
            for _, name, description in sorted(matches, key=lambda row: (-row[0], row[1]))[:5]]


def select_text_fragments(messages, arguments):
    """Read literal quotes; a history read never creates an execution binding."""
    from services.handlers.chat_content_bundle import bundle
    if 'text_exact' in arguments and 'text_exacts' in arguments:
        raise ValueError('CHAT_SOURCE_SELECTION_CONFLICT')
    if not {'text_exact', 'text_exacts'}.intersection(arguments):
        return []
    texts = arguments.get('text_exacts') if 'text_exacts' in arguments else [arguments['text_exact']]
    if (not isinstance(texts, list) or not 1 <= len(texts) <= 100
            or any(not isinstance(text, str) or not 1 <= len(text) <= 20000 for text in texts)
            or len(set(texts)) != len(texts)):
        raise ValueError('CHAT_SOURCE_TEXT_INVALID')
    if sum(len(text.encode()) for text in texts) > 100000:
        raise ValueError('CHAT_SOURCE_TOO_LARGE_SELECT_FEWER_FRAGMENTS')
    selected = []
    for text in texts:
        matches = []
        for message in messages:
            for part in message['parts']:
                if part.get('type') != 'text' or not isinstance(part.get('text'), str):
                    continue
                if (part['text'].count(text) != 1
                        or arguments.get('content_index', part['content_index']) != part['content_index']):
                    continue
                assembled, proof = bundle(part['text'], text, scope='fragment')
                matches.append({'message_id': str(message['message_id']),
                                'content_index': part['content_index'],
                                'sha256': hashlib.sha256(assembled.encode()).hexdigest(), 'text': assembled,
                                'bundle': proof})
        if not matches and arguments.get('_optional') is True:
            continue
        if len(matches) != 1:
            raise ValueError('CHAT_SOURCE_UNAVAILABLE_OR_AMBIGUOUS')
        if any((s['message_id'], s['content_index'], s['sha256']) ==
               (matches[0]['message_id'], matches[0]['content_index'], matches[0]['sha256']) for s in selected):
            raise ValueError('CHAT_SOURCE_OVERLAPPING_UNITS_SELECT_COMPLETE_ITEM')
        selected.append(matches[0])
    return selected


def check_source_scope(db, context):
    from types import SimpleNamespace
    if (context.context_scope != 'user' or not context.personal_context_allowed
            or str(context.workspace_owner_id) != str(context.actor_user_id)):
        raise PermissionError('CHAT_SOURCE_DENIED')
    _check_identity(SimpleNamespace(db=db), context)


async def read_saved_item(runtime, db, context, reference):
    from services.task_execution.content_objects import ContentReference, is_current, load
    from services.task_execution.contracts import TaskState
    from services.task_execution.direct_loop import content_followup_state
    import asyncio

    await asyncio.to_thread(check_source_scope, db, context)
    target = ContentReference.model_validate(reference)
    state = runtime.task_execution_state or content_followup_state(runtime)
    if not is_current(state, target.artifact_id):
        roots = {row['root_task_id'] for row in getattr(runtime, 'task_content_groups', ())
                 if row['artifact_id'] == target.artifact_id}
        if len(roots) != 1 or runtime.task_execution_state is not None:
            raise ValueError('CHAT_CONTENT_VERSION_UNAVAILABLE')
        root = roots.pop()
        source = await runtime.read_content_root(root)
        if not source:
            raise ValueError('CHAT_CONTENT_VERSION_UNAVAILABLE')
        state = TaskState.model_validate(source['state'])
        if str(state.root_task_id) != str(root):
            raise PermissionError('CHAT_SOURCE_DENIED')
    if state is None or (str(state.conversation_id), str(state.org_id), str(state.actor_user_id)) != (
            str(context.conversation_id), str(context.org_id), str(context.actor_user_id)):
        raise PermissionError('CHAT_SOURCE_DENIED')
    identity = state.execution_identity
    if (identity.workspace_owner_id != str(context.workspace_owner_id)
            or identity.context_scope != context.context_scope
            or identity.personal_context_allowed != context.personal_context_allowed):
        raise PermissionError('CHAT_SOURCE_DENIED')
    if not is_current(state, target.artifact_id):
        raise ValueError('CHAT_CONTENT_VERSION_UNAVAILABLE')
    artifact, document, item = load(state, target)
    return {'document_title': document.title,
            'content_source': {**target.model_dump(mode='json'), 'sha256': artifact.sha256},
            'item': item.model_dump(mode='json', exclude_none=True)}



def _resolve_image_view(handler, request, context, reference):
    from services.task_execution.content_objects import ImageMessageReference
    from services.task_execution.image_resources import native_resolver

    check_source_scope(handler.db, context)
    locator = ImageMessageReference.model_validate(reference).model_dump(mode='json')
    native = native_resolver(handler, request, context)
    image, = native.resolve([{**locator, 'role': '查看来源'}])
    url = native.preview(image)
    if not url:
        raise ValueError('CHAT_IMAGE_VIEW_UNAVAILABLE')
    return locator, image, url


def read_image_view(handler, request, context, reference):
    locator, _, url = _resolve_image_view(handler, request, context, reference)
    return {'reference': locator, 'visually_present': True}, url


def show_image(handler, request, context, reference):
    locator, image, url = _resolve_image_view(handler, request, context, reference)
    return {'type': 'image', 'url': url, 'alt': '会话中的已有图片',
            'width': image.get('width'), 'height': image.get('height'),
            'source_message_id': locator['message_id'], 'source_content_index': locator['content_index']}


def visual_budget_remaining(messages):
    from core.config import get_settings

    count = sum(
        isinstance(part, dict) and part.get('type') == 'image_url'
        for message in messages
        for part in (message['content'] if isinstance(message.get('content'), list) else ())
    )
    return max(0, get_settings().chat_context_max_images - count)


def visual_context_notice(messages):
    count = sum(isinstance(part, dict) and part.get('type') == 'image_url'
                for message in messages
                for part in (message['content'] if isinstance(message.get('content'), list) else ()))
    if not count:
        return ''
    return (f'[Current visual input]\n本次模型请求已附带{count}个图片输入。'
            '可以直接依据当前图像内容判断；追加读取的预算只限制额外图片，不表示已有图片不可见。'
            '图像分辨率低或内容不清时如实说明，不将其编造为视觉额度耗尽。')


def read_image_task_status(runtime, db, context, value):
    from services.task_execution.image_continuation import model_recovery_facts

    check_source_scope(db, context)
    task_id = str(UUID(value))
    state = runtime.task_execution_state
    if state is None or (str(state.conversation_id), str(state.org_id), str(state.actor_user_id)) != (
            str(context.conversation_id), str(context.org_id), str(context.actor_user_id)):
        raise PermissionError('CHAT_SOURCE_DENIED')
    operation = next((op for op in state.operations if op.tool_name == 'generate_image'
                      and op.receipt_ref == f'task:{task_id}'), None)
    if operation is None:
        raise ValueError('CHAT_IMAGE_STATUS_UNAVAILABLE')
    failure = next((fact for fact in model_recovery_facts(runtime) if fact['task_id'] == task_id), None)
    return {'task_id': task_id, 'status': operation.status, 'scope': 'original_operation',
            **({'failure': failure} if failure else {})}


def read_image_recipe(handler, request, context, image_task_id):
    from services.handlers.chat_image_request import REQUEST_KEY
    from services.task_execution.image_resources import native_resolver

    check_source_scope(handler.db, context)
    task_id = str(UUID(image_task_id))
    native = native_resolver(handler, request, context)
    # The task and its output message must both be in this input's fixed scope.
    native.validate_source_task(task_id)
    row = handler.db.table('tasks').select('user_id,org_id,conversation_id,request_params').eq(
        'id', task_id).single().execute().data
    if not row or (str(row['user_id']), str(row['org_id']), str(row['conversation_id'])) != (
            str(context.actor_user_id), str(context.org_id), str(context.conversation_id)):
        raise PermissionError('CHAT_SOURCE_DENIED')
    snapshot = (row.get('request_params') or {}).get(REQUEST_KEY) or {}
    prompt = snapshot.get('prompt')
    if not isinstance(prompt, str) or len(prompt.encode()) > 100000:
        raise ValueError('CHAT_IMAGE_RECIPE_UNAVAILABLE')
    original_inputs = []
    references = snapshot.get('references', [])
    if not isinstance(references, list) or len(references) > 16 or any(not isinstance(item, dict) for item in references):
        raise ValueError('CHAT_IMAGE_RECIPE_UNAVAILABLE')
    for item in references:
        try:
            native.verify([item], digest=False)
        except (OSError, TypeError, ValueError, PermissionError, KeyError):
            original_inputs.append({'role': item.get('role'), 'available': False})
            continue
        if item.get('source_message_id') is not None and type(item.get('source_content_index')) is int:
            ref = {'message_id': item['source_message_id'], 'content_index': item['source_content_index']}
        else:
            ref = {key: item[key] for key in ('message_id', 'content_index', 'asset_id', 'resource_ref', 'file_id')
                   if key in item}
            # Native snapshots may carry a derived file ID in addition to the
            # original selector. Export one identity accepted by MaterialReference.
            if 'message_id' in ref and type(ref.get('content_index')) is int:
                ref = {key: ref[key] for key in ('message_id', 'content_index')}
            else:
                ref = next(({key: ref[key]} for key in ('asset_id', 'resource_ref', 'file_id') if key in ref), {})
        original_inputs.append({'role': item.get('role'), 'available': bool(ref), 'reference': ref})
    return {'image_task_id': task_id, 'prompt': prompt, 'original_inputs': original_inputs,
            'options': {key: snapshot[key] for key in ('aspect_ratio', 'resolution', 'output_format') if key in snapshot}}


def source_text(db, task_id, context, args):
    check_source_scope(db, context)
    from types import SimpleNamespace
    parent = db.table('tasks').select('user_id,org_id,conversation_id,base_context_revision').eq(
        'id', task_id).single().execute().data
    if (not parent or str(parent['user_id']) != str(context.actor_user_id)
            or str(parent['org_id']) != str(context.org_id)
            or str(parent['conversation_id']) != str(context.conversation_id)
            or parent['base_context_revision'] is None):
        raise PermissionError('CHAT_SOURCE_DENIED')
    query = db.table('messages').select('id,role,content,context_revision,generation_params,conversation_id').eq(
        'conversation_id', context.conversation_id).eq('message_kind', 'conversation').eq(
        'status', 'completed').in_('role', ['user', 'assistant']).lte(
        'context_revision', parent['base_context_revision'])
    query = query.eq('org_id', context.org_id) if context.org_id else query.is_('org_id', 'null')
    message_id = args.get('message_id')
    if message_id is not None:
        query = query.eq('id', str(UUID(message_id)))
    rows = query.order('context_revision', desc=True).limit(8).execute().data or []
    if message_id is not None and not rows and not args.get('_for_execution'):
        # Legacy/interrupted text uses the same frozen-history proof as native image sources.
        from services.task_execution.image_resources import native_resolver
        request = SimpleNamespace(task_id=task_id, conversation_id=context.conversation_id)
        reader = native_resolver(SimpleNamespace(db=db), request, context)
        row = reader._message(str(UUID(message_id)))
        if row.get('role') not in {'user', 'assistant'}:
            raise PermissionError('CHAT_SOURCE_DENIED')
        rows = [row]
    if args.get('list_images') is True:
        from services.handlers.chat_context.image_sources import discovered_image_sources, model_image_sources
        groups = []
        remaining = 20
        for row in rows:
            if remaining <= 0:
                break
            images = discovered_image_sources(row, db, org_id=context.org_id,
                owner_id=context.workspace_owner_id, scope=context.context_scope, include_dimensions=False)
            if images:
                groups.append({'message_id': str(row['id']), 'context_revision': row.get('context_revision'),
                               'images': model_image_sources(images[:remaining])})
                remaining -= len(images)
        return {'image_groups': groups, 'limit': 20,
                'scope': 'selected_message' if message_id else 'recent_closed_messages'}
    messages, history = [], []
    from services.handlers.chat_context.image_sources import content_parts
    for row in rows:
        parts = [{'type': 'text', 'content_index': index, 'text': part['text'],
                  **({'content_artifact_id': part['content_artifact_id']} if part.get('content_artifact_id') else {})}
                 for index, part in enumerate(content_parts(row['content']))
                 if isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str)
                 and not (args.get('_for_execution') and part.get('content_incomplete'))]
        messages.append({'message_id': str(row['id']), 'parts': parts})
        for part in parts:
            from services.handlers.chat_input_handoff import line_starts
            if args.get('_full') and len(part['text'].encode()) > 100000:
                raise ValueError('CHAT_INPUT_TOO_LARGE')
            item = {'message_id': str(row['id']), 'role': row['role'],
                    'context_revision': row.get('context_revision'),
                    'content_index': part['content_index'],
                    'text': part['text'] if args.get('_full') else part['text'][:20000],
                    'sha256': hashlib.sha256(part['text'].encode()).hexdigest(),
                    'line_starts': line_starts(part['text'] if args.get('_full') else part['text'][:20000])}
            if part.get('content_artifact_id'):
                item['content_artifact_id'] = part['content_artifact_id']
            if not args.get('_full') and len(part['text']) > 20000:
                item.update(requires_explicit_read=True, total_chars=len(part['text']), truncated=True)
            history.append(item)
    selected = select_text_fragments(messages, args)
    if selected:
        from services.agent.tool_result_envelope import SOURCE_SELECTION_BUDGET
        result = {'sources': selected} if 'text_exacts' in args else {'source': selected[0]}
        if len(json.dumps(result, ensure_ascii=False)) > SOURCE_SELECTION_BUDGET:
            raise ValueError('CHAT_SOURCE_TOO_LARGE_SELECT_FEWER_FRAGMENTS')
        return result
    total = 0
    for item in history:
        size = len(item['text'].encode())
        if total + size > 100000:
            item['text'] = ''
            item['requires_explicit_read'] = True
        else:
            total += size
    result = {'messages': history}
    if (message_id and not args.get('_for_execution') and history
            and all(part['role'] == 'assistant' for part in history)):
        try:
            images = message_source_images(db, task_id, context, str(UUID(message_id)))
        except (ValueError, PermissionError):
            images = ()  # Text reading remains available when image provenance is absent.
        if images:
            result['source_images'] = [{'index': index, 'name': image.name,
                'reference': image.reference.model_dump(mode='json', exclude_none=True)}
                for index, image in enumerate(images, 1)]
    return result


def historical_image_source(db, request, context):
    from types import SimpleNamespace
    from services.task_execution.content_objects import source_images_from_message
    from services.task_execution.image_resources import native_resolver

    native = native_resolver(SimpleNamespace(db=db), request, context)
    query = db.table('messages').select('id,role,content').eq(
        'conversation_id', context.conversation_id).eq('message_kind', 'conversation').eq(
        'status', 'completed').in_('role', ['assistant', 'user']).lte(
        'context_revision', native.base_revision).neq('id', native.input_message_id)
    query = query.eq('org_id', context.org_id) if context.org_id else query.is_('org_id', 'null')
    history = query.order('context_revision', desc=True).limit(8).execute().data or []
    has_reply = False
    for row in history:
        if row['role'] == 'assistant':
            has_reply = True
        elif has_reply and source_images_from_message(row):
            return True
    return False


def _author_input(db, task_id, context, message_id):
    from types import SimpleNamespace
    from services.task_execution.image_resources import native_resolver

    native = native_resolver(SimpleNamespace(db=db), SimpleNamespace(task_id=task_id,
        conversation_id=context.conversation_id), context)
    owner = db.table('tasks').select('user_id,org_id,conversation_id,input_message_id').eq(
        'assistant_message_id', message_id).eq('type', 'chat').eq('status', 'completed').limit(2).execute().data or []
    owner = [row for row in owner if str(row['user_id']) == str(context.actor_user_id)
             and row['org_id'] == context.org_id and row['conversation_id'] == context.conversation_id]
    if len(owner) != 1 or not owner[0].get('input_message_id'):
        raise ValueError('CONTENT_MESSAGE_SOURCE_ORIGIN_UNAVAILABLE')
    original = native._message(str(owner[0]['input_message_id']))
    if original['role'] != 'user':
        raise ValueError('CONTENT_MESSAGE_SOURCE_ORIGIN_UNAVAILABLE')
    return original


def message_source_images(db, task_id, context, message_id):
    from services.task_execution.content_objects import source_images_from_message
    return source_images_from_message(_author_input(db, task_id, context, message_id))


def prepare_message_content(db, task_id, context, source, observed):
    """Copy authorized completed message ranges and their authoritative original images."""
    from services.handlers.chat_input_handoff import _range
    from services.task_execution.content_objects import ContentDocument, source_images_from_message
    from services.handlers.chat_context.image_sources import content_parts

    message_id = str(source.message_id)
    query = {'message_id': message_id, '_full': True, '_for_execution': True}
    messages = source_text(db, task_id, context, query)['messages']
    from services.task_execution.content_groups import index_document, bind_group
    parts = [m for m in messages if m['role'] == 'assistant'
             and (source.content_index is None or m['content_index'] == source.content_index)]
    if len(parts) > 1 and not source.items:
        parts = [m for m in parts if index_document(m['text'], allow_plain=True) is not None]
    if len(parts) != 1:
        raise ValueError('CONTENT_MESSAGE_SOURCE_BOUNDARIES_REQUIRED')
    part = parts[0]
    if part.get('content_artifact_id'):
        raise ValueError('CONTENT_MESSAGE_SOURCE_ALREADY_REGISTERED')
    key = f"{message_id}:{part['content_index']}"
    if (source.items or key in observed) and observed.get(key) != {k: part[k] for k in ('sha256', 'context_revision')}:
        raise ValueError('CONTENT_MESSAGE_SOURCE_UNAVAILABLE_OR_CHANGED')
    text = part['text']
    items, ranges, gaps = [], [], ()
    if source.items:
        for item in source.items:
            start, end = _range(text, {'first_line': item.first_line, 'last_line': item.last_line})
            if ranges and start < ranges[-1][1]:
                raise ValueError('CONTENT_MESSAGE_SOURCE_OVERLAP')
            ranges.append((start, end))
            items.append({'title': item.title, 'body': text[start:end]})
        document = ContentDocument.model_validate({'title': source.title, 'items': items})
    else:
        group = index_document(text, allow_plain=True)
        if group is None:
            raise ValueError('CONTENT_MESSAGE_SOURCE_BOUNDARIES_REQUIRED')
        document, ranges, gaps = group.document, group.ranges, group.gaps

    proof = {'message_id': message_id, 'content_index': part['content_index'], 'ranges': ranges,
             'sha256': part['sha256'], 'context_revision': part['context_revision']}
    origin = _author_input(db, task_id, context, message_id)
    images = source_images_from_message(origin)
    question = '\n'.join(p.get('text', '') for p in content_parts(origin['content']) if p.get('type') == 'text')
    document = bind_group(document, images, question, source.source_image_indices)
    document = document.model_copy(update={'notes': json.dumps({'message_source': proof}, ensure_ascii=False)})
    return document, images, {**proof, 'display_gaps': gaps}


def inject_source_code(code, text):
    import ast
    lines = code.splitlines(keepends=True)
    offset = 0
    try:
        body = ast.parse(code).body
    except SyntaxError:
        body = ()
    for node in body:
        if isinstance(node, ast.ImportFrom) and node.module == '__future__':
            offset = node.end_lineno
        elif offset == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            offset = node.end_lineno
        else:
            break
    lines.insert(offset, f"\nchat_source_text = {text!r}\n")
    return ''.join(lines)


def verified_video_status_text(text, *, fresh=False, confirmed_item=None):
    if fresh:

        parts = re.split(r'([。！？；\n])', text)
        for index in range(0, len(parts), 2):
            mentioned = {int(value) for value in re.findall(r'第\s*([1-9][0-9]?)\s*(?:条|项|个|段)', parts[index])}
            if not mentioned or confirmed_item is None or mentioned == {confirmed_item}:
                parts[index] = re.sub(r'(?:此前|先前|之前|早前|早已)\s*(?:就\s*)?(?:已经|已)?\s*受理',
                                      '本轮已受理', parts[index])
        text = ''.join(parts)
    return re.sub(r'(?:视频|短片)(?:任务)?(?:已经|已)(?:成功)?(?:生成(?:完成)?|制作完成|完成|发布)',
                  '视频任务已受理', text)


def semantic_context(request, runtime=None):
    projection = getattr(runtime, 'workflow_projection', None) or (getattr(request, 'params', None) or {}).get('_ecom_workflow') or {}
    raw = projection.get('request_context')
    if raw is None:
        return None
    from services.agent.image.ecommerce_planner.workflow import ImageRequestContext
    return ImageRequestContext.model_validate(raw)
