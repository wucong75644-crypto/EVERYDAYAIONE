"""Real PostgreSQL concurrency/rollback/RLS. Requires explicitly isolated DSN.

Run with CHAT_IMAGE_TEST_ADMIN_DSN pointing at the local temporary cluster.
Creates and removes a dedicated database. Never uses application DATABASE_URL.
"""
from concurrent.futures import ThreadPoolExecutor
import asyncio
from copy import deepcopy
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from services.handlers.chat_image_request import freeze_image_request

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def isolated_db():
    dsn = os.getenv("CHAT_IMAGE_TEST_ADMIN_DSN")
    if not dsn:
        pytest.skip("Explicit isolated PostgreSQL DSN required")
    # Restrict this destructive fixture to our Unix-socket temporary cluster.
    info = psycopg.conninfo.conninfo_to_dict(dsn)
    assert info.get("host") == "/private/tmp" and info.get("port") == "55439"
    name = "chat_image_test_" + uuid4().hex
    admin = psycopg.connect(dsn, autocommit=True)
    for role in ("everydayai", "everydayai_worker", "image_untrusted"):
        if not admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s",(role,)).fetchone():
            admin.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
    admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
    test_dsn = psycopg.conninfo.make_conninfo(dsn,dbname=name)
    with psycopg.connect(test_dsn) as connection:
        connection.execute((ROOT/"tests/fixtures/chat_image_lifecycle_schema.sql").read_text())
        connection.execute((ROOT/"migrations/040_atomic_refund_credits.sql").read_text())
        # Existing asset RPC, with only its unrelated FK targets reduced to IDs.
        # Explicit test grants do not prove deployed ownership/permissions.
        connection.execute("CREATE TABLE image_generations(id UUID PRIMARY KEY)")
        connection.execute("CREATE TABLE conversation_attachment_refs(id UUID PRIMARY KEY)")
        connection.execute((ROOT/"migrations/145_user_assets.sql").read_text())
        asset_rpc = connection.execute("SELECT oid::regprocedure::text FROM pg_proc WHERE proname='register_user_asset'").fetchone()[0]
        connection.execute(sql.SQL("GRANT EXECUTE ON FUNCTION {} TO everydayai").format(sql.SQL(asset_rpc)))
        connection.execute((ROOT/"migrations/256_skill_catalog.sql").read_text())
        connection.execute((ROOT/"migrations/257_skill_catalog_metadata.sql").read_text())
        connection.execute("ALTER TABLE skill_packages ADD COLUMN owner_user_id UUID")
        connection.execute((ROOT/"migrations/273_chat_image_lifecycle.sql").read_text())
        connection.execute((ROOT/"migrations/274_chat_image_settlement.sql").read_text())
        trial_schema=(ROOT/"migrations/266_skill_chat_creation_receipts.sql").read_text().split("CREATE TABLE public.skill_draft_trial_runs",1)[1].split("-- A user edit",1)[0]
        connection.execute("CREATE TABLE public.skill_draft_trial_runs"+trial_schema)
        connection.execute((ROOT/"migrations/275_chat_image_trials.sql").read_text())
        connection.execute((ROOT/"migrations/276_chat_image_snapshot_replay.sql").read_text())
    yield test_dsn
    admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    admin.close()


@pytest.fixture
def facts(isolated_db):
    user, parent, conv, token, turn, message = [str(uuid4()) for _ in range(6)]
    with psycopg.connect(isolated_db) as db:
        db.execute("INSERT INTO users(id,credits) VALUES (%s,100)",(user,))
        db.execute("INSERT INTO conversations(id,user_id) VALUES (%s,%s)",(conv,user))
        db.execute("INSERT INTO tasks(id,user_id,conversation_id,type,status,delivery_context,execution_token,turn_id,input_message_id,base_context_revision,request_params) VALUES (%s,%s,%s,'chat','running','{\"actor\":true}',%s,%s,%s,0,'{\"actor_checkpoint\":\"preserve\"}')",(parent,user,conv,token,turn,message))
    origin = {"parent_task_id":parent,"actor_user_id":user,"workspace_owner_id":user,
        "org_id":None,"context_scope":"user","conversation_id":conv,"turn_id":turn,
        "input_message_id":message,"base_context_revision":0,"tool_call_id":"call-1"}
    snapshot = freeze_image_request({"mode":"text_to_image","prompt":"exact"},[],origin=origin,max_requests=4,max_credits=100)
    return {"dsn":isolated_db,"user":user,"parent":parent,"token":token,"snapshot":snapshot}


def connection(facts, *, worker=False, user=None):
    db = psycopg.connect(psycopg.conninfo.make_conninfo(facts["dsn"],user=user or "everydayai"))
    db.execute("SELECT set_config('app.actor_user_id',%s,true),set_config('app.org_id',%s,true),set_config('app.access_kind',%s,true)",
        (facts["user"],facts.get("org") or "","worker" if worker else "runtime"))
    return db


def accept(facts, snapshot=None, token=None):
    with connection(facts) as db:
        return db.execute("SELECT accept_chat_image_request(%s,%s,%s)",
            (facts["parent"], token or facts["token"],Jsonb(snapshot or facts["snapshot"]))).fetchone()[0]


def claim(facts, task):
    with connection(facts,worker=True) as db:
        return db.execute("SELECT claim_chat_image_submission(%s,%s,60,%s)",(task,str(uuid4()),facts.get("org"))).fetchone()[0]


def worker_rpc(facts, name, *args):
    with connection(facts,worker=True) as db:
        return db.execute(sql.SQL("SELECT {}({})").format(sql.Identifier(name),
            sql.SQL(",").join(sql.Placeholder() for _ in args)),args).fetchone()[0]


def uncertain_task(facts):
    task=accept(facts)["task_id"]
    claim(facts,task)
    with psycopg.connect(facts["dsn"]) as db:
        token=db.execute("SELECT request_params->'_media_lifecycle_v1'->>'claim_token' FROM tasks WHERE id=%s",(task,)).fetchone()[0]
    assert worker_rpc(facts,"mark_chat_image_dispatch",task,token)["outcome"]=="dispatch"
    assert worker_rpc(facts,"mark_chat_image_uncertain",task,token,60)["outcome"]=="uncertain"
    return task,token


def publish(facts,task,*,platform=False,status="failed"):
    return worker_rpc(facts,"publish_chat_image_result",task,Jsonb([{"type":"image","failed":status!="completed"}]),status,"test",platform)


def expire_uncertainty(facts,task):
    with psycopg.connect(facts["dsn"]) as db:
        db.execute("UPDATE tasks SET request_params=jsonb_set(request_params,'{_media_lifecycle_v1,uncertain_deadline}',to_jsonb(NOW()-interval '1 second')) WHERE id=%s",(task,))


def cost_report(facts,*,page=1,page_size=20,admin=True):
    if admin:
        with psycopg.connect(facts["dsn"]) as db:
            db.execute("UPDATE users SET role='super_admin' WHERE id=%s",(facts["user"],))
    with connection(facts) as db:
        db.execute("SELECT set_config('app.access_kind','runtime_admin',true)")
        return db.execute("SELECT chat_image_platform_cost_report(NOW()-interval '1 day',NOW()+interval '1 second',%s,%s)",(page,page_size)).fetchone()[0]


