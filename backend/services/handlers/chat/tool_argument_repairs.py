"""Durable, pre-dispatch argument correction for tools offered to the chat model."""

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from services.task_execution.reducer import digest
from services.tools.model_contracts import (
    REVIEWED_MODEL_TOOLS, decode_model_arguments, model_argument_error, offered_argument_error,
)

KEY = 'tool_argument_repairs'
LIMIT = 3
INVALID = 'MODEL_TOOL_ARGUMENTS_INVALID'
EXHAUSTED = 'MODEL_TOOL_ARGUMENT_REPAIR_EXHAUSTED'
STOP = '本轮工具参数多次校验失败，相关调用均未发出。请补充或调整要求后再试。'


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra='forbid')

    version: Literal[1] = 1
    input_id: str = Field(min_length=1, max_length=128)
    input_version: str = Field(min_length=64, max_length=64)
    counts: dict[str, StrictInt]
    stopped: bool


def _input_id(runtime, request, previous):
    if previous and runtime.checkpoint_base.get('_continuation_input'):
        return previous.input_id
    source = getattr(getattr(request, 'context_anchor', None), 'input_message_id', None)
    return str(source or runtime.turn_id)


def _input_version(runtime, input_id):
    changes = getattr(runtime, 'task_user_changes', None)
    delivered = sorted(str(item.event_id) for item in changes.items if item.delivered) if changes else []
    return digest([input_id, delivered])


def read(runtime) -> Checkpoint | None:
    data = getattr(runtime, 'checkpoint_base', {}).get(KEY)
    return Checkpoint.model_validate(data) if data is not None else None


def stopped(runtime, request):
    state = read(runtime)
    changes = getattr(runtime, 'task_user_changes', None)
    if changes and any(not item.delivered for item in changes.items):
        return False
    return bool(state and state.stopped
                and state.input_version == _input_version(runtime, _input_id(runtime, request, state)))


def _operation(args, parameters):
    # Only a declared enum can select a different operation. Unknown fields,
    # cursors, and arbitrary prose never create another correction budget.
    actions = parameters.get('properties', {}).get('action', {}).get('enum', ())
    action = args.get('action')
    return action if isinstance(action, str) and action in actions else None


# These errors come from the host's pre-dispatch source projection.
_SOURCE_RECOVERY = {
    'CONTENT_SOURCE_REQUIRED': ('reload_source', '从当前成果目录选取来源。需要时先用会话读取工具读取原稿。'),
    'CONTENT_OBJECT_UNAVAILABLE_OR_CHANGED': ('reload_source', '重新读取当前目录，使用选定条目的当前版本。'),
    'CONTENT_BASE_VERSION_CHANGED': ('reload_source', '重新读取当前目录中的最新版，核对同一条目。'),
    'CONTENT_ITEM_UNAVAILABLE': ('reload_source', '核对当前目录的条目编号。多个候选无法区分时请用户选择。'),
    'CONTENT_SOURCE_IMAGE_SELECTION_REQUIRED': ('reload_source', '核对该条目的可选原图。用户意图明确时选择对应原图，否则请用户选择。'),
    'CONTENT_IMAGE_SELECTION_CONFLICT': ('repair_arguments', '按本轮意图只填写一种原图选择。'),
    'CHAT_TOOL_DISCOVERY_REQUIRED': ('discover_tool', '先查找本轮可用工具，下一轮按实际提供的定义调用。'),
}


def source_rejections(calls, errors):
    return {call['id']: _SOURCE_RECOVERY[errors[call['id']][0]] for call in calls
            if call['id'] in errors and errors[call['id']][0] in _SOURCE_RECOVERY
            and call['name'] in {'generate_image', 'generate_video', 'code_execute'}}


