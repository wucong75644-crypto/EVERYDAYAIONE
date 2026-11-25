"""Real classified trends: model plan -> department -> RPC -> period/category facts.

The database is test-owned and socket-only; no application data or credentials.
"""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import json

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from tests.test_chat_image_lifecycle_postgres import isolated_db
from config.default_classification_rules import DEFAULT_ORDER_RULES
from services.kuaimai.erp_unified_schema import COLUMN_WHITELIST, RPC_ORDER_STATS_FILTER_FIELDS
from services.kuaimai.order_classifier import OrderClassifier
from services.kuaimai.erp_unified_query import UnifiedQueryEngine

ROOT = Path(__file__).parents[1]
ORG = '12345678-1234-1234-1234-123456789abc'
OTHER = '12345678-1234-1234-1234-123456789abd'

class LocalRPC:
    """Invoke the actual production SQL signatures on this fixture's private DB."""
    def __init__(self, dsn):
        from core.local_db import LocalDBClient
        self.dsn, self.calls = dsn, []
        self.client = LocalDBClient(dsn, min_size=1, max_size=2)

    def rpc(self, name, params):
        assert name == 'erp_order_stats_grouped'
        self.calls.append((name, params))
        return self.client.rpc(name, params)

@pytest.fixture(scope='module')
def trend_db(isolated_db):
    types = {'integer':'integer','numeric':'numeric','text':'text','timestamp':'timestamptz'}
    definitions = ['doc_id uuid', 'org_id uuid', 'doc_type text', 'pay_time timestamptz',
                   'consign_time timestamptz','doc_created_at timestamptz']
    definitions += [f'"{f}" {types[COLUMN_WHITELIST[f].col_type]}' for f in sorted(RPC_ORDER_STATS_FILTER_FIELDS)]
    with psycopg.connect(isolated_db) as conn:
        conn.execute('CREATE TABLE erp_document_items ('+','.join(definitions)+')')
        conn.execute('CREATE TABLE erp_document_items_archive (LIKE erp_document_items)')
        conn.execute('CREATE TABLE erp_shops(org_id uuid,user_id text,name text)')
        conn.execute('CREATE TABLE erp_suppliers(org_id uuid,code text,name text)')
        conn.execute('CREATE TABLE erp_warehouses(org_id uuid,warehouse_id text,name text)')
        # Install legacy unchanged first; seven-argument trend must not hijack it.
        legacy=(ROOT/'migrations/104_rpc_add_not_in_op.sql').read_text().split('-- ── RPC 2:',1)[1]
        legacy=legacy[legacy.index('DROP FUNCTION'):]
        conn.execute(legacy)
        conn.execute((ROOT/'migrations/383_erp_order_scope_trend.sql').read_text())
        for org,day,hour,status,kind,flag,amount in [
            (ORG,15,0,'FINISHED','0',0,100), (ORG,15,12,'CLOSED','0',0,30),
            (ORG,15,13,'CLOSED','10',0,40), (ORG,16,1,'FINISHED','0',1,10),
            (ORG,17,23,'FINISHED','0',0,70), (OTHER,15,12,'FINISHED','0',0,9999),
        ]:
            when=f'2026-10-{day:02d}T{hour:02d}:00:00+08:00'
            conn.execute('''INSERT INTO erp_document_items
                (doc_id,org_id,doc_type,pay_time,doc_created_at,consign_time,platform,shop_name,
                 order_status,order_type,is_scalping,quantity,amount)
                VALUES(%s,%s,'order',%s,%s,%s,'tb','fixture-shop',%s,%s,%s,1,%s)''',
                (uuid4(),org,when,when,when,status,kind,flag,amount))
    db = LocalRPC(isolated_db)
    yield db
    db.client.close()

@pytest.fixture
def classifier(monkeypatch):
    monkeypatch.setattr(OrderClassifier,'for_org',classmethod(lambda cls,db,org: cls(DEFAULT_ORDER_RULES)))

FILTERS=[{'field':'pay_time','op':'gte','value':'2026-10-15T00:00:00+08:00'},
         {'field':'pay_time','op':'lt','value':'2026-10-18T00:00:00+08:00'},
         {'field':'platform','op':'eq','value':'tb'}]

