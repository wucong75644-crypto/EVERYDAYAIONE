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


SCHEMAS = (
    schema(DISCOVER, '查找当前可用业务工具。用能力名称、工具名或简短目标查询；下一轮提供真实工具定义和已保存内容的content_source用法。',
           {'query': {'type': 'string', 'minLength': 1, 'maxLength': 160}}, ('query',)),
    schema(READ, '在当前会话已关闭的历史中读取原文。指定message_id可读取单条消息并取得登记所需的行号。'
                 'text_exact/text_exacts只读取逐字片段。执行已登记内容时选择content_source；未登记的完整原文用save_task_text的message_source登记。',
           {'message_id': {'type': 'string'}, 'text_exact': {'type': 'string', 'maxLength': 20000},
            'text_exacts': {'type': 'array', 'minItems': 1, 'maxItems': 100,
                            'items': {'type': 'string', 'minLength': 1, 'maxLength': 20000}},
}),
)


def model_tools(tools, *, selectable_source_images=False, server_image_plan=False):
    """Project the one entry contract selected by the current workflow."""
    from services.task_execution.content_objects import ContentReference, ImageContentReference, selection_parameters
    result=[]
    for original in tools:
        name=original.get('function',{}).get('name')
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
        source = ImageContentReference if name == 'generate_image' and selectable_source_images else ContentReference
        parameters.setdefault('properties',{})['content_source']=selection_parameters(source)
        # Old source locators are read-only recovery proofs, never a new model input.
        parameters['properties'].pop('source_prompt',None)
        constraints={key:parameters.pop(key) for key in ('required','anyOf','oneOf','allOf','not','dependentRequired') if key in parameters}
        if name in MEDIA_TOOLS:
            tool['function']['parameters'] = {'type': 'object', 'additionalProperties': False,
                'properties': {'content_source': parameters['properties']['content_source']}, 'required': ['content_source']}
            media = '一张图片' if name == 'generate_image' else '一段短视频'
            tool['function']['description'] = (
                f'用 content_source 选择一个已保存条目，异步生成{media}。'
                '后台读取完整原文、素材与规格，受理后由页面展示进度和结果。'
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
    aliases = {'generate_video': ('视频', '短片', 'video'), 'generate_image': ('生图', '图片', 'image'),
               'code_execute': ('word', 'docx', '文件', '沙盒')}
    matches = []
    for spec in registry.specs():
        # Existing domain-specific entrypoints retain their own routing prerequisites.
        if (spec.exposure is not Exposure.PUBLIC or spec.name in blocked
                or spec.name in {'plan_ecommerce_images', 'get_conversation_context', 'image_agent'}
                or not registry.check_access(spec.name, context, policy=policy).allowed):
            continue
        description = spec.to_schema()['function'].get('description') or spec.name
        exact = query in (spec.name, spec.capability)
        name_words = set(spec.name.split('_'))
        score = (10 if exact else 0) + len(words & name_words)
        score += sum(alias in query for alias in aliases.get(spec.name, ()))
        if len(query) >= 2 and query in description.casefold():
            score += 2
        if score:
            matches.append((score, spec.name, description.splitlines()[0][:240]))
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


def source_text(db, task_id, context, args):
    from types import SimpleNamespace
    if (context.context_scope != 'user' or not context.personal_context_allowed
            or str(context.workspace_owner_id) != str(context.actor_user_id)):
        raise PermissionError('CHAT_SOURCE_DENIED')
    _check_identity(SimpleNamespace(db=db), context)
    parent = db.table('tasks').select('user_id,org_id,conversation_id,base_context_revision').eq(
        'id', task_id).single().execute().data
    if (not parent or str(parent['user_id']) != str(context.actor_user_id)
            or str(parent['org_id']) != str(context.org_id)
            or str(parent['conversation_id']) != str(context.conversation_id)
            or parent['base_context_revision'] is None):
        raise PermissionError('CHAT_SOURCE_DENIED')
    query = db.table('messages').select('id,role,content,context_revision').eq(
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
    messages, history = [], []
    from services.handlers.chat_context.image_sources import content_parts
    for row in rows:
        parts = [{'type': 'text', 'content_index': index, 'text': part['text']}
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
    return {'messages': history}


def prepare_message_content(db, task_id, context, source, observed):
    """Copy authorized completed message ranges and their authoritative original images."""
    from types import SimpleNamespace
    from services.handlers.chat_context.image_sources import image_sources
    from services.handlers.chat_input_handoff import _range
    from services.task_execution.content_objects import ContentDocument, SourceImage
    from services.task_execution.image_resources import native_resolver

    message_id = str(source.message_id)
    query = {'message_id': message_id, '_full': True, '_for_execution': True}
    messages = source_text(db, task_id, context, query)['messages']
    part = next((m for m in messages if m['role'] == 'assistant' and m['content_index'] == source.content_index), None)
    key = f'{message_id}:{source.content_index}'
    if part is None or observed.get(key) != {k: part[k] for k in ('sha256', 'context_revision')}:
        raise ValueError('CONTENT_MESSAGE_SOURCE_UNAVAILABLE_OR_CHANGED')
    text = part['text']
    items, ranges = [], []
    for item in source.items:
        start, end = _range(text, {'first_line': item.first_line, 'last_line': item.last_line})
        if ranges and start < ranges[-1][1]:
            raise ValueError('CONTENT_MESSAGE_SOURCE_OVERLAP')
        ranges.append((start, end))
        items.append({'title': item.title, 'body': text[start:end]})

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
    images = tuple(SourceImage(name=image['name'], reference=image['reference'])
                   for image in image_sources(original) if image['available'])
    proof = {'message_id': message_id, 'content_index': source.content_index, 'ranges': ranges,
             'sha256': part['sha256'], 'context_revision': part['context_revision']}
    document = ContentDocument.model_validate({
        'title': source.title, 'items': items,
        'notes': json.dumps({'message_source': proof}, ensure_ascii=False),
    })
    return document, images, proof


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
