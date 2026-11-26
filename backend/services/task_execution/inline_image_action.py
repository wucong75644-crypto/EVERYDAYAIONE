"""Atomically bind one model image draft to the existing content/native ledgers."""
from dataclasses import replace
import json
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from services.tools.argument_validation import _issues, validate_model_image_arguments
from services.tools.policy import ToolPolicy
from services.handlers.image_size_requirements import image_draft_options_schema

from . import content_objects as content
from .contracts import Fact, TaskError
from .reducer import digest

KEY = '_inline_media_action'
TOOL = 'generate_image'


class InlineAction(Fact):
    version: int = 1
    owner_task_id: str
    turn_id: str
    call_id: str
    save_id: str
    input_sha256: str
    arguments_sha256: str
    reference: content.ContentReference
    native_sha256: str


class InlineRequest(Fact):
    draft: content.ContentItem

    @staticmethod
    def parameters(base):
        draft = content.ContentItem.model_json_schema()
        definitions = draft.pop('$defs', {})
        properties = draft['properties']
        properties.pop('display_prefix', None)
        properties['options'] = image_draft_options_schema(base)
        return draft, definitions


class SingleImageEdit(Fact):
    source_message_id: UUID = Field(description='原样复制所选编辑底图的 reference.message_id；可选当前附件或可访问的历史图片。')
    source_content_index: StrictInt = Field(ge=0, description='原样复制 reference.content_index，从 0 开始。')
    edit_instruction: str = Field(min_length=1, max_length=20000, description='本次修改以及需要保留的内容。')
    materials: tuple[content.Material, ...] = Field(default=(), max_length=15,
        description='其他参考图及用途，不重复填写编辑底图。' + content.MATERIAL_GUIDANCE)

    def content_item(self):
        reference = content.MaterialReference(message_id=str(self.source_message_id),
                                              content_index=self.source_content_index)
        additional = []
        for material in self.materials:
            if material.reference == reference:
                if material.usage != 'input':
                    raise TaskError('CHAT_INLINE_IMAGE_SELECTION_CONFLICT')
                continue  # Same base named twice is one input, never two images.
            additional.append(material)
        return content.ContentItem(title='编辑图片', body=self.edit_instruction, materials=(
            content.Material(reference=reference, role='编辑底图', usage='input'),
            *additional,
        ))


class PreviousResultEdit(Fact):
    """Private admission for old calls; never advertised to new model requests."""
    previous_result_edit: str = Field(min_length=1, max_length=20000,
        description='编辑上一轮唯一图片任务的已完成结果：写本次修改与保留要求。后台按原回执绑定底图，不重复使用它的输入图片。')
    edit_instruction: str | None = Field(default=None, min_length=1, max_length=20000)
    source_message_id: UUID | None = None
    source_content_index: StrictInt | None = Field(default=None, ge=0)
    materials: tuple[content.Material, ...] = Field(default=(), max_length=15)

    @model_validator(mode='after')
    def consistent(self):
        if self.edit_instruction is not None and self.edit_instruction != self.previous_result_edit:
            raise ValueError('两份编辑要求冲突，请只保留 edit_instruction 中的本轮完整要求。')
        if (self.source_message_id is None) != (self.source_content_index is None):
            raise ValueError('底图引用不完整，source_message_id 和 source_content_index 必须成对填写。')
        return self


EDIT_GUIDANCE = ('编辑图片统一填写 source_message_id、source_content_index 和 edit_instruction；'
    '其他参考图放 materials，并注明用途。底图引用从图片目录原样复制；'
    '“刚生成那张”选对应已完成结果，不使用旧输入图；多个候选无法确定时读取会话或询问用户。'
)


def validation_parameters(parameters, arguments):
    # Keep legacy calls explicit and private without publishing conflicting schemas.
    if ('edit_instruction' in parameters.get('properties', {})
            and isinstance(arguments, dict) and 'previous_result_edit' in arguments):
        return PreviousResultEdit.model_json_schema()
    return parameters


def request_type(properties, arguments=None):
    if arguments is not None and 'previous_result_edit' in arguments:
        return PreviousResultEdit if 'edit_instruction' in properties else None
    if 'edit_instruction' in properties:
        return SingleImageEdit
    if 'draft' in properties:
        return InlineRequest
    return None


