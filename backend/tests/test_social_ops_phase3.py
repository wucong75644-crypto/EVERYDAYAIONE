"""Real isolated RLS/files/state machine; no model billing or platform publishing."""

import os
import io
from types import SimpleNamespace
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock
import pytest
from PIL import Image
from psycopg.types.json import Jsonb
from tests.test_social_ops_postgres import stores, worker, OWNER, ORG1
from services.social_ops import cards, publishing
from services.social_ops.repository import digest
from services.social_ops.contracts import PublishCreate, PackageCheck, PublishVerify
from services.agent.agent_reach.contracts import ReachError, ReachItem, ReachResult
from services.agent.agent_reach.connections import Connections

pytestmark = pytest.mark.skipif(
    not os.getenv("SOCIAL_OPS_TEST_DSN"), reason="dedicated local DB required"
)


@pytest.fixture
def package(stores, monkeypatch, tmp_path):
    from core.config import Settings

    s = stores
    settings = Settings(
        _env_file=None,
        database_url="postgresql://localhost/unused",
        jwt_secret_key="test-only",
        social_ops_enabled=True,
        agent_reach_enabled=True,
        agent_reach_platforms="xiaohongshu",
        agent_reach_write_actions="xiaohongshu:create_post",
        file_workspace_enabled=True,
        file_workspace_root=str(tmp_path),
        agent_reach_media_staging_dir=str(tmp_path / "staging"),
    )
    monkeypatch.setattr("core.config.get_settings", lambda: settings)
    monkeypatch.setattr(cards, "get_settings", lambda: settings)
    secrets = s.repo.secrets
    monkeypatch.setattr(Connections, "secrets", property(lambda self: secrets))
    w = worker(s)
    job, item = w.claim(s.job["id"])
    url = "https://www.xiaohongshu.com/explore/abc?xsec_token=fixture"
    w.settle(
        job,
        item,
        ReachResult(
            "xiaohongshu",
            "fixture",
            [ReachItem("样本", url, "真实快照正文", "snippet")],
        ).to_agent_result(),
    )
    job, item = w.claim(job["id"])
    w.settle(
        job,
        item,
        ReachResult(
            "xiaohongshu", "fixture", [ReachItem("样本", url, "真实快照正文")]
        ).to_agent_result(),
    )
    evidence = s.repo.sources(str(job["id"]))[0]
    text = {
        "title": "验收测试",
        "body": "基于有限样本整理。",
        "tags": ["手账"],
        "evidence_ids": [str(evidence["id"])],
        "assumptions": [],
    }
    with s.repo.cursor() as c:
        saved = s.repo.save_content(c, job["id"], text, {}, {})
        rendered = cards.render_revision(
            s.repo,
            c,
            str(saved["content_id"]),
            1,
            [
                {"role": "cover", "headline": "验收测试", "body": "有限样本"},
                {"role": "closing", "headline": "回顾", "body": "待核实事实与素材授权"},
            ],
            {
                "background": "#FFF8F0",
                "foreground": "#30343B",
                "accent": "#2563EB",
                "watermark": "验收测试",
            },
            {},
        )
    row = cards.content(s.repo, str(saved["content_id"]), 2)
    with s.repo.cursor() as c:
        c.execute(
            """INSERT INTO social_production_runs(org_id,owner_id,job_id,kind,input,stages,status,result)
            VALUES(%s,%s,%s,'check','{}','[]','completed',%s) RETURNING id""",
            (
                ORG1,
                OWNER,
                job["id"],
                Jsonb(
                    {
                        "content_id": str(row["id"]),
                        "revision": 2,
                        "semantic_stages": [{"output": {"hard_errors": []}}] * 2,
                    }
                ),
            ),
        )
        run = c.fetchone()["id"]
    check = publishing.check_package(
        s.repo, str(row["id"]), PackageCheck(expected_revision=2, semantic_run_id=run)
    )
    request = PublishCreate(
        content_id=row["id"],
        expected_revision=2,
        connection_id=s.connection["id"],
        check_id=check["id"],
        visibility="private",
    )
    return SimpleNamespace(
        s=s, row=row, request=request, check=check, settings=settings, run=run
    )


