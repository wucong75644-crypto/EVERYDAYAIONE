"""One model input format; fixed Markdown is rendered by the host, not authored twice."""
import copy
import json
import re

from .contracts import TaskError, Fact
from pydantic import model_validator

PROFILE = 'reference-prompts-v1'
RENDERED_LIMITS = {'prompt': 20000, 'presentation': 12000, 'reference_analysis': 12000}
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
    f'长度按后台渲染后的字符总数计算（包括固定标题和分隔）：prompt合计不超过{RENDERED_LIMITS["prompt"]}，'
    f'presentation合计不超过{RENDERED_LIMITS["presentation"]}，reference_analysis连同标题不超过{RENDERED_LIMITS["reference_analysis"]}；不是每个子字段各有总预算。'
    '修改旧稿先单独调用read_conversation_text读取指定条目及format_contract；'
    '复制读取回执content_source中的content_ref和item_index，用base和updates修订，不能重建另一组代替原稿。'
    '新建必须填写全部章节。修订updates只列用户指定条目；presentation和prompt是章节补丁，只填改变的子字段，'
    '省略的章节由后台从本次已读取且版本一致的原稿保留；传入章节必须给出该章节的完整新正文。'
    '不改展示说明时省略presentation，'
    '不改执行正文时省略prompt；options若提供须保留未改选项，不换素材省略materials。'
    '新建每条素材放document.items[].materials，修订素材放updates[].materials，顶层没有materials。'
    '修订updates每项的item_index与prompt、presentation、options、materials直接并列，不加item包装。'
    '修改规格时，同步修订prompt和presentation中与新规格冲突的要求，保留其他细节；不能只改options留下旧规格。'
    '只看方案或只改文字continue_execution=false；明确要求改稿并执行才设true，'
    '保存成功后按新回执执行，保存本身不派发生图。用户等待条件用confirmation保留，不授予批准。'
    '案例正文是独立短稿，仅说明合法结构，不是原稿替代品；实际修订必须基于读取的完整内容。'
)


def section_schema(fields, *, patch=False):
    return {'type': 'object', 'additionalProperties': False,
            'description': '所有子章节连同后台生成的固定标题和分隔合计受rendered_limits约束，不按每个子字段分别分配总额度。',
            'required': [] if patch else list(fields), **({'minProperties': 1} if patch else {}),
            'properties': {key: {'type': 'string', 'minLength': 1, 'maxLength': RENDERED_LIMITS['prompt' if fields == PROMPT_FIELDS else 'presentation'],
                'pattern': r'\S', 'description': title + '的完整正文，不写章节标题或占位。'}
                for key, title in fields.items()}}



class ReferenceSectionPatch(Fact):
    presentation: dict[str, str] | None = None
    prompt: dict[str, str] | None = None

    @model_validator(mode='after')
    def valid_sections(self):
        from services.tools.argument_validation import _issues
        if not self.model_fields_set:
            raise ValueError('CONTENT_SECTION_PATCH_EMPTY')
        for name, fields in [('presentation', DISPLAY_FIELDS), ('prompt', PROMPT_FIELDS)]:
            if name in self.model_fields_set:
                value = getattr(self, name)
                if not value or _issues(value, section_schema(fields, patch=True), image_hints=False):
                    raise ValueError('CONTENT_SECTION_PATCH_INVALID')
                if any(sections(text) for text in value.values()):
                    raise ValueError('CONTENT_SECTION_PATCH_HEADERS')
        return self

def sections(text):
    headers = list(re.finditer(r'^#{1,6}[ \t]+([^\r\n]+)\r?$', text or '', re.MULTILINE))
    ends = [h.start() for h in headers[1:]] + [len(text or '')]
    return [(h[1].strip(), text[h.end():end].strip()) for h, end in zip(headers, ends)]


class FormatArgumentsError(TaskError):
    def __init__(self, issues, *, code='CONTENT_PRESENTATION_ARGUMENTS_INVALID'):
        self.issues = issues
        super().__init__(code)


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


