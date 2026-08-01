"""AR-17.2 real Worker/RPC contract on the disposable AR-17 PostgreSQL fixture."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from core.db_scope import AsyncScopedDatabaseClient, DatabaseAccessKind, DatabaseScope
from core.local_db import AsyncLocalDBClient
from services.agent.runtime.domain import ActionAttempt, ActionAttemptStatus, Lease
from services.agent.runtime.domain.scope import RuntimeScope, ScopeKind
from services.agent.runtime.executors.contracts import canonical_request_hash
from services.agent.runtime.executors.real_base import RuntimeReadResources
from services.agent.runtime.executors.real_composition import build_nonproduction_read_registry
from services.agent.runtime.executors.read_registry import READ_TOOL_SPECS
from services.agent.runtime.ports.executor import ExecutionOutcome

from tests.test_agent_runtime_ar17_postgres_external import database


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]
ORG = UUID("22222222-2222-2222-2222-222222222222")
OTHER_ORG = UUID("77777777-7777-7777-7777-777777777777")
USER = UUID("44444444-4444-4444-4444-444444444444")
CONVERSATION = UUID("55555555-5555-5555-5555-555555555555")
CHANNEL_CONVERSATION = UUID("66666666-6666-6666-6666-666666666666")

REQUESTS = {
    "get_conversation_context": {"limit": 5}, "search_knowledge": {"query": "knowledge"},
    "evidence_search": {"query": ""}, "evidence_get": {"artifact_id": "evidence-1"},
    "memory_search": {"query": "memory"}, "memory_get": {"memory_ref": "memory:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaab"},
    "artifact_search": {"query": ""}, "artifact_get": {"artifact_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaac"},
    "artifact_read": {"artifact_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaac", "cursor": 0, "max_tokens": 256},
    "file_search": {"keyword": "read-only"}, "local_product_identify": {"code": "P-1"},
    "local_stock_query": {"product_code": "P-1"}, "local_product_stats": {"product_code": "P-1"},
    "local_platform_map_query": {"product_code": "P-1"}, "local_compare_stats": {"doc_type": "sale", "compare_kind": "wow", "current_period": "today"},
    "local_shop_list": {}, "local_warehouse_list": {}, "local_supplier_list": {},
}


BUSINESS_BOOTSTRAP = """
RESET ROLE;
ALTER ROLE everydayai_agent_runtime_worker NOINHERIT;
SET ROLE everydayai_owner;
CREATE TABLE knowledge_nodes(id UUID PRIMARY KEY, org_id UUID, owner_user_id UUID, is_deleted BOOLEAN NOT NULL DEFAULT FALSE, category TEXT, node_type TEXT, title TEXT, content TEXT, confidence NUMERIC, source TEXT, metadata JSONB NOT NULL DEFAULT '{}');
CREATE TABLE conversation_data_evidence(artifact_id TEXT NOT NULL, conversation_id UUID NOT NULL, org_id UUID, source TEXT, columns JSONB, rows JSONB, query_scope JSONB, metric_definitions JSONB, model_view JSONB, byte_size INTEGER, context_revision BIGINT, validation_status TEXT);
CREATE TABLE memory_atoms(id UUID PRIMARY KEY, user_id UUID, org_id UUID, content TEXT, metadata JSONB, valid_from TIMESTAMPTZ, valid_until TIMESTAMPTZ, source_message_ids UUID[], is_deleted BOOLEAN NOT NULL DEFAULT FALSE, status TEXT);
CREATE TABLE erp_products(org_id UUID, outer_id TEXT, title TEXT, shipper TEXT, active_status INTEGER, barcode TEXT);
CREATE TABLE erp_product_skus(org_id UUID, outer_id TEXT, sku_outer_id TEXT, properties_name TEXT, barcode TEXT);
CREATE TABLE erp_stock_status(org_id UUID, outer_id TEXT, sku_outer_id TEXT, warehouse_id TEXT, sellable_num INTEGER, total_stock INTEGER, lock_stock INTEGER, purchase_num INTEGER, stock_status TEXT);
CREATE TABLE erp_product_daily_stats(org_id UUID, outer_id TEXT, stat_date DATE, order_count INTEGER, order_qty INTEGER, order_amount NUMERIC, purchase_count INTEGER, purchase_qty INTEGER, receipt_count INTEGER, receipt_qty INTEGER, aftersale_count INTEGER, aftersale_qty INTEGER);
CREATE TABLE erp_product_platform_map(org_id UUID, outer_id TEXT, num_iid TEXT, user_id UUID, sku_mappings JSONB);
CREATE TABLE erp_shops(org_id UUID, name TEXT, platform TEXT, state INTEGER, shop_id TEXT, short_name TEXT);
CREATE TABLE erp_warehouses(org_id UUID, warehouse_id TEXT, name TEXT, code TEXT, warehouse_type TEXT, status INTEGER, is_virtual BOOLEAN);
CREATE TABLE erp_suppliers(org_id UUID, code TEXT, name TEXT, status INTEGER, contact_name TEXT, category_name TEXT, remark TEXT);
CREATE TABLE erp_document_items(org_id UUID, doc_id UUID, doc_type TEXT, doc_created_at TIMESTAMPTZ, quantity INTEGER, amount NUMERIC);
ALTER TABLE messages ENABLE ROW LEVEL SECURITY; ALTER TABLE messages FORCE ROW LEVEL SECURITY;
ALTER TABLE conversation_artifacts ENABLE ROW LEVEL SECURITY; ALTER TABLE conversation_artifacts FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge_nodes ENABLE ROW LEVEL SECURITY; ALTER TABLE knowledge_nodes FORCE ROW LEVEL SECURITY;
ALTER TABLE conversation_data_evidence ENABLE ROW LEVEL SECURITY; ALTER TABLE conversation_data_evidence FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_atoms ENABLE ROW LEVEL SECURITY; ALTER TABLE memory_atoms FORCE ROW LEVEL SECURITY;
ALTER TABLE erp_products ENABLE ROW LEVEL SECURITY; ALTER TABLE erp_products FORCE ROW LEVEL SECURITY;
ALTER TABLE erp_product_skus ENABLE ROW LEVEL SECURITY; ALTER TABLE erp_product_skus FORCE ROW LEVEL SECURITY;
ALTER TABLE erp_stock_status ENABLE ROW LEVEL SECURITY; ALTER TABLE erp_stock_status FORCE ROW LEVEL SECURITY;
ALTER TABLE erp_product_daily_stats ENABLE ROW LEVEL SECURITY; ALTER TABLE erp_product_daily_stats FORCE ROW LEVEL SECURITY;
ALTER TABLE erp_product_platform_map ENABLE ROW LEVEL SECURITY; ALTER TABLE erp_product_platform_map FORCE ROW LEVEL SECURITY;
ALTER TABLE erp_shops ENABLE ROW LEVEL SECURITY; ALTER TABLE erp_shops FORCE ROW LEVEL SECURITY;
ALTER TABLE erp_warehouses ENABLE ROW LEVEL SECURITY; ALTER TABLE erp_warehouses FORCE ROW LEVEL SECURITY;
ALTER TABLE erp_suppliers ENABLE ROW LEVEL SECURITY; ALTER TABLE erp_suppliers FORCE ROW LEVEL SECURITY;
ALTER TABLE erp_document_items ENABLE ROW LEVEL SECURITY; ALTER TABLE erp_document_items FORCE ROW LEVEL SECURITY;
DO $$ DECLARE t TEXT; BEGIN FOREACH t IN ARRAY ARRAY['messages','conversation_artifacts','knowledge_nodes','conversation_data_evidence','memory_atoms','erp_products','erp_product_skus','erp_stock_status','erp_product_daily_stats','erp_product_platform_map','erp_shops','erp_warehouses','erp_suppliers','erp_document_items'] LOOP EXECUTE format('CREATE POLICY ar172_owner_%I ON %I FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE)',t,t); END LOOP; END $$;
INSERT INTO knowledge_nodes VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '22222222-2222-2222-2222-222222222222', NULL, FALSE, 'general', 'fact', 'knowledge', 'knowledge fact', 1, 'fixture', '{}');
INSERT INTO organizations(id) VALUES ('77777777-7777-7777-7777-777777777777');
INSERT INTO conversation_data_evidence VALUES ('evidence-1','55555555-5555-5555-5555-555555555555','22222222-2222-2222-2222-222222222222','fixture','["amount"]','[{"amount":4}]','{}','{}','{}',30,1,'ready');
    INSERT INTO memory_atoms VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaab','44444444-4444-4444-4444-444444444444','22222222-2222-2222-2222-222222222222','memory fact','{}',now(),NULL,ARRAY[]::UUID[],FALSE,'active');
    INSERT INTO messages(id,conversation_id,org_id,role,content,status,context_revision) VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaae','55555555-5555-5555-5555-555555555555','22222222-2222-2222-2222-222222222222','user','hello','completed',1);
