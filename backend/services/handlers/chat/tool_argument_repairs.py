"""Durable, pre-dispatch argument correction for tools offered to the chat model."""

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from services.task_execution.reducer import digest
from services.tools.model_contracts import REVIEWED_MODEL_TOOLS, decode_model_arguments, model_argument_error

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


@dataclass
class Batch:
    state: Checkpoint
    rejections: dict[str, str]

    @classmethod
    def prepare(cls, runtime, request, calls, offers):
        previous = read(runtime)
        input_id = _input_id(runtime, request, previous)
        version = _input_version(runtime, input_id)
        counts = dict(previous.counts) if previous and previous.input_version == version else {}
        rejections, targets = {}, {}
        for call in calls:
            name = call['name']
            if name not in REVIEWED_MODEL_TOOLS:
                continue
            parameters = offers.get(name, {}).get('parameters')
            if parameters is None:
                continue
            args = {}
            try:
                args = decode_model_arguments(name, call.get('arguments'), parameters)
            except ValueError as error:
                rejection = str(error)
            else:
                rejection = model_argument_error(name, args, parameters)
            if rejection:
                target = digest([name, _operation(args, parameters), INVALID])
                targets[call['id']] = target
                rejections[call['id']] = rejection
        if not rejections:
            return None

        # A single model round can emit multiple copies of the same rejection.
        # Charge one correction attempt per failed target in that round.
        for target in set(targets.values()):
            counts[target] = min(counts.get(target, 0) + 1, LIMIT)
        exhausted = {call_id for call_id, target in targets.items() if counts[target] >= LIMIT}
        for call_id in exhausted:
            detail = json.loads(rejections[call_id])
            detail.update(
                code=EXHAUSTED,
                recovery={'action': 'request_user_input'},
                message='此操作的参数纠错次数已用完。本次没有发出调用。请等待用户补充要求。',
            )
            rejections[call_id] = json.dumps(detail, ensure_ascii=False)

        return cls(Checkpoint(input_id=input_id, input_version=version, counts=counts,
                              stopped=len(exhausted) == len(calls)), rejections)

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
