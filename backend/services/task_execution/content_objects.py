"""Versioned content in the existing Artifact ledger; one rendered/executable source."""
from __future__ import annotations
import hashlib
import json
import re
from typing import Annotated, Any, Literal
from uuid import UUID
from pydantic import Field, field_validator, model_serializer, model_validator
from .contracts import Artifact, Fact, Key, TaskError, MAX_INLINE_TEXT_CHARS
from .item_confirmation import MediaDirective
from .reducer import digest, register_artifacts, complete_step

SAVE = 'save_task_text'
MIXED_TEXT_GUIDANCE = '聊天说明与保存正文分别展示；开场与document.display_prefix完全相同时后台只展示一次。说明不能代替条目正文，执行只使用所登记条目的body。'
DRAFT_CONFLICT = 'CONTENT_SAVE_DRAFT_CONFLICT'
INVALID_OBJECT = 'CONTENT_OBJECT_INVALID'
ATTACHMENT_USAGE_REQUIRED = 'CONTENT_ATTACHMENT_USAGE_REQUIRED'
PRESENTATION_INCOMPLETE = 'CONTENT_PRESENTATION_INCOMPLETE'
SAVE_FAILED_TEXT = '这次没能保存完整原文，尚未交付方案。请让我重新整理后再执行。'
TAG = 'content.object.v2'
PROTOCOL = 'content-objects-v2'
OUTPUT_START = '<content-delivery>'
OUTPUT_END = '</content-delivery>'
OUTPUT_ITEM = '<content-item>'
OUTPUT_BODY = '<content-body>'
OUTPUT_MARKERS = re.compile(
    rf'(?m)^({OUTPUT_START}|{OUTPUT_END}|{OUTPUT_ITEM}|{OUTPUT_BODY})[ \t]*(?:\r?\n|\Z)'
)

class ContentReference(Fact):
    artifact_id: Key
    item_index: int = Field(strict=True, ge=1, le=100)
    sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')

class ImageMessageReference(Fact):
    message_id: UUID
    content_index: int = Field(strict=True, ge=0)


class ImageContentReference(ContentReference):
    regenerate: str | None = Field(
        default=None, min_length=1, max_length=1000,
        description='用户本轮明确要求再生成一张新图时，逐字填写包含该要求的完整本轮文字。查看或继续已有图片时省略。')
    source_image_indices: tuple[Annotated[int, Field(strict=True, ge=1)], ...] | None = Field(
        default=None, min_length=1, max_length=16,
        description='使用所选成果里尚未绑定的原图时，填写 source_images 序号。')
    image_override: tuple[ImageMessageReference, ...] | None = Field(
        default=None, min_length=1, max_length=16,
        description='复用完整原稿且用户明确指定不同参考图时填写。修改已生成图的内容时，以所选成图为新编辑指令的素材。复制所选图片的 message_id 和 content_index。')
    without_reference: Literal[True] | None = Field(
        default=None, description='仅当本轮明确不使用方案的候选图片，或图片只作分析时设为 true。')

    @model_validator(mode='after')
    def image_choice(self):
        if sum(choice is not None for choice in (
                self.source_image_indices, self.image_override, self.without_reference)) > 1:
            raise ValueError('CONTENT_IMAGE_SELECTION_CONFLICT')
        return self

_MATERIAL_LOCATORS = (
    {'resource_ref': 'string'}, {'file_id': 'string'}, {'asset_id': 'string'},
    {'message_id': 'string', 'content_index': 'integer'},
)
_MATERIAL_LOCATOR_FIELDS = frozenset(key for locator in _MATERIAL_LOCATORS for key in locator)


class MaterialReference(Fact):
    model_config = {**Fact.model_config, 'json_schema_extra': {
        'oneOf': [
            {'required': list(locator), 'properties': {
                key: {'type': locator.get(key, 'null')} for key in sorted(_MATERIAL_LOCATOR_FIELDS)
            }}
            for locator in _MATERIAL_LOCATORS
        ],
    }}
    resource_ref: str | None = Field(default=None,min_length=1)
    file_id: str | None = Field(default=None,min_length=1)
    asset_id: str | None = Field(default=None,min_length=1)
    message_id: str | None = Field(default=None,min_length=1)
    content_index: int | None = Field(default=None,strict=True,ge=0)

    @model_validator(mode='after')
    def identity(self):
        supplied = {key for key in _MATERIAL_LOCATOR_FIELDS if getattr(self, key) is not None}
        if not any(supplied == locator.keys() for locator in _MATERIAL_LOCATORS):
            raise ValueError('CONTENT_MATERIAL_IDENTITY_REQUIRED')
        return self

MATERIAL_REFERENCE_GUIDANCE = (
    '原样复制系统提供的实际引用。只选 resource_ref、file_id、asset_id 其中一个，'
    '或成对填写 message_id 和 content_index。'
)
MATERIAL_ROLE_GUIDANCE = '用简短自然语言描述素材在任务中的角色，例如“原图配色与构图参考”。'
MATERIAL_USAGE_GUIDANCE = ('只填 input（实际传给执行工具）或 analysis（仅供分析，不传给执行工具）。'
    '图片任务中目标底图和需要借用其视觉特征的参考图都用input，包括风格、颜色、纹理参考；'
    '不能因为某张图不是修改目标就标成analysis。只有不需让执行模型看到、仅用于理解资料的素材才用analysis。')
MATERIAL_GUIDANCE = (
    f'每项素材须填写 reference、role、usage。填写 reference 时，{MATERIAL_REFERENCE_GUIDANCE}'
    f'填写 role 时，{MATERIAL_ROLE_GUIDANCE}usage {MATERIAL_USAGE_GUIDANCE}'
)


class Material(Fact):
    reference: MaterialReference = Field(description=MATERIAL_REFERENCE_GUIDANCE)
    role: str = Field(min_length=1, max_length=200, description=MATERIAL_ROLE_GUIDANCE)
    usage: Literal['input', 'analysis'] = Field(description=MATERIAL_USAGE_GUIDANCE)


class Presentation(Fact):
    display_prefix: str | None = Field(
        default=None, min_length=1, max_length=12000,
        description='用户可见的原版 Markdown 前缀，包含本块标题和展示段落；后端只展示一次，不传给执行工具。')

    @model_serializer(mode='wrap')
    def omit_absent_prefix(self, handler):
        result = handler(self)
        if self.display_prefix is None:
            result.pop('display_prefix', None)
        return result


