"""Versioned content in the existing Artifact ledger; one rendered/executable source."""
from __future__ import annotations
import hashlib
import json
import re
from typing import Annotated, Any, Literal
from uuid import UUID
from pydantic import Field, field_validator, model_serializer, model_validator
from .contracts import Artifact, Fact, Key, TaskError
from .reducer import digest, register_artifacts, complete_step

SAVE = 'save_task_text'
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
    source_image_indices: tuple[Annotated[int, Field(strict=True, ge=1)], ...] | None = Field(
        default=None, min_length=1, max_length=16,
        description='使用所选成果里尚未绑定的原图时，填写 source_images 序号。')
    image_override: tuple[ImageMessageReference, ...] | None = Field(
        default=None, min_length=1, max_length=16,
        description='仅当用户明确换图时填写。复制对话中所选图片的 message_id 和 content_index，后台只替换此次执行的参考图。')
    without_reference: Literal[True] | None = Field(
        default=None, description='仅当本轮明确不使用方案的候选图片，或图片只作分析时设为 true。')

    @model_validator(mode='after')
    def image_choice(self):
        if sum(choice is not None for choice in (
                self.source_image_indices, self.image_override, self.without_reference)) > 1:
            raise ValueError('CONTENT_IMAGE_SELECTION_CONFLICT')
        return self

class MaterialReference(Fact):
    resource_ref: str | None = Field(default=None,min_length=1)
    file_id: str | None = Field(default=None,min_length=1)
    asset_id: str | None = Field(default=None,min_length=1)
    message_id: str | None = Field(default=None,min_length=1)
    content_index: int | None = Field(default=None,strict=True,ge=0)

    @model_validator(mode='after')
    def identity(self):
        if sum(v is not None for v in (self.resource_ref,self.file_id,self.asset_id,self.message_id))!=1 or (self.message_id is not None)!=(self.content_index is not None):
            raise ValueError('CONTENT_MATERIAL_IDENTITY_REQUIRED')
        return self

class Material(Fact):
    reference: MaterialReference
    role: str = Field(min_length=1, max_length=200)
    usage: Literal['input', 'analysis'] = Field(description='执行时传给工具用input，仅用来理解素材用analysis。')


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
        description='仅有该条专用的素材用途需要登记时填写。只写文字时，当前原图由后台统一记为来源。')
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
    content_index: int = Field(strict=True, ge=0)
    title: str = Field(min_length=1, max_length=300)
    items: tuple[MessageItem, ...] = Field(min_length=1, max_length=100)
    source_image_indices: tuple[Annotated[int, Field(strict=True, ge=1)], ...] | None = Field(
        default=None, min_length=1, max_length=16,
        description='整组明确沿用原稿所关联的原图时填原图序号。只分析素材时省略。')


class SaveContent(Fact):
    model_config = {**Fact.model_config, 'json_schema_extra': {'oneOf': [
        {'required': ['document']}, {'required': ['base', 'updates']}, {'required': ['message_source']},
    ]}}
    document: ContentDocument | None = None
    base: ContentReference | None = None
    message_source: MessageContentSource | None = None
    updates: tuple[ItemUpdate, ...] = Field(default=(), max_length=100)
    continue_execution: bool = False
    new_content: Literal[True] | None = Field(
        default=None, description='仅当本轮明确是全新制作且无需采用历史正文或素材时，随 document 填 true。')
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
        if (sum(value is not None for value in (self.document, self.base, self.message_source)) != 1
                or self.base is None and self.updates or self.base is not None and not self.updates):
            raise ValueError('CONTENT_SAVE_FORM_INVALID')
        if self.message_source is not None and (not self.continue_execution or self.show_content):
            raise ValueError('CONTENT_MESSAGE_SOURCE_REQUIRES_EXECUTION')
        if self.new_content is not None and (self.document is None or not self.continue_execution):
            raise ValueError('CONTENT_NEW_SOURCE_INVALID')
        return self

def save_parameters(*, require_presentation=False):
    parameters = SaveContent.model_json_schema()
    # Advertise each selector as an object. The oneOf controls the selector choice.
    parameters['properties']['document'] = parameters['$defs'][ContentDocument.__name__]
    parameters['properties']['base'] = selection_parameters()
    parameters['properties']['message_source'] = parameters['$defs'][MessageContentSource.__name__]
    del parameters['$defs'][ContentReference.__name__]
    if require_presentation:
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


