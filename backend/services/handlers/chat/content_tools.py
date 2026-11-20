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
_IMAGE_ACTION = (r'(?:生图|出图|改图|做图|画一张|(?:生成|做成?|制作|绘制)[^。！？!?；;，,\n]{0,14}'
                 r'(?:图片|图像|主图|封面图)|generate (?:an? |the )?image)')
_NEGATED_IMAGE_ACTION = re.compile(
    rf"(?:(?:不(?:要|用|必)?|别|无需|暂不)\s*(?:(?:实际|直接|立即|马上)\s*)?|(?:do not|don't)\s+){_IMAGE_ACTION}",
    re.I,
)
_PROMPT_ONLY_ACTION = re.compile(
    r'(?:只|仅)(?:需|要)?(?:写|修改|改写|整理|输出|提供|给我)[^。！？!?；;，,\n]{0,36}提示词',
)


def image_execution_request(request, runtime=None):
    """Hold execution narration until there is a receipt. This never starts a task."""
    text = ' '.join(getattr(part, 'text', '') for part in (getattr(request, 'content', None) or ()))
    if re.search(r'怎么|如何|为什么|是什么意思|是否支持|怎样', text):
        return False
    if re.search(r'(?:写|修改|改写|整理).{0,24}提示词[？?。！!\s]*$', text):
        return False
    # Remove only the explicitly non-executing phrases. A positive request
    # elsewhere in the same message can still request a different image.
    actions = _PROMPT_ONLY_ACTION.sub(' ', _NEGATED_IMAGE_ACTION.sub(' ', text))
    if runtime and re.search(r'(?:剩余|剩下|其他|其余).{0,10}(?:也|一起|继续).{0,6}(?:做|生成)', actions):
        state = getattr(runtime, 'task_execution_state', None)
        prior = getattr(runtime, 'task_followup_candidate', None) or {}
        if (state and any(op.capability == 'platform.generate_image' for op in state.operations)
                or any(op.get('capability') == 'platform.generate_image' for op in prior.get('state', {}).get('operations', ()))):
            return True
    return bool(re.search(_IMAGE_ACTION, actions, re.I))


def visual_author_request(request, indexed):
    text = '\n'.join(part.text for part in request.content if part.type == 'text')
    if indexed or '提示词' in text or image_execution_request(request) or re.search(
            r'(?:参考|用).{0,8}(?:原图|这张图).{0,12}(?:改|生成|出图)', text):
        return text
    return None


def selects_previous_item(request):
    """An explicit pointer in an execution request cannot authorize a fresh draft."""
    text = ' '.join(part.text for part in request.content if part.type == 'text')
    number = r'[零〇一二两三四五六七八九十百0-9]+'
    item = r'(?:方案|稿件|提示词|脚本|文案|条目)'
    # A bare ordinal is a pointer only when the following words concern execution.
    # “第二条鱼” and “第一张图” are not saved documents.
    pointer = re.search(
        rf'第\s*{number}\s*(?:个|条|项|版|套)(?:{item}|(?=\s*(?:和原图|带原图|帮我|做|生成|出图|执行|$)))'
        rf'|{item}\s*[#＃]?\s*{number}'
        rf'|(?:之前|刚才|上面|旧|原来|已保存)的?{item}'
        rf'|(?:你|助手)(?:之前|刚才|刚|上次|上一轮|先前)?(?:给我|帮我)?'
        rf'(?:写|拟|生成|整理|提供|给|做|出)(?:过)?的(?:那[条份版个])?\s*{item}', text)
    if not pointer:
        return False
    if (re.search(r'(?:不用|别用|不要用|舍弃|忽略|取消)\s*$', text[:pointer.start()])
            or re.match(r'\s*(?:不要了|不用了|先不做)', text[pointer.end():])):
        return False
    # Pasted text explicitly identified as the source is already in the current input.
    if re.search(r'提示词(?:原文)?(?:如下)?\s*[：:]\s*\S', text):
        return False
    # Revising/authoring and then executing is a compound task. Keep both actions available.
    if re.search(rf'(?:写|设计|拟|整理|列出).{{0,24}}{item}', text[:pointer.start()]):
        return False
    return image_execution_request(request) or bool(re.search(
        r'(?:导出|做成|制成|生成|转成)(?:一份|一个|一段)?(?:视频|文件|文档|Word|PDF)', text, re.I))


def is_single_image_source_request(request):
    """Use the early question only when it will not interrupt an independent request."""
    text = ' '.join(part.text for part in request.content if part.type == 'text').strip()
    text = re.sub(r'^(?:这个|这张|这|这是)(?:是)?(?:图片|图|参考图)[。！!，,]\s*', '', text)
    clauses = [part for part in re.split(r'[。！？!?；;，,\n]', text) if part.strip()]
    return (len(clauses) == 1 and not re.search(r'另外|顺便|同时|并且|以及|然后', clauses[0])
            and image_execution_request(request))


def unconfirmed_image_reply(text, has_calls, *, awaiting_prompt=False):
    if has_calls:
        return ''
    # A genuine clarification or failure can accompany a status. A free-form
    # promise is never a substitute for the worker's acceptance event.
    notice = '本轮尚未收到图片任务的受理回执。'
    if text.strip() and (text.rstrip().endswith(('？', '?')) or re.search(r'无法|缺少|失败|请(?:选择|指定|上传|确认)', text)):
        if not re.search(r'已(?:经)?(?:成功)?(?:提交|受理|派发|生成)|提交成功|正在(?:生成|排队)', text):
            if awaiting_prompt and text.rstrip().endswith(('？', '?')):
                return text
            return text + '\n\n' + notice
    return notice