class ContentItem(Presentation):
    title: str = Field(min_length=1, max_length=300, description='条目名称，由页面添加编号。')
    body: str = Field(min_length=1, max_length=20000,description='用户可见并交给目标工具的完整最终原文，包括全部执行要求、限制和禁止项；后台逐字传递，不再拼接其他文字。')
    materials: tuple[Material, ...] = Field(default=(), max_length=16,
        description='按用户意图填写实际使用的素材。只交付文字时可省略，后台保留当前原图供后续选择。' + MATERIAL_GUIDANCE)
    options: dict[str, Any] = Field(default_factory=dict,
        description='仅填写已确定的执行规格。缺省由执行工具决定。',
        json_schema_extra={'propertyNames':{'not':{'enum':['prompt','references','source_prompt','content_source','plan_source','chat_input','code']}}})

    @model_validator(mode='after')
    def output_options(self):
        if set(self.options).intersection({'prompt','references','source_prompt','content_source','plan_source','chat_input','code'}):
            raise ValueError('CONTENT_OPTIONS_RESERVED')
        if not self.body.strip():
            raise ValueError('CONTENT_BODY_EMPTY')
        return self

class ContentDocument(Presentation):
    version: Literal[2] = 2
    title: str = Field(min_length=1, max_length=300)
    notes: str = Field(default='', max_length=8000)
    items: tuple[ContentItem, ...] = Field(min_length=1, max_length=100)


def is_content_output(text):
    return text.lstrip().startswith(OUTPUT_START)


def _without_boundary_newline(text):
    return text.removesuffix('\r\n') if text.endswith('\r\n') else text.removesuffix('\n')


def _output_heading(text):
    title, _, prefix = _without_boundary_newline(text).partition('\n')
    return {'title': title.strip(), **({'display_prefix': prefix} if prefix.strip() else {})}


def document_from_output(text, *, references=''):
    """Use only the author's explicit boundaries, preserving the body verbatim."""
    parts = OUTPUT_MARKERS.split(text.lstrip())
    if (len(parts) < 9 or parts[:2] != ['', OUTPUT_START] or parts[-2] != OUTPUT_END
            or parts[-1].strip() or (len(parts) - 5) % 4):
        raise TaskError(INVALID_OBJECT)
    items = []
    for i in range(3, len(parts) - 2, 4):
        item, header, body_marker, body = parts[i:i + 4]
        if item != OUTPUT_ITEM or body_marker != OUTPUT_BODY:
            raise TaskError(INVALID_OBJECT)
        items.append({**_output_heading(header), 'body': _without_boundary_newline(body)})
    heading = _output_heading(parts[2])
    if references:
        prefix = heading.get('display_prefix') or f"# {heading['title']}"
        combined = prefix + references
        # The existing activity block also carries every source URL.
        if len(combined) <= 12000:
            heading['display_prefix'] = combined
    return ContentDocument.model_validate({**heading, 'items': items})


def render_unregistered_output(text):
    """Strip recognized control lines from a legacy delivery."""
    cleaned = OUTPUT_MARKERS.sub('', text.lstrip())
    for marker in (OUTPUT_START, OUTPUT_END, OUTPUT_ITEM, OUTPUT_BODY):
        cleaned = cleaned.replace(marker, '')
    return cleaned


class SourceImage(Fact):
    name: str
    reference: MaterialReference


class ContentRecord(Fact):
    version: Literal[2] = 2
    object_key: str
    revision: int = Field(strict=True, ge=1)
    document: ContentDocument
    base_artifact_id: str | None = None
    source_images: tuple[SourceImage, ...] = ()
    presentation_profile: str | None = None
    confirmation_items: tuple[int, ...] = ()
    confirmation_tool: Literal['generate_image', 'generate_video'] | None = None

    @property
    def display_gaps(self):
        try:
            layout = json.loads(self.document.notes)['_chat_display_layout']
            if len(layout) != len(self.document.items) + 1:
                return ()
            return tuple(before + (item.display_prefix or '') + after
                         for (before, after), item in zip(layout[:-1], self.document.items)) + (layout[-1],)
        except (ValueError, KeyError, TypeError):
            return ()



class ContentItemPatch(Presentation):
    title: str | None = Field(default=None,min_length=1,max_length=300)
    body: str | None = Field(default=None,min_length=1,max_length=20000,description='修改后的完整最终原文；替换该条原文，不附加旧版本的文字。')
    materials: tuple[Material,...] | None = Field(default=None,max_length=16,description='仅用户要求改变执行素材时提供；否则省略，由后台保留原引用，不重新填写。')
    options: dict[str,Any] | None = Field(default=None,description='仅修改输出规格时提供；否则省略，由后台保留原规格。')

    @model_validator(mode='after')
    def changes(self):
        if not self.model_fields_set or any(getattr(self,key) is None for key in self.model_fields_set):
            raise ValueError('CONTENT_PATCH_EMPTY_OR_NULL')
        return self

class ItemUpdate(Fact):
    item_index: int = Field(strict=True, ge=1, le=100)
    item: ContentItemPatch

class MessageItem(Fact):
    title: str = Field(min_length=1, max_length=300)
    first_line: int = Field(strict=True, ge=1)
    last_line: int = Field(strict=True, ge=1)


class MessageContentSource(Fact):
    message_id: UUID
    content_index: int | None = Field(default=None, strict=True, ge=0)
    title: str = Field(default='对话方案', min_length=1, max_length=300)
    items: tuple[MessageItem, ...] = Field(default=(), max_length=100)
    source_image_indices: tuple[Annotated[int, Field(strict=True, ge=1)], ...] | None = Field(
        default=None, min_length=1, max_length=16,
        description='整组明确沿用原稿所关联的原图时填原图序号。只分析素材时省略。')


