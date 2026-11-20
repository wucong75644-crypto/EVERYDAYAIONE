"""Decode and revalidate historical input mappings and their frozen evidence."""
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


def rebind_call(db, task_id, context, name, arguments, source):
    """Revalidate an existing checkpoint only; no new model locator conversion."""
    if source.get('kind') == 'input_mapping':
        return rebind_mapping(db,task_id,context,name,arguments,source['mapping'])[0]
    document=_read_checkpoint_document(db,task_id,context,source)
    original=dict(arguments)
    instruction=original.pop('prompt',None) if name!='code_execute' else None
    mapping=legacy_mapping(source,document,mode=source.get('mode','exact'),
        target='chat_source_text' if name=='code_execute' else 'prompt',instruction=instruction)
    return rebind_mapping(db,task_id,context,name,original,mapping)[0]


def _read_checkpoint_document(db, task_id, context, source):
    from services.handlers.chat.content_tools import source_text

    history = source_text(db, task_id, context, {'message_id': source['message_id'], '_full': True})
    row = next((item for item in history['messages'] if item['content_index'] == source['content_index']), None)
    if row is None or row.get('requires_explicit_read'):
        raise ValueError('CHAT_INPUT_SOURCE_UNAVAILABLE')
    return row


def rebind_mapping(db, task_id, context, name, arguments, mapping):
    from services.handlers.chat.content_tools import inject_source_code
    from services.tools.catalog import build_tool_catalog
    from services.tools.spec import Exposure
    from services.tools.argument_validation import _issues
    spec = build_tool_catalog().require(name)
    schema = spec.to_schema()
    if spec.exposure is not Exposure.PUBLIC or not schema:
        raise ValueError('CHAT_INPUT_TOOL_UNAVAILABLE')
    parameters = schema['function']['parameters']
    outputs, proof = assemble(mapping, lambda source: _read_checkpoint_document(db, task_id, context, source),
                              parameters, file_input=name == 'code_execute',
                              reserved=('source_prompt', 'plan_source') if name == 'generate_image' else ())
    reserved = {'chat_input', 'chat_text_source'} | ({'source_prompt', 'plan_source'} if name == 'generate_image' else set())
    if reserved.intersection(arguments):
        raise ValueError('CHAT_INPUT_SOURCE_CONFLICT')
    result = dict(arguments)
    document_text = outputs.pop('chat_source_text', None)
    for target, value in outputs.items():
        if target in result and digest(result[target]) != digest(value):
            raise ValueError('CHAT_INPUT_TARGET_CONFLICT')
        result[target] = value
    if document_text is not None:
        if name != 'code_execute' or not isinstance(result.get('code'), str):
            raise ValueError('CHAT_INPUT_FILE_CODE_REQUIRED')
        # Reuse the same literal injection as legacy source binding. The
        # mapped text is data, never substituted into model-authored code.
        result['code'] = inject_source_code(result['code'], document_text)
    issues = _issues(result, parameters)
    if issues:
        raise ValueError('CHAT_INPUT_ARGUMENTS_INVALID:' + json.dumps(issues, ensure_ascii=False))
    proof = {**proof, 'tool': name}
    if name == 'generate_image':
        result['source_prompt'] = {'handoff': proof}
    bound_text = document_text if document_text is not None else outputs.get('prompt', '')
    bound_text = bound_text if isinstance(bound_text, str) else ''
    source = {'kind': 'input_mapping', 'mapping': proof['mapping'], 'proof': proof,
              'input_arguments': deepcopy(arguments),
              'mode': proof['mapping']['mode'], 'text': bound_text, 'sha256': text_hash(bound_text)}
    return result, source
