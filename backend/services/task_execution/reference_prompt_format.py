"""One model input format; fixed Markdown is rendered by the host, not authored twice."""
import copy
import json
import re

from .contracts import TaskError

PROFILE = 'reference-prompts-v1'
DISPLAY_FIELDS = {'scheme_content': '方案内容', 'visual_settings': '视觉设定',
                  'reference_usage': '参考图使用方式'}
PROMPT_FIELDS = {'positive': '完整生图提示词', 'negative': '负面提示词'}
MODEL_GUIDANCE = (
    '参考图方案使用结构化章节，固定标题、章节顺序和分隔由后台生成。'
    'document.reference_analysis填写参考图判断的完整条目正文；每项title填写方案标题，'
    'presentation填写scheme_content（方案内容）、visual_settings（视觉设定）、reference_usage（参考图使用方式），'
    'prompt填写positive（完整生图提示词）和negative（负面提示词）。'
    '每个字段只写该章节的完整正文，保留原项目符号和细节，不重复写Markdown章节标题，'
    '不写“见下方正文”等占位。不要提交display_prefix或body。'
    '后台按同一合同生成原版Markdown，执行只使用生成的完整提示词和负面提示词正文。'
    '修改旧稿先单独调用read_conversation_text读取指定条目及format_contract；'
    '复制读取回执content_source中的content_ref和item_index，用base和updates修订，不能重建另一组代替原稿。'
    'updates只列用户指定条目，只填变化字段；不改展示说明时省略presentation，'
    '不改执行正文时省略prompt；options若提供须保留未改选项，不换素材省略materials。'
    '修改规格时，同步修订prompt和presentation中与新规格冲突的要求，保留其他细节；不能只改options留下旧规格。'
    '只看方案或只改文字continue_execution=false；明确要求改稿并执行才设true，'
    '保存成功后按新回执执行，保存本身不派发生图。用户等待条件用confirmation保留，不授予批准。'
)


def section_schema(fields):
    return {'type': 'object', 'additionalProperties': False, 'required': list(fields),
            'properties': {key: {'type': 'string', 'minLength': 1, 'maxLength': 20000 if fields == PROMPT_FIELDS else 12000,
                'pattern': r'\S', 'description': title + '的完整正文，不写章节标题或占位。'}
                for key, title in fields.items()}}


def sections(text):
    headers = list(re.finditer(r'^#{1,6}[ \t]+([^\r\n]+)\r?$', text or '', re.MULTILINE))
    ends = [h.start() for h in headers[1:]] + [len(text or '')]
    return [(h[1].strip(), text[h.end():end].strip()) for h, end in zip(headers, ends)]