class SaveContent(Fact):
    model_config = {**Fact.model_config, 'json_schema_extra': {'oneOf': [
        {'required': ['document']}, {'required': ['base', 'updates']}, {'required': ['message_source']},
    ], 'allOf': [{'if': {'required': ['new_content'], 'properties': {'new_content': {'const': True}}},
                   'then': {'required': ['continue_execution', 'document'], 'properties': {'continue_execution': {'const': True}}}}]}}
    document: ContentDocument | None = None
    base: ContentReference | None = Field(default=None, description='修订已有稿件时，原样复制读取回执中的content_source；与updates一起填写，不与document或message_source混用。')
    message_source: MessageContentSource | None = None
    updates: tuple[ItemUpdate, ...] = Field(default=(), max_length=100, description='仅列用户选定且需要修改的条目。item_index沿用原编号；item仅填写变化字段，body若提供必须是完整修订正文。未列条目及省略字段由后台保留；options若提供则须保留原有未改选项。')
    delivery_complete: bool = Field(default=True, description='本轮文字已完整交付时为true。仍需继续保存其他稿件时为false；这只控制文字交付结束，不授权图片或其他执行。')
    continue_execution: bool = Field(default=False, description='仅在本轮还要求实际执行时设为 true。保存成功后仍需调用目标工具。')
    confirmation: MediaDirective | None = Field(
        default=None, description='仅当用户设置了后续确认条件时填写tool、wait_for或wait_all。不是审批开关，不能填approved来批准刚写出的稿件。普通方案不新增确认。')
    new_content: Literal[True] | None = Field(
        default=None, description='仅当本轮已明确要求实际执行且 continue_execution=true 时，随 document 标记新的执行正文。仅撰写文字、方案或新系列设计必须省略本字段，不能因内容是新作就填写。可在 materials 指定已选成图或原图。修订已有文字用 base 和 updates。')
    show_content: bool | None = Field(default=None, description=(
        '仅在用户同时要求交付文字且继续执行时设为true；只要求执行时省略，输入在后台登记。'))

    @field_validator('document', 'base', mode='before')
    @classmethod
    def decode_serialized_object(cls, value):
        if isinstance(value, str) and value.lstrip().startswith('{'):
            return json.loads(value)
        return value

    @model_validator(mode='after')
    def choice(self):
        if self.confirmation is not None and (self.confirmation.approved or self.confirmation.confirmation_only):
            raise ValueError('CONTENT_CONFIRMATION_EXISTING_ITEM_REQUIRED')
        if (sum(value is not None for value in (self.document, self.base, self.message_source)) != 1
                or self.base is None and self.updates or self.base is not None and not self.updates):
            raise ValueError('CONTENT_SAVE_FORM_INVALID')
        if self.message_source is not None and (not self.continue_execution or self.show_content):
            raise ValueError('CONTENT_MESSAGE_SOURCE_REQUIRES_EXECUTION')
        if self.new_content is not None and (self.document is None or not self.continue_execution):
            raise ValueError('CONTENT_NEW_SOURCE_INVALID')
        return self

def revision_examples(require_presentation=True):
    # Illustrative references only; the model must copy the real read receipt.
    patch = {'base': {'artifact_id': 'content-example', 'item_index': 3},
             'updates': [{'item_index': 3, 'item': {
                 'body': '## 完整生图提示词\n参考用户所选原图制作绿叶封面，保留本子外形与原图构图。正方形，2K。\n\n## 负面提示词\n禁止改变本子结构、装订方式与数量，禁止添加水印。',
                 'display_prefix': '## 方案三：绿叶封面\n\n### 方案内容\n米白封面中央绘制三片细长绿叶，左上安排浅灰小字Spring，右下保留留白。\n\n### 视觉设定\n低饱和浅绿与米白配色，细线平面印刷，柔和自然光，正方形1:1，2K。\n\n### 参考图使用方式\n以原图作为产品结构参考，保持本子外形、扣带、缝线、斜置角度和布面背景，仅替换封面图案。',
                 'options': {'aspect_ratio': '1:1', 'resolution': '2K', 'output_format': 'png'}}}],
             'continue_execution': False}
    if not require_presentation:
        patch['updates'][0]['item'] = {'body': '春季新品将于周五上午十点发布。活动持续三天，具体商品和价格以活动页面为准。'}
    return [patch, {**patch, 'continue_execution': True}]


def revision_usage(require_presentation=True):
    from .content_revision import READ_GUIDANCE
    return (READ_GUIDANCE +
        'base复制读取回执中的稿件引用，updates[].item_index沿用原编号，仅列用户指定条目。'
        'item只提交变化字段；body是完整修订正文，保留未改细节和原结构。'
        'options若填写会整体替换旧值，先复制完整原options再改指定字段；不换素材省略materials。'
        '只改文字continue_execution=false；用户明确要求改稿并执行才为true，保存本身不派发。'
        '以下两个完整JSON分别示范仅修改与修改后继续执行：'
        + json.dumps(revision_examples(require_presentation), ensure_ascii=False)
        + '。示例身份、正文和options仅示意格式，必须替换为真实读取的引用及原稿修订后的完整内容；'
          '不能复制示例覆盖用户原稿。普通文字不添加图片章节。'
          '第二例保存后用新回执content_source调用执行工具，其他条目的确认条件不变。')


REVISION_USAGE = revision_usage()


def save_parameters(*, require_presentation=False):
    parameters = SaveContent.model_json_schema()
    # SaveContent.choice already forbids approving existing items here. Mirror
    # that rule in the shared directive's model-visible save-only projection.
    approval = parameters['$defs']['MediaDirective']['properties']['approved']
    approval.update(const=[], description='本保存工具不接受条目批准，省略或空数组；本次批准由真实用户请求及后台决策绑定。')
    # Advertise each selector as an object. The oneOf controls the selector choice.
    parameters['properties']['document'] = parameters['$defs'][ContentDocument.__name__]
    parameters['properties']['base'] = {**selection_parameters(),
        'description': SaveContent.model_fields['base'].description}
    parameters['properties']['message_source'] = parameters['$defs'][MessageContentSource.__name__]
    del parameters['$defs'][ContentReference.__name__]
    if require_presentation:
        from services.handlers.image_size_requirements import image_draft_options_schema

        for name in ('ContentItem', 'ContentItemPatch'):
            parameters['$defs'][name]['properties']['options'] = image_draft_options_schema()
        for name in ('ContentDocument', 'ContentItem'):
            shape = parameters['$defs'][name]
            shape['required'].append('display_prefix')
            prefix = shape['properties']['display_prefix']
            shape['properties']['display_prefix'] = {
                **next(branch for branch in prefix['anyOf'] if branch['type'] == 'string'),
                'description': prefix['description'],
            }
    return parameters


def selection_parameters(model=ContentReference):
    parameters = model.model_json_schema()
    del parameters['properties']['sha256']
    if model is ImageContentReference:
        definitions = parameters.pop('$defs')
        array = next(option for option in parameters['properties']['image_override']['anyOf']
                     if option['type'] == 'array')
        array['items'] = definitions[ImageMessageReference.__name__]
    return parameters


