"""Real scoped commands/transactions for the final legacy retirement workflow."""
import hashlib,json
from pathlib import Path
from uuid import uuid4
import psycopg
import pytest
from psycopg.rows import dict_row
from core.db_scope import SET_DATABASE_SCOPE_SQL
from services.knowledge_workspace import legacy_transfer as transfer
from tests.test_knowledge_derivation import (derived_db,cleanup_db,resize_db,manual_db,summary_db,geometry_db,groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db)

@pytest.fixture(scope='module')
def transfer_db(derived_db):
    make,actors=derived_db
    with psycopg.connect(**make.admin_args) as c:
        c.execute('SET LOCAL ROLE everydayai')
        c.execute('''CREATE TABLE knowledge_nodes(id uuid PRIMARY KEY,category text,node_type text,title text,content text,scope text,org_id uuid,owner_user_id uuid,source text,is_deleted boolean DEFAULT false,updated_at timestamptz DEFAULT now())''')
        c.execute('CREATE TABLE knowledge_edges(id uuid PRIMARY KEY,source_id uuid REFERENCES knowledge_nodes(id),target_id uuid REFERENCES knowledge_nodes(id),relation_type text,weight float)')
        c.execute('RESET ROLE')
        c.execute((Path(__file__).resolve().parents[1]/'migrations/334_knowledge_legacy_import_ledger.sql').read_text())
    return make,actors

