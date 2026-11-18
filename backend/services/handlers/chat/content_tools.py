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
                 'text_exact 成功后后台保存选择并自动绑定后续内容工具。改编时显式传 mode=adapted。',
           {'message_id': {'type': 'string'}, 'text_exact': {'type': 'string', 'maxLength': 20000}}),
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
                'required': ['message_id', 'content_index', 'sha256', 'text', 'mode'],
                'properties': {
                    'message_id': {'type': 'string'}, 'content_index': {'type': 'integer', 'minimum': 0},
                    'sha256': {'type': 'string'}, 'text': {'type': 'string', 'maxLength': 20000},
                    'mode': {'enum': ['exact', 'adapted']},
                },
                'description': ('read_conversation_text 返回的来源和原文。exact 沿用原文，adapted 表示用户要求改编。'
                                'code_execute 中用 chat_source_text 读取后台注入的正文，调用 emit_file 交付文件。'),
            }
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
    exact = args.get('text_exact')
    if 'text_exact' in args and (not isinstance(exact, str) or not 1 <= len(exact) <= 20000):
        raise ValueError('CHAT_SOURCE_TEXT_INVALID')
    selected, history = [], []
    for row in rows:
        from services.handlers.chat_context.image_sources import content_parts
        for index, part in enumerate(content_parts(row['content'])):
            if not isinstance(part, dict) or part.get('type') != 'text' or not isinstance(part.get('text'), str):
                continue
            text = part['text']
            if exact is None:
                history.append({'message_id': str(row['id']), 'role': row['role'], 'content_index': index,
                                'text': text[:20000]})
            elif (args.get('content_index', index) == index and text.count(exact) == 1):
                selected.append({'message_id': str(row['id']), 'content_index': index,
                                 'sha256': hashlib.sha256(exact.encode()).hexdigest(), 'text': exact})
    if exact is not None:
        if len(selected) != 1:
            raise ValueError('CHAT_SOURCE_UNAVAILABLE_OR_AMBIGUOUS')
        return {'source': selected[0]}
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
    if (name not in INPUT_TOOLS or not isinstance(source, dict)
            or set(source) != {'message_id', 'content_index', 'sha256', 'text', 'mode'}
            or type(source['content_index']) is not int or source['content_index'] < 0
            or source['mode'] not in ('exact', 'adapted')):
        raise ValueError('CHAT_SOURCE_INVALID')
    verified = source_text(db, task_id, context, {'message_id': source['message_id'],
        'content_index': source['content_index'], 'text_exact': source['text']})['source']
    if verified != {key: value for key, value in source.items() if key != 'mode'}:
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
    return result