def test_concurrent_platform_publication_refunds_and_records_once(facts):
    task,_=uncertain_task(facts)
    with pytest.raises(psycopg.Error,match="UNCERTAIN_NOT_DUE"):
        publish(facts,task,platform=True)
    expire_uncertainty(facts,task)
    with ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda _:publish(facts,task,platform=True),range(4)))
    assert sum(r["outcome"]=="published" for r in results)==1
    with psycopg.connect(facts["dsn"]) as db:
        assert db.execute("SELECT credits FROM users WHERE id=%s",(facts["user"],)).fetchone()[0]==100
        assert db.execute("SELECT count(*) FROM credits_history WHERE user_id=%s",(facts["user"],)).fetchone()[0]==1
        child=db.execute("SELECT status,request_params,credits_used FROM tasks WHERE id=%s",(task,)).fetchone()
        assert child[0]=="failed" and child[2]==0
        cost=child[1]["_media_platform_cost_v1"]
        assert cost["estimated_provider_credits"]==facts["snapshot"]["estimated_provider_credits"]
        assert cost["refunded_user_credits"]==facts["snapshot"]["estimated_credits"]
        assert cost["evidence"]=="unconfirmed"
        parent=db.execute("SELECT status,request_params FROM tasks WHERE id=%s",(facts["parent"],)).fetchone()
        assert parent[0]=="running" and parent[1]["actor_checkpoint"]=="preserve"
        assert db.execute("SELECT context_revision FROM conversations WHERE id=%s",(facts["snapshot"]["origin"]["conversation_id"],)).fetchone()[0]==1


def test_platform_refund_and_cost_roll_back_with_message_failure(facts):
    task,_=uncertain_task(facts)
    expire_uncertainty(facts,task)
    with psycopg.connect(facts["dsn"]) as db:
        db.execute("CREATE FUNCTION fail_publication() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected publication'; END $$")
        db.execute("CREATE TRIGGER inject_publication BEFORE UPDATE ON messages FOR EACH ROW EXECUTE FUNCTION fail_publication()")
    try:
        with pytest.raises(psycopg.Error,match="injected publication"):
            publish(facts,task,platform=True)
        with psycopg.connect(facts["dsn"]) as db:
            assert db.execute("SELECT credits FROM users WHERE id=%s",(facts["user"],)).fetchone()[0]==100-facts["snapshot"]["estimated_credits"]
            assert db.execute("SELECT status FROM credit_transactions WHERE task_id=%s",(task,)).fetchone()[0]=="pending"
            assert db.execute("SELECT request_params ? '_media_platform_cost_v1' FROM tasks WHERE id=%s",(task,)).fetchone()[0] is False
    finally:
        with psycopg.connect(facts["dsn"]) as db:
            db.execute("DROP TRIGGER inject_publication ON messages")
            db.execute("DROP FUNCTION fail_publication()")
    assert publish(facts,task,platform=True)["outcome"]=="published"


def test_unknown_submission_never_reclaims_or_resends(facts):
    task,token=uncertain_task(facts)
    assert claim(facts,task)["outcome"]=="not_queued"
    assert worker_rpc(facts,"mark_chat_image_dispatch",task,token)["outcome"]=="not_owned"
    assert worker_rpc(facts,"bind_chat_image_submission",task,token,"confirmed-external")["outcome"]=="accepted"
    assert worker_rpc(facts,"bind_chat_image_submission",task,token,"confirmed-external")["outcome"]=="accepted"
    assert worker_rpc(facts,"bind_chat_image_submission",task,token,"different-external")["outcome"]=="not_owned"


def test_stale_queue_failure_cannot_refund_a_paid_send(facts):
    task,token=uncertain_task(facts)
    assert worker_rpc(facts,"bind_chat_image_submission",task,token,"paid-external")["outcome"]=="accepted"
    assert worker_rpc(facts,"fail_chat_image_before_dispatch",task,"stale queue timeout","queued",None)["outcome"]=="state_changed"
    assert worker_rpc(facts,"fail_chat_image_before_dispatch",task,"stale lease","submitting",token)["outcome"]=="state_changed"
    with psycopg.connect(facts["dsn"]) as db:
        assert db.execute("SELECT request_params->'_media_lifecycle_v1'->>'phase',result FROM tasks WHERE id=%s",(task,)).fetchone()==("accepted",None)
        assert db.execute("SELECT status FROM credit_transactions WHERE task_id=%s",(task,)).fetchone()[0]=="pending"


def test_definite_success_wins_before_platform_refund(facts):
    task,_=uncertain_task(facts)
    expire_uncertainty(facts,task)
    assert worker_rpc(facts,"record_chat_image_provider_result",task,Jsonb({"status":"success","image_urls":["result"]}))["outcome"]=="settling"
    with pytest.raises(psycopg.Error,match="UNCERTAIN_NOT_DUE"):
        publish(facts,task,platform=True)
    assert publish(facts,task,status="completed")["outcome"]=="published"
    with psycopg.connect(facts["dsn"]) as db:
        assert db.execute("SELECT status FROM credit_transactions WHERE task_id=%s",(task,)).fetchone()[0]=="confirmed"
        assert db.execute("SELECT request_params ? '_media_platform_cost_v1' FROM tasks WHERE id=%s",(task,)).fetchone()[0] is False


def test_late_result_cannot_charge_or_overwrite_platform_refund(facts):
    task,token=uncertain_task(facts)
    expire_uncertainty(facts,task)
    publish(facts,task,platform=True)
    assert worker_rpc(facts,"bind_chat_image_submission",task,token,"late-external")["outcome"]=="not_owned"
    assert worker_rpc(facts,"record_chat_image_provider_result",task,Jsonb({"status":"success"}))["outcome"]=="replay"
    assert publish(facts,task,status="completed")["outcome"]=="replay"
    assert worker_rpc(facts,"ack_chat_image_delivery",task)["outcome"]=="delivered"
    assert worker_rpc(facts,"ack_chat_image_delivery",task)["outcome"]=="delivered"
    with psycopg.connect(facts["dsn"]) as db:
        assert db.execute("SELECT status,request_params->'_media_lifecycle_v1'->>'delivery_pending' FROM tasks WHERE id=%s",(task,)).fetchone()==("failed","false")


def test_cost_report_exact_totals_and_admin_only(facts):
    for index in range(4):
        facts["snapshot"]["origin"]["tool_call_id"]=str(index)
        task,_=uncertain_task(facts)
        expire_uncertainty(facts,task)
        publish(facts,task,platform=True)
    with pytest.raises(psycopg.Error,match="COST_ADMIN_DENIED"):
        cost_report(facts,admin=False)
    # One item per page, aggregates cover all records; filter to this user's
    # facts because the shared isolated DB contains earlier tests' records.
    with psycopg.connect(facts["dsn"]) as db:
        db.execute("UPDATE tasks SET completed_at=NOW()-interval '2 days' WHERE user_id<>%s",(facts["user"],))
    report=cost_report(facts,page_size=1)
    assert report["summary"]["count"]==4 and len(report["items"])==1
    assert report["summary"]["estimated_provider_credits"]==4*facts["snapshot"]["estimated_provider_credits"]
    assert report["summary"]["refunded_user_credits"]==4*facts["snapshot"]["estimated_credits"]
    assert cost_report(facts,page=2,page_size=1)["items"][0]["task_id"] != report["items"][0]["task_id"]


def test_queue_stop_races_claim_without_supplier_cancel(facts):
    task=accept(facts)["task_id"]
    with connection(facts) as db:
        assert db.execute("SELECT stop_queued_chat_image(%s)",(task,)).fetchone()[0]["outcome"]=="stopped"
    assert claim(facts,task)["outcome"]=="not_queued"
    publish(facts,task,status="cancelled")
    other=deepcopy(facts["snapshot"])
    other["origin"]["tool_call_id"]="second"
    accepted=accept(facts,other)["task_id"]
    claim(facts,accepted)
    with connection(facts) as db:
        assert db.execute("SELECT stop_queued_chat_image(%s)",(accepted,)).fetchone()[0]["outcome"]=="already_submitted"


