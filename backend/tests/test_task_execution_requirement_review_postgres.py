"""Review proposal and frozen requirements commit under the original Actor fence."""
from dataclasses import replace
from uuid import UUID
import psycopg
from psycopg.types.json import Jsonb
import pytest
from tests.test_task_execution_postgres import task_db,composition_db,ecom_db,isolated_db,save
from tests.test_skill_composition_postgres import seed,connect
from tests.test_task_execution_bootstrap import bootstrap
from tests.test_task_execution_model_bridge import setup_case,execute,call
from tests.test_task_execution_requirement_review import proposal,observation
from services.handlers.chat.execution_engine import _build_replay_context
from services.task_execution.requirement_review import apply_review


@pytest.mark.asyncio
async def test_source_proposal_checkpoint_precedes_atomic_review_and_requirement_freeze(task_db):
    f=seed(task_db)
    boot,initial,*_=bootstrap()
    goal='请生成五张详情图'
    boot=replace(boot,task_id=UUID(f['task']),actor_user_id=UUID(f['user']),conversation_id=UUID(f['conv']),
        input_message_id=UUID(f['message']),original_goal=goal,require_coverage_review=True,
        execution_identity=boot.execution_identity.model_copy(update={'workspace_owner_id':f['user']}))
    with psycopg.connect(task_db) as c:
        c.execute('UPDATE messages SET content=%s WHERE id=%s',(Jsonb([{'type':'text','text':goal}]),f['message']))
        original_content=c.execute('SELECT content FROM messages WHERE id=%s',(f['message'],)).fetchone()[0]
    async def checkpoint(point,payload):
        with connect(f) as c:
            if '_task_execution' in payload:return save(c,f,payload,expected=payload['_task_execution_expected_revision'])
            return c.execute('SELECT save_generation_checkpoint(%s,%s,%s,%s)',
                (f['task'],f['token'],point.value,Jsonb(payload))).fetchone()[0]
    runtime,prepared,handler,sink=setup_case(initial=initial,checkpoint=checkpoint)
    runtime.task_id=f['task'];runtime.conversation_id=f['conv'];runtime.turn_id=f['turn']
    runtime.task_execution_state=None;runtime.task_bootstrap=boot
    prepared.messages=[{'role':'system','content':'原摘要和权限上下文'},{'role':'user','content':goal}]
    prepared.last_provider_output=None
    blocks=[]
    await execute(runtime,prepared,handler,sink,[call('start_task_execution',proposal(),'start')],blocks)
    with connect(f) as c:
        original=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert original['_task_requirement_review']['pending']
        assert c.execute('SELECT count(*) FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()[0]==0
    await apply_review(runtime=runtime,prepared=prepared,blocks=blocks,text=observation(),
        has_tool_calls=False,model_round=1,next_model_round=2,build_checkpoint=_build_replay_context,turn=1)
    with connect(f) as c:
        stored=c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()[0]
        checkpoint=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert checkpoint['_task_execution']==stored==runtime.task_execution_state.model_dump(mode='json')
        assert checkpoint['_task_requirement_review']['approved_proposal_sha256']
        assert checkpoint['messages'][:len(original['messages'])]==original['messages']
        assert stored['requirements'][0]['artifact_kind']=='image' and stored['requirements'][0]['minimum']==5
        assert c.execute('SELECT content FROM messages WHERE id=%s',(f['message'],)).fetchone()[0]==original_content
    handler._execute_tool_calls.assert_not_called()
