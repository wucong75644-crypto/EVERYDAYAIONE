"""Close the original pending native batch before another model sample."""
from .contracts import TaskError


def pending_calls(messages):
    pending, index = {}, None
    for position, message in enumerate(messages):
        if message.get('role') == 'assistant' and message.get('tool_calls'):
            if pending:
                raise TaskError('TASK_PENDING_TRANSCRIPT_INVALID')
            calls = message['tool_calls']
            try:
                pending = {call['id']: {'id': call['id'], 'name': call['function']['name'],
                    'arguments': call['function']['arguments']} for call in calls}
            except (KeyError, TypeError):
                raise TaskError('TASK_PENDING_TRANSCRIPT_INVALID') from None
            if len(pending) != len(calls):
                raise TaskError('TASK_PENDING_TRANSCRIPT_INVALID')
            index = position
        elif message.get('role') == 'tool':
            pending.pop(message.get('tool_call_id'), None)
        elif pending and message.get('role') in {'assistant', 'user'}:
            raise TaskError('TASK_PENDING_TRANSCRIPT_INVALID')
    return tuple(pending.values()), index


async def recover_pending_batch(*, prepared, runtime, request, handler, sink, totals, blocks,
                                cancellation_event, model_round, execute_tools, repeated_tool_call_guard):
    calls, index = pending_calls(prepared.messages)
    if not calls:
        return
    from services.task_execution.model_bridge import TASK_CONTROL_TOOLS
    from services.skills.runtime import ACTIVATE_SKILL, DEACTIVATE_SKILL
    if any(call['name'] in {*TASK_CONTROL_TOOLS, ACTIVATE_SKILL, DEACTIVATE_SKILL} for call in calls):
        raise TaskError('TASK_PENDING_CONTROL_BATCH_INVALID')
    await execute_tools(handler=handler, request=request, prepared=prepared, turn=prepared.budget.turns_used,
        turn_text='', calls=list(calls), previewed_call_ids={call['id'] for call in calls},
        cancellation_event=cancellation_event, sink=sink, blocks=blocks, runtime=runtime,
        next_model_round=model_round, totals=totals, repeated_tool_call_guard=repeated_tool_call_guard,
        assistant_already_present=True, recovery_only=True)
    if pending_calls(prepared.messages)[0]:
        raise TaskError('TASK_PENDING_BATCH_NOT_CLOSED')