@dataclass
class Batch:
    state: Checkpoint
    rejections: dict[str, str]
    targets: dict[str, str]

    @classmethod
    def prepare(cls, runtime, request, calls, offers, *, repair_chat_images=False):
        previous = read(runtime)
        input_id = _input_id(runtime, request, previous)
        version = _input_version(runtime, input_id)
        counts = dict(previous.counts) if previous and previous.input_version == version else {}
        rejections, targets = {}, {}
        for call in calls:
            name = call['name']
            parameters = offers.get(name, {}).get('parameters')
            if parameters is None:
                continue
            chat_image = (repair_chat_images and name == 'generate_image'
                          and 'content_source' in parameters.get('properties', {}))
            if not chat_image and name not in REVIEWED_MODEL_TOOLS:
                continue
            args = {}
            try:
                args = decode_model_arguments(name, call.get('arguments'), parameters)
            except ValueError as error:
                rejection = str(error)
            else:
                if chat_image:
                    from services.task_execution.content_objects import ImageContentReference
                    from services.task_execution.inline_image_action import InlineRequest

                    input_type = ImageContentReference if 'content_source' in args else InlineRequest
                    reference = args.get('content_source')
                    if isinstance(reference, dict) and 'sha256' in reference:
                        # Old clients may still supply a proof that is no longer
                        # advertised. Validate it and keep it for the version check.
                        fields = parameters['properties']
                        source = fields['content_source']
                        parameters = {**parameters, 'properties': {
                            **fields, 'content_source': {**source, 'properties': {
                                **source['properties'],
                                'sha256': ImageContentReference.model_json_schema()['properties']['sha256'],
                            }},
                        }}
                    rejection = offered_argument_error(name, args, parameters, input_type=input_type)
                else:
                    rejection = model_argument_error(name, args, parameters)
            if rejection:
                target = digest([name, _operation(args, parameters), INVALID])
                targets[call['id']] = target
                rejections[call['id']] = rejection
        if not rejections:
            return None

        batch = cls(Checkpoint(input_id=input_id, input_version=version, counts=counts,
                               stopped=False), rejections, targets)
        batch.finish(calls)
        return batch

    @classmethod
    def with_sources(cls, runtime, request, calls, errors, existing=None):
        recoverable = source_rejections(calls, errors)
        if not recoverable:
            return existing
        previous = read(runtime)
        input_id = _input_id(runtime, request, previous)
        version = _input_version(runtime, input_id)
        counts = dict(previous.counts) if previous and previous.input_version == version else {}
        batch = cls(Checkpoint(input_id=input_id, input_version=version, counts=counts,
                               stopped=False), dict(existing.rejections) if existing else {},
                    dict(existing.targets) if existing else {})
        for call in calls:
            call_id = call['id']
            if call_id not in recoverable or call_id in batch.rejections:
                continue
            code, _ = errors[call_id]
            action, guidance = recoverable[call_id]
            batch.targets[call_id] = digest([call['name'], None, INVALID])
            batch.rejections[call_id] = json.dumps({
                'code': code, 'stage': 'validate', 'submission_state': 'not_dispatched',
                'recovery': {'action': action}, 'message': guidance,
            }, ensure_ascii=False)
        batch.finish(calls)
        return batch

    def finish(self, calls):
        # Charge once per target per sampling round, regardless of call IDs.
        counts = self.state.counts
        for target in set(self.targets.values()):
            counts[target] = min(counts.get(target, 0) + 1, LIMIT)
        exhausted = {call_id for call_id, target in self.targets.items() if counts[target] >= LIMIT}
        for call_id in exhausted:
            detail = json.loads(self.rejections[call_id])
            detail.update(
                code=EXHAUSTED,
                recovery={'action': 'request_user_input'},
                message='此操作的参数纠错次数已用完。本次没有发出调用。请等待用户补充要求。',
            )
            self.rejections[call_id] = json.dumps(detail, ensure_ascii=False)
        self.state.stopped = len(exhausted) == len(calls)

    def record(self, runtime, messages, blocks):
        # Preserve tool-call/result pairing in the old transcript format. A
        # rollback reader also sees these calls as closed and cannot dispatch them.
        recorded = {message.get('tool_call_id') for message in messages if message.get('role') == 'tool'}
        for call_id, output in self.rejections.items():
            if call_id not in recorded:
                messages.append({'role': 'tool', 'tool_call_id': call_id, 'content': output})
        for block in blocks:
            if block.get('tool_call_id') in self.rejections:
                block.update(status='error', output='工具参数未通过校验。')
        runtime.checkpoint_base[KEY] = self.state.model_dump(mode='json')