IMAGE_REVISION_GUIDANCE = (
    ' 用户要求旧图片方案修改比例、清晰度或内容并出图时，不是原样执行：先读取所选条目的完整正文、options和素材，'
    '用save_task_text的base及updates修订同一稿件，保存成功后再提交新回执中的content_source。'
    '比例修改包括旧options.aspect_ratio为auto的情况；同时修订body、display_prefix内与新规格冲突的竖版/横版等要求。'
    'updates[].item.options会整体替换旧options，须复制完整原options再只改用户指定字段，保留未改的分辨率和输出格式；'
    '未要求换素材就省略materials，由后台保留。用户已要求修订并出图时continue_execution=true，'
    '无需让用户再说一次先改方案；已有等待确认条件仍有效，不将本说明作为批准。'
    '用户只要求改方案或先看文字时不提交图片。'
    '目录items[].options是各条实际保存规格，不以整组标题推断其他条目的规格。'
    '收到CONTENT_IMAGE_REVISION_REQUIRED表示尚未派发，可在同一轮按recovery读取并修订保存；'
    '必须用保存成功回执中的新artifact_id执行，不能再次提交旧content_source。'
    '待确认条目若在本次明确要求改规格并出图，后台仅将本次已确认条目绑定到保存成功的新版本，'
    '回执approved_execution给出可执行条目；其他条目的等待条件保留。修改但未明确出图不算批准。')


SCHEMA = {'type': 'function', 'function': {'name': SAVE,
    'description': '保存或修订结构化稿件。新写分条方案、提示词或脚本可直接回复编号标题与完整正文，后台自动登记，无需重复保存。'
        '明确结构化保存时提交document，每条写title和body，页面展示原文。整组优先一次保存；分次交付时前几次delivery_complete=false，最后一次才为true，不能用continue_execution表示继续写稿。'
        '用户要求部分或全部条目等后续确认再执行时，必须结构化保存整组，用confirmation指定tool及wait_for或wait_all，保留编号。'
        '同轮要求立即执行其他项时continue_execution=true；不把等待条件变成已批准，不给普通写作新增确认。'
        + MIXED_TEXT_GUIDANCE + '普通问答直接回复。'
        '只执行已有成果时，直接将目录条目的content_source交给目标工具。未登记的分条正文用message_source指定原回复消息，后台定位整组原文；continue_execution=true。'
        '仅后台无法识别边界时才按已核实的完整行范围指定条目。'
        '用户明确修改一张成图时，可用document和new_content=true编写新的编辑指令，在materials绑定这张图。只修订历史文字用base和updates。'
        'body包含最终要求和限制；按用户或方法要求交付的可见结构放display_prefix，内部说明放notes。materials记录用于执行或分析的素材，options仅记已确定执行规格。'
        '修订既有条目时，base使用该条目的content_source，updates每项为{item_index,item:{body:完整修订正文}}。后台绑定版本并保留未改条目、素材和规格。'
        '本轮还要求执行时设continue_execution=true，再调用目标工具。有当前上传图片时，在每条的materials中绑定所用图片。还要求看文字时设show_content=true。'
        + revision_usage(False),
    'parameters': save_parameters()}}


def save_schema(runtime):
    from .direct_loop import active_image_methods
    from .methods import REFERENCE_PROMPTS_PROFILE, REFERENCE_PROMPTS_PRESENTATION

    from .content_revision import selected_format, READ_GUIDANCE
    selected = selected_format(runtime)
    reference = (selected['profile_id'] == REFERENCE_PROMPTS_PROFILE if selected is not None else
                 any(method[0] == REFERENCE_PROMPTS_PROFILE for method in active_image_methods(runtime).values()))
    if selected is None and not reference:
        return SCHEMA
    description = (REFERENCE_PROMPTS_PRESENTATION if reference else '') + SCHEMA['function']['description'] + READ_GUIDANCE
    if selected is not None:
        description += ' 所选旧稿的真实格式合同：' + json.dumps(selected, ensure_ascii=False)
    parameters = save_parameters(require_presentation=reference)
    parameters['examples'] = revision_examples(reference)
    if reference:
        parameters['$defs']['ContentItemPatch']['properties']['body']['description'] += (
            ' 按旧稿format_contract保留完整生图提示词、负面提示词两个独立Markdown章节及完整正文。')
        parameters['$defs']['ContentItemPatch']['properties']['display_prefix']['description'] += (
            ' 复制读取回执的原版详细内容，只改用户指定部分；不能用简短概述代替。')
    return {**SCHEMA, 'function': {**SCHEMA['function'],
        'description': description, 'parameters': parameters}}


def objects(state):
    if state is None: return []
    committed = {a for result in state.results for a in result.artifact_ids}
    return [a for a in state.artifacts if TAG in a.validations and a.id in committed]


def _load_record(state, reference):
    reference = ContentReference.model_validate(reference)
    artifact = next((a for a in objects(state) if a.id == reference.artifact_id), None)
    if artifact is None or (reference.sha256 is not None and artifact.sha256 != reference.sha256):
        raise TaskError('CONTENT_OBJECT_UNAVAILABLE_OR_CHANGED')
    if hashlib.sha256(artifact.inline_text.encode()).hexdigest() != artifact.sha256:
        raise TaskError('CONTENT_OBJECT_CHANGED')
    record = ContentRecord.model_validate_json(artifact.inline_text)
    if reference.item_index > len(record.document.items): raise TaskError('CONTENT_ITEM_UNAVAILABLE')
    return artifact, record, record.document.items[reference.item_index-1]


def load(state, reference):
    artifact, record, item = _load_record(state, reference)
    return artifact, record.document, item