@pytest.fixture
async def lifecycle(facts,monkeypatch,tmp_path):
    from unittest.mock import AsyncMock
    from redis.asyncio import Redis
    from core.config import Settings
    from core.local_db import LocalDBClient
    from core.db_scope import ScopedDatabaseClient, DatabaseScope, DatabaseAccessKind
    from core.redis import RedisClient
    from services.task_limit_service import TaskLimitService
    from services.handlers.chat_image_lifecycle import ChatImageLifecycle
    socket=os.getenv("CHAT_IMAGE_TEST_REDIS_SOCKET")
    if not socket:
        pytest.skip("Explicit isolated Redis socket required for runtime fault tests")
    assert socket=="/private/tmp/everydayai-chat-image-redis.sock"
    redis=Redis(unix_socket_path=socket,decode_responses=True)
    monkeypatch.setattr(RedisClient,"_instance",redis)
    limiter=TaskLimitService(redis)
    monkeypatch.setattr("api.deps.get_task_limit_service",AsyncMock(return_value=limiter))
    monkeypatch.setattr("services.websocket_manager.ws_manager.send_to_task_or_user",AsyncMock())
    settings=Settings(_env_file=None,database_url="postgresql://invalid/test",jwt_secret_key="isolated-test-key",
        file_workspace_root=str(tmp_path),chat_image_async_enabled=False)
    monkeypatch.setattr("core.config.get_settings",lambda:settings)
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(facts["dsn"],user="everydayai"),min_size=1,max_size=4)
    worker=ScopedDatabaseClient(raw,DatabaseScope(facts["user"],None,DatabaseAccessKind.WORKER))
    service=ChatImageLifecycle(worker,settings)
    yield service
    await redis.delete(limiter._global_key(facts["user"]),limiter._conversation_key(facts["user"],facts["snapshot"]["origin"]["conversation_id"]))
    await redis.aclose()
    raw.pool.close()


@pytest.mark.asyncio
async def test_runtime_two_workers_submit_once_with_acceptance_disabled(facts,lifecycle,monkeypatch):
    from unittest.mock import AsyncMock,Mock
    from services.adapters.base import ImageGenerateResult,TaskStatus
    task_id=accept(facts)["task_id"]
    task=await lifecycle.refresh({"id":task_id,"user_id":facts["user"],"org_id":None})
    adapter=Mock(generate=AsyncMock(return_value=ImageGenerateResult(task_id="single-external",status=TaskStatus.PENDING)),close=AsyncMock())
    monkeypatch.setattr("services.adapters.factory.create_image_adapter",lambda *a,**kw:adapter)
    await asyncio.gather(lifecycle.submit(task),lifecycle.submit(task))
    assert adapter.generate.await_count==1
    args=adapter.generate.await_args.kwargs
    assert args["wait_for_result"] is False and args["_chat_image_single_submit"] is True
    assert (await lifecycle.refresh(task))["external_task_id"]=="single-external"
    with psycopg.connect(facts["dsn"]) as db:
        assert db.execute("SELECT count(*) FROM credit_transactions WHERE task_id=%s",(task_id,)).fetchone()[0]==1


@pytest.mark.asyncio
@pytest.mark.parametrize("window",["lost_response","bind_failure"])
async def test_runtime_unknown_acknowledgement_never_resubmits(facts,lifecycle,monkeypatch,window):
    from unittest.mock import AsyncMock,Mock
    from services.adapters.base import ImageGenerateResult,TaskStatus
    task_id=accept(facts)["task_id"]
    task=await lifecycle.refresh({"id":task_id,"user_id":facts["user"],"org_id":None})
    adapter=Mock(generate=AsyncMock(return_value=ImageGenerateResult(task_id="external",status=TaskStatus.PENDING)),close=AsyncMock())
    if window=="lost_response":
        adapter.generate.side_effect=TimeoutError("sent but response lost")
    else:
        original=lifecycle.rpc
        async def injected_rpc(task,name,**params):
            if name=="bind_chat_image_submission":
                raise ConnectionError("DB acknowledgement unavailable")
            return await original(task,name,**params)
        monkeypatch.setattr(lifecycle,"rpc",injected_rpc)
    monkeypatch.setattr("services.adapters.factory.create_image_adapter",lambda *a,**kw:adapter)
    await lifecycle.submit(task)
    current=await lifecycle.refresh(task)
    assert current["request_params"]["_media_lifecycle_v1"]["phase"]=="uncertain"
    await lifecycle.advance(current)
    await lifecycle.submit(task)  # stale queued snapshot cannot claim again
    assert adapter.generate.await_count==1
    expire_uncertainty(facts,task_id)
    await lifecycle.advance(await lifecycle.refresh(task))
    current=await lifecycle.refresh(task)
    assert current["status"]=="failed"
    assert current["request_params"]["_media_platform_cost_v1"]["evidence"]=="unconfirmed"
    assert current["request_params"]["_media_lifecycle_v1"]["delivery_pending"] is False


@pytest.mark.asyncio
async def test_runtime_storage_and_delivery_recover_without_generation(facts,lifecycle,monkeypatch):
    from unittest.mock import AsyncMock,Mock
    from services.adapters.base import ImageGenerateResult,TaskStatus
    task_id,token=uncertain_task(facts)
    worker_rpc(facts,"bind_chat_image_submission",task_id,token,"result-external")
    task=await lifecycle.refresh({"id":task_id,"user_id":facts["user"],"org_id":None})
    saved={"kind":"image","url":"https://local.invalid/saved.png","workspace_path":"generated/saved.png"}
    from services.file_executor import FileExecutor
    from PIL import Image
    files=FileExecutor(lifecycle.settings.file_workspace_root,facts["user"],None)
    saved_path=files.resolve_safe_path("generated/saved.png"); saved_path.parent.mkdir(parents=True,exist_ok=True)
    Image.new("RGB",(4,4)).save(saved_path)
    storage=AsyncMock(side_effect=[[{"url":"https://provider.invalid/temporary.png"}],[saved]])
    provider=Mock()
    monkeypatch.setattr("services.file_upload.persist_media_urls_to_workspace",storage)
    monkeypatch.setattr("services.adapters.factory.create_image_adapter",provider)
    monkeypatch.setattr("services.assets.asset_registry.register_task_media_best_effort",lambda *a,**kw:[{"asset":{"id":str(uuid4())}}])
    result=ImageGenerateResult(task_id="result-external",status=TaskStatus.SUCCESS,image_urls=["https://provider.invalid/temporary.png"])
    with pytest.raises(RuntimeError,match="STORAGE_PENDING"):
        await lifecycle.record_provider_result(task,result)
    pending=await lifecycle.refresh(task)
    assert pending["status"]=="running" and pending["result"]["status"]=="success"
    # Simulate WS failure after atomic publication. Terminal state must remain
    # recoverable, without storage or provider repetition.
    ws=AsyncMock(side_effect=ConnectionError("delivery failed"))
    monkeypatch.setattr("services.websocket_manager.ws_manager.send_to_task_or_user",ws)
    with pytest.raises(ConnectionError):
        await lifecycle.advance(pending)
    terminal=await lifecycle.refresh(task)
    assert terminal["status"]=="completed" and terminal["request_params"]["_media_lifecycle_v1"]["delivery_pending"] is True
    ws.side_effect=None
    await lifecycle.advance(terminal)
    assert (await lifecycle.refresh(task))["request_params"]["_media_lifecycle_v1"]["delivery_pending"] is False
    assert storage.await_count==2
    provider.assert_not_called()


def test_bounded_work_scan_excludes_delivered_and_paginates(facts):
    ids=[]
    for index in range(3):
        facts["snapshot"]["origin"]["tool_call_id"]=str(index)
        ids.append(accept(facts)["task_id"])
    # Keep the time cursor focused on this fixture, independent of previous
    # tests. A bounded scan can advance past a stuck first item.
    with psycopg.connect(facts["dsn"]) as db:
        since=db.execute("SELECT min(created_at)-interval '1 microsecond' FROM tasks WHERE id=ANY(%s::uuid[])",(ids,)).fetchone()[0]
    first=worker_rpc(facts,"scan_chat_image_work",None,since,str(uuid4()),1)
    second=worker_rpc(facts,"scan_chat_image_work",None,first[0]["created_at"],first[0]["id"],1)
    assert len(first)==len(second)==1 and first[0]["id"]!=second[0]["id"]


