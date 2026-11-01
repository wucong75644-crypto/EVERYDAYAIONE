"""Real ImageHandler + scoped SQL acceptance retains in-flight v2/v3 gates."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import psycopg
import pytest

from tests.test_task_execution_children_postgres import (
    children_db,operation_db,task_db,composition_db,ecom_db,isolated_db,child_case,accept_child)


@pytest.mark.asyncio
@pytest.mark.parametrize('changed',[False,True])
@pytest.mark.parametrize('binding_version,coordinator',[(2,True),(3,True),(3,False)])
async def test_native_handler_checks_managed_proof_even_after_new_admission_disabled(children_db,monkeypatch,changed,tmp_path,binding_version,coordinator):
    from core.local_db import LocalDBClient
    from core.db_scope import ScopedDatabaseClient,DatabaseScope,DatabaseAccessKind
    from services.handlers.image_handler import ImageHandler
    from services.handlers.chat_image_request import ChatImageNotAcceptedError
    f,first,snapshot=await child_case(children_db,monkeypatch,binding_version=binding_version)
    client=LocalDBClient(psycopg.conninfo.make_conninfo(f['dsn'],user='everydayai'),min_size=1,max_size=2)
    scoped=ScopedDatabaseClient(client,DatabaseScope(f['user'],None,DatabaseAccessKind.RUNTIME))
    owner=SimpleNamespace(db=scoped,user_id=f['user'],workspace_user_id=f['user'],org_id=None,
        conversation_id=f['conv'],task_id=f['task'],context_scope='user',execution_mode='interactive',
        image_execution_token=f['token'],cancellation_event=None,image_skill_snapshot=(),
        _task_image_operations=SimpleNamespace(runtime=SimpleNamespace(task_execution_state=first.state)))
    owner._task_execution_state=first.state
    if not coordinator:del owner._task_image_operations
    monkeypatch.setattr('services.tools.dispatcher.current_dispatch_call_id',lambda:'native-1')
    monkeypatch.setattr('services.websocket_manager.ws_manager.send_to_task_or_user',AsyncMock())
    monkeypatch.setattr('core.config.get_settings',lambda:SimpleNamespace(chat_image_async_enabled=True,
        chat_image_allowed_user_ids='',chat_image_transparent_enabled=False,chat_image_max_requests=6,
        chat_image_max_credits=100,agent_task_execution_enabled=False,file_workspace_root=str(tmp_path)))
    args={k:snapshot[k] for k in ('mode','prompt','aspect_ratio')}
    args['size_requirement']={'mode':'explicit'}
    if changed:args['prompt']='Unplanned substitute'
    try:
        handler=ImageHandler(scoped)
        if changed:
            with pytest.raises(ChatImageNotAcceptedError):await handler.accept_chat_image(owner,args)
        else:
            receipt=await handler.accept_chat_image(owner,args)
            # Retry the exact persisted snapshot; ToolRuntime normally replays
            # its receipt without re-running mutable size/source normalization.
            with psycopg.connect(f['dsn']) as c:
                frozen=c.execute("SELECT request_params->'_media_request_v1' FROM tasks WHERE id=%s",(receipt['task_id'],)).fetchone()[0]
            again=accept_child(f,frozen)
            assert receipt['task_id']==again['task_id']
        with psycopg.connect(f['dsn']) as c:
            assert c.execute("SELECT count(*) FROM tasks WHERE type='image' AND user_id=%s",(f['user'],)).fetchone()[0]==(0 if changed else 1)
            if not changed:
                budget=c.execute("SELECT request_params->'_media_budget_v1' FROM tasks WHERE id=%s",(f['task'],)).fetchone()[0]
                assert budget['requests']==1
    finally:client.close()