@pytest.mark.asyncio
@pytest.mark.parametrize('scope,counts,amounts',[
    ('valid',[1,0,1],[100,0,70]), ('all',[3,1,1],[170,10,70]),
    ('exclude_scalping',[2,0,1],[130,0,70]), ('only_scalping',[1,1,0],[40,10,0]),
])
async def test_actual_rpc_scope_and_shanghai_periods(trend_db,classifier,scope,counts,amounts):
    out=await UnifiedQueryEngine(trend_db,ORG).execute('order','summary',FILTERS,
        query_type='trend',order_scope=scope,metrics=['count','amount'],limit=366)
    assert not out.error_message, out.error_message
    assert [r['period'] for r in out.data] == ['2026-10-15','2026-10-16','2026-10-17']
    assert [r['order_count'] for r in out.data] == counts
    assert [r['order_amount'] for r in out.data] == amounts
    assert out.metadata['order_scope']==scope and out.metadata['time_column']=='pay_time'

@pytest.mark.asyncio
@pytest.mark.parametrize('grain,period', [('week','2026-10-12'),('month','2026-10-01')])
async def test_time_and_dimension_grouping(trend_db,classifier,grain,period):
    filters=[dict(f) for f in FILTERS]
    filters[0]['value']='2026-10-12T00:00:00+08:00'
    filters[1]['value']='2026-10-19T00:00:00+08:00'
    out=await UnifiedQueryEngine(trend_db,ORG).execute('order','summary',filters,
        query_type='trend',order_scope='exclude_scalping',time_granularity=grain,
        group_by=['platform'],limit=366)
    assert not out.error_message, out.error_message
    assert out.data==[{'period':period,'group_key':'淘宝','order_count':3,'order_amount':200.0}]

@pytest.mark.asyncio
async def test_legacy_summary_and_detail_filter_kept(trend_db,classifier):
    engine=UnifiedQueryEngine(trend_db,ORG)
    before=await engine.execute('order','summary',FILTERS,order_scope='exclude_scalping')
    assert before.data[0]['doc_count']==3 and before.data[0]['total_amount']==200
    filtered=await engine.execute('order','summary',FILTERS+[{'field':'amount','op':'gt','value':80}],
        query_type='trend',order_scope='all',limit=366)
    assert not filtered.error_message
    assert sum(r['order_count'] for r in filtered.data)==1
    assert sum(r['order_amount'] for r in filtered.data)==100

@pytest.mark.asyncio
async def test_unsupported_filter_does_not_silently_disappear(trend_db,classifier):
    before=len(trend_db.calls)
    out=await UnifiedQueryEngine(trend_db,ORG).execute('order','summary',
        FILTERS+[{'field':'receiver_name','op':'eq','value':'any'}],query_type='trend',order_scope='all')
    assert out.error_message
    assert len(trend_db.calls)==before

@pytest.mark.asyncio
async def test_archive_and_time_column_are_real_inputs(trend_db,classifier):
    with psycopg.connect(trend_db.dsn) as conn:
        conn.execute('''INSERT INTO erp_document_items_archive
            (doc_id,org_id,doc_type,pay_time,doc_created_at,platform,order_type,order_status,is_scalping,quantity,amount)
            VALUES(%s,%s,'order','2025-01-01T16:00:00Z','2025-01-01T15:00:00Z','tb','0','FINISHED',0,1,23)''',(uuid4(),ORG))
    for field,day in [('pay_time','2025-01-02'),('doc_created_at','2025-01-01')]:
        f=[{'field':field,'op':'gte','value':'2025-01-01T00:00:00+08:00'},
           {'field':field,'op':'lt','value':'2025-01-03T00:00:00+08:00'}]
        out=await UnifiedQueryEngine(trend_db,ORG).execute('order','summary',f,query_type='trend',order_scope='valid')
        assert not out.error_message
        assert [r['period'] for r in out.data if r['order_count']]==[day]
        assert sum(r['order_amount'] for r in out.data)==23