@pytest.fixture
def trial_facts(facts):
    org,trial,change=[str(uuid4()) for _ in range(3)]
    conv=facts["snapshot"]["origin"]["conversation_id"]
    digest="a"*64
    with psycopg.connect(facts["dsn"]) as db:
        db.execute("INSERT INTO organizations(id) VALUES (%s)",(org,))
        db.execute("INSERT INTO org_members(org_id,user_id) VALUES (%s,%s)",(org,facts["user"]))
        db.execute("UPDATE conversations SET org_id=%s WHERE id=%s",(org,conv))
        db.execute("INSERT INTO change_sets(id,org_id,created_by,status,resource_type,revision,proposed_snapshot) VALUES (%s,%s,%s,'awaiting_approval','skill_draft',1,%s)",(change,org,facts["user"],Jsonb({"content_sha256":digest})))
        db.execute("INSERT INTO skill_draft_trial_runs(id,org_id,actor_user_id,change_set_id,idempotency_key,candidate_revision,content_sha256,mode,input_sha256,model_id,status) VALUES (%s,%s,%s,%s,%s,1,%s,'image',%s,%s,'running')",(trial,org,facts["user"],change,str(uuid4()),digest,digest,facts["snapshot"]["model"]))
    snapshot=freeze_image_request({"mode":"text_to_image","prompt":"  trial exact  "},[],max_requests=1,max_credits=100,
        origin={"destination":"skill_trial","trial_id":trial,"change_set_id":change,"candidate_revision":1,"content_sha256":digest,
            "actor_user_id":facts["user"],"workspace_owner_id":facts["user"],"org_id":org,"context_scope":"user",
            "conversation_id":conv,"base_context_revision":0,"input_message_id":""})
    return {**facts,"org":org,"trial":trial,"change":change,"trial_snapshot":snapshot}


def accept_trial(facts,snapshot=None):
    with connection(facts) as db:
        db.execute("SELECT set_config('app.access_kind','runtime_admin',true)")
        return db.execute("SELECT accept_chat_image_trial(%s,%s,%s,%s)",(facts["trial"],Jsonb(snapshot or facts["trial_snapshot"]),Jsonb({"mode":"image","output":"  trial exact  "}),facts["org"])).fetchone()[0]


def test_trial_accept_replays_one_task_without_chat_message(trial_facts):
    with ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda _:accept_trial(trial_facts),range(4)))
    assert len({result["task_id"] for result in results})==1
    assert sum(result["outcome"]=="accepted" for result in results)==1
    with psycopg.connect(trial_facts["dsn"]) as db:
        assert db.execute("SELECT count(*) FROM messages WHERE conversation_id=%s",(trial_facts["trial_snapshot"]["origin"]["conversation_id"],)).fetchone()[0]==0
        assert db.execute("SELECT assistant_message_id,turn_id,execution_token FROM tasks WHERE id=%s",(results[0]["task_id"],)).fetchone()==(None,None,None)


def test_trial_complete_after_candidate_edit_preserves_isolation(trial_facts):
    task=accept_trial(trial_facts)["task_id"]
    assert claim(trial_facts,task)["outcome"]=="claimed"
    with psycopg.connect(trial_facts["dsn"]) as db:
        db.execute("UPDATE change_sets SET revision=2,proposed_snapshot='{}' WHERE id=%s",(trial_facts["change"],))
    worker_rpc(trial_facts,"record_chat_image_provider_result",task,Jsonb({"status":"success","image_urls":["saved"]}),trial_facts["org"])
    worker_rpc(trial_facts,"publish_chat_image_result",task,Jsonb([{"type":"image","url":"saved"}]),"completed","",False,trial_facts["org"])
    with psycopg.connect(trial_facts["dsn"]) as db:
        result=db.execute("SELECT status,candidate_revision,result FROM skill_draft_trial_runs WHERE id=%s",(trial_facts["trial"],)).fetchone()
        assert result[0]=="completed" and result[1]==1 and result[2]["images"][0]["url"]=="saved"
        assert result[2]["output"]=="  trial exact  "
        assert db.execute("SELECT context_revision FROM conversations WHERE id=%s",(trial_facts["trial_snapshot"]["origin"]["conversation_id"],)).fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM messages WHERE conversation_id=%s",(trial_facts["trial_snapshot"]["origin"]["conversation_id"],)).fetchone()[0]==0


def test_trial_rls_rejects_other_actor_and_unrelated_worker(trial_facts):
    alien={**trial_facts,"user":str(uuid4())}
    with pytest.raises(psycopg.Error,match="TRIAL_DENIED"):
        accept_trial(alien)
    with connection(trial_facts,worker=True) as db:
        assert db.execute("SELECT id FROM skill_draft_trial_runs WHERE id=%s",(trial_facts["trial"],)).fetchone() is None
    accept_trial(trial_facts)
    with connection(trial_facts,worker=True) as db:
        assert db.execute("SELECT id FROM skill_draft_trial_runs WHERE id=%s",(trial_facts["trial"],)).fetchone() is not None
    with connection({**trial_facts,"org":str(uuid4())},worker=True) as db:
        assert db.execute("SELECT id FROM skill_draft_trial_runs WHERE id=%s",(trial_facts["trial"],)).fetchone() is None


@pytest.mark.asyncio
async def test_exact_history_selects_prompt_without_summary_guess(facts,monkeypatch):
    import hashlib,json
    from core.config import Settings
    from core.local_db import LocalDBClient
    from core.db_scope import ScopedDatabaseClient,DatabaseScope,DatabaseAccessKind
    from services.agent.tool_executor import ToolExecutor
    from services.handlers.chat_image_request import ChatImageInputResolver
    first,second=str(uuid4()),str(uuid4())
    prompt="  精确原文\nreference B  "
    conv=facts["snapshot"]["origin"]["conversation_id"]
    with psycopg.connect(facts["dsn"]) as db:
        db.execute("INSERT INTO messages(id,conversation_id,role,status,context_revision,content) VALUES (%s,%s,'assistant','completed',1,%s)",(first,conv,json.dumps([{"type":"text","text":"方案一\n"+prompt+"\n结尾"}],ensure_ascii=False)))
        db.execute("INSERT INTO messages(id,conversation_id,role,status,context_revision,content) VALUES (%s,%s,'assistant','completed',3,%s)",(second,conv,'[{"type":"text","text":"future"}]'))
        db.execute("UPDATE tasks SET base_context_revision=2 WHERE id=%s",(facts["parent"],))
    settings=Settings(_env_file=None,database_url="postgresql://invalid/test",jwt_secret_key="isolated-test-key",chat_image_async_enabled=True)
    monkeypatch.setattr("core.config.get_settings",lambda:settings)
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(facts["dsn"],user="everydayai"),min_size=1,max_size=2)
    try:
        owner=ToolExecutor(ScopedDatabaseClient(raw,DatabaseScope(facts["user"],None,DatabaseAccessKind.RUNTIME)),facts["user"],conv,task_id=facts["parent"])
        history=json.loads(await owner._get_conversation_context({"message_ids":[first],"text_exact":prompt}))
        selected=history["selected_prompt"]
        assert selected["prompt"]==prompt and selected["source_prompt"]["sha256"]==hashlib.sha256(prompt.encode()).hexdigest()
        resolver=ChatImageInputResolver(owner,base_revision=2,input_message_id="")
        source=resolver.source_prompt(selected["source_prompt"],prompt)
        assert source["end"]-source["start"]==len(prompt)
        with pytest.raises(PermissionError,match="REVISION_DENIED"):
            await owner._get_conversation_context({"message_ids":[second]})
        with pytest.raises(ValueError,match="AMBIGUOUS"):
            await owner._get_conversation_context({"message_ids":[first],"text_exact":"summary guess"})
    finally: raw.pool.close()


