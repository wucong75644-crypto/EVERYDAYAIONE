"""Versioned content in the existing Artifact ledger; one rendered/executable source."""
from __future__ import annotations
import hashlib
import json
from typing import Any, Literal
from uuid import UUID
from pydantic import Field, model_validator
from .contracts import Artifact, Fact, Key, TaskError
from .reducer import digest, register_artifacts, complete_step

SAVE = 'save_task_text'
TAG = 'content.object.v2'
PROTOCOL = 'content-objects-v2'

class ContentReference(Fact):
    artifact_id: Key
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    item_index: int = Field(strict=True, ge=1, le=100)

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
    usage: Literal['input', 'analysis']

class ContentItem(Fact):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20000,description='用户可见并交给目标工具的完整最终原文，包括全部执行要求、限制和禁止项；后台逐字传递，不再拼接其他文字。')
    materials: tuple[Material, ...] = Field(max_length=16)
    options: dict[str, Any] = Field(json_schema_extra={'propertyNames':{'not':{'enum':['prompt','references','source_prompt','content_source','plan_source','chat_input','code']}}})

    @model_validator(mode='after')
    def output_options(self):
        if set(self.options).intersection({'prompt','references','source_prompt','content_source','plan_source','chat_input','code'}):
            raise ValueError('CONTENT_OPTIONS_RESERVED')
        if not self.body.strip():
            raise ValueError('CONTENT_BODY_EMPTY')
        return self

class ContentDocument(Fact):
    version: Literal[2] = 2
    title: str = Field(min_length=1, max_length=300)
    notes: str = Field(default='', max_length=8000)
    items: tuple[ContentItem, ...] = Field(min_length=1, max_length=100)

class ContentRecord(Fact):
    version: Literal[2] = 2
    object_key: str
    revision: int = Field(strict=True, ge=1)
    document: ContentDocument
    base_artifact_id: str | None = None


class ContentItemPatch(Fact):
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

class SaveContent(Fact):
    model_config = {**Fact.model_config,'json_schema_extra':{'oneOf':[{'required':['document'],'not':{'required':['base']}},{'required':['base','updates'],'not':{'required':['document']}}]}}
    document: ContentDocument | None = None
    base: ContentReference | None = None
    updates: tuple[ItemUpdate, ...] = Field(default=(), max_length=100)
    continue_execution: bool = False

    @model_validator(mode='after')
    def choice(self):
        if ((self.document is not None) == (self.base is not None)
                or self.base is None and self.updates or self.base is not None and not self.updates):
            raise ValueError('CONTENT_SAVE_FORM_INVALID')
        return self

SCHEMA = {'type': 'function', 'function': {'name': SAVE,
    'description': '交付方案、提示词、脚本等可复用内容时，保存每条完整最终原文body；原文必须包含需要执行的全部要求、限制、负面描述，后台不再翻译、拼接或补写。平台从同一原文直接展示。'
        'materials绑定素材身份并区分执行输入与仅分析，options保存输出规格；分析说明放notes，不进入执行原文。'
        '修订既有条目时用base+updates替换该条完整body，未改条目、素材和规格由后台保留。默认只交付文字，用户同时要求继续执行时才设continue_execution=true。',
    'parameters': SaveContent.model_json_schema()}}


def objects(state):
    if state is None: return []
    committed = {a for result in state.results for a in result.artifact_ids}
    return [a for a in state.artifacts if TAG in a.validations and a.id in committed]


def load(state, reference):
    reference = ContentReference.model_validate(reference)
    artifact = next((a for a in objects(state) if a.id == reference.artifact_id), None)
    if artifact is None or artifact.sha256 != reference.sha256:
        raise TaskError('CONTENT_OBJECT_UNAVAILABLE_OR_CHANGED')
    if hashlib.sha256(artifact.inline_text.encode()).hexdigest() != artifact.sha256:
        raise TaskError('CONTENT_OBJECT_CHANGED')
    document = ContentRecord.model_validate_json(artifact.inline_text).document
    if reference.item_index > len(document.items): raise TaskError('CONTENT_ITEM_UNAVAILABLE')
    return artifact, document, document.items[reference.item_index-1]


def catalog(state):
    latest = {}
    for artifact in objects(state):
        record = ContentRecord.model_validate_json(artifact.inline_text)
        previous = latest.get(record.object_key)
        if previous is None or record.revision > previous[1].revision:
            latest[record.object_key] = (artifact,record)
    return [{'artifact_id': artifact.id, 'sha256': artifact.sha256, 'object_key': record.object_key,
             'revision': record.revision, 'title': record.document.title,
             'items': [{'item_index': i, 'title': item.title, 'body_sha256':hashlib.sha256(item.body.encode()).hexdigest(), 'materials':[m.model_dump(mode='json',exclude_none=True) for m in item.materials], 'options':item.options} for i,item in enumerate(record.document.items,1)]}
            for artifact,record in latest.values()]