def has_prepared_call(runtime, call):
    record = getattr(runtime, 'checkpoint_base', {}).get(KEY)
    return isinstance(record, dict) and record.get('call_id') == call['id']


def is_inline_call(call):
    if call['name'] != TOOL:
        return False
    # Recognize the form even if strict decoding later rejects duplicate keys.
    try:
        value = json.loads(call.get('arguments') or '{}')
    except (ValueError, TypeError, RecursionError):
        return False
    return isinstance(value, dict) and bool({'draft', 'edit_instruction', 'previous_result_edit'} & value.keys())


def allowed_for_offer(runtime, *, server_image_plan, reuse_source):
    from core.config import get_settings
    from .direct_loop import active_image_methods, saved_image_plans

    return (get_settings().chat_inline_image_enabled and not server_image_plan and not reuse_source
            and not active_image_methods(runtime) and not saved_image_plans(runtime.task_execution_state))


def input_version(runtime, request):
    anchor = request.context_anchor
    return digest([getattr(anchor, 'input_message_id', None), sorted(runtime.task_direct_seen)])


def current_attachment_edit(request):
    """Resolve the existing semantic target against actual current attachments."""
    text='\n'.join(part.text for part in request.content if part.type=='text')
    images=[index for index,part in enumerate(request.content) if part.type=='image']
    projection=(getattr(request,'params',None) or {}).get('_ecom_workflow') or {}
    semantics=projection.get('request_context')
    target=(semantics or {}).get('canvas_image')
    message=getattr(request.context_anchor,'input_message_id',None)
    if not message or target is None or not 1<=target<=len(images):
        return None
    return str(message),images[target-1],target,text,images


def previous_result_task(state):
    """The shortcut represents exactly one native receipt, never a guessed latest item."""
    if state is None:
        return None
    tasks = {op.receipt_ref.removeprefix('task:') for op in state.operations
             if op.tool_name == TOOL and op.status in {'accepted', 'completed'}
             and op.receipt_ref and op.receipt_ref.startswith('task:')}
    if len(tasks) != 1:
        return None  # Multiple prior outputs still require an explicit selection.
    return next(iter(tasks))


def previous_result_reference(state, handler, request, context):
    """Continue the single previous image receipt, not whichever old input the model names."""
    from .image_resources import native_resolver
    task = previous_result_task(state)
    if task is None:
        return None
    resolver = native_resolver(handler, request, context)
    matches = [(row, index) for row, index, part in resolver.known_sources()
               if row.get('role') == 'assistant' and row.get('status') == 'completed'
               and str(part.get('task_id')) == task]
    if len(matches) != 1:
        return None
    row, index = matches[0]
    reference = {'message_id': str(row['id']), 'content_index': index}
    return reference  # Discovery only; prepare/accept retain native resource validation.


async def complete_transfer_materials(item, handler, request, context, *, runtime=None):
    """Merge semantic material selections by verified native identity, not wording."""
    from services.handlers.chat.content_tools import semantic_context
    from .image_resources import native_resolver
    import asyncio
    frame = semantic_context(request, runtime)
    if frame is None or frame.materials is None:
        return item
    if runtime is not None and (getattr(getattr(runtime, 'task_user_changes', None), 'items', ())
                                or getattr(runtime, '_steer_messages', ())):
        return item  # Never reapply initiating material choices over a newer input.
    def resolve():
        native = native_resolver(handler, request, context)
        def selectors(materials):
            return [{**m.reference.model_dump(mode='json', exclude_none=True), 'role': m.role} for m in materials]
        return native.resolve(selectors(item.materials)), native.resolve(selectors(frame.materials))
    existing, wanted = await asyncio.to_thread(resolve)
    materials = list(item.materials)
    for material, resolved in zip(frame.materials, wanted, strict=True):
        matches = [i for i, original in enumerate(existing)
                   if original['workspace_path'] == resolved['workspace_path']]
        if matches:
            for index in matches:
                materials[index] = materials[index].model_copy(update={'usage': material.usage})
        else:
            materials.append(material)
            existing.append(resolved)
    return item.model_copy(update={'materials': tuple(materials)})