def test_real_png_layout_overflow_and_org_isolation(package):
    p = package
    for selected in p.row["data"]["assets"]:
        row, path = cards.asset(p.s.repo, selected["id"])
        with Image.open(path) as im:
            assert im.size == (1080, 1440) and im.format == "PNG"
        with pytest.raises(ReachError):
            cards.asset(p.s.foreign, selected["id"])
    with pytest.raises(ReachError, match="溢出"):
        cards.raster(
            {"role": "body", "headline": "过长", "body": "中" * 1500}, {}, 0, 2
        )
    assert not cards.validate_package(p.s.repo, p.row)["hard_errors"]


def test_intent_idempotency_concurrent_consumption_and_credential_change(package):
    p = package
    first = publishing.create_intent(p.s.repo, p.request)
    assert publishing.create_intent(p.s.repo, p.request)["id"] == first["id"]
    other = p.request.model_copy(update={"visibility": "public"})
    renewed = publishing.create_intent(p.s.repo, other)
    assert (
        renewed["id"] == first["id"]
        and renewed["approval_hash"] != first["approval_hash"]
    )
    with pytest.raises(ReachError):
        publishing.consume(p.s.repo, first["id"], first["approval_hash"])

    def consume_once(_):
        try:
            return publishing.consume(p.s.repo, first["id"], renewed["approval_hash"])[
                "id"
            ]
        except ReachError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(v is not None for v in pool.map(consume_once, range(2))) == 1
    with pytest.raises(ReachError):
        publishing.get_intent(p.s.foreign, first["id"])
    with p.s.repo.cursor() as c:
        c.execute(
            "UPDATE social_publish_intents SET updated_at=now()-interval '181 seconds' WHERE id=%s",
            (first["id"],),
        )
    assert publishing.get_intent(p.s.repo, first["id"])["state"] == "dispatching"
    with p.s.repo.cursor() as c:
        c.execute(
            "UPDATE social_publish_intents SET updated_at=now()-interval '211 seconds' WHERE id=%s",
            (first["id"],),
        )
    assert publishing.get_intent(p.s.repo, first["id"])["state"] == "uncertain"
    with pytest.raises(ReachError):
        publishing.consume(p.s.repo, first["id"], renewed["approval_hash"])


def test_content_asset_changes_invalidate_checks_and_confirmations(package):
    p = package
    intent = publishing.create_intent(p.s.repo, p.request)
    with p.s.repo.cursor() as c:
        p.s.repo.save_content(
            c,
            p.row["job_id"],
            {**p.row["data"], "title": "变化后的标题"},
            {},
            {},
            p.row["id"],
            2,
        )
    assert publishing.get_intent(p.s.repo, intent["id"])["state"] == "invalidated"
    with pytest.raises(ReachError):
        publishing.consume(p.s.repo, intent["id"], intent["approval_hash"])
    current = cards.content(p.s.repo, p.row["id"], 3)
    assert (
        "文案已变化，需要重新制作或确认图片版本"
        in cards.validate_package(p.s.repo, current)["hard_errors"]
    )
    item, path = cards.asset(p.s.repo, p.row["data"]["assets"][0]["id"])
    path.write_bytes(b"changed")
    with pytest.raises(ReachError, match="变化"):
        cards.asset(p.s.repo, item["id"])


