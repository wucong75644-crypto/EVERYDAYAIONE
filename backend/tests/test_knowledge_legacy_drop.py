"""The destructive forward migration must prove every source survived first."""
from pathlib import Path
import psycopg
import pytest
from services.knowledge_workspace import legacy_transfer as transfer
from tests.test_knowledge_legacy_transfer import (transfer_db,source,derived_db,cleanup_db,resize_db,manual_db,summary_db,geometry_db,groups_db,editing_db,entry_db,rev,directional_db,structure_db,current,visual_db,tools_db,payload,local_db,command,layout_db,before_db,create,toggle,theme_db,rich_db)

SQL=(Path(__file__).resolve().parents[1]/'migrations/335_retire_legacy_knowledge_store.sql').read_text()


def test_drop_rejects_unmigrated_source(source):
    cursor,p,_=source
    with pytest.raises(psycopg.Error,match='LEGACY_RETIREMENT_RUN_INCOMPLETE'):
        with cursor() as c:c.execute(SQL)
    with cursor() as c:assert c.execute('SELECT count(*) AS n FROM knowledge_nodes').fetchone()['n']==5


@pytest.mark.parametrize('change', ['source','body','parent','archive_acl','graph','unknown_dependency','unknown_overload'])
def test_drop_rejects_changes_without_partial_ddl(source,transfer_db,change):
    cursor,p,ids=source;make,actors=transfer_db
    with cursor() as c:
        transfer.apply_batch(c.cursor(),p,p['groups']);transfer.verify(c.cursor(),p,mark=True)
    with pytest.raises(psycopg.Error,match='LEGACY_RETIREMENT'):
        with cursor() as c:
            state=c.execute('SELECT state FROM kb_legacy_import_runs WHERE id=%s',(transfer.run_id(p),)).fetchone()['state']
            if change=='source':c.execute("UPDATE knowledge_nodes SET content='changed' WHERE id=%s",(ids[0],))
            if change=='body':
                oid=c.execute('SELECT object_id FROM kb_legacy_import_items WHERE legacy_id=%s',(ids[0],)).fetchone()['object_id']
                transfer.command(c.cursor(),transfer.run_id(p),'edit-before-drop','save_document',{'object_id':str(oid),'expected_revision':1,'body':'manual change'})
            if change=='parent':
                c.execute('SET LOCAL ROLE everydayai_owner')
                c.execute('UPDATE kb_objects SET parent_id=NULL WHERE id=(SELECT object_id FROM kb_legacy_import_items WHERE legacy_id=%s)',(ids[0],))
            if change=='archive_acl':
                transfer.command(c.cursor(),transfer.run_id(p),'unexpected-grant','set_member',{'container_id':state['archive_container'],'user_id':actors[1],'role':'viewer','status':'active'})
            if change=='graph':c.execute('UPDATE knowledge_edges SET weight=2')
            if change=='unknown_dependency':
                c.execute("CREATE FUNCTION public.unreviewed_legacy_reference() RETURNS void LANGUAGE plpgsql AS $$ BEGIN PERFORM 'knowledge_nodes'; END $$")
            if change=='unknown_overload':
                c.execute("CREATE FUNCTION public.kb_ai_import_entry() RETURNS void LANGUAGE plpgsql AS $$ BEGIN PERFORM 'knowledge_nodes'; END $$")
            c.execute(SQL)
    with cursor() as c:assert c.execute("SELECT to_regclass('public.knowledge_nodes') IS NOT NULL AS kept").fetchone()['kept']


def test_drop_all_old_tables_and_preserve_new_content_atomically(source):
    cursor,p,_=source
    with cursor() as c:
        transfer.apply_batch(c.cursor(),p,p['groups']);transfer.verify(c.cursor(),p,mark=True)
    with cursor() as c:
        # Mirror production ownership: journal importer is owned by the isolated
        # knowledge role, while legacy worker functions and tables use app role.
        c.execute('SET LOCAL ROLE everydayai_owner')
        c.execute("CREATE FUNCTION public.kb_ai_import_entry(uuid,text) RETURNS void LANGUAGE plpgsql AS $$ BEGIN PERFORM 'knowledge_nodes'; END $$")
        c.execute('SET LOCAL ROLE everydayai')
        c.execute("CREATE FUNCTION public._worker_upsert_model_score_knowledge(uuid,uuid,text,text,text,jsonb,text,double precision,text) RETURNS void LANGUAGE plpgsql AS $$ BEGIN PERFORM 'knowledge_nodes'; END $$")
        c.execute("CREATE FUNCTION public.worker_replace_global_knowledge_seed(jsonb) RETURNS void LANGUAGE plpgsql AS $$ BEGIN PERFORM 'knowledge_edges'; END $$")
        c.execute(SQL)
        assert c.execute("SELECT to_regprocedure('public.kb_ai_import_entry(uuid,text)') IS NULL AND to_regprocedure('public.worker_replace_global_knowledge_seed(jsonb)') IS NULL AS gone").fetchone()['gone']
        assert c.execute("SELECT to_regclass('public.knowledge_nodes') IS NULL AND to_regclass('public.knowledge_edges') IS NULL AS gone").fetchone()['gone']
        assert c.execute('SELECT count(*) AS n FROM kb_legacy_import_items WHERE run_id=%s',(transfer.run_id(p),)).fetchone()['n']==5
        assert c.execute("SELECT (state->>'legacy_tables_dropped')::boolean AS retired FROM kb_legacy_import_runs WHERE id=%s",(transfer.run_id(p),)).fetchone()['retired']
        c.rollback()  # disposable fixture keeps the pre-drop state for other cases