async def test_history_filters_failures_before_pagination_and_keeps_parent_marker(facts):
    import json
    from core.local_db import LocalDBClient
    from core.db_scope import ScopedDatabaseClient,DatabaseScope,DatabaseAccessKind
    from services.handlers.chat_context.history_loader import build_context_messages
    conv=facts["snapshot"]["origin"]["conversation_id"]
    with psycopg.connect(facts["dsn"]) as db:
        db.execute("INSERT INTO messages(id,conversation_id,role,content,status,context_revision,created_at) VALUES (%s,%s,'assistant',%s,'interrupted',1,NOW()-interval '1 hour')",(str(uuid4()),conv,json.dumps([{"type":"text","text":"parent partial"},{"type":"interrupt_marker","interrupted_at":"2026-10-04T06:00:00Z","reason":"user_cancel"}])))
        for _ in range(105):
            db.execute("INSERT INTO messages(id,conversation_id,role,content,status,context_revision,created_at) VALUES (%s,%s,'assistant','[{\"type\":\"text\",\"text\":\"unrelated failed chat\"}]','failed',2,NOW()-interval '30 minutes')",(str(uuid4()),conv))
        db.execute("INSERT INTO messages(id,conversation_id,role,content,status,context_revision,generation_params) VALUES (%s,%s,'assistant',%s,'failed',3,'{\"origin\":\"chat_image\"}')",(str(uuid4()),conv,json.dumps([{"type":"image","failed":True,"error":"cancelled image","task_id":"own-child"}])))
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(facts["dsn"],user="everydayai"),min_size=1,max_size=2)
    try:
        db=ScopedDatabaseClient(raw,DatabaseScope(facts["user"],None,DatabaseAccessKind.RUNTIME))
        history=await build_context_messages(db,conv,"new input",base_revision=3,strict=True)
        serialized=json.dumps(history,ensure_ascii=False)
        assert "parent partial" in serialized and "cancelled image" in serialized and "own-child" in serialized
        assert "unrelated failed chat" not in serialized
        assert any(message["role"]=="system" for message in history)
        older=await build_context_messages(db,conv,"new input",base_revision=1,strict=True)
        assert "own-child" not in json.dumps(older)
    finally: raw.pool.close()


def test_actual_image_skill_publish_assign_and_hash_in_isolated_catalog(facts,tmp_path):
    import hashlib
    from core.local_db import LocalDBClient
    from core.db_scope import DatabaseScope,DatabaseAccessKind
    from services.skills.contracts import PackageCreate,PublishRevision,SkillError,revision_path
    from services.skills.repository import SkillRepository
    from services.skills.catalog import SkillCatalog
    from core.config import Settings
    with psycopg.connect(facts["dsn"]) as db:
        org=str(uuid4()); db.execute("INSERT INTO organizations(id) VALUES (%s)",(org,))
    package_input=PackageCreate(skill_key="chat-image-orchestration",source="platform",scope_kind="platform")
    document=(ROOT.parent/"examples/skills/catalog/platform/chat-image-orchestration/v1/SKILL.md").read_text()
    body=document.split("\n---\n",1)[1]
    skill_root=tmp_path/"isolated-skills"
    target=skill_root/revision_path(package_input,"v1"); target.parent.mkdir(parents=True); target.write_text(document)
    publication=PublishRevision(revision="v1",content_sha256=hashlib.sha256(document.encode()).hexdigest(),body_sha256=hashlib.sha256(body.encode()).hexdigest())
    configured=Settings(_env_file=None,database_url="postgresql://invalid/test",jwt_secret_key="isolated-test-key",
        skill_catalog_enabled=True,skill_storage_root=str(skill_root),file_workspace_root=str(tmp_path/"workspace"))
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(facts["dsn"],user="everydayai"),min_size=1,max_size=2)
    try:
        platform=SkillCatalog(SkillRepository(raw.pool,DatabaseScope(facts["user"],None,DatabaseAccessKind.RUNTIME_ADMIN)),configured)
        catalog=SkillCatalog(SkillRepository(raw.pool,DatabaseScope(facts["user"],org,DatabaseAccessKind.RUNTIME_ADMIN)),configured)
        package=platform.create_package(package_input)
        revision=platform.publish_revision(package.id,publication)
        with pytest.raises(SkillError,match="ASSIGNMENT_DISABLED"):
            catalog.read_assigned_skill(package.id)
        catalog.set_assignment(package.id,revision.id,enabled=True)
        loaded=catalog.read_assigned_skill(package.id)
        assert loaded.body==body and loaded.content_sha256==publication.content_sha256
        assert set(loaded.catalog_metadata.allowed_tool_names)=={"generate_image","get_conversation_context","file_search"}
        catalog.set_assignment(package.id,revision.id,enabled=False)
        with pytest.raises(SkillError,match="ASSIGNMENT_DISABLED"):
            catalog.read_assigned_skill(package.id)
    finally: raw.pool.close()


def test_concurrent_accept_replays_one_task_without_charge(facts):
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _:accept(facts),range(4)))
    assert len({r["task_id"] for r in results}) == 1
    assert sum(r["outcome"]=="accepted" for r in results) == 1
    with psycopg.connect(facts["dsn"]) as db:
        assert db.execute("SELECT credits FROM users WHERE id=%s",(facts["user"],)).fetchone()[0] == 100
        params = db.execute("SELECT request_params FROM tasks WHERE id=%s",(facts["parent"],)).fetchone()[0]
        assert params["actor_checkpoint"] == "preserve"
        assert params["_media_budget_v1"]["requests"] == 1


def replay_snapshot(facts, source, request_id):
    from services.handlers.chat_image_request import canonical_hash
    snapshot=deepcopy(facts["snapshot"])
    snapshot.pop("request_hash")
    snapshot["origin"].pop("tool_call_id")
    snapshot["origin"].update(retry_of_task_id=source,retry_request_id=request_id)
    snapshot["source_task_id"]=source
    snapshot["request_hash"]=canonical_hash(snapshot)
    return snapshot


def replay_sql(facts,source,request_id,snapshot=None,*,allow_new=True):
    with connection(facts) as db:
        return db.execute("SELECT replay_chat_image_snapshot(%s,%s,%s,NULL,%s)",
            (source,request_id,Jsonb(snapshot or replay_snapshot(facts,source,request_id)),allow_new)).fetchone()[0]


def failed_source(facts):
    task=accept(facts)["task_id"]
    with connection(facts) as db:
        db.execute("SELECT stop_queued_chat_image(%s)",(task,))
    publish(facts,task,status="cancelled")
    with psycopg.connect(facts["dsn"]) as db:
        db.execute("UPDATE tasks SET status='completed' WHERE id=%s",(facts["parent"],))
    return task


def test_snapshot_retry_is_atomic_new_version_and_bounded(facts):
    source=failed_source(facts)
    request_id=str(uuid4())
    assert replay_sql(facts,source,request_id,allow_new=False)["outcome"]=="new_required"
    with ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda _:replay_sql(facts,source,request_id),range(4)))
    assert sum(r["outcome"]=="accepted" for r in results)==1
    assert len({r["task_id"] for r in results})==1
    for _ in range(3): replay_sql(facts,source,str(uuid4()))
    with pytest.raises(psycopg.Error,match="REPLAY_BUDGET_EXCEEDED"):
        replay_sql(facts,source,str(uuid4()))
    with psycopg.connect(facts["dsn"]) as db:
        old=db.execute("SELECT status,request_params,assistant_message_id FROM tasks WHERE id=%s",(source,)).fetchone()
        new=db.execute("SELECT request_params,turn_id,assistant_message_id FROM tasks WHERE id=%s",(results[0]["task_id"],)).fetchone()
        assert old[0]=="cancelled" and old[1]["_media_request_v1"]==facts["snapshot"]
        assert new[0]["_media_request_v1"]["prompt"]==facts["snapshot"]["prompt"]
        assert new[1] is None and new[2]!=old[2]
        assert db.execute("SELECT credits FROM users WHERE id=%s",(facts["user"],)).fetchone()[0]==100
        assert db.execute("SELECT count(*) FROM credit_transactions WHERE user_id=%s",(facts["user"],)).fetchone()[0]==0
    assert claim(facts,results[0]["task_id"])["outcome"]=="claimed"