@pytest.mark.parametrize("outcome", ["submitted", "uncertain", "denied"])
async def test_immediate_public_tool_confirmation_and_submitted_without_id(
    package, monkeypatch, outcome
):
    p = package
    s = p.s
    monkeypatch.setattr(
        "services.permissions.checker.PermissionChecker.check",
        AsyncMock(return_value=True),
    )

    import psycopg
    from core.local_db import LocalDBClient

    with psycopg.connect(s.dsn) as conn:
        conn.execute(
            "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS features jsonb DEFAULT '{}'::jsonb"
        )
    db = object.__new__(LocalDBClient)
    db._pool = s.pool  # Real fluent reads and real FORCE-RLS transaction scope.
    upstream = AsyncMock(
        return_value=ReachResult(
            "xiaohongshu",
            "fixture",
            status="partial",
            receipt={"stage": "backend_submitted", "remote_id": ""},
        )
    )
    if outcome == "uncertain":
        upstream.side_effect = ReachError("WRITE_UNCERTAIN", "模拟发送后结果未知")
    monkeypatch.setattr("services.agent.agent_reach.handler.write", upstream)
    intent = publishing.create_intent(s.repo, p.request)
    if outcome == "denied":
        p.settings.agent_reach_enabled = False
    saved = await publishing.confirm_intent(
        db, s.repo, str(intent["id"]), intent["approval_hash"]
    )
    if outcome == "denied":
        assert saved["state"] == "failed" and saved["error"].startswith(
            "TOOL_NOT_DISPATCHED"
        )
        assert not saved["operation_id"] and upstream.await_count == 0
        return
    assert saved["state"] == outcome and not saved["receipt"].get("remote_id")
    assert saved["operation_id"] and upstream.await_count == 1
    with pytest.raises(ReachError):
        await publishing.confirm_intent(
            db, s.repo, str(intent["id"]), intent["approval_hash"]
        )
    assert upstream.await_count == 1
    proof = PublishVerify(
        remote_id="a" * 24,
        url="https://www.xiaohongshu.com/explore/" + "a" * 24,
        note="测试人工核验依据，未实际发布",
    )
    verified = publishing.verify_intent(s.repo, str(intent["id"]), proof)
    assert (
        verified["state"] == "verified"
        and verified["verification"]["method"] == "manual"
    )


def test_credential_rotation_invalidates_approval(package):
    p = package
    intent = publishing.create_intent(p.s.repo, p.request)
    with p.s.repo.cursor(admin=True) as c:
        c.execute(
            "UPDATE reach_connections SET credential_version=credential_version+1 WHERE id=%s",
            (p.s.connection["id"],),
        )
    assert publishing.get_intent(p.s.repo, intent["id"])["state"] == "invalidated"
    with pytest.raises(ReachError):
        publishing.consume(p.s.repo, intent["id"], intent["approval_hash"])


async def test_cards_stage_request_is_idempotent_and_final_version_is_saved(
    package, monkeypatch
):
    from services.social_ops.production import create_run, ProductionWorker, STAGES
    from services.social_ops.contracts import Produce

    p = package
    binder = AsyncMock(return_value={"skills": [], "schema_version": 1})
    monkeypatch.setattr("services.social_ops.production.bind_scheduled_skills", binder)
    data = Produce(
        kind="cards",
        job_id=p.row["job_id"],
        content_id=p.row["id"],
        expected_revision=2,
        client_request_id=uuid4(),
        visual={},
        stages=[{"skill_id": k, "revision": "fixed"} for k in STAGES["cards"]],
        max_credits=2,
    )
    db = SimpleNamespace(pool=p.s.pool)
    run = await create_run(db, p.s.repo, data)
    replay = await create_run(db, p.s.repo, data)
    assert run["id"] == replay["id"] and binder.await_count == 2
    output = {
        "cards": [
            {"role": "cover", "headline": "封面", "body": "来自固定正文"},
            {"role": "closing", "headline": "回顾", "body": "明确未知数据"},
        ],
        "evidence": [],
        "notes": "",
    }
    pw = ProductionWorker(db, p.s.scope)
    for stage in range(2):
        current = pw.claim(run["id"])
        pw.settle(current, output, {"skill_revision": "fixed", "model": "substitute"})
    saved = cards.content(p.s.repo, p.row["id"], 3)
    assert len(saved["data"]["assets"]) == 2 and len(saved["manifest"]["stages"]) == 2
    assert not pw.claim(run["id"])
    assert (await create_run(db, p.s.repo, data))["id"] == run["id"]