INSERT INTO conversation_artifacts(id,conversation_id,org_id) VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaac','55555555-5555-5555-5555-555555555555','22222222-2222-2222-2222-222222222222');
ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS tool_call_id TEXT; ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS tool_name TEXT; ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS artifact_type TEXT; ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS status TEXT; ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS storage_kind TEXT; ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS inline_content JSONB; ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS storage_ref JSONB; ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS model_view JSONB; ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS history_view JSONB; ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS content_hash TEXT; ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS byte_size INTEGER; ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS metadata JSONB; ALTER TABLE conversation_artifacts ADD COLUMN IF NOT EXISTS context_revision BIGINT;
UPDATE conversation_artifacts SET tool_name='fixture',artifact_type='table',status='ready',storage_kind='inline',inline_content='{"rows":[{"id":"r1"}]}'::jsonb,model_view='{}',history_view='{}',content_hash='hash',byte_size=20,metadata='{}',context_revision=1 WHERE id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaac';
INSERT INTO erp_products VALUES ('22222222-2222-2222-2222-222222222222','P-1','Product','local',1,'B-1');
INSERT INTO erp_product_skus VALUES ('22222222-2222-2222-2222-222222222222','P-1','S-1','Blue','B-1');
INSERT INTO erp_stock_status VALUES ('22222222-2222-2222-2222-222222222222','P-1','S-1','W-1',2,3,0,0,'ok');
INSERT INTO erp_product_daily_stats VALUES ('22222222-2222-2222-2222-222222222222','P-1',CURRENT_DATE,1,2,3,0,0,0,0,0,0);
INSERT INTO erp_product_platform_map VALUES ('22222222-2222-2222-2222-222222222222','P-1','N-1','44444444-4444-4444-4444-444444444444','[]');
INSERT INTO erp_shops VALUES ('22222222-2222-2222-2222-222222222222','Shop','local',1,'S-1','Shop');
INSERT INTO erp_warehouses VALUES ('22222222-2222-2222-2222-222222222222','W-1','Warehouse','W','local',1,FALSE);
INSERT INTO erp_suppliers VALUES ('22222222-2222-2222-2222-222222222222','SUP-1','Supplier',1,'','general','');
INSERT INTO erp_document_items VALUES ('22222222-2222-2222-2222-222222222222','aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaad','sale',now(),2,3);
RESET ROLE;
"""


def _insert_actions(url: str) -> dict[str, tuple[str, str, str]]:
    actions: dict[str, tuple[str, str, str]] = {}
    with psycopg.connect(url) as conn:
        conn.execute("SET ROLE everydayai_owner")
        session_ids: dict[bool, UUID] = {}
        for index, tool in enumerate(READ_TOOL_SPECS, 1):
            channel = tool.startswith("local_")
            if channel not in session_ids:
                session_ids[channel] = uuid4()
                conversation = CHANNEL_CONVERSATION if channel else CONVERSATION
                user = None if channel else USER
                scope_kind, scope_id = ("channel", "wecom:group:test") if channel else ("user", str(USER))
                conn.execute("INSERT INTO agent_runtime_sessions(id,conversation_id,org_id,user_id,scope_kind,scope_id,created_by_user_id,agent_definition_id,agent_definition_revision) VALUES(%s,%s,%s,%s,%s,%s,%s,'fixture','v1')", (session_ids[channel],conversation,ORG,user,scope_kind,scope_id,USER))
            session_id = session_ids[channel]
            command_id, run_id, step_id = [uuid4() for _ in range(3)]
            action_id, attempt_id, token, policy_id = [uuid4() for _ in range(4)]
            user = None if channel else USER
            conn.execute("INSERT INTO agent_session_commands(id,session_id,org_id,user_id,command_type,idempotency_key,payload,request_hash) VALUES(%s,%s,%s,%s,'submit_input',%s,'{}','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')", (command_id,session_id,ORG,user,str(command_id)))
            conn.execute("INSERT INTO agent_runs(id,session_id,command_id,org_id,user_id,run_kind,idempotency_key,request_hash,status,execution_token,lease_expires_at) VALUES(%s,%s,%s,%s,%s,'user',%s,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','running',%s,clock_timestamp()+interval '10 minutes')", (run_id,session_id,command_id,ORG,user,str(run_id),uuid4()))
            conn.execute("INSERT INTO agent_model_steps(id,run_id,session_id,org_id,user_id,step_number,model_id,provider,model_revision,prompt_revision,tool_catalog_revision) VALUES(%s,%s,%s,%s,%s,%s,'fixture','fixture','v1','v1','v1')", (step_id,run_id,session_id,ORG,user,index))
            args = json.dumps({"context_revision": 1})
            request_hash = canonical_request_hash(REQUESTS[tool])
            conn.execute("INSERT INTO agent_actions(id,session_id,run_id,model_step_id,org_id,user_id,action_index,stable_tool_call_id,tool_name,arguments,arguments_hash,request_hash,batch_hash,policy_decision,policy_snapshot,policy_revision,retry_disposition,status) VALUES(%s,%s,%s,%s,%s,%s,0,%s,%s,%s,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',%s,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','preauthorized','{}','v1','retry_safe','running')", (action_id,session_id,run_id,step_id,ORG,user,str(action_id),tool,args,request_hash))
            conn.execute("INSERT INTO agent_action_attempts(id,action_id,session_id,run_id,org_id,user_id,attempt_number,status,dispatch_phase,worker_id,execution_token,lease_expires_at,idempotency_key,request_hash,retry_disposition) VALUES(%s,%s,%s,%s,%s,%s,1,'dispatching','request_started','ar172-worker',%s,clock_timestamp()+interval '10 minutes',%s,%s,'retry_safe')", (attempt_id,action_id,session_id,run_id,ORG,user,token,str(attempt_id),request_hash))
            conn.execute("INSERT INTO agent_policy_receipts(id,action_id,session_id,run_id,org_id,user_id,decision,arguments_hash,executor_type,executor_revision,policy_revision,effective_scope,reason_codes,receipt_hash,expires_at) VALUES(%s,%s,%s,%s,%s,%s,'allow','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',%s,1,'v1','{}',ARRAY['fixture'],%s::text,clock_timestamp()+interval '10 minutes')", (policy_id,action_id,session_id,run_id,ORG,user,f'runtime_read:{tool}',str(uuid4()).replace('-','')+'0'*32))
            conn.execute("INSERT INTO agent_action_dispatch_intents(attempt_id,action_id,policy_receipt_id,execution_token,request_hash,executor_type,executor_revision,policy_revision,external_idempotency_key,recovery_mode) VALUES(%s,%s,%s,%s,%s,%s,1,'v1',%s,'idempotent_replay')", (attempt_id,action_id,policy_id,token,request_hash,f'runtime_read:{tool}',str(uuid4())))
            actions[tool] = (str(action_id), str(attempt_id), str(token))
        conn.commit()
    return actions


def _attempt(tool: str, ids: tuple[str, str, str], request: dict[str, object]) -> ActionAttempt:
    now = datetime.now(timezone.utc)
    channel = tool.startswith("local_")
    return ActionAttempt(
        attempt_id=ids[1], action_id=ids[0],
        scope=RuntimeScope(ScopeKind.CHANNEL if channel else ScopeKind.USER, "wecom:group:test" if channel else str(USER), None if channel else str(USER), str(ORG)),
        attempt_number=1, status=ActionAttemptStatus.DISPATCHING, worker_id="ar172-worker",
        idempotency_key=f"id-{ids[1]}", request_hash=canonical_request_hash(request),
        lease=Lease(fencing_token=ids[2], expires_at=now+timedelta(minutes=1)),
        started_at=now,
    )


@pytest.mark.asyncio
async def test_real_agent_runtime_worker_executes_all_database_adapters(database, tmp_path: Path) -> None:
    url = database
    with psycopg.connect(url) as conn:
        conn.execute(BUSINESS_BOOTSTRAP)
        conn.execute((ROOT / "migrations/225_01_agent_runtime_read_capability_rpcs.sql").read_text())
        conn.commit()
    ids = _insert_actions(url)
    worker_url = url.replace("postgres@", "everydayai_agent_runtime_worker@")
    client = AsyncLocalDBClient(worker_url, min_size=1, max_size=2)
    await client.open()
    scoped = AsyncScopedDatabaseClient(client, DatabaseScope(actor_user_id=str(USER), org_id=str(ORG), access_kind=DatabaseAccessKind.AGENT_RUNTIME, request_id="ar172-worker"))
    try:
        workspace = tmp_path / "personal" / str(USER)
        workspace.mkdir(parents=True)
        (workspace / "read-only.txt").write_text("fixture", encoding="utf-8")
        resources = RuntimeReadResources(database=scoped, workspace_root=tmp_path)
        registry = build_nonproduction_read_registry(resources)
        for index, tool in enumerate(READ_TOOL_SPECS, 1):
            request = REQUESTS[tool]
            _, executor = registry.resolve(tool)
            attempt = _attempt(tool, ids[tool], request)
            receipt = await executor.dispatch(attempt, request)
            assert receipt.outcome is ExecutionOutcome.COMPLETED, (tool, receipt.external_receipt)
            assert receipt.result and receipt.result.data and receipt.result.data.get("count", 1) > 0, tool
        with psycopg.connect(url) as conn:
            before = conn.execute("SELECT count(*),max(updated_at) FROM agent_actions").fetchone()
            business_tables = ('messages','knowledge_nodes','conversation_data_evidence','memory_atoms','conversation_artifacts','erp_products','erp_product_skus','erp_stock_status','erp_product_daily_stats','erp_product_platform_map','erp_shops','erp_warehouses','erp_suppliers','erp_document_items')
            business_before = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in business_tables}
            await asyncio.gather(*[
                registry.resolve("local_shop_list")[1].dispatch(
                    _attempt("local_shop_list", ids["local_shop_list"], REQUESTS["local_shop_list"]), REQUESTS["local_shop_list"]) for _ in range(50)
            ])
            after = conn.execute("SELECT count(*),max(updated_at) FROM agent_actions").fetchone()
            assert before == after
            assert business_before == {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in business_tables}
        bad_token = _attempt("local_shop_list", ids["local_shop_list"], REQUESTS["local_shop_list"])
        bad_token = ActionAttempt(
            **{**bad_token.__dict__, "lease": Lease(fencing_token=str(uuid4()), expires_at=datetime.now(timezone.utc)+timedelta(minutes=1))},
        )
        failed = await registry.resolve("local_shop_list")[1].dispatch(bad_token, REQUESTS["local_shop_list"])
        assert failed.outcome is ExecutionOutcome.FAILED
        bad_scope_resources = RuntimeReadResources(database=AsyncScopedDatabaseClient(client, DatabaseScope(actor_user_id=str(USER), org_id=str(ORG), access_kind=DatabaseAccessKind.RUNTIME, request_id="wrong-kind")))
        bad_scope = build_nonproduction_read_registry(bad_scope_resources)
        failed_scope = await bad_scope.resolve("local_shop_list")[1].dispatch(_attempt("local_shop_list", ids["local_shop_list"], REQUESTS["local_shop_list"]), REQUESTS["local_shop_list"])
        assert failed_scope.outcome is ExecutionOutcome.FAILED
        with psycopg.connect(url) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute("UPDATE agent_action_attempts SET status='failed',ended_at=clock_timestamp() WHERE id=%s", (UUID(ids["local_shop_list"][1]),))
            conn.execute("UPDATE agent_actions SET status='failed',completed_at=clock_timestamp() WHERE id=%s", (UUID(ids["local_shop_list"][0]),))
            conn.commit()
        terminal = await registry.resolve("local_shop_list")[1].dispatch(_attempt("local_shop_list", ids["local_shop_list"], REQUESTS["local_shop_list"]), REQUESTS["local_shop_list"])
        assert terminal.outcome is ExecutionOutcome.FAILED
        with psycopg.connect(url) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute("UPDATE agent_actions SET org_id=%s WHERE id=%s", (OTHER_ORG, UUID(ids["local_warehouse_list"][0])))
            conn.commit()
        cross_tenant = await registry.resolve("local_warehouse_list")[1].dispatch(_attempt("local_warehouse_list", ids["local_warehouse_list"], REQUESTS["local_warehouse_list"]), REQUESTS["local_warehouse_list"])
        assert cross_tenant.outcome is ExecutionOutcome.FAILED
        with psycopg.connect(url) as conn:
            conn.execute("SET ROLE everydayai_owner")
            conn.execute("UPDATE agent_actions SET session_id=(SELECT id FROM agent_runtime_sessions WHERE conversation_id=%s) WHERE id=%s", (CONVERSATION, UUID(ids["local_supplier_list"][0])))
            conn.commit()
        wrong_conversation = await registry.resolve("local_supplier_list")[1].dispatch(_attempt("local_supplier_list", ids["local_supplier_list"], REQUESTS["local_supplier_list"]), REQUESTS["local_supplier_list"])
        assert wrong_conversation.outcome is ExecutionOutcome.FAILED
    finally:
        await client.close()


def test_225_apply_rollback_reapply_and_permissions(database) -> None:
    url = database
    with psycopg.connect(url) as conn:
        conn.execute(BUSINESS_BOOTSTRAP)
        migration = (ROOT / "migrations/225_01_agent_runtime_read_capability_rpcs.sql").read_text()
        rollback = (ROOT / "migrations/rollback/225_01_agent_runtime_read_capability_rpcs_rollback.sql").read_text()
        conn.execute(migration); conn.commit()
        assert conn.execute("SELECT rolinherit FROM pg_roles WHERE rolname='everydayai_agent_runtime_worker'").fetchone()[0] is False
        conn.execute(rollback); conn.commit()
        assert conn.execute("SELECT count(*) FROM pg_proc WHERE proname='read_agent_runtime_erp'").fetchone()[0] == 0
        conn.execute(migration); conn.commit()
        for role in ('everydayai_runtime','everydayai_worker','everydayai','everydayai_sync','everydayai_projection_worker','everydayai_authorization_worker','everydayai_sandbox_worker','everydayai_runtime_admin'):
            assert conn.execute("SELECT has_function_privilege(%s,(SELECT oid::regprocedure FROM pg_proc WHERE proname='read_agent_runtime_erp'),'EXECUTE')", (role,)).fetchone()[0] is False
        assert conn.execute("SELECT has_function_privilege('everydayai_agent_runtime_worker',(SELECT oid::regprocedure FROM pg_proc WHERE proname='read_agent_runtime_erp'),'EXECUTE')").fetchone()[0] is True
        for table in ('messages','knowledge_nodes','conversation_data_evidence','memory_atoms','conversation_artifacts','erp_products','erp_product_skus','erp_stock_status','erp_product_daily_stats','erp_product_platform_map','erp_shops','erp_warehouses','erp_suppliers','erp_document_items'):
            assert conn.execute("SELECT has_table_privilege('everydayai_agent_runtime_worker',%s,'SELECT')", (table,)).fetchone()[0] is False
            assert conn.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid=%s::regclass", (table,)).fetchone()[0] is True