def content_image_receipts(state):
    if state is None:
        return {}
    operations = {operation.step_id: operation for operation in state.operations}
    completed = {result.step_id for result in state.results}
    receipts = {}
    for artifact in state.artifacts:
        if 'image.direct.recipe' not in artifact.validations:
            continue
        operation = operations.get(artifact.step_id)
        status = 'completed' if artifact.step_id in completed else operation.status if operation else None
        if status not in {'accepted', 'completed', 'uncertain', 'reserved'}:
            continue
        recipe = json.loads(artifact.inline_text)['normalized']
        proof = (recipe.get('source_prompt') or {}).get('content_object')
        if proof is None or proof['source'].get('image_override'):
            continue
        source = proof['source']
        key = (source['artifact_id'], source['item_index'])
        receipt = {'status': status, 'selection': source, 'specs': {name: recipe[name] for name in (
            'aspect_ratio', 'resolution', 'output_format') if name in recipe}}
        if operation and operation.receipt_ref and operation.receipt_ref.startswith('task:'):
            receipt['image_task_id'] = operation.receipt_ref.removeprefix('task:')
        receipts[key] = receipt
    # An unchanged slot keeps its receipt across revisions of other slots.
    documents = {}
    for artifact in objects(state):
        record = ContentRecord.model_validate_json(artifact.inline_text)
        prior = documents.get(record.base_artifact_id)
        if prior:
            for index, (old, new) in enumerate(zip(prior.items, record.document.items), 1):
                previous = receipts.get((record.base_artifact_id, index))
                if previous and old == new:
                    receipts.setdefault((artifact.id, index), previous)
        documents[artifact.id] = record.document
    return receipts


def repeat_image_reference(state, reference):
    """Reuse the verified original choice when a repeat omits the reference options."""
    material_fields = ('source_image_indices', 'without_reference', 'image_override')
    if any(reference.get(field) is not None for field in material_fields):
        return reference
    receipt = content_image_receipts(state).get((reference['artifact_id'], reference['item_index']))
    if receipt is None:
        return reference
    return {**reference, **{field: receipt['selection'][field] for field in material_fields
                            if field in receipt['selection']}}


def with_item_sources(row):
    """Project actionable references without changing stored records or history rows."""
    return {**row, 'items': [
        {**item, 'content_source': {'artifact_id': row['artifact_id'], 'item_index': item['item_index']}}
        for item in row['items']
    ]}


def catalog(state, *, for_model=False, include_image_markers=False):
    receipts = content_image_receipts(state) if for_model else {}
    latest = {}
    documents = {}
    for artifact in objects(state):
        record = ContentRecord.model_validate_json(artifact.inline_text)
        if for_model:
            documents[artifact.id] = record.document
        previous = latest.get(record.object_key)
        if previous is None or record.revision > previous[1].revision:
            latest[record.object_key] = (artifact,record)
    rows = []
    for artifact, record in latest.values():
        base = documents.get(record.base_artifact_id)
        items = []
        for index, item in enumerate(record.document.items, 1):
            entry = {'item_index': index, 'title': item.title, 'options': item.options}
            if index in record.confirmation_items:
                entry['awaiting_confirmation'] = True
            if for_model:
                entry['has_bound_inputs'] = any(material.usage == 'input' for material in item.materials)
                if base and index <= len(base.items) and item != base.items[index - 1]:
                    entry['changed_in_revision'] = record.revision
                receipt = receipts.get((artifact.id, index))
                if receipt:
                    entry['image_status'] = receipt['status']
                    if receipt.get('image_task_id'):
                        entry['image_task_id'] = receipt['image_task_id']
            else:
                entry['body_sha256'] = hashlib.sha256(item.body.encode()).hexdigest()
                entry['materials'] = [material.model_dump(mode='json', exclude_none=True) for material in item.materials]
            items.append(entry)
        row = {'artifact_id': artifact.id, 'object_key': record.object_key,
               'revision': record.revision, 'title': record.document.title, 'items': items}
        if for_model and record.confirmation_tool:
            row['confirmation_tool'] = record.confirmation_tool
        if not for_model:
            row['sha256'] = artifact.sha256
        if record.source_images and (not for_model or any(not item['has_bound_inputs'] for item in items)):
            row['source_images'] = [{'index': index, 'name': image.name}
                                    for index, image in enumerate(record.source_images, 1)]
        if include_image_markers and record.source_images:
            row['uploads'] = [{'name': image.name, 'reference': image.reference.model_dump(
                mode='json', include={'message_id', 'content_index'})}
                for image in record.source_images if image.reference.message_id]
        rows.append(with_item_sources(row) if for_model else row)
    return rows

def is_current(state, artifact_id):
    return any(row['artifact_id'] == artifact_id for row in catalog(state, for_model=True))


def has_selectable_source_images(state):
    return any(row.get('source_images') for row in catalog(state, for_model=True))


def render(document, gaps=()):
    if gaps and len(gaps) == len(document.items) + 1:
        return gaps[0] + ''.join(item.body + gap for item, gap in zip(document.items, gaps[1:]))
    parts = [document.display_prefix or f'# {document.title}']
    for i, item in enumerate(document.items, 1):
        parts.extend([item.display_prefix or f'## {i}. {item.title}', item.body])
    return '\n\n'.join(parts)


def matches_visible_prefix(value, text):
    """Only an explicit, byte-identical display prefix can accompany a save.

    Nonempty text alone says nothing about whether the requested body was shown.
    Never silently replace a different visible draft with tool arguments.
    """
    return bool(text and value.document is not None and value.document.display_prefix
                and text == value.document.display_prefix)


def retry_pending(messages):
    """Recognize a failed draft registration in the current user turn."""
    tool_results = {}
    for message in reversed(messages):
        role = message.get('role')
        if role == 'user':
            break
        if role == 'tool':
            tool_results[message.get('tool_call_id')] = message.get('content')
        if role == 'assistant':
            for call in message.get('tool_calls') or ():
                if call.get('function', {}).get('name') == SAVE and call['id'] in tool_results:
                    try:
                        result = json.loads(tool_results[call['id']])
                    except (TypeError, ValueError):
                        continue
                    if isinstance(result, dict):
                        if result.get('ok') is True:
                            return False
                        if result.get('ok') is False:
                            return True
    return False



def save_repair_exhausted(messages):
    """Bound failed saves for one user input, including mixed errors and reads."""
    calls, failed, exhausted = set(), set(), False
    for message in messages:
        if message.get('role') == 'user':
            calls.clear()
            failed.clear()
            exhausted = False
        elif message.get('role') == 'assistant':
            calls.update(call['id'] for call in message.get('tool_calls', ())
                         if call.get('function', {}).get('name') == SAVE)
        elif message.get('role') == 'tool' and message.get('tool_call_id') in calls:
            try:
                payload = json.loads(message.get('content', ''))
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get('ok') is True:
                failed.clear()
                exhausted = False
            elif payload.get('ok') is False:
                failed.add(message['tool_call_id'])
                if len(failed) >= (2 if payload.get('code') == DRAFT_CONFLICT else 3):
                    exhausted = True
    return exhausted