def checkpoint_state(runtime):
    state = runtime.checkpoint_base.get(STATE) or {}
    return state if state.get('task_id') == str(runtime.task_id) else {'task_id': str(runtime.task_id)}


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


SCHEMAS = (
    schema(DISCOVER, '查找当前可用工具。用精确工具名或简短目标查找，只返回当前权限范围内的匹配项。后续按实际提供的定义调用；没有匹配时不要猜工具名。',
           {'query': {'type': 'string', 'minLength': 1, 'maxLength': 160}}, ('query',)),
    schema(READ, '在当前会话固定历史中读取资料。修订已保存提示词时用content_source读取指定版本的完整条目。'
                 '按原图重做用image_task_id读取所选成图的原始输入、实际提示词和规格。'
                 '用message_id查看指定消息。定位历史图时设list_images=true，仅返回图片分组和定位。图片序号只在所属消息或成果组内有效，仍有多个候选时请用户选择。'
                 '需要实际查看某张图时，用view_image复制它的reference。只读动作不会提交生成。'
                 'text_exact/text_exacts读取逐字片段。'
                 '执行已登记稿件时把其content_source交给目标工具。各读取方式分别调用。',
           {'content_source': {'type': 'object', 'additionalProperties': False,
                               'required': ['artifact_id', 'item_index'],
                               'properties': {'artifact_id': {'type': 'string'}, 'item_index': {'type': 'integer'},
                                              'sha256': {'type': 'string'}}},
            'view_image': {'type': 'object', 'additionalProperties': False,
                           'required': ['message_id', 'content_index'],
                           'properties': {'message_id': {'type': 'string'}, 'content_index': {'type': 'integer'}}},
            'image_task_id': {'type': 'string'}, 'list_images': {'type': 'boolean'}, 'message_id': {'type': 'string'}, 'text_exact': {'type': 'string', 'maxLength': 20000},
            'text_exacts': {'type': 'array', 'minItems': 1, 'maxItems': 100,
                            'items': {'type': 'string', 'minLength': 1, 'maxLength': 20000}},
}),
)


def model_tools(tools, *, selectable_source_images=False, server_image_plan=False, reuse_source=False, first_delivery=False, current_images=(), inline_image=False):
    """Project the one entry contract selected by the current workflow."""
    from services.task_execution.content_objects import ContentReference, ImageContentReference, selection_parameters
    result=[]
    for original in tools:
        name=original.get('function',{}).get('name')
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
                from services.task_execution.content_objects import SCHEMA
                description = function['description'].removesuffix(SCHEMA['function']['description'])
                function['description'] = description + (
                    '写方案、提示词或脚本时只交付一次document。每条body保留完整原文和限制，'
                    '只交付文字时页面直接展示正文，后台保留当前原图待选。还要求执行时设continue_execution=true，'
                    '有上传图片则逐条填写materials（reference、role、usage），保存后再调用目标工具。')
            if current_images and not reuse_source:
                refs = json.dumps(material_candidates(current_images), ensure_ascii=False)
                function['description'] += (
                    ' 本轮上传图的可选 reference（仅按用户意图选择，执行用 input，分析用 analysis）：' + refs
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
                   '每次调用处理一张图。多张已选且相互独立的图片按条目顺序在同一轮提交各自的调用。'
                   if name == 'generate_image' else ' 仅接受文字脚本，不传图片素材；不支持视频剪辑。')
            )
            if name == 'generate_image' and reuse_source:
                tool['function']['description'] += (
                    ' 这轮要沿用已有原稿。目录未定位时先查会话原文；'
                    '找不到或无法唯一确定时请用户提供或选择，不另写一份替代。'
                    '用户指定当前上传图片时，从本轮图片引用中绑定该图。'
                )
            if name == 'generate_image' and inline_image:
                from services.task_execution.inline_image_action import InlineRequest

                projected = tool['function']['parameters']
                draft, definitions = InlineRequest.parameters(parameters['properties'])
                projected.pop('required')
                projected['properties']['draft'] = draft
                projected['oneOf'] = [{'required': ['content_source']}, {'required': ['draft']}]
                if definitions:
                    projected['$defs'] = definitions
                tool['function']['description'] += (
                    ' 用户要求创作单张新图或编辑一张已选图时，单独调用本工具传draft。'
                    '用户指定已有提示词时先定位原稿，找不到就请用户补充。'
                    '填写完整编辑要求body，并在materials逐一标记明确的图片引用和用途。'
                    '系统保存原稿并提交，不需要先调用save_task_text。'
                    '只改提示词时使用保存工具；多张图或已有策划继续用content_source。'
                )
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



def read_image_view(handler, request, context, reference):
    from services.task_execution.content_objects import ImageMessageReference
    from services.task_execution.image_resources import native_resolver

    check_source_scope(handler.db, context)
    locator = ImageMessageReference.model_validate(reference).model_dump(mode='json')
    native = native_resolver(handler, request, context)
    image, = native.resolve([{**locator, 'role': '查看来源'}])
    url = native.preview(image)
    if not url:
        raise ValueError('CHAT_IMAGE_VIEW_UNAVAILABLE')
    return {'reference': locator, 'visually_present': True}, url


def visual_budget_remaining(messages):
    from core.config import get_settings

    count = sum(
        isinstance(part, dict) and part.get('type') == 'image_url'
        for message in messages
        for part in (message['content'] if isinstance(message.get('content'), list) else ())
    )
    return max(0, get_settings().chat_context_max_images - count)


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
        parts = [m for m in parts if index_document(m['text']) is not None]
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
        group = index_document(text)
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