class FormatArgumentsError(TaskError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__('CONTENT_PRESENTATION_ARGUMENTS_INVALID')


def _render(value, fields, path):
    from services.tools.argument_validation import _issues
    errors = _issues(value, section_schema(fields), image_hints=False)
    if errors:
        raise FormatArgumentsError([{**e, 'path': path + e['path'][1:]} for e in errors])
    for key, text in value.items():
        if sections(text):
            raise FormatArgumentsError([{'path': path + '.' + key,
                'reason': '仅填写该章节正文，固定章节标题由后台生成'}])
    return '\n\n'.join('## ' + title + '\n\n' + value[key] for key, title in fields.items())


def _heading(title, index):
    return title if re.match(r'^方案\s*(?:\d+|[一二三四五六七八九十])', title) else f'方案{index}｜{title}'


def decode_save(value, item_title):
    """Only explicit wire fields are transformed; native saved records stay unchanged."""
    if not isinstance(value, dict):
        return value  # Native argument validation owns malformed root objects.
    result = copy.deepcopy(value)
    document = result.get('document')
    if isinstance(document, dict) and 'reference_analysis' in document:
        if 'display_prefix' in document:
            raise FormatArgumentsError([{'path': '$.document.display_prefix', 'reason': '不可混用原生Markdown与结构化章节'}])
        analysis = document.pop('reference_analysis')
        document['display_prefix'] = _render({'reference_analysis': analysis},
            {'reference_analysis': '参考图判断'}, '$.document').replace('## 参考图判断', '# 参考图判断', 1)
    items = [(item, index, f'$.document.items[{index - 1}]', item.get('title'))
             for index, item in enumerate(document.get('items', ()) if isinstance(document, dict) else (), 1)
             if isinstance(item, dict)]
    base = result.get('base') or {}
    if not isinstance(base, dict):
        base = {}
    for index, update in enumerate(result.get('updates', ())):
        if isinstance(update, dict) and isinstance(update.get('item'), dict):
            item = update['item']
            title = item.get('title') or item_title(base.get('artifact_id'), update.get('item_index'))
            items.append((item, update.get('item_index'), f'$.updates[{index}].item', title))
    for item, index, path, title in items:
        if 'presentation' in item:
            if 'display_prefix' in item or not isinstance(title, str) or not title.strip():
                raise FormatArgumentsError([{'path': path + '.presentation', 'reason': '需要真实条目标题，且不可混用display_prefix'}])
            item['display_prefix'] = '# ' + _heading(title, index) + '\n\n' + _render(
                item.pop('presentation'), DISPLAY_FIELDS, path + '.presentation')
        if 'prompt' in item:
            if 'body' in item:
                raise FormatArgumentsError([{'path': path + '.prompt', 'reason': '不可混用prompt与body'}])
            item['body'] = _render(item.pop('prompt'), PROMPT_FIELDS, path + '.prompt')
    return result


def encode_item(item):
    result = copy.deepcopy(item)
    for native, public, fields in [('display_prefix', 'presentation', DISPLAY_FIELDS), ('body', 'prompt', PROMPT_FIELDS)]:
        if native not in result:
            continue
        parsed = sections(result[native])
        if native == 'display_prefix':
            parsed = parsed[1:]  # Existing format validation already checked its title.
        if [name for name, _ in parsed] != list(fields.values()):
            raise FormatArgumentsError([{'path': '$.item.' + native, 'reason': '旧稿章节与保存的格式合同不一致，请核实原稿'}])
        result[public] = dict(zip(fields, (body for _, body in parsed)))
        result.pop(native)
    return result


def encode_example(value):
    result = copy.deepcopy(value)
    if isinstance(result.get('document'), dict):
        doc = result['document']
        if 'display_prefix' in doc:
            doc['reference_analysis'] = sections(doc.pop('display_prefix'))[0][1].rstrip('\n-')
        doc['items'] = [encode_item(item) for item in doc.get('items', ())]
    for update in result.get('updates', ()):
        update['item'] = encode_item(update['item'])
    return result


def project_tool(function):
    """Project schemas, descriptions and every example together, after native tool selection."""
    from .methods import REFERENCE_PROMPTS_PRESENTATION
    if not function.get('description', '').startswith(REFERENCE_PROMPTS_PRESENTATION):
        return function
    function = copy.deepcopy(function)
    parameters = function['parameters']

    def shape(node):
        if isinstance(node, list):
            return [shape(v) for v in node]
        if not isinstance(node, dict):
            return node
        result = {k: shape(v) for k, v in node.items()}
        props = result.get('properties', {})
        renamed = {}
        if result.get('title') == 'ContentDocument':
            renamed['display_prefix'] = 'reference_analysis'
            props['reference_analysis'] = section_schema({'reference_analysis': '参考图判断'})['properties']['reference_analysis']
        if isinstance(result.get('title'), str) and result['title'] in {'ContentItem', 'ContentItemPatch'}:
            renamed.update(display_prefix='presentation', body='prompt')
            props['presentation'] = section_schema(DISPLAY_FIELDS)
            props['prompt'] = section_schema(PROMPT_FIELDS)
            props['title']['description'] = '方案标题，由后台生成固定标题行；修订时未改标题则省略。'
        for old in renamed:
            props.pop(old, None)
        if 'required' in result:
            result['required'] = [renamed.get(k, k) for k in result['required']]
        return result

    examples = [encode_example(v) for v in parameters.get('examples', ())]
    function['parameters'] = shape(parameters)
    function['parameters']['examples'] = examples
    function['description'] = MODEL_GUIDANCE + ' 完整JSON案例（身份和正文须按真实目录及用户要求填写）：' + json.dumps(examples, ensure_ascii=False)
    return function