def test_retry_rejects_changed_input_ambiguous_source_and_other_actor(facts):
    source=accept(facts)["task_id"]
    with pytest.raises(psycopg.Error,match="REPLAY_SOURCE_DENIED"):
        replay_sql(facts,source,str(uuid4()))
    worker_rpc(facts,"fail_chat_image_before_dispatch",source,"test","queued",None)
    publish(facts,source)
    request_id=str(uuid4())
    snapshot=replay_snapshot(facts,source,request_id); snapshot["prompt"]="new message must not replace original"
    with pytest.raises(psycopg.Error,match="REPLAY_INPUT_CHANGED"):
        replay_sql(facts,source,request_id,snapshot)
    alien={**facts,"user":str(uuid4())}
    with pytest.raises(psycopg.Error,match="REPLAY_SOURCE_DENIED"):
        replay_sql(alien,source,request_id)


async def test_real_controls_preserve_snapshot_and_recover_receipt_when_disabled(facts,monkeypatch):
    from core.config import Settings
    from core.local_db import LocalDBClient
    from core.db_scope import ScopedDatabaseClient,DatabaseScope,DatabaseAccessKind
    from services.handlers.chat_image_controls import ChatImageControls
    source=failed_source(facts)
    settings=Settings(_env_file=None,database_url="postgresql://invalid/test",jwt_secret_key="isolated-test-key",chat_image_async_enabled=True)
    monkeypatch.setattr("core.config.get_settings",lambda:settings)
    monkeypatch.setattr("services.handlers.chat_image_controls.get_settings",lambda:settings)
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(facts["dsn"],user="everydayai"),min_size=1,max_size=2)
    try:
        controls=ChatImageControls(ScopedDatabaseClient(raw,DatabaseScope(facts["user"],None,DatabaseAccessKind.RUNTIME)),facts["user"],None)
        request_id=str(uuid4()); first=await controls.replay(source,request_id)
        settings.chat_image_async_enabled=False
        again=await controls.replay(source,request_id)
        assert first["task_id"]==again["task_id"] and again["outcome"]=="replay"
        assert (await controls.details(source))["input"]==facts["snapshot"]
        await controls.feedback(source,"not_helpful")
        assert (await controls.details(source))["feedback"]["rating"]=="not_helpful"
        assert (await controls.stop(first["task_id"]))["outcome"]=="stopped"
        from core.exceptions import PermissionDeniedError
        with pytest.raises(PermissionDeniedError): await controls.replay(source,str(uuid4()))
    finally: raw.pool.close()


def test_same_call_changed_input_and_stale_fencing_rejected(facts):
    accept(facts)
    changed = deepcopy(facts["snapshot"])
    changed["prompt"]="different"
    with pytest.raises(psycopg.Error,match="CALL_CONFLICT"):
        accept(facts,changed)
    with pytest.raises(psycopg.Error,match="FENCING_LOST"):
        accept(facts,token=str(uuid4()))


def test_random_variants_cannot_bypass_concurrent_budget(facts):
    def attempt(i):
        snapshot=deepcopy(facts["snapshot"])
        snapshot["origin"]["tool_call_id"]=str(i)
        snapshot["variant_id"]=str(uuid4())
        try:
            return accept(facts,snapshot)["outcome"]
        except psycopg.Error as error:
            assert "BUDGET_EXCEEDED" in str(error)
            return "limited"
    with ThreadPoolExecutor(8) as pool:
        results=list(pool.map(attempt,range(8)))
    assert results.count("accepted")==4 and results.count("limited")==4


def test_two_workers_only_one_ledger_and_debit(facts):
    task=accept(facts)["task_id"]
    with ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda _:claim(facts,task),range(4)))
    assert sum(r["outcome"]=="claimed" for r in results)==1
    with psycopg.connect(facts["dsn"]) as db:
        cost=facts["snapshot"]["estimated_credits"]
        assert db.execute("SELECT credits FROM users WHERE id=%s",(facts["user"],)).fetchone()[0]==100-cost
        assert db.execute("SELECT count(*) FROM credit_transactions WHERE task_id=%s",(task,)).fetchone()[0]==1
        assert db.execute("SELECT expires_at FROM credit_transactions WHERE task_id=%s",(task,)).fetchone()[0] is None


def test_credit_insert_failure_rolls_back_balance_and_claim(facts):
    task=accept(facts)["task_id"]
    with psycopg.connect(facts["dsn"]) as db:
        db.execute("CREATE FUNCTION fail_credit_insert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected failure'; END $$")
        db.execute("CREATE TRIGGER inject_credit_failure BEFORE INSERT ON credit_transactions FOR EACH ROW EXECUTE FUNCTION fail_credit_insert()")
    try:
        with pytest.raises(psycopg.Error,match="injected failure"):
            claim(facts,task)
        with psycopg.connect(facts["dsn"]) as db:
            assert db.execute("SELECT credits FROM users WHERE id=%s",(facts["user"],)).fetchone()[0]==100
            assert db.execute("SELECT request_params->'_media_lifecycle_v1'->>'phase',credit_transaction_id FROM tasks WHERE id=%s",(task,)).fetchone()==("queued",None)
    finally:
        with psycopg.connect(facts["dsn"]) as db:
            db.execute("DROP TRIGGER inject_credit_failure ON credit_transactions")
            db.execute("DROP FUNCTION fail_credit_insert()")


def test_rls_and_untrusted_role_cannot_accept_foreign_task(facts):
    alien=deepcopy(facts)
    alien["user"]=str(uuid4())
    with pytest.raises(psycopg.Error,match="PARENT_DENIED"):
        accept(alien)
    with connection(facts,user="image_untrusted") as db:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("SELECT accept_chat_image_request(%s,%s,%s)",(facts["parent"],facts["token"],Jsonb(facts["snapshot"])))


@pytest.mark.parametrize("delivery", [None, {}, {"actor":False}])
def test_parent_must_be_proven_actor(facts, delivery):
    with psycopg.connect(facts["dsn"]) as db:
        db.execute("UPDATE tasks SET delivery_context=%s WHERE id=%s",(Jsonb(delivery) if delivery is not None else None,facts["parent"]))
    with pytest.raises(psycopg.Error,match="PARENT_DENIED"):
        accept(facts)


def test_insufficient_balance_keeps_queue_and_no_ledger(facts):
    task=accept(facts)["task_id"]
    with psycopg.connect(facts["dsn"]) as db:
        db.execute("UPDATE users SET credits=0 WHERE id=%s",(facts["user"],))
    assert claim(facts,task)["outcome"]=="insufficient_credits"
    with psycopg.connect(facts["dsn"]) as db:
        assert db.execute("SELECT count(*) FROM credit_transactions WHERE task_id=%s",(task,)).fetchone()[0]==0
        assert db.execute("SELECT request_params->'_media_lifecycle_v1'->>'phase' FROM tasks WHERE id=%s",(task,)).fetchone()[0]=="queued"


def test_worker_scope_is_mandatory_and_does_not_grant_table_access(facts):
    task=accept(facts)["task_id"]
    with connection(facts) as db:
        with pytest.raises(psycopg.Error,match="WORKER_DENIED"):
            db.execute("SELECT claim_chat_image_submission(%s,%s,60)",(task,str(uuid4())))
    # The RPC grant alone cannot bypass existing worker table/RLS privileges.
    with connection(facts,worker=True,user="everydayai_worker") as db:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("SELECT claim_chat_image_submission(%s,%s,60)",(task,str(uuid4())))


def test_accept_failure_rolls_back_message_task_and_budget(facts):
    with psycopg.connect(facts["dsn"]) as db:
        db.execute("CREATE FUNCTION fail_image_insert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.type='image' THEN RAISE EXCEPTION 'injected acceptance'; END IF; RETURN NEW; END $$")
        db.execute("CREATE TRIGGER inject_acceptance_failure BEFORE INSERT ON tasks FOR EACH ROW EXECUTE FUNCTION fail_image_insert()")
    try:
        with pytest.raises(psycopg.Error,match="injected acceptance"):
            accept(facts)
        with psycopg.connect(facts["dsn"]) as db:
            assert db.execute("SELECT count(*) FROM messages WHERE conversation_id=%s",(facts["snapshot"]["origin"]["conversation_id"],)).fetchone()[0]==0
            assert db.execute("SELECT request_params ? '_media_budget_v1' FROM tasks WHERE id=%s",(facts["parent"],)).fetchone()[0] is False
    finally:
        with psycopg.connect(facts["dsn"]) as db:
            db.execute("DROP TRIGGER inject_acceptance_failure ON tasks")
            db.execute("DROP FUNCTION fail_image_insert()")


