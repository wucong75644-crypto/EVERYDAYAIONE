"""Private phase timings on the existing chat path, never a delivery/billing gate."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import socket
import time

from loguru import logger

_CLOCK_DOMAIN = hashlib.sha256(socket.gethostname().encode()).hexdigest()[:16]
_PENDING: set[asyncio.Task] = set()
_CAPTURE: ContextVar = ContextVar('chat_lifecycle_capture', default=None)
_SCOPE: ContextVar = ContextVar('chat_lifecycle_scope', default=None)
_PHASES = frozenset('enqueue.begin enqueue.persisted enqueue.wakeup claim.begin claim.end executor.begin executor.inputs context.begin context.history context.knowledge context.ready answer.prepare.begin answer.prepare.end model.begin model.first_body model.end verification.begin verification.end sink.first_body sink.first_persisted sink.first_push generation.end terminal.commit.begin terminal.commit.end'.split())


@contextmanager
def chat_lifecycle_scope(task_id, *, turn_id=None, attempt=None):
    token = _SCOPE.set({'task_id': str(task_id), 'turn_id': str(turn_id) if turn_id else None, 'attempt': attempt})
    try:
        yield
    finally:
        _SCOPE.reset(token)


@contextmanager
def capture_chat_lifecycle(rows):
    """Local acceptance observer; no process-global production callback."""
    token = _CAPTURE.set(rows.append)
    try:
        yield
    finally:
        _CAPTURE.reset(token)


def chat_phase(phase, *, task_id=None, turn_id=None, attempt=None, model_round=None, elapsed_ms=None, rpc_started_ns=None):
    if phase not in _PHASES:
        return
    scope = _SCOPE.get() or {}
    row = {'phase': phase, 'clock_domain': _CLOCK_DOMAIN, 'monotonic_ns': time.monotonic_ns(),
           'wall_time_ns': time.time_ns(), 'task_id': str(task_id) if task_id else scope.get('task_id'),
           'turn_id': str(turn_id) if turn_id else scope.get('turn_id'),
           'attempt': attempt if attempt is not None else scope.get('attempt')}
    if model_round is not None:
        row['model_round'] = model_round
    if elapsed_ms is not None:
        row['elapsed_ms'] = elapsed_ms
    if rpc_started_ns is not None:
        row['rpc_started_ns'] = rpc_started_ns
    try:
        observer = _CAPTURE.get()
        if observer is not None:
            observer(row)
            return
        if len(_PENDING) >= 64:
            return  # Diagnostic pressure cannot block chat or grow without bound.
        task = asyncio.get_running_loop().create_task(_publish(row))
        _PENDING.add(task)
        task.add_done_callback(_PENDING.discard)
    except Exception:
        pass


async def _publish(row):
    try:
        await asyncio.to_thread(logger.info, 'Chat lifecycle timing {}', json.dumps(row, separators=(',', ':')))
    except Exception:
        pass
