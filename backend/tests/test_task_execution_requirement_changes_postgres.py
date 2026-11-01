from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace
import psycopg
from psycopg.types.json import Jsonb
import pytest
from services.task_execution.requirement_changes import candidate,RevisionReview,projection
from services.task_execution.contracts import TaskState
from tests.test_task_execution_requirement_changes import review,observation
from tests.test_task_execution_user_changes_postgres import (change_db,continuation_db,children_db,operation_db,task_db,composition_db,ecom_db,isolated_db,prepared,steer,capture,connect)
ROOT=Path(__file__).parents[1]


@pytest.fixture(scope='module')
def revision_db(change_db):
    with psycopg.connect(change_db) as c:c.execute((ROOT/'migrations/364_agent_task_requirement_revisions.sql').read_text())
    yield change_db
    with psycopg.connect(change_db) as c:
        roots=[v[0] for v in c.execute("SELECT root_task_id FROM agent_task_states WHERE state ? 'requirements_history'").fetchall()]
        c.execute('DELETE FROM conversation_turn_checkpoints WHERE task_id IN (SELECT owner_task_id FROM agent_task_states WHERE root_task_id=ANY(%s))',(roots,))
        c.execute('DELETE FROM agent_task_states WHERE root_task_id=ANY(%s)',(roots,))
        c.execute('DELETE FROM agent_task_user_change_reviews')
        c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        c.execute((ROOT/'migrations/rollback/364_agent_task_requirement_revisions_rollback.sql').read_text())


def staged(dsn,relation='amend'):
    f,p=prepared(dsn,binding_version=3);s=TaskState.model_validate(p['_task_execution'])
    with connect(f) as c:
        event=steer(c,f,'先只要一份文字报告，不用文件');inbox=capture(c,f,event)['inbox']
        events=c.execute('SELECT read_agent_task_user_change_pending(%s,%s,%s)',(f['task'],f['token'],f['task'])).fetchone()[0]['events']
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
    book=review(s,relation).model_copy(update={'events':tuple(events)})
    new=candidate(s,book,None)
    payload={**deepcopy(cp),'_task_execution':new.model_dump(mode='json'),'safe_point':'after_tool'}
    proof={**book.model_dump(mode='json'),'observation':__import__('json').loads(observation(relation)),'base_projection':projection(s)}
    return f,s,new,proof,payload


def apply(c,f,s,proof,payload):
    return c.execute('SELECT apply_agent_task_requirement_revision(%s,%s,%s,%s,%s,%s)',(f['task'],f['token'],f['task'],s.revision,Jsonb(proof),Jsonb(payload))).fetchone()[0]


@pytest.mark.parametrize('relation',['continue','amend','unrelated'])
def test_actual_atomic_revision_preserves_context_and_current_scope(revision_db,relation):
    f,s,new,proof,payload=staged(revision_db,relation)
    with connect(f) as c:
        gate=c.execute('SELECT check_agent_task_revision_gate(%s,%s,%s,%s)',(f['task'],f['token'],f['task'],s.requirements_revision)).fetchone()[0]
        assert gate['outcome']=='user_change_pending'
        result=apply(c,f,s,proof,payload);assert result['outcome']=='saved'
        saved=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        assert saved['messages']==payload['messages'] and saved['opaque_context']=={'keep':True}
        assert saved['_task_execution']==new.model_dump(mode='json')
        gate=c.execute('SELECT check_agent_task_revision_gate(%s,%s,%s,%s)',(f['task'],f['token'],f['task'],new.requirements_revision)).fetchone()[0]
        assert gate['outcome']=='ready'
        assert new.original_goal==s.original_goal and new.artifacts==s.artifacts and new.operations==s.operations
        assert c.execute('SELECT count(*) FROM agent_task_user_change_reviews WHERE root_task_id=%s',(f['task'],)).fetchone()[0]==1


