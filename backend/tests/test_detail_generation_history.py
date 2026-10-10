"""Successive page runs reuse frozen plans, image acceptance and task history."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import psycopg
from psycopg.types.json import Jsonb
import pytest

from core.local_db import LocalDBClient
from core.exceptions import AppException
from services.detail_page_generation import DetailPageGeneration
from services.detail_project_service import DetailProjectService
from tests.test_chat_image_lifecycle_postgres import isolated_db
from tests.test_ecommerce_recovery_postgres import ecom_db
from tests.test_detail_page_generation_postgres import page_db, ready_item, scoped, snapshot
from tests.test_detail_page_delivery_recovery import delivery_db, settle
from tests.test_detail_project_tasks import multi_db

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope='module')
def history_db(multi_db):
    with psycopg.connect(multi_db) as db:
        db.execute((ROOT/'migrations/289_detail_generation_history.sql').read_text())
        db.execute((ROOT/'migrations/290_detail_project_reuse_inputs.sql').read_text())
    return multi_db


def next_entries(dsn, user, project, old_plan, run):
    with psycopg.connect(dsn) as db:
        row = db.execute('SELECT input_snapshot,model_settings,prompt_versions,target_size FROM ecom_image_plans WHERE id=%s', (old_plan,)).fetchone()
    entry = dict(zip(['input_snapshot','model_settings','prompt_versions','target_size'], deepcopy(row)))
    entry.update(id=str(uuid4()), project_id=project, generation_run_id=run, user_id=user, org_id=None,
        invocation_key='main_images', image_count=1, input_digest='b'*64)
    entry['input_snapshot'].update(task_type='main_images', messages=[{'parts':[{'text':'新风格，原文保留'}]}])
    return [entry]


def accept_and_finish(dsn):
    user, project, run, plan, images = ready_item(dsn)
    with psycopg.connect(dsn) as db:
        db.execute("UPDATE ecom_image_plans SET input_snapshot=input_snapshot||%s,model_settings=model_settings||%s WHERE id=%s", (
            Jsonb({'task_type':'main_images','messages':[{'parts':[{'text':'旧要求'}]}]}),
            Jsonb({'image_budget':{'max_requests':1,'max_credits':images[0]['snapshot']['estimated_credits']}}), plan))
    with scoped(dsn,user) as db:
        task = db.execute('SELECT accept_detail_page_image(%s,%s,%s,%s)', (project,plan,images[0]['item_id'],Jsonb(images[0]['snapshot']))).fetchone()[0]['task_id']
    settle(dsn,user,task,success=True)
    with psycopg.connect(dsn) as db:
        db.execute("UPDATE detail_projects SET status='completed' WHERE id=%s", (project,))
    return user,project,run,plan,task


def test_new_run_retains_original_and_budget_is_per_run(history_db,tmp_path):
    user,project,old_run,old_plan,old_task = accept_and_finish(history_db)
    request = str(uuid4()); entries = next_entries(history_db,user,project,old_plan,request)
    with scoped(history_db,user) as db:
        db.execute('UPDATE detail_projects SET requirement=%s,version=version+1 WHERE id=%s', ('新风格，原文保留',project))
        version = db.execute('SELECT version FROM detail_projects WHERE id=%s',(project,)).fetchone()[0]
        for _ in range(2):
            result = db.execute('SELECT start_detail_page_run(%s,%s,%s,%s)', (project,version,request,Jsonb(entries))).fetchone()[0]
            assert result['run_state']['run_id']==request
    plan=entries[0]['id']; item=str(uuid4()); frozen=snapshot(user,project,request,plan,item)
    with psycopg.connect(history_db) as db:
        db.execute("UPDATE ecom_image_plans SET status='ready',current_stage=3,items=%s WHERE id=%s", (Jsonb([{
            'item_id':item,'position':1,'name':'新主图','request_text':'exact',
            'request_text_sha256':frozen['prompt_sha256'],'aspect_ratio':'1:1'}]),plan))
    with scoped(history_db,user) as db:
        task=db.execute('SELECT accept_detail_page_image(%s,%s,%s,%s)', (project,plan,item,Jsonb(frozen))).fetchone()[0]['task_id']
        assert task!=old_task
        assert db.execute('SELECT accept_detail_page_image(%s,%s,%s,%s)', (project,plan,item,Jsonb(frozen))).fetchone()[0]['task_id']==task
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(history_db,user='everydayai'),min_size=1,max_size=2)
    try:
        with patch('services.detail_project_service.get_settings') as settings:
            settings.return_value.file_workspace_root=str(tmp_path)
            service=DetailPageGeneration(raw,user,None)
        result=service.read(project)
        assert [r['run_id'] for r in result['runs']]==[old_run,request]
        assert [r['requirement'] for r in result['runs']]==['旧要求','新风格，原文保留']
        assert result['groups'][0]['plan_id']==plan
        assert result['runs'][0]['groups'][0]['tasks'][0]['id']==old_task
        assert result['runs'][0]['groups'][0]['auto_recovery'] is None
        assert not result['runs'][0]['groups'][0]['can_resume']
        with pytest.raises(AppException):
            DetailPageGeneration(raw,str(uuid4()),None).read(project)
        with psycopg.connect(history_db) as db:
            assert db.execute('SELECT COUNT(*) FROM ecom_image_plans WHERE project_id=%s',(project,)).fetchone()[0]==2
            assert db.execute('SELECT input_snapshot->\'messages\'->0->\'parts\'->0->>\'text\' FROM ecom_image_plans WHERE id=%s',(old_plan,)).fetchone()[0]=='旧要求'
    finally:
        raw.pool.close()


def test_active_and_uncertain_runs_cannot_be_replaced(history_db):
    user,project,run,plan,images=ready_item(history_db)
    entries=next_entries(history_db,user,project,plan,str(uuid4()))
    with scoped(history_db,user) as db:
        with pytest.raises(psycopg.Error,match='DETAIL_RUN_ACTIVE'):
            db.execute('SELECT start_detail_page_run(%s,2,%s,%s)', (project,entries[0]['generation_run_id'],Jsonb(entries)))
    with psycopg.connect(history_db) as db:
        db.execute("UPDATE detail_projects SET status='failed' WHERE id=%s",(project,))
        db.execute("UPDATE ecom_image_plans SET status='failed',recovery_state=%s WHERE id=%s",(
            Jsonb({'attempts':{'call':{'outcome':'uncertain'}}}),plan))
    with scoped(history_db,user) as db:
        with pytest.raises(psycopg.Error,match='ECOM_PLAN_EXECUTION_UNCERTAIN'):
            db.execute('SELECT start_detail_page_run(%s,2,%s,%s)', (project,entries[0]['generation_run_id'],Jsonb(entries)))


def test_concurrent_reruns_accept_only_one_new_run(history_db):
    user,project,old_run,old_plan,old_task=accept_and_finish(history_db)
    entries=[next_entries(history_db,user,project,old_plan,str(uuid4())) for _ in range(2)]
    def start(rows):
        try:
            with scoped(history_db,user) as db:
                db.execute('SELECT start_detail_page_run(%s,2,%s,%s)',(project,rows[0]['generation_run_id'],Jsonb(rows)))
            return True
        except psycopg.Error as error:
            assert 'DETAIL_RUN_ACTIVE' in str(error)
            return False
    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(start,entries))==1
    with psycopg.connect(history_db) as db:
        assert db.execute('SELECT COUNT(*) FROM ecom_image_plans WHERE project_id=%s',(project,)).fetchone()[0]==2


def test_edit_and_archive_reuse_existing_service_and_keep_history(history_db,tmp_path):
    user,project,run,plan,task=accept_and_finish(history_db)
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(history_db,user='everydayai'),min_size=1,max_size=2)
    try:
        with patch('services.detail_project_service.get_settings') as settings:
            settings.return_value.file_workspace_root=str(tmp_path)
            service=DetailProjectService(raw,user,None)
        row=service.update_settings(project,2,{'requirement':'  新风格\n保留原文  '})
        assert row['requirement']=='  新风格\n保留原文  ' and row['status']=='completed'
        service.archive(project); service.archive(project)  # Safe replay after a lost HTTP response.
        with psycopg.connect(history_db) as db:
            assert db.execute('SELECT status FROM detail_projects WHERE id=%s',(project,)).fetchone()[0]=='archived'
            assert db.execute('SELECT COUNT(*) FROM ecom_image_plans WHERE project_id=%s',(project,)).fetchone()[0]==1
            assert db.execute('SELECT COUNT(*) FROM tasks WHERE id=%s',(task,)).fetchone()[0]==1
        other=ready_item(history_db)
        with patch('services.detail_project_service.get_settings') as settings:
            settings.return_value.file_workspace_root=str(tmp_path)
            active=DetailProjectService(raw,other[0],None)
        with pytest.raises(AppException,match='后台执行'):
            active.archive(other[1])
        with pytest.raises(AppException):
            service.archive(other[1])
    finally:
        raw.pool.close()


@pytest.mark.parametrize('status', ['completed','failed'])
@pytest.mark.parametrize('content_type', ['detail_page','default'])
def test_reuse_all_inputs_preserves_old_files_plans_and_image_order(history_db,tmp_path,monkeypatch,status,content_type):
    from PIL import Image
    from core.config import get_settings
    from schemas.ecom_requirement import RequirementSettings
    from services.agent.image.input_adapters import DetailProjectRequirementAdapter
    from services.detail_page_generation import PageImageInputResolver, page_owner

    config=get_settings()
    for key,value in {'file_workspace_root':str(tmp_path),'detail_page_generation_enabled':True,
        'kie_api_key':'test-key','detail_gemini_input_credits_per_million':1,
        'detail_gemini_output_credits_per_million':1,'chat_image_max_requests':15,
        'chat_image_max_credits':300}.items():
        monkeypatch.setattr(config,key,value)
    monkeypatch.setenv('KIE_SHADOW_OVERSEAS_PROXY','http://127.0.0.1:7891')
    monkeypatch.setattr('services.detail_page_generation.chat_image_acceptance_allowed',lambda *_:True)
    monkeypatch.setattr('services.file_executor.FileExecutor.get_cdn_url',lambda self,path:f'https://cdn.example.invalid/{path}')
    user,project,old_run,old_plan,old_task=accept_and_finish(history_db)
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(history_db,user='everydayai'),min_size=1,max_size=2)
    try:
        service=DetailProjectService(raw,user,None)
        for index,name in enumerate(['old-a.png','old-b.png','new-a.png','new-b.png']):
            target=service.executor.resolve_safe_path(name);target.parent.mkdir(parents=True,exist_ok=True)
            Image.new('RGB',(16,16),(index*50,0,0)).save(target)
        for name in ['old-a.png','old-b.png']:
            row=service.attach_image(name,'product',project)
        resolver=PageImageInputResolver(page_owner(raw,user,None,project))
        frozen_refs=resolver.bind(row['images'])
        with psycopg.connect(history_db) as db:
            db.execute("UPDATE ecom_image_plans SET input_snapshot=input_snapshot||%s WHERE id=%s",(
                Jsonb({'resolved_references':frozen_refs,'target_size':{'aspect_ratio':'1:1','resolution':'1K'}}),old_plan))
            old_snapshot=db.execute('SELECT input_snapshot,model_settings,target_size,items FROM ecom_image_plans WHERE id=%s',(old_plan,)).fetchone()
            db.execute('UPDATE detail_projects SET status=%s WHERE id=%s',(status,project))
        ratio='1:1' if content_type=='default' else '3:4'
        count=14 if content_type=='default' else 3
        row=service.update_settings(project,row['version'],{'content_type':content_type,'platform':'jd',
            'language':'none','aspect_ratio':ratio,'quality':'2k','image_count':count,'prompt_model':'gemini-3.8-flash',
            'requirement':'新的产品，深蓝背景'})
        for image in list(row['images']):
            row=service.remove_image(project,image['id'],row['version'])
        assert row['images']==[]
        for name in ['new-a.png','new-b.png']:
            row=service.attach_image(name,'product',project)
        row=service.reorder_images(project,row['version'],[image['id'] for image in reversed(row['images'])])
        assert [image['workspace_path'] for image in row['images']]==['new-b.png','new-a.png']
        adapter=DetailProjectRequirementAdapter(service,user,None)
        assist=adapter.adapt(project,RequirementSettings(**{key:row[key] for key in (
            'content_type','platform','language','aspect_ratio','quality','image_count','requirement')}))
        assert [image.display_name for image in assist.product_images]==['new-b.png','new-a.png']
        assert [image.position for image in assist.product_images]==[1,2]
        assert assist.aspect_ratio==ratio and assist.quality=='2k' and assist.image_count==count
        assert assist.user_requirement=='新的产品，深蓝背景'
        resolver.verify(frozen_refs)
        assert all(service.executor.resolve_safe_path(name).is_file() for name in ['old-a.png','old-b.png'])
        request=str(uuid4())
        result=DetailPageGeneration(raw,user,None).start(project,row['version'],request)
        assert [run['run_id'] for run in result['runs']]==[old_run,request]
        assert result['runs'][0]['groups'][0]['tasks'][0]['id']==old_task
        with psycopg.connect(history_db) as db:
            assert db.execute('SELECT input_snapshot,model_settings,target_size,items FROM ecom_image_plans WHERE id=%s',(old_plan,)).fetchone()==old_snapshot
            plans=db.execute('SELECT invocation_key,input_snapshot,model_settings,target_size,image_count FROM ecom_image_plans WHERE generation_run_id=%s ORDER BY invocation_key DESC',(request,)).fetchall()
        expected=[('main_images','1:1',7),('detail_page','3:4',7)] if content_type=='default' else [('detail_page','3:4',3)]
        assert [(kind,target['aspect_ratio'],count) for kind,snap,model,target,count in plans]==expected
        for kind,snap,model,target,count in plans:
            assert snap['task_type']==kind and snap['platform']=='jd' and snap['language']=='none'
            assert target==snap['target_size'] and target['resolution']=='2K'
            assert model['model']=='gemini-3.8-flash'
            assert [ref['workspace_path'] for ref in snap['resolved_references']]==['new-b.png','new-a.png']
    finally:
        raw.pool.close()


@pytest.mark.parametrize('status', ['analyzing','plan_ready','generating','archived'])
def test_active_or_archived_inputs_still_reject_mutations(history_db,tmp_path,status):
    from PIL import Image
    user,project,run,plan,task=accept_and_finish(history_db)
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(history_db,user='everydayai'),min_size=1,max_size=2)
    try:
        with patch('services.detail_project_service.get_settings') as settings:
            settings.return_value.file_workspace_root=str(tmp_path)
            service=DetailProjectService(raw,user,None)
        for name in ['old.png','new.png']:
            target=service.executor.resolve_safe_path(name);target.parent.mkdir(parents=True,exist_ok=True)
            Image.new('RGB',(2,2)).save(target)
        row=service.attach_image('old.png','product',project)
        with psycopg.connect(history_db) as db:
            db.execute('UPDATE detail_projects SET status=%s WHERE id=%s',(status,project))
        with pytest.raises(AppException) as exc:
            service.attach_image('new.png','product',project)
        assert exc.value.code=='DETAIL_PROJECT_NOT_DRAFT'
        with pytest.raises(AppException):
            service.remove_image(project,row['images'][0]['id'],row['version'])
        with pytest.raises(AppException):
            service.update_settings(project,row['version'],{'quality':'2k'})
        with scoped(history_db,str(uuid4())) as db:
            with pytest.raises(psycopg.Error,match='DENIED'):
                db.execute("SELECT * FROM attach_detail_project_image_by_id(%s,'new.png','product')",(project,))
        with psycopg.connect(history_db) as db:
            assert db.execute('SELECT COUNT(*) FROM detail_project_images WHERE project_id=%s',(project,)).fetchone()[0]==1
    finally:
        raw.pool.close()


def test_reuse_inputs_migration_rollback_keeps_history_and_permissions(history_db):
    user,project,run,plan,task=accept_and_finish(history_db)
    with psycopg.connect(history_db) as db:
        before=db.execute("SELECT pg_get_userbyid(proowner),prosecdef,proacl FROM pg_proc WHERE oid='attach_detail_project_image_by_id(uuid,text,text)'::regprocedure").fetchone()
        db.execute((ROOT/'migrations/rollback/290_detail_project_reuse_inputs_rollback.sql').read_text())
        after=db.execute("SELECT pg_get_userbyid(proowner),prosecdef,proacl FROM pg_proc WHERE oid='attach_detail_project_image_by_id(uuid,text,text)'::regprocedure").fetchone()
        assert before==after and before[:2]==('everydayai',False)
        assert db.execute('SELECT COUNT(*) FROM tasks WHERE id=%s',(task,)).fetchone()[0]==1
        db.execute("SET SESSION AUTHORIZATION everydayai")
        db.execute("SELECT set_config('app.actor_user_id',%s,true),set_config('app.org_id','',true),set_config('app.access_kind','runtime',true)",(user,))
        with pytest.raises(psycopg.Error,match='DETAIL_PROJECT_NOT_DRAFT'):
            with db.transaction():
                db.execute("SELECT * FROM attach_detail_project_image_by_id(%s,'new.png','product')",(project,))
        db.rollback()


def test_history_migration_rollback_preserves_data(history_db):
    user,project,run,plan,task=accept_and_finish(history_db)
    with psycopg.connect(history_db) as db:
        db.execute('SELECT 1')
        with pytest.raises(psycopg.Error,match='DETAIL_HISTORY_ROLLBACK_ACTIVE'):
            with db.transaction():
                db.execute((ROOT/'migrations/rollback/289_detail_generation_history_rollback.sql').read_text())
        db.execute("UPDATE detail_projects d SET status='completed' WHERE (SELECT COUNT(DISTINCT p.generation_run_id) FROM ecom_image_plans p WHERE p.project_id=d.id)>1")
        db.execute((ROOT/'migrations/rollback/289_detail_generation_history_rollback.sql').read_text())
        assert db.execute('SELECT COUNT(*) FROM tasks WHERE id=%s',(task,)).fetchone()[0]==1
        db.execute((ROOT/'migrations/289_detail_generation_history.sql').read_text())
        db.rollback()
