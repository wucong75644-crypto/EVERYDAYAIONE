"""Model-proposed input mapping, host-verified sources and lossless assembly.

No headings, field labels or scene classifiers determine content purpose here.
Coverage proves accounting, not correctness of the model's semantic decisions.
"""
from copy import deepcopy
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def text_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def line_starts(text):
    offsets, position = [], 0
    for line in text.splitlines(keepends=True):
        offsets.append(position)
        position += len(line)
    return offsets


def mapping_view(part):
    """Display source coordinates once; line numbers never decide purpose."""
    text = part['text']
    return {**{key: value for key, value in part.items() if key not in {'text', 'line_starts'}},
            'numbered_text': ''.join(f'{i}|{line}' for i, line in enumerate(text.splitlines(keepends=True), 1)),
            'line_count': len(text.splitlines(keepends=True))}


def model_schema(parameters, *, file_input=False, reserved=()):
    targets = [name for name in parameters.get('properties', {})
               if name not in {'chat_input', 'chat_text_source', *reserved}]
    if file_input:
        targets.append('chat_source_text')
    return {'type': 'object', 'additionalProperties': False, 'required': ['sources', 'parts'],
        'description': ('引用聊天内容执行任务时提交输入映射。sources引用完整原文part；parts逐段说明进入哪个真实参数、'
                        '排除原因或被哪条修订覆盖，须覆盖全部原文（含空白）。正文用位置或行号引用，勿重抄；'
                        'for_mapping返回numbered_text供定位，行号不是原文，first_line/last_line为1起且含首尾行。'
                        '同一来源/版本复用已读取位置，不重复读整份方案。分析说明是否进入输入取决于本次任务，不取决于标题。'
                        '模型负责用途判断，后台只核验来源、覆盖和参数。'
                        '文件正文映射到chat_source_text，在code中读取；materials登记素材参数，原生工具仍核验权限和文件。'),
        'properties': {
            'mode': {'type': 'string', 'enum': ['exact', 'adapted'],
                     'description': '默认exact逐字交付；仅用户要求总结、翻译、改编等转换时用adapted并说明reason'},
            'reason': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
            'sources': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['message_id', 'content_index'], 'properties': {
                    'message_id': {'type': 'string'}, 'content_index': {'type': 'integer', 'minimum': 0},
                    'sha256': {'type': 'string'},
                    'context_revision': {'type': ['integer', 'null'], 'description': '读取结果的原版本；省略时由后台绑定'},
                }}},
            'parts': {'type': 'array', 'minItems': 1, 'maxItems': 256, 'items': {
                'type': 'object', 'additionalProperties': False, 'required': ['source', 'action'],
                'properties': {
                    'source': {'type': 'integer', 'minimum': 0},
                    'start': {'type': 'integer', 'minimum': 0, 'description': '原文字符起点，0起；与end组成左闭右开区间'},
                    'end': {'type': 'integer', 'minimum': 1},
                    'first_line': {'type': 'integer', 'minimum': 1}, 'last_line': {'type': 'integer', 'minimum': 1},
                    'text_exact': {'type': 'string'},
                    'action': {'type': 'string', 'enum': ['text', 'value', 'exclude', 'superseded']},
                    'target': {'type': 'string', 'enum': targets},
                    'value': {}, 'reason': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
                    'separator': {'type': 'string', 'enum': ['', '\n', '\n\n'],
                                  'description': '同一参数多段text间的连接符，默认两个换行；保留代码/原格式时可用空串'},
                    'superseded_by': {'type': 'integer', 'minimum': 0},
                }}},
            'materials': {'type': 'array', 'maxItems': 32, 'items': {
                'type': 'object', 'additionalProperties': False, 'required': ['target', 'value', 'reason'],
                'properties': {'target': {'type': 'string', 'enum': targets}, 'value': {},
                               'reason': {'type': 'string', 'minLength': 1, 'maxLength': 1000}}}},
        }}


def _range(text, part):
    styles = [bool({'start', 'end'} & part.keys()), bool({'first_line', 'last_line'} & part.keys()),
              'text_exact' in part]
    if sum(styles) != 1:
        raise ValueError('CHAT_INPUT_RANGE_INVALID')
    if styles[0]:
        start, end = part.get('start'), part.get('end')
    elif styles[1]:
        first, last = part.get('first_line'), part.get('last_line')
        offsets = line_starts(text)
        if type(first) is not int or type(last) is not int or not 1 <= first <= last <= len(offsets):
            raise ValueError('CHAT_INPUT_RANGE_INVALID')
        start, end = offsets[first-1], offsets[last] if last < len(offsets) else len(text)
    else:
        quote = part['text_exact']
        if not isinstance(quote, str) or not quote or text.count(quote) != 1:
            raise ValueError('CHAT_INPUT_RANGE_AMBIGUOUS')
        start, end = text.index(quote), text.index(quote) + len(quote)
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        raise ValueError('CHAT_INPUT_RANGE_INVALID')
    return start, end