@pytest.mark.parametrize('fault',['fake_source','change_goal','drop_history','rewrite_context','bad_revision','wrong_actor','extra_delivery','delete_gate','extra_gate','fake_method','fake_applicable','bad_observation_kind','bad_observation_action','fake_observer_quote'])
def test_bad_revision_does_not_acknowledge_review_or_open_dispatch(revision_db,fault):
    from uuid import uuid4
    f,s,new,proof,payload=staged(revision_db)
    if fault=='fake_source':proof['events'][0]['event_id']=str(uuid4())
    if fault=='change_goal':payload['_task_execution']['original_goal']='模型自授权目标'
    if fault=='drop_history':payload['_task_execution']['requirements_history']=[]
    if fault=='rewrite_context':payload['messages']=[]
    if fault=='bad_revision':payload['_task_execution']['requirements_revision']=9
    if fault=='extra_delivery':payload['_task_execution']['requirements'].append({**payload['_task_execution']['requirements'][0],
        'id':'r1.user.unrequested','artifact_kind':'image','minimum':5})
    if fault=='extra_gate':payload['_task_execution']['capability_gates']=[{'capability':'platform.generate_image','requirement_ids':['r1.user.report']}]
    if fault=='fake_method':payload['_task_execution']['method_bindings']=[{'profile_id':'reference-prompts-v1'}]
    if fault=='fake_applicable':payload['_task_execution']['applicable_method_ids']=['unselected']
    if fault=='bad_observation_kind':proof['observation']['requirements']['deliveries'][0]['artifact_kind']='image'
    if fault=='bad_observation_action':proof['observation']['requirements']['action']='execute_prompt'
    if fault=='fake_observer_quote':proof['observation']['requirements']['deliveries'][0]['evidence']=['伪造授权']
    if fault=='delete_gate':
        # A genuine image amendment must gain the canonical host planning gate.
        proposal=proof['pending']['proposal'];proposal['requirements'][0]['artifact_kind']='image'
        proof['observation']['requirements']['deliveries'][0]['artifact_kind']='image'
        payload['_task_execution']['requirements'][0]['artifact_kind']='image'
    with connect(f) as c:
        if fault=='wrong_actor':c.execute("SELECT set_config('app.actor_user_id',%s,true)",(str(uuid4()),))
        with pytest.raises(psycopg.Error):
            with c.transaction():apply(c,f,s,proof,payload)
    with psycopg.connect(revision_db) as c:
        assert c.execute('SELECT count(*) FROM agent_task_user_change_reviews WHERE root_task_id=%s',(f['task'],)).fetchone()[0]==0
        assert c.execute('SELECT state FROM agent_task_states WHERE root_task_id=%s',(f['task'],)).fetchone()[0]==s.model_dump(mode='json')


@pytest.fixture(scope='module')
def child_gate_db(revision_db):
    with psycopg.connect(revision_db) as c:c.execute((ROOT/'migrations/365_agent_task_child_revision_gates.sql').read_text())
    yield revision_db
    with psycopg.connect(revision_db) as c:
        # Isolated fixture only: gate rollback correctly refuses live v3 work.
        roots=[v[0] for v in c.execute("SELECT root_task_id FROM agent_task_states WHERE state->>'image_binding_version'='3'").fetchall()]
        c.execute('DELETE FROM conversation_turn_checkpoints WHERE task_id IN (SELECT owner_task_id FROM agent_task_states WHERE root_task_id=ANY(%s))',(roots,))
        c.execute('DELETE FROM agent_task_states WHERE root_task_id=ANY(%s)',(roots,))
        c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        c.execute((ROOT/'migrations/rollback/365_agent_task_child_revision_gates_rollback.sql').read_text())


@pytest.mark.asyncio
async def test_native_image_marker_waits_for_review_and_never_dispatches_retired_step(child_gate_db,monkeypatch):
    from tests.test_task_execution_children_postgres import child_case,accept_child
    from tests.test_chat_image_lifecycle_postgres import claim as claim_image,worker_rpc
    from services.task_execution.requirement_changes import RevisionReview
    f,first,snapshot=await child_case(child_gate_db,monkeypatch,binding_version=3)
    child=accept_child(f,snapshot)
    assert claim_image(f,child['task_id'])['outcome']=='claimed'
    with psycopg.connect(child_gate_db) as c:token=c.execute("SELECT request_params#>>'{_media_lifecycle_v1,claim_token}' FROM tasks WHERE id=%s",(child['task_id'],)).fetchone()[0]
    with connect(f) as c:
        event=steer(c,f,'先只要一份文字报告，不用文件');capture(c,f,event)
        pending_events=c.execute('SELECT read_agent_task_user_change_pending(%s,%s,%s)',(f['task'],f['token'],f['task'])).fetchone()[0]['events']
        cp=c.execute('SELECT state FROM conversation_turn_checkpoints WHERE task_id=%s',(f['task'],)).fetchone()[0]
        s=TaskState.model_validate(cp['_task_execution'])
    assert worker_rpc(f,'mark_chat_image_dispatch',child['task_id'],token)['outcome']=='not_owned'
    book=review(s).model_copy(update={'events':tuple(pending_events)})
    new=candidate(s,book,None)
    payload={**cp,'_task_execution':new.model_dump(mode='json')}
    proof={**book.model_dump(mode='json'),'observation':__import__('json').loads(observation()),'base_projection':projection(s)}
    with connect(f) as c:assert apply(c,f,s,proof,payload)['outcome']=='saved'
    assert worker_rpc(f,'mark_chat_image_dispatch',child['task_id'],token)['outcome']=='superseded'
    with psycopg.connect(child_gate_db) as c:
        assert c.execute("SELECT request_params#>'{_media_lifecycle_v1,dispatch_started_at}' FROM tasks WHERE id=%s",(child['task_id'],)).fetchone()[0] is None


