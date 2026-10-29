"""Stable transport identities; Actor IDs remain unchanged in storage and audits."""
import base64
import copy
import hashlib


def wire_call_id(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError('DASHSCOPE_CALL_ID_INVALID')
    # Preserve valid provider/legacy IDs. The prefix namespace is reserved so an
    # application ID cannot collide with a generated identity by spelling it.
    if len(value) <= 64 and value.isascii() and not value.startswith('wcid_'):
        return value
    digest = base64.urlsafe_b64encode(hashlib.sha256(value.encode()).digest()).decode().rstrip('=')
    return 'wcid_' + digest


def wire_messages(messages: list[dict]) -> list[dict]:
    result = copy.deepcopy(messages)
    identities: dict[str, str] = {}

    def convert(raw):
        wire = wire_call_id(raw)
        if wire in identities and identities[wire] != raw:
            raise ValueError('DASHSCOPE_CALL_ID_COLLISION')
        identities[wire] = raw
        return wire

    for message in result:
        if message.get('role') == 'tool':
            message['tool_call_id'] = convert(message.get('tool_call_id'))
        for call in message.get('tool_calls') or []:
            call['id'] = convert(call.get('id'))
    return result
