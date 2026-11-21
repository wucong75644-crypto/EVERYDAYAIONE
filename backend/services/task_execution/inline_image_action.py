"""Atomically bind one model image draft to the existing content/native ledgers."""
from dataclasses import replace
import json

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


def is_inline_call(call):
    if call['name'] != TOOL:
        return False
    # Recognize the form even if strict decoding later rejects duplicate keys.
    try:
        value = json.loads(call.get('arguments') or '{}')
    except (ValueError, TypeError, RecursionError):
        return False
    return isinstance(value, dict) and 'draft' in value


def allowed_for_offer(runtime, *, server_image_plan, reuse_source):
    from core.config import get_settings
    from .direct_loop import active_image_methods, saved_image_plans

    return (get_settings().chat_inline_image_enabled and not server_image_plan and not reuse_source
            and not active_image_methods(runtime) and not saved_image_plans(runtime.task_execution_state))


def input_version(runtime, request):
    anchor = request.context_anchor
    return digest([getattr(anchor, 'input_message_id', None), sorted(runtime.task_direct_seen)])


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
    value = InlineRequest.model_validate(args)
    item = value.draft
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
    candidate, artifact, _, _ = content.save(runtime, {
        'id': save_id, 'name': content.SAVE, 'arguments': save.model_dump_json(),
    }, registry, has_image_attachments=any(part.type == 'image' for part in request.content),
        source_images=content.current_source_images(request))
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
            raise TaskError(errors[call['id']][0])
        runtime.task_execution_state = candidate
        candidate, errors = await bind_image_recipes(runtime, [native_call], registry)
        if errors:
            raise TaskError(errors[call['id']][0])
    finally:
        runtime.task_execution_state = original
    record = InlineAction(
        owner_task_id=str(runtime.task_id), turn_id=turn_id, call_id=call['id'], save_id=save_id,
        input_sha256=version, arguments_sha256=digest(args), reference=reference, native_sha256=digest(compiled),
    )
    return candidate, record