def source_images_from_message(row):
    from services.handlers.chat_context.image_sources import image_sources
    return tuple(SourceImage(name=image['name'], reference=image['reference'])
                 for image in image_sources(row) if image['available'])


def historical_source_images(runtime):
    """Candidates retain provenance; only semantic material selection executes them."""
    if runtime is None or not hasattr(runtime, 'task_execution_state'):
        return ()
    from .direct_loop import content_followup_state
    state = runtime.task_execution_state or content_followup_state(runtime)
    latest = {}
    for artifact in objects(state):
        record = ContentRecord.model_validate_json(artifact.inline_text)
        latest[record.object_key] = record
    images = []
    for record in latest.values():
        for image in record.source_images:
            if image.reference not in [value.reference for value in images]:
                images.append(image)
    return tuple(images)


def current_source_images(request, runtime=None):
    message_id = getattr(request.context_anchor, 'input_message_id', None)
    if not message_id:
        return ()
    row = {'id': message_id, 'role': 'user', 'content': [part.model_dump(mode='json') for part in request.content]}
    images=list(source_images_from_message(row))
    for change in getattr(getattr(runtime,'task_user_changes',None),'items',()):
        if change.delivered and str(change.owner_task_id)==str(request.task_id):
            images.extend(SourceImage(name=attachment.workspace_path.rsplit('/',1)[-1],
                reference=MaterialReference(resource_ref=attachment.resource_ref))
                for attachment in change.attachments if attachment.kind=='image')
    return tuple(images)


def registration_source_images(request, runtime):
    # Historic candidates are provenance, not current attachment positions.
    images = list(current_source_images(request, runtime))
    for image in historical_source_images(runtime):
        if image.reference not in [value.reference for value in images]:
            images.append(image)
    return tuple(images)