async def label_current_inputs(item, handler, request, context, *, runtime=None, edit_reference=None):
    """Label verified positions; preserve the model's instruction and image order."""
    import asyncio
    from .image_resources import native_resolver
    images = [index for index, part in enumerate(request.content) if part.type == 'image']
    sources=content.current_source_images(request,runtime)
    inputs = [material for material in item.materials if material.usage == 'input']
    message_id = getattr(getattr(request, 'context_anchor', None), 'input_message_id', None)
    if len(sources) < 2 or not inputs or not message_id:
        return item
    def resolve():
        native = native_resolver(handler, request, context)
        selected = native.resolve([{**m.reference.model_dump(mode='json', exclude_none=True), 'role':m.role} for m in inputs])
        current = native.resolve([{**source.reference.model_dump(mode='json',exclude_none=True),'role':'输入位置核验'} for source in sources])
        return selected, current
    selected, current = await asyncio.to_thread(resolve)
    lines = []
    for position, source in enumerate(selected, 1):
        matches = [number for number, original in enumerate(current, 1)
                   if (source.get('message_id') is not None and source.get('content_index') is not None
                       and (source.get('message_id'),source.get('content_index'))==
                           (original.get('message_id'),original.get('content_index')))]
        if not matches:
            matches=[number for number,original in enumerate(current,1)
                if source.get('workspace_path') is not None and source.get('content_sha256') is not None
                and all(source.get(key)==original.get(key) for key in ('workspace_path','content_sha256'))]
        if len(matches) == 1:
            number=matches[0]
            lines.append(f'输入图片{position}对应用户图{number}。' if number<=len(images)
                else f'输入图片{position}对应追加图{number-len(images)}（用户图{number}）。')
    if lines and edit_reference:
        positions = [i for i, source in enumerate(selected, 1)
                     if all(source.get(key) == value for key, value in edit_reference.items())]
        if len(positions) != 1:
            raise TaskError('CHAT_INLINE_IMAGE_SELECTION_CONFLICT')
        number = next((n for n, original in enumerate(current, 1)
                       if all(original.get(key) == value for key, value in edit_reference.items())), None)
        suffix = f'（用户图{number}）' if number is not None else ''
        lines.append(f'编辑底图为输入图片{positions[0]}{suffix}。')
    legend = '\n'.join(lines)
    if not legend or item.body.startswith(legend+'\n\n'):
        return item
    return item.model_copy(update={'body':legend+'\n\n'+item.body})


def read_record(runtime):
    value = runtime.checkpoint_base.get(KEY)
    if value is None:
        return None
    record = InlineAction.model_validate(value)
    if record.version != 1 or (record.owner_task_id, record.turn_id) != (str(runtime.task_id), str(runtime.turn_id)):
        raise TaskError('CHAT_INLINE_IMAGE_CHECKPOINT_INVALID')
    return record


def restore(runtime, record, call, args):
    state = runtime.task_execution_state
    if (record.call_id != call['id'] or record.arguments_sha256 != digest(args)
            or record.save_id != 'inline-save-' + digest([record.turn_id, record.call_id])[:32]):
        raise TaskError('CHAT_INLINE_IMAGE_CHECKPOINT_INVALID')
    reference = record.reference.model_dump(mode='json', exclude_none=True)
    artifact, _, _ = content.load(state, reference)
    if artifact.source_call_id != record.save_id:
        raise TaskError('CHAT_INLINE_IMAGE_CHECKPOINT_INVALID')
    compiled, _ = content.compile_input(state, TOOL, {}, reference, allow_old=True)
    if digest(compiled) != record.native_sha256:
        raise TaskError('CHAT_INLINE_IMAGE_CHECKPOINT_INVALID')
    binding = next((b for b in state.call_bindings if b.call_id == record.call_id), None)
    if not binding or binding.tool_name != TOOL or binding.arguments_sha256 != record.native_sha256:
        raise TaskError('CHAT_INLINE_IMAGE_CHECKPOINT_INVALID')
    recipe = next((item for item in state.artifacts if item.step_id == binding.step_id
                   and item.source_call_id == record.call_id and 'image.direct.recipe' in item.validations), None)
    if recipe is None:
        raise TaskError('CHAT_INLINE_IMAGE_CHECKPOINT_INVALID')
    # The operation RPC and the checkpoint share a transaction. A reserved or
    # already executed call always follows the existing replay-only path.
    started = any(op.step_id == binding.step_id for op in state.operations)
    return reference, started