def test_dispatch_rollback_refuses_live_work_and_preserves_functions_and_rls(child_gate_db):
    f,_,_,_,_=staged(child_gate_db)
    with psycopg.connect(child_gate_db) as c:
        with pytest.raises(psycopg.Error,match='NOT_DRAINED'):
            with c.transaction():c.execute((ROOT/'migrations/rollback/365_agent_task_child_revision_gates_rollback.sql').read_text())
        assert c.execute("SELECT to_regprocedure('public.agent_child_revision_gate(uuid,uuid)') IS NOT NULL").fetchone()[0]
        assert c.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid='public.agent_task_states'::regclass").fetchone()[0]


@pytest.mark.parametrize('action,kind,count',[('plan','text',1),('plan','image',5),('execute_prompt','image',5)])
def test_projection_matches_original_two_pinned_methods_without_new_skills_or_providers(revision_db,action,kind,count):
    from uuid import UUID
    from services.task_execution.methods import PROFILES
    from services.task_execution.requirement_changes import RevisionProposal
    f,p=prepared(revision_db,binding_version=3);s=TaskState.model_validate(p['_task_execution'])
    active={};directory={};wire={'active':[],'directory':[],'manual_skill_id':None,'session_skill_ids':[]}
    for package,key,version,checksum in PROFILES:
        active[key]=SimpleNamespace(revision=version,body_sha256=checksum)
        directory[key]=SimpleNamespace(package_id=UUID(package),skill_key=key,catalog_metadata=SimpleNamespace(task_stage=None))
        wire['active'].append({'skill_key':key,'revision':version,'body_sha256':checksum})
        wire['directory'].append({'package_id':package,'skill_key':key,'revision':version,'catalog_metadata':{}})
    skills=SimpleNamespace(active=active,directory=directory,manual_skill_id=next(iter(active)),session_skill_ids=())
    wire['manual_skill_id']=skills.manual_skill_id
    prop=RevisionProposal(relation='amend',evidence=['新要求'],proposal={'action':action,'applicable_method_ids':list(active) if action=='plan' else [],
        'requirements':[{'id':'delivery','description':'新交付','artifact_kind':kind,'minimum':count,'evidence':['新要求']}]})
    book=review(s).model_copy(update={'pending':prop});new=candidate(s,book,skills)
    with connect(f) as c:
        def valid(value,wire_value=wire):
            return c.execute('SELECT agent_task_revision_projection_valid(%s,%s,%s,%s,%s)',
                tuple(Jsonb(v) for v in (s.model_dump(mode='json'),value,prop.proposal.model_dump(mode='json'),wire_value,wire))).fetchone()[0]
        raw=new.model_dump(mode='json');assert valid(raw)
        if action=='plan' and kind=='image':
            from services.task_execution.methods import append_active_methods
            one_key=next(iter(active))
            original_skills=SimpleNamespace(active={one_key:active[one_key]},directory=directory,
                manual_skill_id=one_key,session_skill_ids=())
            first_book=book.model_copy(update={'pending':prop.model_copy(update={'proposal':prop.proposal.model_copy(update={'applicable_method_ids':(one_key,)})})})
            first=candidate(s,first_book,original_skills)
            extended=append_active_methods(first,skills)
            assert len(extended.method_bindings)==2
            assert all(r.id.startswith('r1.') for r in extended.requirements)
            assert c.execute('SELECT agent_task_method_extension_valid(%s,%s,%s)',
                tuple(Jsonb(v) for v in (first.model_dump(mode='json'),extended.model_dump(mode='json'),wire))).fetchone()[0]
        if raw['method_bindings']:
            for fault in ('delete_binding','delete_gate','profile','count','extra_delivery','pin','drop_manual','drop_participant','clear_runtime'):
                changed=deepcopy(raw);other_wire=deepcopy(wire)
                if fault=='delete_binding':changed['method_bindings'].pop()
                if fault=='delete_gate':changed['capability_gates'].pop()
                if fault=='profile':changed['method_bindings'][0]['profile_id']='arbitrary'
                if fault=='count':changed['method_bindings'][0]['image_count']+=1
                if fault=='extra_delivery':changed['requirements'].append({**changed['requirements'][0],'id':'r1.user.extra'})
                if fault=='pin':other_wire['active'][0]['body_sha256']='0'*64
                if fault=='drop_manual':other_wire['manual_skill_id']=None
                if fault=='drop_participant':other_wire['active']=[]
                if fault=='clear_runtime':other_wire=None
                assert not valid(changed,other_wire),fault
