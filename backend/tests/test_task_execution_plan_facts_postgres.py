"""Draft, review and phase proof commit with actual scoped Actor checkpoints."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tests.test_task_execution_postgres import task_db,composition_db,ecom_db,isolated_db,bind_seed,save
from tests.test_skill_composition_postgres import seed,connect
from tests.test_task_execution_methods import bound,document
from tests.test_task_execution_model_bridge import setup_case,execute,call
from tests.test_task_execution_plan_facts import observation,review
from services.task_execution.plan_facts import review_proof,restore_reviews
from services.task_execution.reducer import digest


@pytest.mark.asyncio
async def test_factual_rejection_correction_and_method_handoff_persist_atomically(task_db,monkeypatch):
    f=seed(task_db)
    state,skills=await bound(monkeypatch)
    state=bind_seed(state,f).model_copy(update={'image_binding_version':3})
    skills.turn_id=f['turn'];skills.composition=skills._compose()
    async def checkpoint(point,payload):
        with connect(f) as c:return save(c,f,payload,expected=payload['_task_execution_expected_revision'])
    runtime,prepared,handler,sink=setup_case(initial=state,checkpoint=checkpoint)
    runtime.task_id=f['task'];runtime.conversation_id=f['conv'];runtime.turn_id=f['turn'];runtime.skill_runtime=skills
    goal=state.original_goal
    with psycopg.connect(task_db) as c:
        c.execute('UPDATE messages SET content=%s WHERE id=%s',(Jsonb([{'type':'text','text':goal}]),f['message']))
        original_content=c.execute('SELECT content FROM messages WHERE id=%s',(f['message'],)).fetchone()[0]
    sources={'references':[],'resolved':[],'texts':[{'id':'user:original','text':'8入大容量风琴包'}]}
    sources['sha256']=digest(sources)
    runtime.task_plan_source_provider=AsyncMock(return_value=(sources,[]))
    blocks=[]
    data=json.loads(document());data['product_facts']='8寸防水PP材质风琴包'
    await execute(runtime,prepared,handler,sink,[call('save_task_text',{'expected_revision':state.revision,
        'step_id':'planning','text':json.dumps(data,ensure_ascii=False)},'draft')],blocks)
    draft=runtime.task_execution_state.artifacts[0]
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{
        'expected_revision':runtime.task_execution_state.revision,'step_id':'planning','artifact_ids':[draft.id]},'pending')],blocks)
    with connect(f) as c:
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert cp['_task_plan_reviews']['pending']['artifact_id']==draft.id
        assert cp['_task_execution']['results']==[]
    await review(runtime,prepared,sink,blocks,observation(draft,rejected=True))
    assert review_proof(runtime.task_execution_state,draft) is None
    data['product_facts']='8入大容量风琴包，尺寸/材质/防水性能均未核实，不作断言。'
    await execute(runtime,prepared,handler,sink,[call('save_task_text',{
        'expected_revision':runtime.task_execution_state.revision,'step_id':'planning','text':json.dumps(data,ensure_ascii=False)},'repair')],blocks)
    corrected=runtime.task_execution_state.artifacts[-1]
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{
        'expected_revision':runtime.task_execution_state.revision,'step_id':'planning','artifact_ids':[corrected.id]},'pending-repair')],blocks)
    await review(runtime,prepared,sink,blocks,observation(corrected),round=3)
    await execute(runtime,prepared,handler,sink,[call('complete_task_step',{
        'expected_revision':runtime.task_execution_state.revision,'step_id':'planning','artifact_ids':[corrected.id]},'complete')],blocks)
    with connect(f) as c:
        stored=c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()[0]
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert cp['_task_execution']==stored==runtime.task_execution_state.model_dump(mode='json')
        assert cp['_task_plan_reviews']['pending'] is None
        assert cp['_task_execution']['results'][0]['artifact_ids']==[corrected.id]
        assert cp['skill_runtime']['composition']['participants'][0]['phase_resident'] is True
        assert c.execute('SELECT content FROM messages WHERE id=%s',(f['message'],)).fetchone()[0]==original_content
    assert restore_reviews(cp['_task_plan_reviews'],runtime.task_execution_state)==runtime.task_plan_reviews
    handler._execute_tool_calls.assert_not_called()