def save(runtime, call, registry, *, has_image_attachments=False, source_images=(), checked_message=None,
         display_gaps=(), author_request=None, allow_implicit_execution_materials=False, confirmation_items=(),
         confirmation_tool=None, request_context=None):
    from services.tools import ToolCall
    from .model_directed import record_calls, call_binding
    from .direct_loop import active_image_methods
    from .methods import REFERENCE_PROMPTS_PROFILE
    value = SaveContent.model_validate_json(call['arguments'])
    if request_context is None and '_media_decisions_v1' in getattr(runtime,'checkpoint_base',{}):
        from services.agent.image.ecommerce_planner.workflow import ImageRequestContext
        request_context = ImageRequestContext()
    if value.confirmation is not None:
        conditions = value.confirmation
        selected = tuple(range(1, len(value.document.items) + 1)) if conditions.wait_all and value.document else tuple(conditions.wait_for)
        confirmation_items = tuple(sorted(set(confirmation_items) | set(selected)))
        confirmation_tool = conditions.tool if selected else confirmation_tool
    state = runtime.task_execution_state
    base_record = None
    if value.base is not None:
        base_record = _load_record(state, value.base)
        if value.base.sha256 is None:
            value = value.model_copy(update={
                'base': value.base.model_copy(update={'sha256': base_record[0].sha256}),
            })
    arguments = value.model_dump(mode='json')
    if value.delivery_complete:
        arguments.pop('delivery_complete')  # Preserve pre-field call fingerprints.
    if value.show_content is None:
        arguments.pop('show_content')
    if value.message_source is None:
        arguments.pop('message_source')
    else:
        # Preserve fingerprints of completed pre-index calls when new optional fields are absent.
        for key in ('source_image_indices', 'content_index'):
            if arguments['message_source'].get(key) is None:
                arguments['message_source'].pop(key, None)
    if value.new_content is None:
        arguments.pop('new_content')
    if value.confirmation is None:
        arguments.pop('confirmation')
    bound = next((b for b in state.call_bindings if b.call_id == call['id']),None) if state else None
    if bound:
        if bound.tool_name != SAVE or bound.arguments_sha256 != digest(arguments):
            raise TaskError('CONTENT_SAVE_CALL_CHANGED')
        artifact = next((a for a in objects(state) if a.source_call_id == call['id']),None)
        if artifact is None: raise TaskError('CONTENT_SAVE_UNCONFIRMED')
        return state, artifact, ContentRecord.model_validate_json(artifact.inline_text).document, value
    document = value.document
    # Layout is produced only from the verified author output, never from model notes.
    if document is not None and '_chat_display_layout' in document.notes:
        raise TaskError(INVALID_OBJECT)
    explicit_execution = value.continue_execution and not allow_implicit_execution_materials
    message_proof = None
    if value.message_source is not None:
        if checked_message is None:
            raise TaskError('CONTENT_MESSAGE_SOURCE_UNVERIFIED')
        document, source_images, message_proof = checked_message
        display_gaps = tuple(message_proof.get('display_gaps', ()))
        message_proof = {k: v for k, v in message_proof.items() if k != 'display_gaps'}
    if document is not None and source_images and (author_request is not None or explicit_execution or value.message_source is not None):
        from .content_groups import bind_group
        document = bind_group(document, source_images, author_request or '', infer_materials=not explicit_execution,
            semantic_context=request_context)
    if (value.message_source is None and document is not None and value.continue_execution and has_image_attachments
            and any(not item.materials for item in document.items)):
        raise TaskError(ATTACHMENT_USAGE_REQUIRED)
    identity = message_identity(message_proof) if message_proof is not None else [str(runtime.task_id), call['id']]
    object_key = 'object-'+digest(identity)[:32]
    revision = 1
    # Importing a completed message supplies an execution source without
    # claiming to have run any currently active authoring method.
    methods = active_image_methods(runtime) if value.message_source is None else {}
    if len(methods) > 1:
        raise TaskError('CONTENT_METHOD_CONFLICT')
    presentation_profile = next((method[0] for method in methods.values()
                                 if method[0] == REFERENCE_PROMPTS_PROFILE), None)
    if base_record is not None:
        prior_artifact, prior_record, _ = base_record
        previous = prior_record.document
        confirmation_items = tuple(sorted(set(confirmation_items) | set(prior_record.confirmation_items)))
        confirmation_tool = prior_record.confirmation_tool or confirmation_tool
        source_images = prior_record.source_images
        presentation_profile = prior_record.presentation_profile
        if not is_current(state, prior_artifact.id):
            raise TaskError('CONTENT_BASE_VERSION_CHANGED')
        object_key, revision = prior_record.object_key, prior_record.revision+1
        items = list(previous.items); indices = set()
        for update in value.updates:
            if update.item_index in indices or update.item_index > len(items): raise TaskError('CONTENT_UPDATE_INVALID')
            items[update.item_index-1] = ContentItem.model_validate({**items[update.item_index-1].model_dump(mode='json'),**update.item.model_dump(mode='json',exclude_unset=True)}); indices.add(update.item_index)
        document = previous.model_copy(update={'items':tuple(items)})
    if value.confirmation is not None and value.confirmation.wait_all:
        confirmation_items = tuple(range(1,len(document.items)+1))
        confirmation_tool = value.confirmation.tool
    from .methods import presentation_issues, ContentPresentationError
    issues = presentation_issues(document, presentation_profile)
    if issues:
        if value.base is not None:
            for issue in issues:
                for position, update in enumerate(value.updates):
                    issue['path'] = issue['path'].replace(
                        f'$.document.items[{update.item_index - 1}].', f'$.updates[{position}].item.')
        raise ContentPresentationError(issues, presentation_profile)
    if presentation_profile == REFERENCE_PROMPTS_PROFILE and value.message_source is None:
        from services.handlers.image_size_requirements import image_draft_options_schema
        from services.tools.argument_validation import _issues

        changed_options = ([update.item.options for update in value.updates if 'options' in update.item.model_fields_set]
                           if base_record is not None else [item.options for item in value.document.items])
        options_schema = image_draft_options_schema()
        if any(_issues(options, options_schema) for options in changed_options):
            raise TaskError('CONTENT_IMAGE_OPTIONS_INVALID')
    if display_gaps:
        # Store separators around existing display prefixes in the v2 notes slot.
        # Earlier readers can still validate and execute these same item bodies.
        layout = []
        for gap, item in zip(display_gaps[:-1], document.items, strict=True):
            before, prefix, after = gap.rpartition(item.display_prefix or '')
            if not prefix:
                raise TaskError(INVALID_OBJECT)
            layout.append([before, after])
        notes = json.loads(document.notes) if document.notes else {}
        notes['_chat_display_layout'] = [*layout, display_gaps[-1]]
        document = ContentDocument.model_validate({**document.model_dump(),
            'notes': json.dumps(notes, ensure_ascii=False, separators=(',', ':'))})
    if any(not 1 <= index <= len(document.items) for index in confirmation_items):
        raise TaskError('CONTENT_CONFIRMATION_ITEMS_REQUIRED')
    body = ContentRecord(object_key=object_key,revision=revision,document=document,source_images=source_images,
                         base_artifact_id=value.base.artifact_id if value.base else None,
                         presentation_profile=presentation_profile, confirmation_items=confirmation_items,
                         confirmation_tool=confirmation_tool).model_dump_json(exclude={
                             name for name, present in (
                                 ('source_images', source_images), ('presentation_profile',presentation_profile),
                                 ('confirmation_items', confirmation_items), ('confirmation_tool', confirmation_tool))
                             if not present})
    # Content records share the Artifact character cap and the existing task
    # byte budget. A separate 50KB cap rejected otherwise valid Chinese drafts.
    if len(body) > MAX_INLINE_TEXT_CHARS:
        raise TaskError('CONTENT_OBJECT_TOO_LARGE')
    checksum = hashlib.sha256(body.encode()).hexdigest()
    if message_proof is not None and (existing := message_artifact(state, message_proof)) is not None:
        if existing.sha256 != checksum:
            raise TaskError('CONTENT_MESSAGE_SOURCE_ALREADY_REGISTERED')
        return state, existing, document, value
    for artifact in objects(state):
        if artifact.sha256 == checksum: return state, artifact, document, value
    parsed = ToolCall(call['id'], SAVE, arguments)
    state = record_calls(runtime.task_execution_state, runtime.task_bootstrap, [parsed], registry)
    step, requirement = call_binding(state, parsed)
    from .contracts import MethodBinding
    method=None
    if methods:
        key,(profile,retire,source)=next(iter(methods.items()))
        selected=runtime.skill_runtime.directory[key];active=runtime.skill_runtime.active[key]
        method=MethodBinding(package_id=selected.package_id,skill_key=key,revision=active.revision,
            body_sha256=active.body_sha256,profile_id=profile,contract_source=source,
            requirement_ids=(requirement.id,),image_count=len(document.items),retire_on_complete=retire)
    elif base_record and base_record[0].image_method is not None:
        method = base_record[0].image_method.model_copy(update={'requirement_ids': (requirement.id,)})
    artifact = Artifact(id='content-'+digest(step.id)[:32], kind='text', reference='task-text:'+str(state.root_task_id)+':content-'+digest(step.id)[:32],
        sha256=checksum, inline_text=body, source_task_id=UUID(str(runtime.task_id)), delivery_task_id=UUID(str(runtime.task_id)),
        source_call_id=call['id'], step_id=step.id, validations=('artifact.exists',TAG),image_method=method)
    state = register_artifacts(state,[artifact])
    return complete_step(state,step.id,[artifact.id],owner_task_id=runtime.task_id), artifact, document, value


def message_identity(proof):
    return [proof[key] for key in ('message_id', 'content_index', 'sha256', 'context_revision')]


def message_artifact(state, proof):
    identity = message_identity(proof)
    for artifact in reversed(objects(state)):
        record = ContentRecord.model_validate_json(artifact.inline_text)
        try:
            source = json.loads(record.document.notes or '{}').get('message_source')
            if source and message_identity(source) == identity:
                return artifact
        except (ValueError, KeyError, AttributeError, TypeError):
            continue
    return None


def image_materials(images, indices):
    if len(set(indices)) != len(indices) or any(index < 1 or index > len(images) for index in indices):
        raise TaskError('CONTENT_SOURCE_IMAGE_UNAVAILABLE')
    return [Material(reference=images[index - 1].reference, role='原图参考', usage='input') for index in indices]


def _selected_image_inputs(record, indices, inputs):
    if not record.source_images:
        # A single registered execution image already answers the only possible
        # selection. Indices cannot select a subset of multiple bound images.
        if indices == (1,) and len(inputs) == 1:
            return inputs
        raise TaskError('CONTENT_SOURCE_IMAGE_UNAVAILABLE')
    materials = image_materials(record.source_images, indices)
    selected = [material.reference.model_dump(mode='json', exclude_none=True) for material in materials]
    if inputs:
        registered = [{key: value for key, value in image.items() if key != 'role'} for image in inputs]
        if selected != registered:
            raise TaskError('CONTENT_ARGUMENT_CONFLICT')
        return inputs
    return [{**reference, 'role': '原图参考'} for reference in selected]