def assemble(mapping, read_source, parameters, *, file_input=False, reserved=()):
    """Return exact target values plus a canonical, reproducible mapping proof."""
    if (not isinstance(mapping, dict) or set(mapping) - {'sources', 'parts', 'materials', 'mode', 'reason'}
            or not isinstance(mapping.get('sources'), list) or not 1 <= len(mapping['sources']) <= 8
            or not isinstance(mapping.get('parts'), list) or not 1 <= len(mapping['parts']) <= 256):
        raise ValueError('CHAT_INPUT_MAPPING_INVALID')
    mode = mapping.get('mode', 'exact')
    if mode not in {'exact', 'adapted'}:
        raise ValueError('CHAT_INPUT_MODE_INVALID')
    if (mode == 'adapted' or 'reason' in mapping) and (not isinstance(mapping.get('reason'), str) or not 1 <= len(mapping['reason'].strip()) <= 1000):
        raise ValueError('CHAT_INPUT_TRANSFORMATION_REASON_REQUIRED')
    texts, sources, revisions = [], [], []
    for source in mapping['sources']:
        if (not isinstance(source, dict) or not {'message_id', 'content_index'} <= source.keys()
                or source.keys() - {'message_id', 'content_index', 'sha256', 'context_revision'}
                or type(source['content_index']) is not int or source['content_index'] < 0):
            raise ValueError('CHAT_INPUT_SOURCE_INVALID')
        original = read_source(source)
        text = original['text']
        if not isinstance(text, str) or source.get('sha256', text_hash(text)) != text_hash(text):
            raise ValueError('CHAT_INPUT_SOURCE_CHANGED')
        revision = original.get('context_revision')
        if ('context_revision' in source and (source['context_revision'] != revision
                or source['context_revision'] is not None and type(source['context_revision']) is not int)):
            raise ValueError('CHAT_INPUT_SOURCE_CHANGED')
        canonical = {**source, 'sha256': text_hash(text), 'context_revision': revision}
        if any(s['message_id'] == source['message_id'] and s['content_index'] == source['content_index'] for s in sources):
            raise ValueError('CHAT_INPUT_SOURCE_DUPLICATE')
        texts.append(text); sources.append(canonical); revisions.append(original.get('context_revision'))
    if sum(len(text.encode()) for text in texts) > 100000:
        raise ValueError('CHAT_INPUT_TOO_LARGE')
    properties = parameters.get('properties', {})
    intervals = [[] for _ in sources]
    parts, targets = [], {}
    for part in mapping['parts']:
        if (not isinstance(part, dict) or part.keys() - {'source', 'start', 'end', 'first_line', 'last_line',
                'text_exact', 'action', 'target', 'value', 'reason', 'superseded_by', 'separator'}):
            raise ValueError('CHAT_INPUT_PART_INVALID')
        index, action = part.get('source'), part.get('action')
        if type(index) is not int or not 0 <= index < len(texts) or action not in {'text', 'value', 'exclude', 'superseded'}:
            raise ValueError('CHAT_INPUT_PART_INVALID')
        start, end = _range(texts[index], part)
        intervals[index].append((start, end))
        canonical = {'source': index, 'start': start, 'end': end, 'action': action}
        if 'separator' in part:
            if action != 'text' or part['separator'] not in ('', '\n', '\n\n'):
                raise ValueError('CHAT_INPUT_SEPARATOR_INVALID')
            canonical['separator'] = part['separator']
        if action in {'exclude', 'superseded', 'value'} or 'reason' in part:
            reason = part.get('reason')
            if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
                raise ValueError('CHAT_INPUT_REASON_REQUIRED')
            canonical['reason'] = reason
        if action == 'exclude' and 'target' in part:
            raise ValueError('CHAT_INPUT_PART_INVALID')
        if action != 'exclude':
            target = part.get('target')
            if not isinstance(target, str) or target not in properties and not (file_input and target == 'chat_source_text'):
                raise ValueError('CHAT_INPUT_TARGET_UNAVAILABLE')
            if target in {'chat_input', 'chat_text_source', *reserved}:
                raise ValueError('CHAT_INPUT_TARGET_UNAVAILABLE')
            canonical['target'] = target
            if action == 'superseded':
                canonical['superseded_by'] = part.get('superseded_by')
            else:
                value = texts[index][start:end] if action == 'text' else part.get('value')
                if action == 'value' and 'value' not in part:
                    raise ValueError('CHAT_INPUT_VALUE_REQUIRED')
                if target == 'chat_source_text' and not isinstance(value, str):
                    raise ValueError('CHAT_INPUT_TEXT_TARGET_REQUIRED')
                if (mode == 'exact' and action == 'value' and isinstance(value, str)
                        and not properties.get(target, {}).get('enum')):
                    raise ValueError('CHAT_INPUT_TEXT_MUST_BE_REFERENCED')
                if action == 'text' and target != 'chat_source_text':
                    from services.tools.argument_validation import _issues
                    if _issues(value, properties[target]):
                        raise ValueError('CHAT_INPUT_TEXT_TARGET_REQUIRED')
                if target in targets:
                    previous, previous_action = targets[target]
                    if action != 'text' or previous_action != 'text':
                        raise ValueError('CHAT_INPUT_TARGET_CONFLICT')
                    targets[target] = (previous + part.get('separator', '\n\n') + value, action)
                else:
                    targets[target] = (value, action)
                if action == 'value':
                    canonical['value'] = deepcopy(value)
        if action != 'value' and 'value' in part or action != 'superseded' and 'superseded_by' in part:
            raise ValueError('CHAT_INPUT_PART_INVALID')
        parts.append(canonical)
    for index, ranges in enumerate(intervals):
        cursor = 0
        for start, end in sorted(ranges):
            if start < cursor:
                raise ValueError('CHAT_INPUT_COVERAGE_OVERLAP')
            if texts[index][cursor:start]:
                raise ValueError('CHAT_INPUT_COVERAGE_MISSING')
            cursor = end
        if texts[index][cursor:]:
            raise ValueError('CHAT_INPUT_COVERAGE_MISSING')
    for part in parts:
        if part['action'] != 'superseded':
            continue
        replacement = part['superseded_by']
        if type(replacement) is not int or not 0 <= replacement < len(parts):
            raise ValueError('CHAT_INPUT_REVISION_INVALID')
        newer = parts[replacement]
        before, after = revisions[part['source']], revisions[newer['source']]
        if (newer['action'] not in {'text', 'value'} or newer['target'] != part['target']
                or type(before) is int and type(after) is int and after < before):
            raise ValueError('CHAT_INPUT_REVISION_INVALID')
    materials = mapping.get('materials', [])
    if not isinstance(materials, list) or len(materials) > 32:
        raise ValueError('CHAT_INPUT_MATERIAL_INVALID')
    for material in materials:
        if (not isinstance(material, dict) or set(material) != {'target', 'value', 'reason'}
                or not isinstance(material['target'], str) or material['target'] not in properties or material['target'] in targets
                or material['target'] in {'chat_text_source', 'chat_input', *reserved}
                or not isinstance(material['reason'], str) or not 1 <= len(material['reason'].strip()) <= 1000):
            raise ValueError('CHAT_INPUT_MATERIAL_INVALID')
        # A locator can be a string, an array or a structured object. Its real
        # tool schema/resolver owns validation; JSON shape does not decide use.
        targets[material['target']] = (deepcopy(material['value']), 'value')
    if not targets:
        raise ValueError('CHAT_INPUT_NO_EXECUTION_CONTENT')
    outputs = {key: value for key, (value, _) in targets.items()}
    canonical_mapping = {'sources': sources, 'parts': parts, 'materials': deepcopy(materials), 'mode': mode}
    if 'reason' in mapping:
        canonical_mapping['reason'] = mapping['reason']
    proof = {'version': 2, 'mapping': canonical_mapping,
             'outputs_sha256': digest(outputs)}
    return outputs, proof


