"""Skill phases on the existing bounded private chat diagnostics channel."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import time

from services.agent.observability.chat_lifecycle import chat_phase


_TRACE = ContextVar("skill_preparation_trace", default=None)


@contextmanager
def skill_span(stage, *, task_id=None, turn_id=None):
    inherited = _TRACE.get() or {}
    task_id = task_id or inherited.get('task_id')
    turn_id = turn_id or inherited.get('turn_id')
    token = _TRACE.set({'task_id': task_id, 'turn_id': turn_id})
    started = time.monotonic()
    metrics = {'cancelled': False}
    chat_phase(f'skill.{stage}.begin', task_id=task_id, turn_id=turn_id)
    try:
        yield metrics
        metrics.setdefault('ok', True)
    except asyncio.CancelledError:
        metrics['ok'] = False
        metrics['cancelled'] = True
        raise
    except BaseException:
        metrics['ok'] = False
        raise
    finally:
        chat_phase(f'skill.{stage}.end', task_id=task_id, turn_id=turn_id,
                   elapsed_ms=round((time.monotonic() - started) * 1000, 3), metrics=metrics)
        _TRACE.reset(token)


def measure_skill(stage):
    def decorate(fn):
        @wraps(fn)
        async def measured(*args, **kwargs):
            owner = args[0] if args else None
            context = getattr(owner, 'context', None)
            metadata = kwargs.get('metadata')
            task_id = getattr(context, 'task_id', None) or getattr(metadata, 'client_task_id', None)
            turn_id = kwargs.get('turn_id') or getattr(owner, 'turn_id', None) or getattr(metadata, 'turn_id', None)
            with skill_span(stage, task_id=task_id, turn_id=turn_id) as metrics:
                result = await fn(*args, **kwargs)
                if isinstance(result, dict) and 'ok' in result:
                    metrics['ok'] = result['ok'] is True
                if isinstance(result, list):
                    metrics['candidate_count'] = len(result)
            return result
        return measured
    return decorate