def render(document):
    parts = [f'# {document.title}']
    if document.notes: parts.append('分析说明（不作为执行要求）\n'+document.notes)
    for i,item in enumerate(document.items,1):
        parts.extend([f'## 方案{i}｜{item.title}', item.body])
        if item.options: parts.append('输出规格\n'+json.dumps(item.options,ensure_ascii=False))
        if item.materials: parts.append('素材用途\n'+'\n'.join(f'{m.role}：'+('执行参考' if m.usage=='input' else '仅分析') for m in item.materials))
    return '\n\n'.join(parts)


def save(runtime, call, registry):
    from services.tools import ToolCall
    from .model_directed import record_calls, call_binding
    value = SaveContent.model_validate_json(call['arguments'])
    state = runtime.task_execution_state
    bound = next((b for b in state.call_bindings if b.call_id == call['id']),None) if state else None
    if bound:
        if bound.tool_name != SAVE or bound.arguments_sha256 != digest(value.model_dump(mode='json')):
            raise TaskError('CONTENT_SAVE_CALL_CHANGED')
        artifact = next((a for a in objects(state) if a.source_call_id == call['id']),None)
        if artifact is None: raise TaskError('CONTENT_SAVE_UNCONFIRMED')
        return state, artifact, ContentRecord.model_validate_json(artifact.inline_text).document, value
    document = value.document
    object_key = 'object-'+digest([str(runtime.task_id),call['id']])[:32]
    revision = 1
    if value.base:
        prior_artifact, previous, _ = load(runtime.task_execution_state, value.base)
        prior_record = ContentRecord.model_validate_json(prior_artifact.inline_text)
        if not any(row['artifact_id'] == prior_artifact.id for row in catalog(state)):
            raise TaskError('CONTENT_BASE_VERSION_CHANGED')
        object_key, revision = prior_record.object_key, prior_record.revision+1
        items = list(previous.items); indices = set()
        for update in value.updates:
            if update.item_index in indices or update.item_index > len(items): raise TaskError('CONTENT_UPDATE_INVALID')
            items[update.item_index-1] = ContentItem.model_validate({**items[update.item_index-1].model_dump(mode='json'),**update.item.model_dump(mode='json',exclude_unset=True)}); indices.add(update.item_index)
        document = previous.model_copy(update={'items':tuple(items)})
    body = ContentRecord(object_key=object_key,revision=revision,document=document,
                         base_artifact_id=value.base.artifact_id if value.base else None).model_dump_json()
    if len(body.encode()) > 50000: raise TaskError('CONTENT_OBJECT_TOO_LARGE')
    checksum = hashlib.sha256(body.encode()).hexdigest()
    for artifact in objects(runtime.task_execution_state):
        if artifact.sha256 == checksum: return runtime.task_execution_state, artifact, document, value
    parsed = ToolCall(call['id'], SAVE, value.model_dump(mode='json'))
    state = record_calls(runtime.task_execution_state, runtime.task_bootstrap, [parsed], registry)
    step, requirement = call_binding(state, parsed)
    from .direct_loop import active_image_methods
    from .contracts import MethodBinding
    methods=active_image_methods(runtime)
    if len(methods)>1: raise TaskError('CONTENT_METHOD_CONFLICT')
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


def compile_input(state, name, arguments, reference, *, allow_old=False):
    """Copy the one saved execution text; adapters only translate parameter names."""
    from services.tools.catalog import build_tool_catalog
    from services.tools.argument_validation import _issues
    _, document, item = load(state, reference)
    if not allow_old and not any(row['artifact_id'] == reference['artifact_id'] for row in catalog(state)):
        raise TaskError('CONTENT_BASE_VERSION_CHANGED')
    text = item.body
    inputs = [{**m.reference.model_dump(mode='json',exclude_none=True),'role':m.role} for m in item.materials if m.usage=='input']
    result = dict(item.options)
    if name == 'generate_image':
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
    proof = {'version':1,'source':ContentReference.model_validate(reference).model_dump(mode='json'),
             'tool':name,'arguments':arguments,'arguments_sha256':digest(result)}
    if name=='generate_image': result['source_prompt']={'content_object':proof}
    binding = {'kind':'content_object','reference':proof['source'],'input_arguments':arguments,'allow_old':allow_old,
               'text':text,'mode':'exact','proof':proof}
    return result,binding


def requires_reference(runtime):
    skills = getattr(runtime,'skill_runtime',None)
    return bool(skills and any(skills.directory[key].catalog_metadata.content_protocol==PROTOCOL for key in skills.active))