def _bounded(text, field, path):
    limit = RENDERED_LIMITS[field]
    if len(text) > limit:
        raise FormatArgumentsError([{'path': path, 'reason':
            f'后台生成固定标题和分隔后合计{len(text)}字符，超过{limit}；保留原稿，按容量分次保存，不自动截断'}])
    return text


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
        document['display_prefix'] = _bounded(_render({'reference_analysis': analysis},
            {'reference_analysis': '参考图判断'}, '$.document').replace('## 参考图判断', '# 参考图判断', 1),
            'reference_analysis', '$.document.reference_analysis')
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
        deferred = {}
        is_revision = path.startswith('$.updates[')
        for name, fields in [('presentation', DISPLAY_FIELDS), ('prompt', PROMPT_FIELDS)]:
            part = item.get(name)
            if is_revision and isinstance(part, dict) and set(part) != set(fields):
                from services.tools.argument_validation import _issues
                errors = _issues(part, section_schema(fields, patch=True), image_hints=False)
                if not part or errors:
                    raise FormatArgumentsError([{'path': path + '.' + name + e['path'][1:], 'reason': e['reason']}
                        for e in errors] or [{'path': path + '.' + name, 'reason': '章节补丁至少填写一个非空章节'}])
                native = 'display_prefix' if name == 'presentation' else 'body'
                if native in item or 'reference_sections' in item:
                    raise FormatArgumentsError([{'path': path + '.' + name, 'reason': '不可混用原生正文与章节补丁'}])
                deferred[name] = item.pop(name)
        if deferred:
            try:
                item['reference_sections'] = ReferenceSectionPatch.model_validate(deferred).model_dump(exclude_unset=True)
            except ValueError:
                raise FormatArgumentsError([{'path': path, 'reason': '章节补丁字段、正文或标题格式错误'}]) from None
        if 'presentation' in item:
            if 'display_prefix' in item or not isinstance(title, str) or not title.strip():
                raise FormatArgumentsError([{'path': path + '.presentation', 'reason': '需要真实条目标题，且不可混用display_prefix'}])
            item['display_prefix'] = _bounded('# ' + _heading(title, index) + '\n\n' + _render(
                item.pop('presentation'), DISPLAY_FIELDS, path + '.presentation'), 'presentation', path + '.presentation')
        if 'prompt' in item:
            if 'body' in item:
                raise FormatArgumentsError([{'path': path + '.prompt', 'reason': '不可混用prompt与body'}])
            item['body'] = _bounded(_render(item.pop('prompt'), PROMPT_FIELDS, path + '.prompt'), 'prompt', path + '.prompt')
    return result


def encode_item(item):
    result = copy.deepcopy(item)
    patch = result.pop('reference_sections', None)
    if patch:
        result.update(patch)
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
    function = copy.deepcopy(function)
    parameters = function['parameters']
    if parameters.pop('x-content-profile', None) != PROFILE:
        return function

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
            patch = result['title'] == 'ContentItemPatch'
            props['presentation'] = section_schema(DISPLAY_FIELDS, patch=patch)
            props['prompt'] = section_schema(PROMPT_FIELDS, patch=patch)
            props['title']['description'] = '方案标题，由后台生成固定标题行；修订时未改标题则省略。'
        if isinstance(result.get('title'), str) and result['title'] in {'ContentItem', 'ContentItemPatch'}:
            result['description'] = '方案条目；正文使用prompt，展示说明使用presentation。'
        if 'updates' in props and isinstance(props['updates'], dict):
            props['updates']['description'] = '仅列用户选定且改变的条目；item使用prompt和presentation章节补丁。省略字段保留原稿。'
        for old in renamed:
            props.pop(old, None)
        if 'required' in result:
            result['required'] = [renamed.get(k, k) for k in result['required']]
        return result

    examples = [encode_example(v) for v in parameters.get('examples', ())]
    if examples and 'base' in examples[0]:
        partial = copy.deepcopy(examples[0])
        partial['updates'][0]['item'] = {'prompt': {'positive': '单件产品居中展示，柔和自然光，产品边缘完整，表面纹理清晰。'}}
        examples.append(partial)
    function['parameters'] = shape(parameters)
    function['parameters']['examples'] = examples
    function['description'] = MODEL_GUIDANCE
    return function


