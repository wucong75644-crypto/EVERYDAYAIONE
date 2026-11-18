"""Private model controls for finding tools and binding closed chat text."""
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
STATE = '_chat_content_tools'


def checkpoint_state(runtime):
    state = runtime.checkpoint_base.get(STATE) or {}
    return state if state.get('task_id') == str(runtime.task_id) else {'task_id': str(runtime.task_id)}


def enabled(runtime, context):
    from services.task_execution.model_directed import enabled as model_directed
    return bool(model_directed(runtime) and context.execution_mode == 'interactive'
                and context.context_scope == 'user' and context.personal_context_allowed)


def schema(name, description, properties, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': {'type': 'object', 'additionalProperties': False,
                       'properties': properties, 'required': list(required)}}}


SCHEMAS = (
    schema(DISCOVER, '查找当前可用但尚未提供的业务工具。用能力名称或简短目标查询，下一轮使用真实工具定义。',
           {'query': {'type': 'string', 'minLength': 1, 'maxLength': 160}}, ('query',)),
    schema(READ, '在当前会话已关闭的历史中读取原文。先找消息，再用 text_exact 精确选择目标片段。'
                 'text_exact 定位一条，text_exacts 批量定位；默认交付完整条目及共同要求，不能遗漏补充说明和限制。'
                 '仅用户明确只使用局部原文时传 selection_scope=fragment。改编时显式传 mode=adapted。',
           {'message_id': {'type': 'string'}, 'text_exact': {'type': 'string', 'maxLength': 20000},
            'text_exacts': {'type': 'array', 'minItems': 1, 'maxItems': 100,
                            'items': {'type': 'string', 'minLength': 1, 'maxLength': 20000}},
            'selection_scope': {'type': 'string', 'enum': ['item', 'fragment']}}),
)


def model_tools(tools):
    result = []
    for tool in tools:
        name = tool.get('function', {}).get('name')
        if name in INPUT_TOOLS:
            tool = copy.deepcopy(tool)
            params = tool['function']['parameters']
            params['properties']['chat_text_source'] = {
                'type': 'object', 'additionalProperties': False,
                'required': ['message_id', 'content_index', 'mode'],
                'properties': {
                    'message_id': {'type': 'string'}, 'content_index': {'type': 'integer', 'minimum': 0},
                    'sha256': {'type': 'string'}, 'text': {'type': 'string', 'maxLength': 20000},
                    'mode': {'enum': ['exact', 'adapted']},
                    'bundle': {'type': 'object', 'description': '原样使用读取结果中的完整性凭据，不能修改'},
                },
                'description': ('选择已读取的来源；多条同消息片段用返回的sha256区分，后台取回原文。也可提供text由后台核验。exact 沿用原文，adapted 表示用户要求改编。'
                                'code_execute 中用 chat_source_text 读取后台注入的正文，调用 emit_file 交付文件。'),
            }
            if name == 'generate_image':
                params['oneOf'] = [*params['oneOf'], {
                    'required': ['mode', 'chat_text_source'], 'not': {'required': ['prompt']},
                }]
            elif name == 'generate_video':
                params['required'] = [key for key in params.get('required', []) if key != 'prompt']
                params['anyOf'] = [{'required': ['prompt']}, {'required': ['chat_text_source']}]
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
                or spec.name in {'plan_ecommerce_images', 'get_conversation_context'}
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
    """Quotes locate complete execution units; explicit fragments stay available."""
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
                assembled, proof = bundle(part['text'], text, scope=arguments.get('selection_scope', 'item'))
                matches.append({'message_id': str(message['message_id']),
                                'content_index': part['content_index'],
                                'sha256': hashlib.sha256(assembled.encode()).hexdigest(), 'text': assembled,
                                'bundle': proof})
        if len(matches) != 1:
            raise ValueError('CHAT_SOURCE_UNAVAILABLE_OR_AMBIGUOUS')
        if any((s['message_id'], s['content_index'], s['sha256']) ==
               (matches[0]['message_id'], matches[0]['content_index'], matches[0]['sha256']) for s in selected):
            raise ValueError('CHAT_SOURCE_OVERLAPPING_UNITS_SELECT_COMPLETE_ITEM')
        selected.append(matches[0])
    return selected


def remember_selection(state, result, *, append=False):
    sources = result.get('sources') or ([result['source']] if 'source' in result else [])
    if not sources:
        prompts = result.get('selected_prompts') or ([result['selected_prompt']] if 'selected_prompt' in result else [])
        sources = [{**item['source_prompt'], 'text': item['prompt']} for item in prompts]
    if not sources:
        return state
    existing = state.get('selected_sources', []) if append else []
    selected = {(source['message_id'], source['content_index'], source['sha256']): source
                for source in [*existing, *sources]}
    sources = list(selected.values())
    if len(sources) > 100 or sum(len(source['text'].encode()) for source in sources) > 100000:
        raise ValueError('CHAT_SOURCE_TOO_LARGE_SELECT_FEWER_FRAGMENTS')
    updated = {**state, 'source_selection_failed': False,
               'selected_sources': [{**source, 'mode': 'exact'} for source in sources]}
    updated.pop('selected_source', None)
    updated.pop('stop_message', None)
    return updated