@pytest.mark.asyncio
async def test_image_handler_accepts_without_native_prepare_or_provider(facts,monkeypatch,tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch
    from core.config import Settings
    from core.local_db import LocalDBClient
    from core.db_scope import ScopedDatabaseClient, DatabaseScope, DatabaseAccessKind
    from services.handlers.image_handler import ImageHandler
    from services.tools.dispatcher import _dispatch_call_id
    settings=Settings(_env_file=None,database_url="postgresql://invalid/test",jwt_secret_key="isolated-test-key",
        file_workspace_root=str(tmp_path),chat_image_async_enabled=True)
    monkeypatch.setattr("core.config.get_settings",lambda:settings)
    raw=LocalDBClient(psycopg.conninfo.make_conninfo(facts["dsn"],user="everydayai"),min_size=1,max_size=3)
    scoped=ScopedDatabaseClient(raw,DatabaseScope(facts["user"],None,DatabaseAccessKind.RUNTIME))
    owner=SimpleNamespace(db=scoped,user_id=facts["user"],workspace_user_id=facts["user"],
        org_id=None,task_id=facts["parent"],conversation_id=facts["snapshot"]["origin"]["conversation_id"],
        image_execution_token=facts["token"],context_scope="user",execution_mode="interactive",
        resource_manifest=None,cancellation_event=None)
    token=_dispatch_call_id.set("handler-call")
    try:
        with patch("services.skills.media.prepare_media_prompt") as prepare, patch("services.adapters.factory.create_image_adapter") as provider:
            result=await ImageHandler(scoped).accept_chat_image(owner,{"mode":"text_to_image","prompt":"  original  "})
            assert result["status"]=="submitted" and result["submission_state"]=="queued"
            assert result["task_id"] != facts["parent"]
            assert result["message_id"] != facts["snapshot"]["origin"]["input_message_id"]
            prepare.assert_not_called()
            provider.assert_not_called()
        with psycopg.connect(facts["dsn"]) as db:
            child=db.execute("SELECT request_params,turn_id,execution_token,credit_transaction_id FROM tasks WHERE id=%s",(result["task_id"],)).fetchone()
            assert child[0]["_media_request_v1"]["prompt"]=="  original  "
            assert child[1:]==(None,None,None)
    finally:
        _dispatch_call_id.reset(token)
        raw.pool.close()


def test_bad_supplier_output_refunds_once_and_records_platform_estimate(facts):
    task,_=uncertain_task(facts)
    worker_rpc(facts,"record_chat_image_provider_result",task,Jsonb({"status":"success","image_urls":["opaque-output"]}))
    assert publish(facts,task)["outcome"]=="published"
    assert publish(facts,task)["outcome"]=="replay"
    with psycopg.connect(facts["dsn"]) as db:
        assert db.execute("SELECT credits FROM users WHERE id=%s",(facts["user"],)).fetchone()[0]==100
        assert db.execute("SELECT status FROM credit_transactions WHERE task_id=%s",(task,)).fetchone()[0]=="refunded"
        cost=db.execute("SELECT request_params->'_media_platform_cost_v1' FROM tasks WHERE id=%s",(task,)).fetchone()[0]
        assert cost["reason"]=="image_output_contract_failure" and cost["evidence"]=="provider_success_unbilled"
        assert cost["estimated_provider_credits"]==facts["snapshot"]["estimated_provider_credits"]
        assert db.execute("SELECT count(*) FROM credits_history WHERE user_id=%s",(facts["user"],)).fetchone()[0]==1


@pytest.mark.parametrize("output",["opaque","invalid","missing","ordinary_missing","storage_error"])
async def test_runtime_transparency_failure_or_storage_recovery(facts,lifecycle,monkeypatch,output):
    from unittest.mock import AsyncMock,Mock
    from PIL import Image
    from services.handlers.chat_image_request import canonical_hash
    from services.file_executor import FileExecutor
    snapshot=deepcopy(facts["snapshot"])
    if output!="ordinary_missing": snapshot["background"]="transparent"
    snapshot["request_hash"]=canonical_hash({k:v for k,v in snapshot.items() if k!="request_hash"})
    task_id=accept(facts,snapshot)["task_id"]
    claim(facts,task_id)
    worker_rpc(facts,"record_chat_image_provider_result",task_id,Jsonb({"status":"success","image_urls":["provider-result"]}))
    files=FileExecutor(lifecycle.settings.file_workspace_root,facts["user"],None)
    path=files.resolve_safe_path("result.png")
    if output=="opaque": Image.new("RGB",(4,4)).save(path)
    elif output=="invalid": path.write_bytes(b"invalid provider image")
    with psycopg.connect(facts["dsn"]) as db:
        db.execute("UPDATE tasks SET result_data=%s WHERE id=%s",(Jsonb({"type":"image","workspace_path":"result.png","url":"saved"}),task_id))
    provider=Mock(); storage=AsyncMock()
    monkeypatch.setattr("services.adapters.factory.create_image_adapter",provider)
    monkeypatch.setattr("services.file_upload.persist_media_urls_to_workspace",storage)
    if output=="storage_error":
        Image.new("RGBA",(4,4),(10,20,30,0)).save(path)
        monkeypatch.setattr("services.handlers.chat_image_lifecycle.inspect_transparent_result",Mock(side_effect=OSError("temporary filesystem")))
    task=await lifecycle.refresh({"id":task_id,"user_id":facts["user"],"org_id":None})
    if output in {"missing","ordinary_missing","storage_error"}:
        with pytest.raises((ValueError,OSError)): await lifecycle.finish_recorded(task)
        current=await lifecycle.refresh(task)
        assert current["status"]=="running" and current["result"]["status"]=="success"
        assert (current["result_data"] is None) is (output in {"missing","ordinary_missing"})
    else:
        await lifecycle.finish_recorded(task)
        current=await lifecycle.refresh(task)
        assert current["status"]=="failed" and current["credits_used"]==0
        assert current["request_params"]["_media_platform_cost_v1"]["reason"]=="image_output_contract_failure"
    provider.assert_not_called(); storage.assert_not_called()


def test_out_of_order_partial_failure_preserves_parent_and_independent_bills(facts):
    first=accept(facts)["task_id"]
    second_snapshot=deepcopy(facts["snapshot"])
    second_snapshot["origin"]["tool_call_id"]="second-output"; second_snapshot["variant_id"]="v2"
    second=accept(facts,second_snapshot)["task_id"]
    claim(facts,first); claim(facts,second)
    worker_rpc(facts,"record_chat_image_provider_result",second,Jsonb({"status":"success","image_urls":["second"]}))
    publish(facts,second,status="completed")
    worker_rpc(facts,"record_chat_image_provider_result",first,Jsonb({"status":"failed","error":"first failed"}))
    publish(facts,first)
    with psycopg.connect(facts["dsn"]) as db:
        assert db.execute("SELECT status,request_params->>'actor_checkpoint' FROM tasks WHERE id=%s",(facts["parent"],)).fetchone()==("running","preserve")
        assert db.execute("SELECT credits FROM users WHERE id=%s",(facts["user"],)).fetchone()[0]==100-facts["snapshot"]["estimated_credits"]
        bills=db.execute("SELECT task_id,status FROM credit_transactions WHERE task_id=ANY(%s::uuid[])",([first,second],)).fetchall()
        assert {str(task):status for task,status in bills}=={first:"refunded",second:"confirmed"}
        revisions=db.execute("SELECT generation_params->>'task_id',context_revision FROM messages WHERE conversation_id=%s ORDER BY context_revision",(facts["snapshot"]["origin"]["conversation_id"],)).fetchall()
        assert revisions==[(second,1),(first,2)]


@pytest.mark.parametrize("sent",[False,True])
async def test_new_service_instance_recovers_expired_submission_without_resend(facts,lifecycle,monkeypatch,sent):
    from unittest.mock import Mock
    from services.handlers.chat_image_lifecycle import ChatImageLifecycle
    task=accept(facts)["task_id"]
    claim(facts,task)
    with psycopg.connect(facts["dsn"]) as db:
        token=db.execute("SELECT request_params->'_media_lifecycle_v1'->>'claim_token' FROM tasks WHERE id=%s",(task,)).fetchone()[0]
    if sent: worker_rpc(facts,"mark_chat_image_dispatch",task,token)
    with psycopg.connect(facts["dsn"]) as db:
        db.execute("UPDATE tasks SET request_params=jsonb_set(request_params,'{_media_lifecycle_v1,lease_expires_at}',to_jsonb(NOW()-interval '1 second')) WHERE id=%s",(task,))
    provider=Mock();monkeypatch.setattr("services.adapters.factory.create_image_adapter",provider)
    restarted=ChatImageLifecycle(lifecycle.db,lifecycle.settings)
    identity={"id":task,"user_id":facts["user"],"org_id":None}
    await restarted.advance(await restarted.refresh(identity))
    if sent:
        assert (await restarted.refresh(identity))["request_params"]["_media_lifecycle_v1"]["phase"]=="uncertain"
        expire_uncertainty(facts,task)
        await restarted.advance(await restarted.refresh(identity))
    current=await restarted.refresh(identity)
    assert current["status"]=="failed" and current["request_params"]["_media_lifecycle_v1"]["delivery_pending"] is False
    assert ("_media_platform_cost_v1" in current["request_params"]) is sent
    provider.assert_not_called()


async def test_isolated_tool_to_http_completion_and_snapshot_replay(facts,lifecycle,monkeypatch):
    """Real task/ledger/RLS, KIE HTTP codec, and application routes; no network.

    Auth identity is overridden. Storage writes a real isolated PNG and the
    existing asset RPC runs in PostgreSQL. WebSocket is a stub; no NAS/OSS or
    browser proof is claimed.
    """
    import hashlib
    import json
    from types import SimpleNamespace
    from unittest.mock import Mock
    import httpx
    from fastapi import FastAPI
    from PIL import Image
    from api.deps import get_org_context,get_scoped_db
    from api.routes.task import router
    from core.db_scope import ScopedDatabaseClient,DatabaseScope,DatabaseAccessKind
    from services.agent.tool_executor import ToolExecutor
    from services.adapters.base import ImageGenerateResult,TaskStatus
    from services.adapters.kie.client import KieClient
    from services.adapters.kie.image_adapter import KieImageAdapter
    from services.file_executor import FileExecutor
    from services.task_completion_service import TaskCompletionService
    from services.tools.dispatcher import _dispatch_call_id
    lifecycle.settings.chat_image_async_enabled=True
    monkeypatch.setattr("services.handlers.chat_image_controls.get_settings",lambda:lifecycle.settings)
    runtime_db=ScopedDatabaseClient(lifecycle.db,DatabaseScope(facts["user"],None,DatabaseAccessKind.RUNTIME))
    conversation=facts["snapshot"]["origin"]["conversation_id"]
    executor=ToolExecutor(db=runtime_db,user_id=facts["user"],conversation_id=conversation,org_id=None,
        workspace_user_id=facts["user"],task_id=facts["parent"],image_execution_token=facts["token"])
    requests=[]
    def provider_http(request):
        assert request.url.path=="/api/v1/jobs/createTask"
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={"code":200,"msg":"success","data":{"taskId":"isolated-external"}})
    client=KieClient("isolated-unused-key")
    client._client=httpx.AsyncClient(base_url="https://isolated.invalid",transport=httpx.MockTransport(provider_http),trust_env=False)
    monkeypatch.setattr(client,"_schedule_shadow_upload",Mock())
    monkeypatch.setattr("services.adapters.factory.create_image_adapter",lambda model,**kw:KieImageAdapter(client,model))
    files=FileExecutor(lifecycle.settings.file_workspace_root,facts["user"],None)
    owner_hash=hashlib.md5(facts["user"].encode(),usedforsecurity=False).hexdigest()[:8]
    saved_url=f"https://isolated.invalid/workspace/personal/{owner_hash}/result.png"
    monkeypatch.setattr("services.assets.asset_identity.configured_asset_hosts",lambda:frozenset({"isolated.invalid"}))
    async def save(urls,*args,**kwargs):
        assert urls==["https://isolated.invalid/provider.png"]
        Image.new("RGB",(4,4)).save(files.resolve_safe_path("result.png"))
        return [{"url":saved_url,"workspace_path":"result.png"}]
    monkeypatch.setattr("services.file_upload.persist_media_urls_to_workspace",save)
    app=FastAPI();app.include_router(router)
    app.dependency_overrides[get_org_context]=lambda:SimpleNamespace(user_id=facts["user"],org_id=None)
    app.dependency_overrides[get_scoped_db]=lambda:runtime_db
    token=_dispatch_call_id.set("end-to-end-single-call")
    try:
        receipt=(await executor._generate_image({"mode":"text_to_image","prompt":"  exact end-to-end prompt  "})).metadata
        assert receipt["completed"] is False and not requests
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://isolated-app") as browser:
            task_id=receipt["task_id"]
            detail=await browser.get(f"/tasks/{task_id}/image")
            assert detail.status_code==200 and detail.json()["submission_state"]=="queued"
            assert detail.json()["input"]["prompt"]=="  exact end-to-end prompt  "
            await lifecycle.submit(await lifecycle.refresh({"id":task_id,"user_id":facts["user"],"org_id":None}))
            assert len(requests)==1 and requests[0]["input"]["prompt"]=="  exact end-to-end prompt  "
            callback=ImageGenerateResult(task_id="isolated-external",status=TaskStatus.SUCCESS,image_urls=["https://isolated.invalid/provider.png"])
            completion=TaskCompletionService(lifecycle.db)
            assert await completion.process_result("isolated-external",callback)
            assert await completion.process_result("isolated-external",callback)
            detail=(await browser.get(f"/tasks/{task_id}/image")).json()
            assert detail["status"]=="completed" and detail["result"][0]["task_id"]==task_id
            asset_id=detail["result"][0]["asset_id"]
            assert asset_id and detail["result"][0]["url"]==saved_url
            preview=(await browser.post("/tasks/image/estimate",json={"mode":"text_to_image","image_count":2})).json()
            assert preview["total_credits"]==facts["snapshot"]["estimated_credits"]*2
            request_id=str(uuid4())
            version=await browser.post(f"/tasks/{task_id}/image/replay",json={"request_id":request_id})
            assert version.status_code==200 and version.json()["task_id"]!=task_id
            again=await browser.post(f"/tasks/{task_id}/image/replay",json={"request_id":request_id})
            assert again.json()["task_id"]==version.json()["task_id"]
            forged=await browser.post(f"/tasks/{task_id}/image/replay",json={"request_id":str(uuid4()),"prompt":"recent wrong user text"})
            assert forged.status_code==422 and len(requests)==1
        with psycopg.connect(facts["dsn"]) as db:
            assert db.execute("SELECT count(*) FROM credit_transactions WHERE user_id=%s",(facts["user"],)).fetchone()[0]==1
            assert db.execute("SELECT status FROM tasks WHERE id=%s",(facts["parent"],)).fetchone()[0]=="running"
            assert db.execute("SELECT count(*) FROM user_assets WHERE id=%s",(asset_id,)).fetchone()[0]==1
            refs=db.execute("SELECT prompt,metadata,source_message_id,source_task_id FROM user_asset_refs WHERE actor_user_id=%s",(facts["user"],)).fetchall()
            assert len(refs)==1 and refs[0][0]=="  exact end-to-end prompt  "
            assert refs[0][1]["parent_task_id"]==facts["parent"]
            assert refs[0][1]["request_hash"]==detail["input"]["request_hash"]
            assert str(refs[0][2])==receipt["message_id"] and str(refs[0][3])==task_id
    finally:
        _dispatch_call_id.reset(token)
        await client.close()