def workflow_guidance(workflow):
    state = workflow['state']
    if workflow['revision_required']:
        return (' 当前已选稿件的修订流程（后台核验状态，不是新的选图建议）：'
            + json.dumps(state, ensure_ascii=False)
            + '。read_calls里的arguments可直接调用；成功读取后按本工具JSON结构修订。'
            '要求生成时仅用保存成功的新回执，不调用旧版本。')
    return (' 仅在本次需要修订所选旧稿时，先按以下已核验参数读取：'
        + json.dumps(state['read_calls'], ensure_ascii=False) + '。原样执行旧稿无需额外读取或保存。')


def workflow_examples(examples, workflow):
    """Illustrate only the offered stage, never introduce a different design or size."""
    rows = [row for row in workflow.get('items', ()) if row.get('required_update_fields')]
    if not rows:
        return examples
    sample = next((copy.deepcopy(value) for value in examples if value.get('updates')), None)
    if sample is None:
        return []
    row = rows[0]
    fields = row['required_update_fields']
    size = {**row.get('saved_options', {}), **row.get('requested_size', {})}
    specification = '、'.join(f'{key}={size[key]}' for key in ('aspect_ratio', 'resolution') if key in size)
    positive = '保留产品外形、数量、封面图案与文字、背景、拍摄视角和光影。' + (
        '输出规格：' + specification + '。' if specification else '产品居中，边缘完整，细节清晰。')
    negative = '禁止改变产品结构、数量与封面文字；禁止水印、模糊和拉伸变形。'
    sample['base'] = copy.deepcopy(row['content_source'])
    item = {}
    sample['updates'] = [{'item_index': row['content_source']['item_index'], 'item': item}]
    if 'options' in fields:
        item['options'] = {**row.get('saved_options', {}), **{key: value for key, value in row.get('requested_size', {}).items()
            if key in {'aspect_ratio', 'resolution'}}}
    if 'materials' in fields:
        item['materials'] = copy.deepcopy(row.get('saved_materials', []))
        for material in row['required_changes']['materials']:
            if material not in item['materials']:
                item['materials'].append(copy.deepcopy(material))
    if 'prompt' in fields:
        item['prompt'] = {'positive': positive, 'negative': negative}
    if 'presentation' in fields:
        item['presentation'] = {
            'scheme_content': '单件产品展示，封面图案与文字清楚可见，产品轮廓完整。',
            'visual_settings': '柔和自然光，表面纹理清晰，阴影自然，颜色准确。',
            'reference_usage': '沿用参考图的产品结构、背景、拍摄视角和光影。' + (
                '输出规格：' + specification + '。' if specification else '')}
    if 'body' in fields:
        item['body'] = positive + '\n' + negative
    if 'display_prefix' in fields:
        item['display_prefix'] = '单件产品展示，柔和自然光，产品结构与封面文字清晰。' + specification
    sample['continue_execution'] = workflow.get('continue_execution', False)
    return [sample]


def save_recovery_fields(parameters, roots):
    """Recover only a form the current model contract actually offers."""
    props = parameters.get('properties', {})
    forms = [list(branch.get('required', ())) for branch in parameters.get('oneOf', ())]
    if not forms and parameters.get('required'):
        forms = [list(parameters['required'])]
    forms = [form for form in forms if form and set(form) <= props.keys()]
    selected = [form for form in forms if set(form) & roots]
    form = selected[0] if len(selected) == 1 else forms[0] if len(forms) == 1 else []
    required = {}
    def collect(node, path):
        if node.get('required'):
            required[path] = list(node['required'])
        for key, child in node.get('properties', {}).items():
            array = child if 'items' in child else next((b for b in child.get('anyOf', ()) if 'items' in b), None)
            if array is not None:
                collect(array['items'], path + '.' + key + '[]')
    for key in form:
        collect(props[key], key)
        if props[key].get('items'):
            collect(props[key]['items'], key + '[]')
    return required, forms