@pytest.mark.asyncio
async def test_partial_day_is_visible_even_when_no_order_has_arrived(trend_db,classifier):
    filters=[dict(f) for f in FILTERS]
    filters[1]['value']='2026-10-18T12:00:00+08:00'
    out=await UnifiedQueryEngine(trend_db,ORG).execute('order','summary',filters,
        query_type='trend',order_scope='valid',limit=366)
    assert not out.error_message
    assert out.data[-1]=={'period':'2026-10-18','order_count':0,'order_amount':0}
    assert sum(r['order_amount'] for r in out.data)==170

@pytest.mark.asyncio
async def test_second_time_predicate_is_applied(trend_db,classifier):
    before=len(trend_db.calls)
    out=await UnifiedQueryEngine(trend_db,ORG).execute('order','summary',
        FILTERS+[{'field':'consign_time','op':'gte','value':'2026-10-16T00:00:00+08:00'}],
        query_type='trend',order_scope='valid')
    assert not out.error_message
    assert sum(r['order_count'] for r in out.data)==1
    assert sum(r['order_amount'] for r in out.data)==70
    assert len(trend_db.calls)==before+1


@pytest.mark.asyncio
@pytest.mark.parametrize('op,value,amount',[('in',[0],240),('not_in',[1],240)])
async def test_integer_array_filters_are_typed(trend_db,classifier,op,value,amount):
    out=await UnifiedQueryEngine(trend_db,ORG).execute('order','summary',
        FILTERS+[{'field':'is_scalping','op':op,'value':value}],query_type='trend',order_scope='all')
    assert not out.error_message, out.error_message
    assert sum(r['order_amount'] for r in out.data)==amount


@pytest.mark.asyncio
@pytest.mark.parametrize('key',['time_type','time_col'])
async def test_department_compiles_both_time_names_before_query(trend_db,classifier,key):
    from services.agent.departments.trade_agent import TradeAgent
    agent=TradeAgent(db=trend_db,org_id=ORG)
    out=await agent.execute('按付款时间看每天销售额',dag_mode=True,params={
        'doc_type':'order','mode':'summary','time_range':'2026-10-15 ~ 2026-10-17',
        key:'pay_time','order_scope':'valid','query_type':'trend','metrics':['amount']})
    assert not out.error_message
    assert out.metadata['time_column']=='pay_time'
    assert sum(r['order_amount'] for r in out.data)==170


def test_explicit_time_col_keeps_priority_over_compat_alias():
    from services.agent.param_converter import params_to_filters
    filters,_=params_to_filters({'doc_type':'order','time_range':'2026-10-15 ~ 2026-10-17',
                                'time_col':'consign_time','time_type':'pay_time'})
    assert {f['field'] for f in filters}=={'consign_time'}


def test_trend_rpc_is_invoker_and_untrusted_role_cannot_read(trend_db):
    with psycopg.connect(trend_db.dsn) as conn:
        assert conn.execute("SELECT prosecdef FROM pg_proc WHERE oid='erp_order_stats_grouped(uuid,timestamptz,timestamptz,varchar,jsonb,varchar,varchar)'::regprocedure").fetchone()[0] is False
        conn.execute('SET LOCAL ROLE image_untrusted')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT erp_order_stats_grouped(%s::uuid,'2026-10-15'::timestamptz,'2026-10-18'::timestamptz,'pay_time'::varchar,NULL::jsonb,NULL::varchar,'day'::varchar)",(ORG,))


def test_rollback_removes_only_trend_overload(trend_db):
    with psycopg.connect(trend_db.dsn) as conn:
        conn.execute('SAVEPOINT migration_rollback_check')
        conn.execute((ROOT/'migrations/rollback/383_erp_order_scope_trend_rollback.sql').read_text())
        assert conn.execute("SELECT to_regprocedure('erp_order_stats_grouped(uuid,timestamptz,timestamptz,varchar,jsonb,varchar,varchar)') IS NULL").fetchone()[0]
        assert conn.execute("SELECT to_regprocedure('erp_order_stats_grouped(uuid,timestamptz,timestamptz,varchar,jsonb,varchar)') IS NOT NULL").fetchone()[0]
        conn.execute('ROLLBACK TO SAVEPOINT migration_rollback_check')