def selected_source(state, arguments):
    sources = state.get('selected_sources') or ([state['selected_source']] if state.get('selected_source') else [])
    explicit = arguments.get('chat_text_source')
    if explicit is not None:
        if not isinstance(explicit, dict):
            raise ValueError('CHAT_SOURCE_INVALID')
        if (not {'message_id', 'content_index', 'mode'} <= set(explicit)
                or set(explicit) - {'message_id', 'content_index', 'mode', 'text', 'sha256', 'bundle'}
                or type(explicit['content_index']) is not int or explicit['content_index'] < 0):
            raise ValueError('CHAT_SOURCE_INVALID')
        if 'text' in explicit:
            return explicit
        matches = [source for source in sources if all((source.get(key) == value
                   or key == 'sha256' and source.get('bundle', {}).get('selection', {}).get('sha256') == value)
                   for key, value in explicit.items() if key != 'mode')]
        if len(matches) != 1:
            raise ValueError('CHAT_SOURCE_SELECTION_REQUIRED')
        return {**matches[0], 'mode': explicit.get('mode', 'exact')}
    if not sources:
        return None
    prompt = arguments.get('prompt')
    matches = [source for source in sources if source['text'] == prompt or isinstance(prompt, str)
               and source.get('bundle', {}).get('selection', {}).get('sha256') == hashlib.sha256(prompt.encode()).hexdigest()]
    if len(matches) == 1:
        return matches[0]
    if len(sources) == 1:
        return sources[0]
    raise ValueError('CHAT_SOURCE_SELECTION_REQUIRED')


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
    if message_id is not None and not rows:
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
                 if isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str)]
        messages.append({'message_id': str(row['id']), 'parts': parts})
        for part in parts:
            item = {'message_id': str(row['id']), 'role': row['role'],
                    'content_index': part['content_index'], 'text': part['text'][:20000]}
            if len(part['text']) > 20000:
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


def bind_call(db, task_id, context, name, arguments, source):
    from services.handlers.chat_content_bundle import verify, adapted_input
    if (name not in INPUT_TOOLS or not isinstance(source, dict)
            or not {'message_id', 'content_index', 'text', 'mode'} <= set(source)
            or set(source) - {'message_id', 'content_index', 'sha256', 'text', 'mode', 'bundle'}
            or type(source['content_index']) is not int or source['content_index'] < 0
            or source['mode'] not in ('exact', 'adapted')):
        raise ValueError('CHAT_SOURCE_INVALID')
    if 'bundle' in source:
        # Fetch through the existing scoped/frozen-history reader. Do not trust
        # checkpoint or model supplied offsets, spans or completeness claims.
        history = source_text(db, task_id, context, {'message_id': source['message_id']})
        part = next((item for item in history['messages'] if item['content_index'] == source['content_index']), None)
        if part is None or part.get('truncated'):
            raise ValueError('CHAT_SOURCE_UNAVAILABLE')
        verify(part['text'], source['text'], source['bundle'])
        verified = {key: value for key, value in source.items() if key != 'mode'}
        if verified.get('sha256', hashlib.sha256(verified['text'].encode()).hexdigest()) != hashlib.sha256(verified['text'].encode()).hexdigest():
            raise ValueError('CHAT_SOURCE_CHANGED')
    else:
        verified = source_text(db, task_id, context, {'message_id': source['message_id'],
            'content_index': source['content_index'], 'text_exact': source['text']})['source']
        if 'sha256' in source and hashlib.sha256(source['text'].encode()).hexdigest() != source['sha256']:
            raise ValueError('CHAT_SOURCE_CHANGED')
    result = dict(arguments)
    if name == 'code_execute':
        code = result.get('code')
        if not isinstance(code, str) or not code.strip():
            raise ValueError('CHAT_SOURCE_CODE_REQUIRED')
        # A Python string literal keeps chat text data separate from model-authored code.
        # Insert after any module docstring and future imports when the input is valid Python.
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
        lines.insert(offset, f"\nchat_source_text = {verified['text']!r}\n")
        result['code'] = ''.join(lines)
    elif source['mode'] == 'exact':
        if name == 'generate_image':
            if 'plan_source' in result or 'source_prompt' in result:
                raise ValueError('CHAT_SOURCE_IMAGE_REFERENCE_CONFLICT')
            result['source_prompt'] = {k: v for k, v in verified.items() if k != 'text'}
        result['prompt'] = verified['text']
    elif (name == 'generate_image' and ('plan_source' in result or 'source_prompt' in result)
          or not isinstance(result.get('prompt'), str) or not result['prompt'].strip()):
        raise ValueError('CHAT_SOURCE_PROMPT_REQUIRED')
    elif verified.get('bundle', {}).get('scope') == 'item':
        instruction = result['prompt']
        result['prompt'] = adapted_input(verified['text'], instruction)
        if name == 'generate_image':
            result['source_prompt'] = {**{k: v for k, v in verified.items() if k not in {'text', 'sha256'}},
                                      'sha256': hashlib.sha256(result['prompt'].encode()).hexdigest(),
                                      'adaptation': instruction}
    return result