async def prepare(runtime, handler, request, prepared, call, args, registry, *, version):
    import asyncio
    from .image_resources import native_resolver
    from .direct_loop import bind_image_recipes, stage_native_calls

    context = prepared.execution_context
    edit = None
    if 'previous_result_edit' in args:
        from .direct_loop import content_followup_state
        legacy = PreviousResultEdit.model_validate(args)
        source = await asyncio.to_thread(previous_result_reference,
            runtime.task_execution_state or content_followup_state(runtime), handler, request, context)
        if source is None:
            raise TaskError('CHAT_INLINE_IMAGE_GENERATED_SOURCE_REQUIRED')
        if legacy.source_message_id is not None and (
                str(legacy.source_message_id), legacy.source_content_index) != (
                    source['message_id'], source['content_index']):
            error = TaskError('CHAT_INLINE_IMAGE_SELECTION_CONFLICT')
            error.guidance = '上一轮结果与明确填写的底图不同。本次未提交；请按用户指代只填写一份底图引用及 edit_instruction。'
            raise error
        edit = SingleImageEdit(source_message_id=source['message_id'], source_content_index=source['content_index'],
                               edit_instruction=legacy.previous_result_edit, materials=legacy.materials)
    elif 'edit_instruction' in args:
        edit = SingleImageEdit.model_validate(args)
    item = edit.content_item() if edit is not None else InlineRequest.model_validate(args).draft
    if edit is not None:
        from services.handlers.chat.content_tools import semantic_context
        frame = semantic_context(request, runtime)
        if frame is not None:
            explicit_size = frame.size.model_dump(exclude_none=True)
            item = item.model_copy(update={'options': {key: explicit_size[key]
                for key in ('aspect_ratio', 'resolution') if key in explicit_size}})
    current=current_attachment_edit(request) if edit is not None else None
    if current is not None:
        message,index,target,text,images=current
        if (str(edit.source_message_id),edit.source_content_index)!=(message,index):
            raise TaskError('CHAT_INLINE_IMAGE_GENERATED_SOURCE_REQUIRED')
        if (getattr(getattr(runtime,'task_user_changes',None),'items',())
                or getattr(runtime,'_steer_messages',())):
            raise TaskError('CHAT_INLINE_IMAGE_INPUT_CHANGED')
        frame = ((getattr(request, 'params', None) or {}).get('_ecom_workflow') or {}).get('request_context') or {}
        selected = [m.get('reference', {}) for m in frame.get('materials') or () if m.get('usage') == 'input']
        ordinals = sorted({target, *(images.index(ref['content_index']) + 1 for ref in selected
            if str(ref.get('message_id')) == message and ref.get('content_index') in images)})
        if any(number>len(images) for number in ordinals):
            raise TaskError('CHAT_INLINE_IMAGE_REFERENCE_REQUIRED')
        materials=[material.model_copy(update={'role': f'用户图{target}（编辑底图）'})
                   if material.reference.message_id == message and material.reference.content_index == index
                   else material for material in item.materials]
        for n in ordinals:
            reference = content.MaterialReference(message_id=message, content_index=images[n-1])
            if not any(material.reference == reference for material in materials):
                materials.append(content.Material(reference=reference, role=f'用户图{n}（用户明确参考素材）', usage='input'))
        # Preserve original attachment order even when the second image is the base.
        materials.sort(key=lambda material: images.index(material.reference.content_index)
            if material.reference.message_id == message and material.reference.content_index in images else len(images))
        # Keep the instruction intact; label_current_inputs describes actual resolved positions below.
        item=content.ContentItem(title='图片编辑稿',
            body=edit.edit_instruction, options=item.options, materials=tuple(materials))
    from services.handlers.chat.content_tools import semantic_intent
    item = await complete_transfer_materials(item, handler, request, context, runtime=runtime)
    if edit is not None:
        base = next(m for m in item.materials if m.reference.message_id == str(edit.source_message_id)
                    and m.reference.content_index == edit.source_content_index)
        if base.usage != 'input':
            raise TaskError('CHAT_INLINE_IMAGE_SELECTION_CONFLICT')
    if semantic_intent(request, runtime) == 'edit':
        item = await label_current_inputs(item, handler, request, context, runtime=runtime, edit_reference=({'message_id': str(edit.source_message_id),
                'content_index': edit.source_content_index} if edit is not None else None))
    if edit is not None:
        def verify_edit_source():
            resolver = native_resolver(handler, request, context)
            material=next(material for material in item.materials if str(material.reference.message_id)==str(edit.source_message_id)
                          and material.reference.content_index==edit.source_content_index)
            resolver.resolve([
                {**material.reference.model_dump(mode='json', exclude_none=True), 'role': material.role},
            ])
            # Native resolution owns scope, version and byte checks for both
            # uploaded originals and generated results. Never substitute another image.

        try:
            await asyncio.to_thread(verify_edit_source)
        except (ValueError, PermissionError, OSError) as error:
            raise TaskError('CHAT_INLINE_IMAGE_GENERATED_SOURCE_REQUIRED') from error
    from services.tools.argument_validation import ToolArgumentValidationError

    native = registry.require(TOOL).to_schema()['function']['parameters']['properties']
    options = image_draft_options_schema(native)
    issues = _issues(item.options, options)
    if issues:
        raise ToolArgumentValidationError(
            [{**issue, 'path': '$.draft.options' + issue['path'][1:]} for issue in issues],
            {'type': 'object', 'properties': {'draft': {'type': 'object', 'properties': {'options': options}}}})
    if item.display_prefix is not None:
        raise TaskError('CHAT_INLINE_IMAGE_OPTIONS_INVALID')
    selectors = [material.reference.model_dump(mode='json', exclude_none=True) for material in item.materials]
    if len({digest(selector) for selector in selectors}) != len(selectors):
        raise TaskError('CHAT_INLINE_IMAGE_DUPLICATE_SOURCE')
    turn_id = str(runtime.turn_id)
    save_id = 'inline-save-' + digest([turn_id, call['id']])[:32]
    save = content.SaveContent(document=content.ContentDocument(title=item.title, items=(item,)),
                               continue_execution=True, new_content=True)
    registration_sources=content.registration_source_images(request,runtime)
    candidate, artifact, _, _ = content.save(runtime, {
        'id': save_id, 'name': content.SAVE, 'arguments': save.model_dump_json(),
    }, registry, has_image_attachments=bool(registration_sources),
        source_images=registration_sources)
    reference = content.ContentReference(artifact_id=artifact.id, item_index=1, sha256=artifact.sha256)
    compiled, _ = content.compile_input(candidate, TOOL, {}, reference.model_dump(mode='json'))
    validate_model_image_arguments(registry.require(TOOL), compiled)
    if ToolPolicy(registry).decide(TOOL, replace(context, call_id=call['id']), compiled).outcome == 'deny':
        raise PermissionError('CHAT_INLINE_IMAGE_DENIED')
    analysis = [{**selector, 'role': material.role}
                for selector, material in zip(selectors, item.materials, strict=True) if material.usage == 'analysis']
    if analysis:
        def verify_sources():
            native_resolver(handler, request, context).resolve(analysis)
        await asyncio.to_thread(verify_sources)

    # Commit the saved draft and the same native recipe used by the old path
    # together. A prepared checkpoint already pins the resolved original bytes.
    native_call = {**call, 'arguments': json.dumps(compiled, ensure_ascii=False)}
    original = runtime.task_execution_state
    try:
        runtime.task_execution_state = candidate
        candidate, errors = stage_native_calls(runtime, prepared, [native_call], registry)
        if errors:
            error = TaskError(errors[call['id']][0])
            error.guidance = errors[call['id']][1]
            raise error
        runtime.task_execution_state = candidate
        candidate, errors = await bind_image_recipes(runtime, [native_call], registry)
        if errors:
            error = TaskError(errors[call['id']][0])
            error.guidance = errors[call['id']][1]
            raise error
    finally:
        runtime.task_execution_state = original
    record = InlineAction(
        owner_task_id=str(runtime.task_id), turn_id=turn_id, call_id=call['id'], save_id=save_id,
        input_sha256=version, arguments_sha256=digest(args), reference=reference, native_sha256=digest(compiled),
    )
    return candidate, record
