"""Historical failures cannot reopen retired chat-to-tool input paths."""
import json,os
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from services.handlers.chat import content_tools
from tests.test_model_directed_loop import case,execute
from tests.test_task_execution_model_bridge import call

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['source_prompt','chat_text_source','chat_input','bare_copy'])
async def test_real_six_item_failure_inputs_do_not_reopen_old_submission(monkeypatch,kind):
    path=os.getenv('CHAT_CONTENT_REAL_REPLAY')
    if not path:pytest.skip('Private historical conversation required')
    data=json.loads(Path(path).read_text());arguments=[]
    for row in data['messages']:
        parts=json.loads(row['content']) if isinstance(row['content'],str) else row['content']
        if not isinstance(parts,list):continue
        for part in parts:
            if part.get('tool_name')=='generate_image':
                value=part.get('input');value=json.loads(value) if isinstance(value,str) else value
                if isinstance(value,dict) and value.get('prompt'):arguments.append(value)
    assert arguments
    r,p,h,q=case();p.content_controls_available=True
    monkeypatch.setattr('services.handlers.chat.execution_engine.compact_tool_context',AsyncMock())
    for i,value in enumerate(arguments):
        value=dict(value);source=value.pop('source_prompt',{'message_id':'old-source','content_index':0})
        if kind=='source_prompt':value['source_prompt']=source
        elif kind=='chat_text_source':value['chat_text_source']={**source,'mode':'exact'}
        elif kind=='chat_input':value['chat_input']={'sources':[],'parts':[]}
        await execute(r,p,h,q,[call('generate_image',value,f'retired-{i}')])
        assert ('CONTENT_SOURCE_REQUIRED' if kind=='bare_copy' else 'CONTENT_INPUT_PATH_RETIRED') in str(p.messages[-1])
    h._execute_tool_calls.assert_not_awaited()
    assert r.task_execution_state is None
    assert 'mapping_repair' not in content_tools.checkpoint_state(r)