def is_regeneration_variant(value):
    return isinstance(value, str) and re.fullmatch(
        r'regenerate:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value
    ) is not None


def _legacy_image_options(options, original_goal, has_reference):
    """Translate old image-only fields at execution, preserving archive identity."""
    if not {'width', 'height'}.intersection(options):
        return options

    result = dict(options)
    pixels = {key: result.pop(key) for key in ('width', 'height') if key in result}
    requirement = result.get('size_requirement')
    if requirement is not None:
        raise TaskError('CONTENT_ARGUMENTS_INVALID')
    if (len(pixels) != 2 or any(type(value) is not int or value < 1 for value in pixels.values())):
        raise TaskError('CONTENT_ARGUMENTS_INVALID')
    # Legacy exact pixel metadata already specifies its ratio. Do not emit a
    # second retired aspect hint which can conflict with a current typed canvas.
    result.pop('aspect_ratio', None)
    result['size_requirement'] = {'mode': 'explicit', **pixels}
    return result


def compile_input(state, name, arguments, reference, *, allow_old=False):
    """Copy the one saved execution text; adapters only translate parameter names."""
    from services.tools.catalog import build_tool_catalog
    from services.tools.argument_validation import _issues
    selection_type = ImageContentReference if name == 'generate_image' else ContentReference
    selection = selection_type.model_validate(reference)
    if isinstance(selection, ImageContentReference) and selection.regenerate is not None:
        # User evidence is used at the chat boundary and never becomes part of a native proof.
        selection = selection.model_copy(update={'regenerate': None})
    artifact, record, item = _load_record(state, selection)
    if not allow_old and not is_current(state, artifact.id):
        raise TaskError('CONTENT_BASE_VERSION_CHANGED')
    text = item.body
    inputs = [{**m.reference.model_dump(mode='json',exclude_none=True),'role':m.role} for m in item.materials if m.usage=='input']
    result = dict(item.options)
    if name == 'generate_image':
        if selection.image_override:
            if len({(image.message_id, image.content_index) for image in selection.image_override}) != len(selection.image_override):
                raise TaskError('CONTENT_IMAGE_SELECTION_CONFLICT')
            inputs = [{**image.model_dump(mode='json'), 'role': '原图参考'} for image in selection.image_override]
            if result.get('size_requirement', {}).get('mode') == 'inherit_reference':
                # The old index belongs to the original material array. A new
                # image selection keeps its own canvas/receipt authoritative.
                result.pop('size_requirement')
            if result.get('aspect_ratio') == 'auto':
                result.pop('aspect_ratio')
            receipts = content_image_receipts(state)
            previous = receipts.get((artifact.id, selection.item_index))
            if previous:
                result = {**previous['specs'], **result}
            result.pop('mode', None)
        elif selection.without_reference:
            if inputs:
                raise TaskError('CONTENT_ARGUMENT_CONFLICT')
        elif indices := selection.source_image_indices:
            selected = _selected_image_inputs(record, indices, inputs)
            if inputs:
                selection = selection.model_copy(update={'source_image_indices': None})
            inputs = selected
        elif record.source_images and not inputs and not allow_old:
            analysis = [material.reference for material in item.materials if material.usage == 'analysis']
            if not all(image.reference in analysis for image in record.source_images):
                raise TaskError('CONTENT_SOURCE_IMAGE_SELECTION_REQUIRED')
        if {'width', 'height'}.intersection(item.options):
            result = _legacy_image_options(result, state.original_goal, bool(inputs))
        mode='image_to_image' if inputs else 'text_to_image'
        if result.get('mode',mode)!=mode: raise TaskError('CONTENT_ARGUMENT_CONFLICT')
        result.update(prompt=text,references=inputs,mode=mode)
        result.setdefault('plan_item_id',reference['artifact_id']+':'+str(reference['item_index']))
        variant = arguments.get('variant_id')
        if is_regeneration_variant(variant):
            result['variant_id'] = variant
        else:
            result.setdefault('variant_id', result['plan_item_id']+'/1')
    elif name == 'generate_video':
        if inputs: raise TaskError('CONTENT_MATERIAL_NOT_SUPPORTED_BY_TOOL')
        from services.task_execution.video_operations import VIDEO_ASPECT_RATIO

        # The registered draft may repeat the native output ratio as an option.
        # Its selector is model-facing, while the native tool accepts only prompt.
        if result.get('aspect_ratio') in ('16:9', VIDEO_ASPECT_RATIO):
            result.pop('aspect_ratio')
        if result:
            raise TaskError('CONTENT_VIDEO_OPTIONS_UNSUPPORTED')
        result['prompt'] = text
    elif name == 'code_execute':
        if inputs: raise TaskError('CONTENT_MATERIAL_NOT_SUPPORTED_BY_TOOL')
        from services.handlers.chat.content_tools import inject_source_code
        if not isinstance(arguments.get('code'),str): raise TaskError('CONTENT_FILE_CODE_REQUIRED')
        result['code'] = inject_source_code(arguments['code'],text)
    else:
        raise TaskError('CONTENT_TOOL_ADAPTER_UNAVAILABLE')
    for key,value in arguments.items():
        if key=='code' and name=='code_execute': continue
        if key in {'prompt','references','source_prompt','chat_input','chat_text_source','plan_source','content_source'}:
            raise TaskError('CONTENT_ARGUMENT_CONFLICT')
        if key in result and digest(result[key])!=digest(value): raise TaskError('CONTENT_ARGUMENT_CONFLICT')
        result[key] = value
    parameters = build_tool_catalog().require(name).to_schema()['function']['parameters']
    if _issues(result,parameters): raise TaskError('CONTENT_ARGUMENTS_INVALID')
    proof = {'version':1,'source':{**selection.model_dump(mode='json', exclude_none=True), 'sha256': artifact.sha256},
             'tool':name,'arguments':arguments,'arguments_sha256':digest(result)}
    if name=='generate_image': result['source_prompt']={'content_object':proof}
    binding = {'kind':'content_object','reference':proof['source'],'input_arguments':arguments,'allow_old':allow_old,
               'text':text,'mode':'exact','proof':proof}
    return result,binding