SCHEMA = {'type': 'function', 'function': {'name': SAVE,
    'description': '用于修订已登记内容，或在本轮执行前登记输入。只交付新写的文稿时直接写入聊天，后台保存消息。'
        '执行历史稿件时直接选已有成果；未登记则先按消息ID读取，再用message_source和continue_execution=true登记原稿整组。'
        '每条填完整执行原文的行范围，包含已有的限制。行号从1开始，两端均包含。整组复用原图时登记原图序号一次。'
        '已有历史稿件或图片时，只有用户明确开启独立制作才可用document和new_content=true交接全新内容。'
        'body包含最终要求和限制；按用户或方法要求交付的可见结构放display_prefix，内部说明放notes。仅有条目专用素材或已确定执行规格时才填materials或options。'
        '修订既有条目时，base选择成果ID和条目，updates传修订正文。后台绑定版本并保留未改条目、素材和规格。'
        '本轮实际执行时设continue_execution=true；有上传图片时每条明确登记素材及用途。还要求看文字时设show_content=true。',
    'parameters': save_parameters()}}


def save_schema(runtime):
    from .direct_loop import active_image_methods
    from .methods import REFERENCE_PROMPTS_PROFILE, REFERENCE_PROMPTS_PRESENTATION

    if not any(method[0] == REFERENCE_PROMPTS_PROFILE for method in active_image_methods(runtime).values()):
        return SCHEMA
    return {**SCHEMA, 'function': {**SCHEMA['function'],
        'description': REFERENCE_PROMPTS_PRESENTATION + SCHEMA['function']['description'],
        'parameters': save_parameters(require_presentation=True)}}


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
    operations = {operation.step_id: operation.status for operation in state.operations}
    completed = {result.step_id for result in state.results}
    receipts = {}
    for artifact in state.artifacts:
        if 'image.direct.recipe' not in artifact.validations:
            continue
        if artifact.step_id in completed:
            status = 'completed'
        else:
            status = operations.get(artifact.step_id)
        if status not in {'accepted', 'completed', 'uncertain', 'reserved'}:
            continue
        recipe = json.loads(artifact.inline_text)['normalized']
        proof = (recipe.get('source_prompt') or {}).get('content_object')
        if proof is None or proof['source'].get('image_override'):
            continue
        source = proof['source']
        key = (source['artifact_id'], source['item_index'])
        receipts[key] = {'status': status, 'specs': {name: recipe[name] for name in (
            'aspect_ratio', 'resolution', 'output_format') if name in recipe}}
    return receipts


def catalog(state, *, for_model=False):
    receipts = content_image_receipts(state) if for_model else {}
    latest = {}
    for artifact in objects(state):
        record = ContentRecord.model_validate_json(artifact.inline_text)
        previous = latest.get(record.object_key)
        if previous is None or record.revision > previous[1].revision:
            latest[record.object_key] = (artifact,record)
    rows = []
    for artifact, record in latest.values():
        items = []
        for index, item in enumerate(record.document.items, 1):
            entry = {'item_index': index, 'title': item.title, 'options': item.options}
            if for_model:
                entry['has_bound_inputs'] = any(material.usage == 'input' for material in item.materials)
                receipt = receipts.get((artifact.id, index))
                if receipt:
                    entry['image_status'] = receipt['status']
            else:
                entry['body_sha256'] = hashlib.sha256(item.body.encode()).hexdigest()
                entry['materials'] = [material.model_dump(mode='json', exclude_none=True) for material in item.materials]
            items.append(entry)
        row = {'artifact_id': artifact.id, 'object_key': record.object_key,
               'revision': record.revision, 'title': record.document.title, 'items': items}
        if not for_model:
            row['sha256'] = artifact.sha256
        if record.source_images and (not for_model or any(not item['has_bound_inputs'] for item in items)):
            row['source_images'] = [{'index': index, 'name': image.name}
                                    for index, image in enumerate(record.source_images, 1)]
        rows.append(row)
    return rows

def is_current(state, artifact_id):
    return any(row['artifact_id'] == artifact_id for row in catalog(state, for_model=True))


def has_selectable_source_images(state):
    return any(row.get('source_images') for row in catalog(state, for_model=True))


def render(document):
    parts = [document.display_prefix or f'# {document.title}']
    for i, item in enumerate(document.items, 1):
        parts.extend([item.display_prefix or f'## {i}. {item.title}', item.body])
    return '\n\n'.join(parts)


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
                        if result.get('code') in {DRAFT_CONFLICT, INVALID_OBJECT, ATTACHMENT_USAGE_REQUIRED, PRESENTATION_INCOMPLETE}:
                            return True
    return False


def source_images_from_message(row):
    from services.handlers.chat_context.image_sources import image_sources
    return tuple(SourceImage(name=image['name'], reference=image['reference'])
                 for image in image_sources(row) if image['available'])


def current_source_images(request):
    message_id = getattr(request.context_anchor, 'input_message_id', None)
    if not message_id:
        return ()
    row = {'id': message_id, 'role': 'user', 'content': [part.model_dump(mode='json') for part in request.content]}
    return source_images_from_message(row)


