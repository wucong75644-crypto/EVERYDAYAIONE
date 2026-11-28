"""A bounded, owner-scoped proposal journal, outside model-readable history.

Only pure text registration can resume here. Business effects still use their
original operation ledgers, current authorization and input-revision fences.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time

from services.conversation_commands import SafePoint
from . import content_objects as co

KEY = '_content_response_v1'
MAX_BYTES = 1024 * 1024


def input_revision(runtime, request):
    anchor = getattr(request, 'context_anchor', None)
    changes = getattr(getattr(runtime, 'task_user_changes', None), 'items', ())
    values = [str(getattr(anchor, 'input_message_id', '') or ''),
              str(getattr(anchor, 'base_revision', '') or ''),
              [(str(i.event_id), str(i.message_sha256)) for i in changes]]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def pending_response(runtime, request):
    value = runtime.checkpoint_base.get(KEY)
    if not isinstance(value, dict) or value.get('version') != 1 or value.get('phase') != 'proposed':
        return None
    if value.get('input_revision') != input_revision(runtime, request):
        runtime.checkpoint_base[KEY] = {**value, 'phase': 'superseded'}
        return None
    calls = value.get('calls') or []
    if (value.get('projection_source') == 'visible_text' and not calls
            and isinstance(value.get('text'), str) and value.get('finish_reason') != 'length'):
        return copy.deepcopy(value)
    if (isinstance(calls, list) and len(calls) == 1 and isinstance(calls[0], dict)
            and calls[0].get('name') == co.SAVE and isinstance(calls[0].get('arguments'), str)
            and isinstance(calls[0].get('id'), str) and isinstance(value.get('text'), str)):
        return copy.deepcopy(value)
    return None


async def stage_response(runtime, request, prepared, blocks, turn, model_round, text, calls, finish, build_checkpoint, *, source='tool', control_pending=False):
    value = {'version': 1, 'phase': 'proposed', 'input_revision': input_revision(runtime, request),
             'model_round': model_round, 'text': text, 'calls': copy.deepcopy(calls), 'finish_reason': finish,
             'projection_source': source}
    if control_pending:
        value['control_pending']=True
    previous = runtime.checkpoint_base.get(KEY) or {}
    if previous.get('input_revision') == value['input_revision'] and previous.get('control_rejections'):
        value['control_rejections'] = previous['control_rejections']
    if (previous.get('phase') == 'rejected' and previous.get('error_code') == co.DRAFT_CONFLICT
            and previous.get('input_revision') == value['input_revision']):
        value['visible_draft'] = previous.get('visible_draft', previous.get('text', ''))
    raw = json.dumps(value, ensure_ascii=False).encode()
    if len(raw) > MAX_BYTES:
        # Explicit refusal; never silently truncate arguments that might be saved.
        from core.exceptions import AppException
        raise AppException('CONTENT_RESPONSE_TOO_LARGE', '本轮方案超出可恢复记录上限，尚未保存，请分批提交。', 422)
    runtime.checkpoint_base[KEY] = value
    started = time.monotonic()
    await runtime.safe_point(SafePoint.BEFORE_TOOL, replay_payload=build_checkpoint(
        prepared.messages, blocks, turn, next_model_round=model_round))
    from services.agent.observability.chat_lifecycle import chat_phase
    chat_phase('content.proposal_saved', task_id=runtime.task_id, model_round=model_round,
               elapsed_ms=(time.monotonic() - started) * 1000, metrics={'rendered_bytes': len(raw)})


def finish_response(runtime, block, result, *, request=None, source='tool', delivery_complete=True):
    value = runtime.checkpoint_base.get(KEY)
    if not isinstance(value, dict):
        return
    if request is not None and value.get('input_revision') != input_revision(runtime, request):
        runtime.checkpoint_base[KEY] = {**value, 'phase': 'superseded'}
        return
    # Included in the same atomic projection checkpoint as the artifact/blocks.
    runtime.checkpoint_base[KEY] = {**value,
        'phase': 'committed' if result.get('ok') is True else 'rejected',
        'artifact_id': block.get('content_artifact_id') if block else (result.get('object') or {}).get('artifact_id'),
        'projected': block is not None, 'projection_source': source,
        'owner_task_id': str(runtime.task_id), 'delivery_complete': delivery_complete,
        'error_code': result.get('code')}