@pytest.fixture
def source(transfer_db,tmp_path):
    make,_=transfer_db;repo=make();repo.initialize()
    ids=[str(uuid4()) for _ in range(5)]
    with psycopg.connect(**make.admin_args) as c:
        c.execute('DELETE FROM knowledge_edges');c.execute('DELETE FROM knowledge_nodes')
        for i,(category,kind,scope,org,content) in enumerate([
            ('experience','routing_pattern','org',repo.scope.org_id,'先核对口径，再核对结果。'),
            ('experience','failure_pattern','org',repo.scope.org_id,'历史查询失败，不可当成成功方法。'),
            ('experience','pattern','global',None,'全局历史经验，归属待核对。'),
            ('model','performance','global',None,'历史评分，不代表当前能力。'),
            ('tool','parameter','global',None,'token=sk-12345678901234567890')]):
            c.execute('INSERT INTO knowledge_nodes(id,category,node_type,title,content,scope,org_id,source) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(ids[i],category,kind,'迁移合成记录'+str(i),content,scope,org,'auto'))
        c.execute('INSERT INTO knowledge_edges VALUES(%s,%s,%s,%s,1)',(str(uuid4()),ids[0],ids[1],'historical_example'))
    def cursor(actor=0,org=0):
        conn=psycopg.connect(**make.admin_args,row_factory=dict_row)
        conn.execute('SET LOCAL ROLE everydayai');conn.execute(SET_DATABASE_SCOPE_SQL,make(actor,org).scope.settings)
        return conn
    with cursor() as c:
        rows,edges=transfer.read_sources(c.cursor())
        data={'knowledge_nodes':[r['node'] for r in rows],'knowledge_edges':[{}]*edges['count']}
    p=tmp_path/'source.json';p.write_text(json.dumps(data));manifest={'files':{'source.json':hashlib.sha256(p.read_bytes()).hexdigest()}}
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    with cursor() as c:
        approved,_=transfer.plan(c.cursor(),repo.scope.actor_user_id,repo.scope.org_id,tmp_path)
    return cursor,approved,ids


def test_scoped_transfer_idempotency_privacy_graph_and_reconciliation(source,transfer_db):
    cursor,p,ids=source
    with cursor() as c:result=transfer.apply_batch(c.cursor(),p,p['groups'][:2]);assert result['records_saved']>0
    with cursor() as c:
        for start in range(0,len(p['groups']),2):transfer.apply_batch(c.cursor(),p,p['groups'][start:start+2])
    with cursor() as c:
        r=transfer.verify(c.cursor(),p,mark=True);assert r['mapped_records']==5 and not r['errors']
        assert transfer.apply_batch(c.cursor(),p,p['groups'])['records_saved']==0
        row=c.execute("SELECT v.body,o.container_id FROM kb_legacy_import_items i JOIN kb_objects o ON o.id=i.object_id JOIN kb_object_versions v ON v.object_id=o.id AND v.revision=o.current_revision WHERE i.legacy_id=%s",(ids[4],)).fetchone()
        assert 'sk-12345678901234567890' not in row['body'] and '敏感原文已隔离' in row['body']
        archive=row['container_id']
        assert c.execute('SELECT verified_at IS NOT NULL AS ok FROM kb_legacy_import_runs WHERE id=%s',(transfer.run_id(p),)).fetchone()['ok']
    with cursor(0,1) as c:
        assert c.execute('SELECT public.kb_role(%s) AS role',(p['team_id'],)).fetchone()['role'] is None
    with cursor(1) as c:
        assert c.execute('SELECT public.kb_role(%s) AS role',(p['team_id'],)).fetchone()['role']=='viewer'
        assert c.execute('SELECT public.kb_role(%s) AS role',(archive,)).fetchone()['role'] is None
        with pytest.raises(PermissionError):transfer.plan(c.cursor(),transfer_db[0](1).scope.actor_user_id,p['org'],p['backup_path'])


def test_changed_source_rejects_entire_batch(source):
    cursor,p,ids=source
    with cursor() as c:c.execute("UPDATE knowledge_nodes SET content='变化' WHERE id=%s",(ids[0],))
    with pytest.raises(ValueError,match='BACKUP_SOURCE_CHANGED'):
        with cursor() as c:transfer.apply_batch(c.cursor(),p,p['groups'])
    with cursor() as c:assert c.execute('SELECT count(*) AS n FROM kb_legacy_import_items WHERE run_id=%s',(transfer.run_id(p),)).fetchone()['n']==0


def test_backup_tampering_and_scope_are_rejected(source):
    cursor,p,ids=source
    path=Path(p['backup_path'])/'source.json';path.write_text('{}')
    with pytest.raises(ValueError,match='BACKUP_INTEGRITY'):
        with cursor() as c:transfer.plan(c.cursor(),p['actor'],p['org'],p['backup_path'])
    with pytest.raises(ValueError,match='OTHER_ORGANIZATION'):
        transfer.decision({'org_id':str(uuid4())},p['org'])
    with pytest.raises(ValueError,match='PERSONAL_OWNERSHIP'):
        transfer.decision({'org_id':p['org'],'owner_user_id':str(uuid4())},p['org'])


def test_user_edits_prevent_verification_and_rollback(source):
    cursor,p,ids=source
    with cursor() as c:
        transfer.apply_batch(c.cursor(),p,p['groups'])
        oid=c.execute('SELECT object_id FROM kb_legacy_import_items WHERE legacy_id=%s',(ids[0],)).fetchone()['object_id']
        transfer.command(c.cursor(),transfer.run_id(p),'manual-edit','save_document',{'object_id':str(oid),'expected_revision':1,'body':'人工修订必须保留'})
    with cursor() as c:assert transfer.verify(c.cursor(),p)['errors']['destination_modified_or_deleted']==1
    with pytest.raises(ValueError,match='ROLLBACK_WOULD_REMOVE_USER_CHANGES'):
        with cursor() as c:transfer.rollback(c.cursor(),p)


def test_recoverable_batch_rollback(source):
    cursor,p,ids=source
    with cursor() as c:
        transfer.apply_batch(c.cursor(),p,p['groups']);transfer.verify(c.cursor(),p,mark=True)
        assert transfer.rollback(c.cursor(),p)['status']=='rolled_back_recoverably'
        assert c.execute('SELECT bool_and(o.undo_hidden OR o.deleted_at IS NOT NULL) AS hidden FROM kb_legacy_import_items i JOIN kb_objects o ON o.id=i.object_id WHERE i.run_id=%s',(transfer.run_id(p),)).fetchone()['hidden']


def test_partial_batch_rollback_does_not_require_unmigrated_records(source):
    cursor,p,_=source
    with cursor() as c:
        transfer.apply_batch(c.cursor(),p,p['groups'][:1])
        assert transfer.rollback(c.cursor(),p)['status']=='rolled_back_recoverably'


def test_batch_failure_rolls_back_documents_and_mapping(source,monkeypatch):
    cursor,p,_=source;original=transfer.create;calls=0
    def fail_second(*args,**kwargs):
        nonlocal calls
        if len(args)>7 and args[7]=='document':
            calls+=1
            if calls==2:raise RuntimeError('synthetic mid-batch failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(transfer,'create',fail_second)
    with pytest.raises(RuntimeError,match='synthetic'):
        with cursor() as c:transfer.apply_batch(c.cursor(),p,p['groups'])
    with cursor() as c:assert c.execute('SELECT count(*) AS n FROM kb_legacy_import_runs WHERE id=%s',(transfer.run_id(p),)).fetchone()['n']==0


@pytest.mark.asyncio
async def test_retirement_ignores_old_feature_flags_without_database_or_model(monkeypatch):
    from unittest.mock import AsyncMock
    from core.config import get_settings
    from services.knowledge_workspace import retirement
    from services import knowledge_service,knowledge_extractor,model_scorer
    from services.agent.experience_recorder import ExperienceRecorder
    from services.agent.tool_executor import ToolExecutor
    assert retirement.RETIRED
    settings=get_settings()
    for flag in ['kb_legacy_search_enabled','kb_legacy_extraction_enabled','kb_legacy_seed_enabled','kb_legacy_scoring_enabled']:
        monkeypatch.setattr(settings,flag,True)
    connect=AsyncMock(side_effect=AssertionError('retired store cannot connect'))
    monkeypatch.setattr(knowledge_service,'get_pg_connection',connect)
    assert await knowledge_service.search_relevant('历史方法')==[]
    assert await knowledge_service.get_node_by_metadata('key','value') is None
    assert await knowledge_service.add_knowledge(category='experience',node_type='pattern',title='历史',content='内容') is None
    assert await knowledge_service.load_seed_knowledge()==0
    assert await knowledge_extractor.extract_and_save(task_type='chat',model_id='synthetic',status='failed')==0
    assert await model_scorer.aggregate_model_scores() is None
    await ExperienceRecorder(org_id='synthetic',writer='erp_agent').record('routing','历史',[],'合成')
    connect.assert_not_awaited()
    result=await ToolExecutor(None,'synthetic','synthetic',None)._search_knowledge({'query':'old'})
    assert result.error_message=='LEGACY_KNOWLEDGE_DISABLED'


def test_cli_review_apply_sample_verify_and_replay(source,transfer_db,tmp_path):
    import os,subprocess,sys
    from psycopg.conninfo import make_conninfo
    cursor,p,_=source
    with cursor() as c:identity=c.execute('SELECT current_database() AS database,oid AS database_oid FROM pg_database WHERE datname=current_database()').fetchone()
    identity_file=tmp_path/'identity.json';identity_file.write_text(json.dumps(identity))
    args=[sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/transfer_legacy_knowledge.py'),'--actor',p['actor'],'--org',p['org'],'--backup',p['backup_path'],'--identity',str(identity_file)]
    env={**os.environ,'DATABASE_URL':make_conninfo(**transfer_db[0].admin_args,options='-c role=everydayai')}
    review=tmp_path/'review.json'
    def run(extra):
        result=subprocess.run(args+extra,env=env,text=True,capture_output=True,timeout=30)
        assert result.returncode==0,result.stderr
    run(['--report',str(review)])
    assert '历史评分，不代表当前能力' not in review.read_text()
    run(['--apply-reviewed',str(review),'--sample','--report',str(tmp_path/'sample.json')])
    run(['--apply-reviewed',str(review),'--report',str(tmp_path/'apply.json'),'--batch-size','2'])
    run(['--apply-reviewed',str(review),'--verify','--report',str(tmp_path/'verify.json')])
    assert not json.loads((tmp_path/'verify.json').read_text())['errors']
    run(['--apply-reviewed',str(review),'--report',str(tmp_path/'replay.json')])
    assert sum(b['records_saved'] for b in json.loads((tmp_path/'replay.json').read_text())['batches'])==0