def save(runtime, call, registry, *, has_image_attachments=False, source_images=(), checked_message=None):
    from services.tools import ToolCall
    from .model_directed import record_calls, call_binding
    from .direct_loop import active_image_methods
    from .methods import REFERENCE_PROMPTS_PROFILE, valid_reference_prompts_presentation
    value = SaveContent.model_validate_json(call['arguments'])
    state = runtime.task_execution_state
    base_record = None
    if value.base is not None:
        base_record = _load_record(state, value.base)
        if value.base.sha256 is None:
            value = value.model_copy(update={
                'base': value.base.model_copy(update={'sha256': base_record[0].sha256}),
            })
    arguments = value.model_dump(mode='json')
    if value.show_content is None:
        arguments.pop('show_content')
    if value.message_source is None:
        arguments.pop('message_source')
    elif value.message_source.source_image_indices is None:
        arguments['message_source'].pop('source_image_indices')
    if value.new_content is None:
        arguments.pop('new_content')
    bound = next((b for b in state.call_bindings if b.call_id == call['id']),None) if state else None
    if bound:
        if bound.tool_name != SAVE or bound.arguments_sha256 != digest(arguments):
            raise TaskError('CONTENT_SAVE_CALL_CHANGED')
        artifact = next((a for a in objects(state) if a.source_call_id == call['id']),None)
        if artifact is None: raise TaskError('CONTENT_SAVE_UNCONFIRMED')
        return state, artifact, ContentRecord.model_validate_json(artifact.inline_text).document, value
    document = value.document
    message_proof = None
    if value.message_source is not None:
        if checked_message is None:
            raise TaskError('CONTENT_MESSAGE_SOURCE_UNVERIFIED')
        document, source_images, message_proof = checked_message
    elif (document is not None and value.continue_execution and has_image_attachments
            and any(not item.materials for item in document.items)):
        raise TaskError(ATTACHMENT_USAGE_REQUIRED)
    if value.continue_execution and value.message_source is None:
        source_images = ()
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
        source_images = prior_record.source_images
        presentation_profile = prior_record.presentation_profile or presentation_profile
        if not is_current(state, prior_artifact.id):
            raise TaskError('CONTENT_BASE_VERSION_CHANGED')
        object_key, revision = prior_record.object_key, prior_record.revision+1
        items = list(previous.items); indices = set()
        for update in value.updates:
            if update.item_index in indices or update.item_index > len(items): raise TaskError('CONTENT_UPDATE_INVALID')
            items[update.item_index-1] = ContentItem.model_validate({**items[update.item_index-1].model_dump(mode='json'),**update.item.model_dump(mode='json',exclude_unset=True)}); indices.add(update.item_index)
        document = previous.model_copy(update={'items':tuple(items)})
    if (presentation_profile == REFERENCE_PROMPTS_PROFILE
            and not valid_reference_prompts_presentation(document)):
        raise TaskError(PRESENTATION_INCOMPLETE)
    body = ContentRecord(object_key=object_key,revision=revision,document=document,source_images=source_images,
                         base_artifact_id=value.base.artifact_id if value.base else None,
                         presentation_profile=presentation_profile).model_dump_json(exclude={
                             name for name, present in (
                                 ('source_images', source_images), ('presentation_profile',presentation_profile))
                             if not present})
    if len(body.encode()) > 50000: raise TaskError('CONTENT_OBJECT_TOO_LARGE')
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


def compile_input(state, name, arguments, reference, *, allow_old=False):
    """Copy the one saved execution text; adapters only translate parameter names."""
    from services.tools.catalog import build_tool_catalog
    from services.tools.argument_validation import _issues
    selection_type = ImageContentReference if name == 'generate_image' else ContentReference
    selection = selection_type.model_validate(reference)
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
            previous = content_image_receipts(state).get((artifact.id, selection.item_index))
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
        elif record.source_images and not inputs and not allow_old and result.get('mode') != 'text_to_image':
            if not any(material.usage == 'analysis' for material in item.materials):
                raise TaskError('CONTENT_SOURCE_IMAGE_SELECTION_REQUIRED')
        mode='image_to_image' if inputs else 'text_to_image'
        if result.get('mode',mode)!=mode: raise TaskError('CONTENT_ARGUMENT_CONFLICT')
        result.update(prompt=text,references=inputs,mode=mode)
        result.setdefault('plan_item_id',reference['artifact_id']+':'+str(reference['item_index']))
        result.setdefault('variant_id',result['plan_item_id']+'/1')
    elif name == 'generate_video':
        if inputs: raise TaskError('CONTENT_MATERIAL_NOT_SUPPORTED_BY_TOOL')
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
