"""Callbacks are untrusted wake-up hints, never trusted video results or download URLs."""
import re
from uuid import UUID

from fastapi import APIRouter, Request, HTTPException

router = APIRouter()


@router.post('/kie-mcp-callback/{local_id}', status_code=202)
async def receive_callback(local_id: UUID, request: Request):
    from services.kie_mcp_task_flow import store
    # Bound body before parsing to avoid unbounded callback memory usage.
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 65536:
            raise HTTPException(413, 'Callback too large')
        body.extend(chunk)
    import json
    try:
        payload = json.loads(body)
        data = payload.get('data') if isinstance(payload, dict) else None
        task_id = data.get('taskId') if isinstance(data, dict) else None
    except ValueError:
        task_id = None
    if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', task_id):
        raise HTTPException(400, 'Invalid callback')
    # Known IDs must match; unknown IDs require authoritative provider callback URL/input verification.
    await store.wake(local_id, task_id)
    return {'accepted': True}