def check_outputs(outputs, arguments):
    for key, value in outputs.items():
        if key == 'chat_source_text':
            continue
        if key not in arguments or digest(arguments[key]) != digest(value):
            raise ValueError('CHAT_INPUT_TARGET_CHANGED')


def legacy_mapping(source, document, *, mode='exact', target='prompt', instruction=None):
    """Translate whole-part legacy input; partial use needs an explicit purpose map.

    Historical spans prove bytes, not which surrounding analysis belongs in a
    new task. Never infer those decisions from a saved v1 item boundary.
    """
    text = document['text']
    if source['text'] != text:
        raise ValueError('CHAT_INPUT_MAPPING_REQUIRED')
    part = {'source': 0, 'start': 0, 'end': len(text), 'action': 'text', 'target': target}
    mapping = {'sources': [{'message_id': source['message_id'], 'content_index': source['content_index'],
                           'sha256': text_hash(text), 'context_revision': document.get('context_revision')}],
               'parts': [part], 'mode': mode}
    if mode == 'adapted':
        mapping['reason'] = '沿用旧入口显式改编契约，保留完整原稿及改编要求'
        if target != 'chat_source_text':
            if not isinstance(instruction, str) or not instruction.strip():
                raise ValueError('CHAT_SOURCE_PROMPT_REQUIRED')
            from services.handlers.chat_content_bundle import adapted_input
            part.update(action='value', value=adapted_input(text, instruction), reason=mapping['reason'])
    return mapping