def test_api_cards_idempotent_and_strict_scope(package, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core.database import get_db
    from api.deps import get_org_context
    from api.routes.social_ops import router

    p = package
    monkeypatch.setattr("api.routes.social_ops.get_settings", lambda: p.settings)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(pool=p.s.pool)
    app.dependency_overrides[get_org_context] = lambda: SimpleNamespace(
        user_id=OWNER, org_id=ORG1
    )
    with TestClient(app) as client:
        asset_id = p.row["data"]["assets"][0]["id"]
        image = client.get(f"/api/social-ops/assets/{asset_id}/preview")
        assert image.status_code == 200 and image.content[:8] == b"\x89PNG\r\n\x1a\n"
        data = {
            "client_request_id": str(uuid4()),
            "expected_revision": 2,
            "cards": p.row["data"]["cards"],
            "visual": p.row["data"]["visual"],
        }
        first = client.post(f"/api/social-ops/contents/{p.row['id']}/cards", json=data)
        assert first.status_code == 200
        assert (
            client.post(
                f"/api/social-ops/contents/{p.row['id']}/cards", json=data
            ).json()["revision"]
            == first.json()["revision"]
        )
        data["html"] = "<script>fetch()</script>"
        assert (
            client.post(
                f"/api/social-ops/contents/{p.row['id']}/cards", json=data
            ).status_code
            == 422
        )
        app.dependency_overrides[get_org_context] = lambda: SimpleNamespace(
            user_id="00000000-0000-0000-0000-000000000012",
            org_id="00000000-0000-0000-0000-000000000002",
        )
        assert (
            client.get(f"/api/social-ops/assets/{asset_id}/preview").status_code == 404
        )


async def test_layout_failure_preserves_paid_plan_for_manual_repair(
    package, monkeypatch
):
    from services.social_ops.production import create_run, ProductionWorker, STAGES
    from services.social_ops.contracts import Produce

    p = package
    monkeypatch.setattr(
        "services.social_ops.production.bind_scheduled_skills",
        AsyncMock(return_value={"skills": []}),
    )
    run = await create_run(
        SimpleNamespace(pool=p.s.pool),
        p.s.repo,
        Produce(
            kind="cards",
            client_request_id=uuid4(),
            job_id=p.row["job_id"],
            content_id=p.row["id"],
            expected_revision=2,
            stages=[{"skill_id": s, "revision": "fixed"} for s in STAGES["cards"]],
            max_credits=2,
        ),
    )
    pw = ProductionWorker(SimpleNamespace(pool=p.s.pool), p.s.scope)
    output = {
        "cards": [
            {"role": "cover", "headline": "封面", "body": "中" * 1500},
            {"role": "closing", "headline": "收尾", "body": ""},
        ],
        "evidence": [],
        "notes": "",
    }
    for _ in range(2):
        pw.settle(pw.claim(run["id"]), output, {"model": "substitute"})
    with p.s.repo.cursor() as c:
        c.execute(
            "SELECT status,outputs,result,error FROM social_production_runs WHERE id=%s",
            (run["id"],),
        )
        saved = c.fetchone()
    assert saved["status"] == "failed" and saved["error"] == "LAYOUT_OVERFLOW"
    assert len(saved["outputs"]) == 2 and saved["result"]["manual_repair"]
    assert cards.content(p.s.repo, p.row["id"])["current_revision"] == 2
    assert pw.claim(run["id"]) is None


@pytest.mark.parametrize(
    "state,owner,tools",
    [
        ("awaiting_confirmation", OWNER, {"agent_reach"}),
        ("dispatching", "other", {"agent_reach"}),
        ("dispatching", OWNER, {"agent_reach", "code_execute"}),
    ],
)
def test_trusted_nonchat_entrypoint_rejects_unconfirmed_or_expanded_scope(
    state, owner, tools
):
    from services.tools.runtime_context import _check_identity
    from services.tools.context import ToolContext

    class Q:
        def __init__(self, table):
            self.table = table

        def select(self, *a):
            return self

        def eq(self, *a):
            return self

        def maybe_single(self):
            return self

        def execute(self):
            return SimpleNamespace(
                data={
                    "status": "active",
                    "features": {},
                    "role": "owner",
                    "org_id": ORG1,
                    "owner_id": owner,
                    "approved_by": OWNER,
                    "state": state,
                }
            )

    executor = SimpleNamespace(db=SimpleNamespace(table=Q))
    ctx = ToolContext(
        actor_user_id=OWNER,
        workspace_owner_id=OWNER,
        org_id=ORG1,
        context_scope="user",
        personal_context_allowed=True,
        agent_domain="general",
        permission_mode="ask",
        execution_mode="interactive",
        entrypoint="social_ops_interactive",
        task_id=str(uuid4()),
        authorized_tool_names=frozenset(tools),
    )
    with pytest.raises(PermissionError):
        _check_identity(executor, ctx)


def test_semantic_check_must_match_current_version_and_blocks_hard_errors(package):
    p = package
    with p.s.repo.cursor() as c:
        c.execute(
            "UPDATE social_production_runs SET result=result || %s WHERE id=%s",
            (
                Jsonb(
                    {
                        "semantic_stages": [
                            {"output": {"hard_errors": ["事实缺少依据"]}}
                        ]
                        * 2
                    }
                ),
                p.run,
            ),
        )
    check = publishing.check_package(
        p.s.repo,
        str(p.row["id"]),
        PackageCheck(expected_revision=2, semantic_run_id=p.run),
    )
    assert (
        check["structural"]["hard_errors"]
        and check["structural"]["status"] == "blocked"
    )
    with pytest.raises(ReachError):
        publishing.create_intent(
            p.s.repo, p.request.model_copy(update={"check_id": check["id"]})
        )
    with p.s.repo.cursor() as c:
        p.s.repo.save_content(c, p.row["job_id"], p.row["data"], {}, {}, p.row["id"], 2)
    with pytest.raises(ReachError):
        publishing.check_package(
            p.s.repo,
            str(p.row["id"]),
            PackageCheck(expected_revision=3, semantic_run_id=p.run),
        )


def test_api_import_and_reorder_are_versioned_and_authorized(package, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core.database import get_db
    from api.deps import get_org_context
    from api.routes.social_ops import router
    from services.file_resources import FileTargetResolver

    p = package
    monkeypatch.setattr("api.routes.social_ops.get_settings", lambda: p.settings)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(pool=p.s.pool)
    app.dependency_overrides[get_org_context] = lambda: SimpleNamespace(
        user_id=OWNER, org_id=ORG1
    )
    owner = SimpleNamespace(
        user_id=OWNER, org_id=ORG1, execution_mode="interactive", context_scope="user"
    )
    _, path = cards.asset(p.s.repo, p.row["data"]["assets"][0]["id"])
    ref = FileTargetResolver(owner).reference(path)
    with TestClient(app) as client:
        base = f"/api/social-ops/contents/{p.row['id']}"
        replaced = client.post(
            base + "/media/import",
            json={
                "expected_revision": 2,
                "resource_ref": ref,
                "source": "自有原始图",
                "rights": "测试素材仅自用",
                "position": 0,
            },
        )
        assert replaced.status_code == 200 and replaced.json()["revision"] == 3
        ids = [a["id"] for a in replaced.json()["data"]["assets"]]
        sorted_result = client.post(
            base + "/media", json={"expected_revision": 3, "asset_ids": ids[::-1]}
        )
        assert (
            sorted_result.status_code == 200 and sorted_result.json()["revision"] == 4
        )
        assert [a["id"] for a in sorted_result.json()["data"]["assets"]] == ids[::-1]
        assert (
            client.post(
                base + "/media", json={"expected_revision": 3, "asset_ids": ids}
            ).status_code
            == 409
        )
        assert (
            client.post(
                base + "/media/import",
                json={
                    "expected_revision": 4,
                    "resource_ref": "fref1_invalid",
                    "source": "未知",
                    "rights": "未知",
                    "position": 0,
                },
            ).status_code
            == 403
        )
        assert cards.content(p.s.repo, p.row["id"])["current_revision"] == 4


def test_not_published_recovery_requires_new_check_and_preserves_attempt(package):
    from services.social_ops.contracts import PublishNotPublished

    p = package
    repo = p.s.repo
    old = publishing.create_intent(repo, p.request)
    data = PublishNotPublished(
        note="已核对同一测试账号全部笔记、审核中与未通过，结合日志确认在最终提交之前中断。",
        account_checked=True,
        all_statuses_checked=True,
        acknowledge_new_confirmation=True,
    )
    with pytest.raises(ReachError):
        publishing.resolve_not_published(repo, old["id"], data)
    with repo.cursor() as c:
        c.execute(
            "UPDATE social_publish_intents SET state='uncertain',updated_at=now()-interval '211 seconds',error='BUDGET_EXHAUSTED' WHERE id=%s",
            (old["id"],),
        )
    with pytest.raises(ReachError):
        publishing.resolve_not_published(p.s.foreign, old["id"], data)
    settled = publishing.resolve_not_published(repo, old["id"], data)
    assert (
        settled["state"] == "not_published" and settled["error"] == "BUDGET_EXHAUSTED"
    )
    assert settled["verification"]["method"] == "manual_not_published"
    assert publishing.resolve_not_published(repo, old["id"], data)["id"] == old["id"]
    with pytest.raises(ReachError, match="重新检查"):
        publishing.create_intent(repo, p.request)
    check = publishing.check_package(
        repo, str(p.row["id"]), PackageCheck(expected_revision=2, semantic_run_id=p.run)
    )
    fresh = p.request.model_copy(update={"check_id": check["id"]})
    renewed = publishing.create_intent(repo, fresh)
    assert renewed["id"] != old["id"] and renewed["state"] == "awaiting_confirmation"
    assert publishing.create_intent(repo, fresh)["id"] == renewed["id"]
    with pytest.raises(ReachError):
        publishing.consume(repo, old["id"], old["approval_hash"])
    publishing.consume(repo, renewed["id"], renewed["approval_hash"])
    with pytest.raises(ReachError):
        publishing.consume(repo, renewed["id"], renewed["approval_hash"])


@pytest.mark.parametrize(
    "state,receipt",
    [
        ("dispatching", {}),
        ("submitted", {}),
        ("verified", {}),
        ("uncertain", {"remote_id": "a" * 24}),
        ("uncertain", {"stage": "backend_submitted"}),
    ],
)
def test_recovery_rejects_active_or_submitted_evidence(package, state, receipt):
    from services.social_ops.contracts import PublishNotPublished

    p = package
    old = publishing.create_intent(p.s.repo, p.request)
    with p.s.repo.cursor() as c:
        c.execute(
            "UPDATE social_publish_intents SET state=%s,receipt=%s,updated_at=now()-interval '211 seconds' WHERE id=%s",
            (state, Jsonb(receipt), old["id"]),
        )
    data = PublishNotPublished(
        note="已核对同一测试账号全部笔记、审核中与未通过，结合日志确认在最终提交之前中断。",
        account_checked=True,
        all_statuses_checked=True,
        acknowledge_new_confirmation=True,
    )
    with pytest.raises(ReachError):
        publishing.resolve_not_published(p.s.repo, old["id"], data)
