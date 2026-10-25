"""Operator-only transfer. Review defaults to read-only; writes require a matching plan."""
import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import psycopg
from psycopg.rows import dict_row
from core.db_scope import DatabaseScope,DatabaseAccessKind,SET_DATABASE_SCOPE_SQL
from services.knowledge_workspace.legacy_transfer import plan,apply_batch,verify,rollback


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--actor',required=True,type=UUID);p.add_argument('--org',required=True,type=UUID)
    p.add_argument('--backup',required=True,type=Path);p.add_argument('--identity',required=True,type=Path)
    p.add_argument('--report',required=True,type=Path);p.add_argument('--apply-reviewed',type=Path)
    p.add_argument('--sample',action='store_true');p.add_argument('--verify',action='store_true');p.add_argument('--rollback',action='store_true')
    p.add_argument('--batch-size',type=int,default=25)
    a=p.parse_args()
    if a.report.exists() or not 1<=a.batch_size<=50:p.error('new report path and batch size 1..50 required')
    if (a.sample or a.verify or a.rollback) and not a.apply_reviewed:p.error('writes require --apply-reviewed')
    if a.verify and a.rollback:p.error('choose verify or rollback')
    expected=json.loads(a.identity.read_text());actor,org=str(a.actor),str(a.org)
    scope=DatabaseScope(actor,org,DatabaseAccessKind.RUNTIME)
    def prepare(c):
        c.execute("SET LOCAL statement_timeout='45s'")
        c.execute(SET_DATABASE_SCOPE_SQL,scope.settings)
        c.execute('SELECT current_database() AS database,oid FROM pg_database WHERE datname=current_database()')
        actual=c.fetchone()
        if actual['database']!=expected['database'] or actual['oid']!=expected['database_oid']:raise ValueError('DATABASE_IDENTITY_MISMATCH')
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=10) as db:
        with db.transaction(),db.cursor() as c:
            c.execute('SET TRANSACTION READ ONLY');prepare(c)
            approved,_=plan(c,actor,org,a.backup)
        if a.apply_reviewed:
            if json.loads(a.apply_reviewed.read_text())!=approved:raise ValueError('PLAN_CHANGED_REVIEW_AGAIN')
            if a.verify or a.rollback:
                with db.transaction(),db.cursor() as c:
                    prepare(c);result=rollback(c,approved) if a.rollback else verify(c,approved,mark=True)
            else:
                selected=approved['groups']
                if a.sample:
                    seen=set();sample=[]
                    for g in selected:
                        if g['decision'] not in seen:sample.append(g);seen.add(g['decision'])
                    selected=sample
                batches=[]
                for start in range(0,len(selected),a.batch_size):
                    with db.transaction(),db.cursor() as c:
                        prepare(c);batches.append(apply_batch(c,approved,selected[start:start+a.batch_size]))
                    print(json.dumps({'batch_committed':len(batches),'records_saved':batches[-1]['records_saved']}),flush=True)
                with db.transaction(),db.cursor() as c:
                    c.execute('SET TRANSACTION READ ONLY');prepare(c);result=verify(c,approved)
                result['batches']=batches
        else:result=approved
    fd=os.open(a.report,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({'report':str(a.report),'source_records':approved['source_count'],'status':'success'}))


if __name__=='__main__':
    try:main()
    except Exception as error:
        code=str(error) if isinstance(error,(ValueError,PermissionError)) else type(error).__name__
        print(json.dumps({'status':'failed','error':code}),file=sys.stderr);sys.exit(1)
