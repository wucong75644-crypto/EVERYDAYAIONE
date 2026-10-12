"""Dedicated disposable fixture only; never reads DATABASE_URL."""

import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4
from datetime import datetime, timedelta, timezone
import psycopg
from psycopg_pool import ConnectionPool
from psycopg.types.json import Jsonb
import pytest
from core.db_scope import DatabaseScope, DatabaseAccessKind, SET_DATABASE_SCOPE_SQL
from services.agent.agent_reach.connections import Connections
from services.agent.agent_reach.access_budget import AccessBudgetService, account_key
from services.agent.agent_reach.contracts import (
    ReachError,
    ReachRequest,
    ReachItem,
    ReachResult,
)
from services.configuration.envelope import LocalKEKProvider
from services.configuration.material_service import SecretMaterialService
from services.social_ops.repository import Repository
from services.social_ops.research import ResearchWorker
from services.social_ops.production import ProductionWorker
from services.social_ops.contracts import (
    ProfileData,
    ProfileUpdate,
    ResearchCreate,
    BudgetConfig,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("SOCIAL_OPS_TEST_DSN"),
    reason="dedicated local social fixture not configured",
)
ORG1 = "00000000-0000-0000-0000-000000000001"
ORG2 = "00000000-0000-0000-0000-000000000002"
OWNER = "00000000-0000-0000-0000-000000000011"
OTHER = "00000000-0000-0000-0000-000000000012"


@pytest.fixture
def stores():
    dsn = os.environ["SOCIAL_OPS_TEST_DSN"]
    info = psycopg.conninfo.conninfo_to_dict(dsn)
    assert (
        info.get("host") == "/private/tmp"
        and info.get("port") == "55479"
        and info.get("dbname") == "social_phase12_test"
    )
    with psycopg.connect(dsn) as clean:
        clean.execute(
            "TRUNCATE reach_connections,social_profiles RESTART IDENTITY CASCADE"
        )
    pool = ConnectionPool(
        dsn, kwargs={"options": "-c role=everydayai_runtime"}, max_size=8
    )
    secrets = SecretMaterialService(
        LocalKEKProvider(current_version="test", keyring={"test": b"k" * 32})
    )
    scope = DatabaseScope(
        actor_user_id=OWNER, org_id=ORG1, access_kind=DatabaseAccessKind.RUNTIME
    )
    repo = Repository(pool, scope, secrets=secrets)
    foreign = Repository(
        pool,
        DatabaseScope(
            actor_user_id=OTHER, org_id=ORG2, access_kind=DatabaseAccessKind.RUNTIME
        ),
        secrets=secrets,
    )
    connection = repo.save_verified(
        "xiaohongshu", uuid4().hex, "Test", {"service_token": "fixture-only"}
    )
    # Normal connection loading uses KEK, so inject the same test secrets.
    profile = repo.save_profile(
        ProfileData(name="测试品牌", positioning="专业", audience="读者")
    )
    job = repo.create_job(
        ResearchCreate(
            client_request_id=uuid4(),
            connection_id=connection["id"],
            profile_id=profile["profile_id"],
            keywords=["研究"],
        )
    )
    yield SimpleNamespace(
        pool=pool,
        scope=scope,
        repo=repo,
        foreign=foreign,
        connection=connection,
        profile=profile,
        job=job,
        dsn=dsn,
    )
    pool.close()


def worker(s):
    w = ResearchWorker(SimpleNamespace(pool=s.pool), s.scope)
    w.repo = s.repo
    return w


@pytest.mark.parametrize("code,state", [
    ("AUTH_EXPIRED", "paused_auth"),
    ("ACCOUNT_MISMATCH", "paused_auth"),
    ("CAPTCHA_REQUIRED", "paused_platform"),
    ("RATE_LIMITED", "paused_platform"),
])
def test_isolated_account_failure_stops_research_until_manual_recovery(stores, code, state):
    s = stores
    w = worker(s)
    job, item = w.claim(s.job["id"])
    budget = AccessBudgetService(s.pool, s.scope, secrets=s.repo.secrets)
    request = ReachRequest(task="验收测试", platform="xiaohongshu", action="search", query="fixture")
    attempt = budget.reserve(s.connection, request)
    budget.settle(s.connection, attempt, code)
    w.settle(job, item, SimpleNamespace(status="error", error_message=code, metadata={}))
    assert s.repo.job(str(job["id"]))["status"] == state
    assert w.claim(job["id"]) is None
    with pytest.raises(ReachError, match="处理账号异常"):
        s.repo.transition(str(job["id"]), "resume")
    budget.recover(s.connection)
    s.repo.transition(str(job["id"]), "resume")
    assert w.claim(job["id"]) is not None
    s.repo.transition(str(job["id"]), "cancel")


def test_isolated_low_daily_budget_waits_without_losing_queue(stores):
    s = stores
    budget = AccessBudgetService(s.pool, s.scope, secrets=s.repo.secrets)
    budget.configure(s.connection, BudgetConfig(daily_limit=2, min_interval_seconds=1))
    request = ReachRequest(task="验收测试", platform="xiaohongshu", action="search", query="fixture")
    budget.reserve(s.connection, request)
    with pytest.raises(ReachError) as caught:
        budget.reserve(s.connection, request)
    assert caught.value.code == "ACCESS_BUDGET_WAIT"
    w = worker(s)
    job, item = w.claim(s.job["id"])
    w.settle(job, item, SimpleNamespace(status="error", error_message=caught.value.code,
        metadata={"next_eligible_at": caught.value.next_eligible_at}))
    assert s.repo.job(str(job["id"]))["status"] == "waiting_budget"
    assert w.claim(job["id"]) is None
    assert budget.status(s.connection)["consumed"] == 2
    assert not s.repo.sources(str(job["id"]))
    s.repo.transition(str(job["id"]), "pause")


def test_isolated_real_clock_due_occurrence_is_discovered_once(stores):
    """Shorten only this disposable DB deadline, never the production scheduler."""
    import time
    s = stores
    w = worker(s)
    job, item = w.claim(s.job["id"])
    w.settle(job, item, ReachResult("xiaohongshu", "fixture", []).to_agent_result())
    with s.repo.cursor() as c:
        c.execute("UPDATE social_research_jobs SET next_occurrence_at=now()+interval '1 second' WHERE id=%s", (job["id"],))
    assert w.claim(job["id"]) is None
    deadline = time.monotonic() + 5
    claimed = None
    while time.monotonic() < deadline and claimed is None:
        time.sleep(0.1)
        with psycopg.connect(s.dsn) as conn:
            conn.execute("SET LOCAL ROLE everydayai")
            conn.execute(SET_DATABASE_SCOPE_SQL, DatabaseScope(actor_user_id=None, org_id=None, access_kind=DatabaseAccessKind.WORKER).settings)
            due = conn.execute("SELECT * FROM social_ops_due()").fetchall()
        if any(str(r[2]) == str(job["id"]) for r in due):
            claimed = worker(s).claim(job["id"])
    assert claimed is not None
    assert claimed[0]["generation"] == 2
    assert worker(s).claim(job["id"]) is None
    s.repo.transition(str(job["id"]), "pause")
    assert worker(s).claim(job["id"]) is None


def test_rls_versions_idempotency_and_foreign_keys(stores):
    s = stores
    assert not s.foreign.profiles() and not s.foreign.jobs()
    with pytest.raises(ReachError):
        s.foreign.sources(str(s.job["id"]))
    with pytest.raises(ReachError):
        s.repo.save_profile(
            ProfileUpdate(name="n", positioning="p", audience="a", expected_revision=9),
            s.profile["profile_id"],
        )
    r = s.repo.save_profile(
        ProfileUpdate(name="new", positioning="p", audience="a", expected_revision=1),
        s.profile["profile_id"],
    )
    assert r["revision"] == 2
    request = ResearchCreate.model_validate(s.job["plan"])
    assert s.repo.create_job(request)["id"] == s.job["id"]
    with s.foreign.cursor() as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(
                "INSERT INTO social_profiles(org_id,name,created_by) VALUES(%s,%s,%s)",
                (ORG1, "x", OTHER),
            )
    with s.foreign.cursor() as c:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            c.execute(
                """INSERT INTO social_contents(org_id,job_id) VALUES(%s,%s)""",
                (ORG2, s.job["id"]),
            )
    # RLS itself rejects source reads, independent of repository filters.
    with s.foreign.cursor() as c:
        c.execute("SELECT * FROM social_research_jobs WHERE id=%s", (s.job["id"],))
        assert c.fetchall() == []


def test_atomic_budget_cooldown_daily_reset_and_breaker(stores):
    s = stores
    budget = AccessBudgetService(s.pool, s.scope, secrets=s.repo.secrets)
    budget.configure(s.connection, BudgetConfig(daily_limit=2, min_interval_seconds=1))
    req = ReachRequest(task="read", platform="xiaohongshu", action="search", query="q")

    def reserve(_):
        try:
            return budget.reserve(s.connection, req)
        except ReachError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(reserve, range(5)))
    assert results.count("ACCESS_BUDGET_WAIT") == 4
    assert budget.status(s.connection)["consumed"] == 2
    attempt = next(v for v in results if v != "ACCESS_BUDGET_WAIT")
    budget.settle(s.connection, attempt, "AUTH_EXPIRED")
    with pytest.raises(ReachError, match="暂停"):
        budget.reserve(s.connection, req)
    budget.recover(s.connection)
    # Expired day resets consumption while durable cooldown remains.
    with s.repo.cursor() as c:
        c.execute(
            "UPDATE reach_access_budgets SET day=day-1,next_allowed_at=now()-interval '1 second' WHERE account_key=%s",
            (account_key(s.connection),),
        )
    budget.reserve(s.connection, req)
    assert budget.status(s.connection)["consumed"] == 2


def test_crash_reclaim_fences_stale_results_and_deduplicates(stores):
    s = stores
    w = worker(s)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: w.claim(s.job["id"]), range(2)))
    assert sum(c is not None for c in claims) == 1
    job, item = next(c for c in claims if c)
    with s.repo.cursor() as c:
        c.execute(
            "UPDATE social_research_items SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
            (item["id"],),
        )
    newjob, newitem = w.claim(s.job["id"])
    assert newitem["lease_token"] != item["lease_token"]
    url = "https://www.xiaohongshu.com/explore/abc?xsec_token=hidden"
    result = ReachResult(
        "xiaohongshu",
        "fixture",
        [
            ReachItem("标题", url, "摘要", "snippet"),
            ReachItem("标题", url.replace("hidden", "other"), "摘要", "snippet"),
        ],
    ).to_agent_result()
    w.settle(job, item, result)
    assert not s.repo.sources(str(job["id"]))
    w.settle(newjob, newitem, result)
    assert len(s.repo.sources(str(job["id"]))) == 1
    assert "xsec_token" not in str(s.repo.sources(str(job["id"])))
    job, detail = w.claim(job["id"])
    assert detail["action"] == "read"
    full = ReachResult(
        "xiaohongshu", "fixture", [ReachItem("标题", url, "正文证据")]
    ).to_agent_result()
    w.settle(job, detail, full)
    assert s.repo.job(str(job["id"]))["status"] == "completed"
    assert len(s.repo.sources(str(job["id"]))) == 2
    # Reprocessing a saved result is a no-op.
    w.settle(job, detail, full)
    assert len(s.repo.sources(str(job["id"]))) == 2


def test_cancel_blocks_late_commit_pause_resume_retains_progress(stores):
    s = stores
    w = worker(s)
    job, item = w.claim(s.job["id"])
    s.repo.transition(str(job["id"]), "pause")
    assert w.claim(job["id"]) is None
    s.repo.transition(str(job["id"]), "resume")
    job, item = w.claim(job["id"])
    s.repo.transition(str(job["id"]), "cancel")
    w.settle(job, item, ReachResult("xiaohongshu", "fixture", []).to_agent_result())
    assert s.repo.job(str(job["id"]))["status"] == "cancelled"
    assert not s.repo.sources(str(job["id"]))


def test_worker_discovery_and_recurring_generation(stores):
    s = stores
    with s.pool.connection() as conn, conn.transaction(), conn.cursor() as c:
        c.execute(
            SET_DATABASE_SCOPE_SQL,
            DatabaseScope(
                actor_user_id=None, org_id=None, access_kind=DatabaseAccessKind.WORKER
            ).settings,
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM social_ops_due()")
    # Runtime role cannot invoke host discovery; host role only returns IDs.
    with psycopg.connect(s.dsn) as conn:
        conn.execute("SET LOCAL ROLE everydayai")
        conn.execute(
            SET_DATABASE_SCOPE_SQL,
            DatabaseScope(
                actor_user_id=None, org_id=None, access_kind=DatabaseAccessKind.WORKER
            ).settings,
        )
        rows = conn.execute("SELECT * FROM social_ops_due()").fetchall()
        assert any(str(r[2]) == str(s.job["id"]) for r in rows)
    with s.repo.cursor() as c:
        c.execute(
            "UPDATE social_research_jobs SET status='completed',next_occurrence_at=now()-interval '1 day' WHERE id=%s",
            (s.job["id"],),
        )
    job, item = worker(s).claim(s.job["id"])
    assert job["generation"] == 2
    assert s.repo.job(str(job["id"]))["generation"] == 2


def test_content_edit_conflict_preserves_history(stores):
    s = stores
    with s.repo.cursor() as c:
        revision = s.repo.save_content(c, s.job["id"], {"title": "one"}, {}, {})
    with s.repo.cursor() as c:
        s.repo.save_content(
            c, s.job["id"], {"title": "two"}, {}, {}, revision["content_id"], 1
        )
    with s.repo.cursor() as c:
        with pytest.raises(ReachError):
            s.repo.save_content(
                c, s.job["id"], {"title": "stale"}, {}, {}, revision["content_id"], 1
            )
    with s.repo.cursor() as c:
        c.execute(
            "SELECT revision,data FROM social_content_revisions WHERE content_id=%s ORDER BY revision",
            (revision["content_id"],),
        )
        rows = c.fetchall()
        assert [r["data"]["title"] for r in rows] == ["one", "two"]


async def test_production_persists_stages_and_versioned_drafts(stores, monkeypatch):
    from services.social_ops.production import create_run, STAGES
    from services.social_ops.contracts import Produce
    from unittest.mock import AsyncMock

    s = stores
    w = worker(s)
    job, item = w.claim(s.job["id"])
    url = "https://www.xiaohongshu.com/explore/abc?xsec_token=fixture"
    w.settle(
        job,
        item,
        ReachResult(
            "xiaohongshu", "fixture", [ReachItem("标题", url, "正文证据", "snippet")]
        ).to_agent_result(),
    )
    job, item = w.claim(job["id"])
    w.settle(
        job,
        item,
        ReachResult(
            "xiaohongshu", "fixture", [ReachItem("标题", url, "正文证据")]
        ).to_agent_result(),
    )
    sources = s.repo.sources(str(job["id"]))
    evidence = [{"snapshot_id": str(sources[0]["id"]), "quote": "正文证据"}]
    binder = AsyncMock(
        side_effect=lambda *a, **k: {
            "version": 1,
            "skills": [{"selection": k["selections"][0], "hash": "fixed"}],
        }
    )
    monkeypatch.setattr("services.social_ops.production.bind_scheduled_skills", binder)
    request = Produce(
        kind="plan",
        job_id=job["id"],
        stages=[{"skill_id": k, "revision": "fixed-v1"} for k in STAGES["plan"]],
        max_credits=20,
    )
    run = await create_run(SimpleNamespace(pool=s.pool), s.repo, request)
    pw = ProductionWorker(SimpleNamespace(pool=s.pool), s.scope)
    pw.repo = s.repo
    output = {
        "topics": [
            {
                "title": "选题",
                "audience": "读者",
                "angle": "具体角度",
                "evidence": evidence,
                "unknowns": [],
                "decision": "adopt",
                "planned_date": None,
                "format": "图文",
                "required_materials": ["自有图片"],
            }
        ],
        "analysis": "来自样本的分析",
        "missing_inputs": ["图片"],
    }
    for i in range(4):
        current = pw.claim(run["id"])
        assert current["stage_index"] == i
        pw.settle(current, output, {"stage": i, "model": "fixture"})
    with s.repo.cursor() as c:
        c.execute("SELECT * FROM social_production_runs WHERE id=%s", (run["id"],))
        saved = c.fetchone()
        assert saved["status"] == "completed" and len(saved["outputs"]) == 4
        c.execute("SELECT * FROM social_topic_candidates WHERE run_id=%s", (run["id"],))
        topic = c.fetchone()
        assert topic["data"]["evidence"] == evidence
    assert pw.claim(run["id"]) is None
    request = Produce(
        kind="draft",
        job_id=job["id"],
        topic="选题",
        stages=[{"skill_id": STAGES["draft"][0], "revision": "fixed-v1"}],
    )
    run = await create_run(SimpleNamespace(pool=s.pool), s.repo, request)
    current = pw.claim(run["id"])
    draft = {
        "title": "标题",
        "body": "正文",
        "tags": ["标签"],
        "evidence": evidence,
        "assumptions": [],
    }
    pw.settle(current, draft, {"model": "fixture", "snapshot": "fixed"})
    saved = s.repo.contents(str(job["id"]))[0]
    assert saved["current_revision"] == 1 and saved["checks"]["status"] == "needs_media"
    assert saved["manifest"]["stages"][0]["snapshot"] == "fixed"
    assert binder.await_count == 5


def test_account_time_window_comment_cap_and_midnight_budget(stores):
    s = stores
    b = AccessBudgetService(s.pool, s.scope, secrets=s.repo.secrets)
    local_hour = datetime.now(timezone.utc).hour
    start = (local_hour + 2) % 24
    end = (start + 1) % 24 or 24
    b.configure(
        s.connection,
        BudgetConfig(
            daily_limit=100,
            min_interval_seconds=1,
            timezone="UTC",
            allowed_start_hour=start,
            allowed_end_hour=end,
            max_comment_pages=1,
        ),
    )
    req = ReachRequest(task="q", platform="xiaohongshu", action="search", query="q")
    with pytest.raises(ReachError) as e:
        b.reserve(s.connection, req)
    assert e.value.code == "ACCESS_BUDGET_WAIT" and e.value.next_eligible_at
    with pytest.raises(ReachError) as e:
        b.reserve(
            s.connection,
            ReachRequest(
                task="c",
                platform="xiaohongshu",
                action="read_comments",
                url="https://www.xiaohongshu.com/explore/abc?xsec_token=x",
                page=2,
            ),
        )
    assert e.value.code == "COMMENT_PAGE_LIMIT"
    assert b.status(s.connection)["consumed"] == 0


def test_production_interruption_keeps_outputs_and_cost_reservations(stores):
    s = stores
    with s.repo.cursor() as c:
        c.execute(
            """INSERT INTO social_production_runs(org_id,owner_id,job_id,kind,input,stages)
            VALUES(%s,%s,%s,'plan',%s,%s) RETURNING id""",
            (
                ORG1,
                OWNER,
                s.job["id"],
                Jsonb({"request": {"max_credits": 4}, "spent_reservations": 0}),
                Jsonb([{}] * 4),
            ),
        )
        id = c.fetchone()["id"]
    pw = ProductionWorker(SimpleNamespace(pool=s.pool), s.scope)
    first = pw.claim(id)
    assert first["input"]["spent_reservations"] == 1
    with s.repo.cursor() as c:
        c.execute(
            "UPDATE social_production_runs SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
            (id,),
        )
    assert pw.claim(id) is None
    with s.repo.cursor() as c:
        c.execute(
            "SELECT status,error,stage_index,input FROM social_production_runs WHERE id=%s",
            (id,),
        )
        r = c.fetchone()
        assert (
            r["status"] == "needs_input"
            and r["stage_index"] == 0
            and r["input"]["spent_reservations"] == 1
        )
        c.execute(
            "UPDATE social_production_runs SET status='queued' WHERE id=%s", (id,)
        )
    second = pw.claim(id)
    assert second["input"]["spent_reservations"] == 2
    pw.settle(first, {}, {})
    assert second["lease_token"] != first["lease_token"]


async def test_tick_uses_real_public_tool_policy_and_handler(stores, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from core.config import Settings

    s = stores
    config = Settings(
        _env_file=None,
        database_url="postgresql://localhost/unused",
        jwt_secret_key="test-only",
        agent_reach_enabled=True,
        social_ops_enabled=True,
        agent_reach_platforms="xiaohongshu",
    )
    monkeypatch.setattr("core.config.get_settings", lambda: config)
    secrets = s.repo.secrets
    monkeypatch.setattr(Connections, "secrets", property(lambda self: secrets))
    monkeypatch.setattr(
        "services.permissions.checker.PermissionChecker.check",
        AsyncMock(return_value=True),
    )
    upstream = AsyncMock(
        return_value=ReachResult(
            "xiaohongshu",
            "fixture",
            [
                ReachItem(
                    "标题",
                    "https://www.xiaohongshu.com/explore/abc?xsec_token=secret",
                    "正文证据",
                    "snippet",
                )
            ],
        ).to_agent_result()
    )
    monkeypatch.setattr("services.agent.agent_reach.handler.execute", upstream)
    db = MagicMock(pool=s.pool)
    db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = SimpleNamespace(
        data={"status": "active", "features": {}, "role": "owner"}
    )
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = SimpleNamespace(
        data={"status": "active", "role": "owner"}
    )
    w = ResearchWorker(db, s.scope)
    await w.tick(s.job["id"])
    upstream.assert_awaited_once()
    assert len(s.repo.sources(str(s.job["id"]))) == 1
    assert AccessBudgetService(s.pool, s.scope).status(s.connection)["consumed"] == 2
    await w.tick(s.job["id"])
    assert upstream.await_count == 1
    assert s.repo.job(str(s.job["id"]))["status"] == "waiting_budget"


async def test_scoped_api_profile_conflicts_and_sources(stores, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from core.config import Settings
    from core.database import get_db
    from api.deps import get_org_context
    from api.routes.social_ops import router

    s = stores
    config = Settings(
        _env_file=None,
        database_url="postgresql://localhost/unused",
        jwt_secret_key="test-only",
        social_ops_enabled=True,
    )
    monkeypatch.setattr("core.config.get_settings", lambda: config)
    monkeypatch.setattr("api.routes.social_ops.get_settings", lambda: config)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(pool=s.pool)
    app.dependency_overrides[get_org_context] = lambda: SimpleNamespace(
        user_id=OWNER, org_id=ORG1
    )
    with TestClient(app) as client:
        response = client.get("/api/social-ops/profiles")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        response = client.patch(
            "/api/social-ops/profiles/" + str(s.profile["profile_id"]),
            json={
                "name": "n",
                "positioning": "p",
                "audience": "a",
                "expected_revision": 99,
            },
        )
        assert response.status_code == 409
        response = client.post(
            "/api/social-ops/profiles",
            json={"name": "n", "positioning": "p", "audience": "a", "org_id": ORG2},
        )
        assert response.status_code == 422
        assert (
            client.get("/api/social-ops/research-jobs/" + str(s.job["id"])).status_code
            == 200
        )
        app.dependency_overrides[get_org_context] = lambda: SimpleNamespace(
            user_id=OTHER, org_id=ORG2
        )
        assert (
            client.get(
                "/api/social-ops/research-jobs/" + str(s.job["id"]) + "/sources"
            ).status_code
            == 404
        )
        assert client.get("/api/social-ops/profiles").json() == []


def test_revoked_creator_is_paused_by_host_discovery(stores):
    s = stores
    with psycopg.connect(s.dsn) as conn:
        conn.execute(
            "UPDATE org_members SET status='inactive' WHERE org_id=%s AND user_id=%s",
            (ORG1, OWNER),
        )
    try:
        with psycopg.connect(s.dsn) as conn:
            conn.execute("SET LOCAL ROLE everydayai")
            conn.execute(
                SET_DATABASE_SCOPE_SQL,
                DatabaseScope(
                    actor_user_id=None,
                    org_id=None,
                    access_kind=DatabaseAccessKind.WORKER,
                ).settings,
            )
            conn.execute("SELECT * FROM social_ops_due()").fetchall()
        with psycopg.connect(s.dsn) as conn:
            row = conn.execute(
                "SELECT status,last_error FROM social_research_jobs WHERE id=%s",
                (s.job["id"],),
            ).fetchone()
            assert row == ("paused_auth", "CREATOR_ACCESS_REVOKED")
    finally:
        with psycopg.connect(s.dsn) as conn:
            conn.execute(
                "UPDATE org_members SET status='active' WHERE org_id=%s AND user_id=%s",
                (ORG1, OWNER),
            )


async def test_source_paging_keeps_full_text_and_historical_draft_evidence(
    stores, monkeypatch
):
    from unittest.mock import AsyncMock
    from services.social_ops.production import create_run, STAGES
    from services.social_ops.contracts import Produce

    s = stores
    with s.repo.cursor() as c:
        c.execute(
            "SELECT id FROM social_research_items WHERE job_id=%s LIMIT 1",
            (s.job["id"],),
        )
        item = c.fetchone()["id"]
        c.execute(
            """INSERT INTO social_source_snapshots(org_id,job_id,item_id,generation,source_key,source_url,title,body,body_hash,coverage,adapter_version,fetched_at)
        VALUES(%s,%s,%s,1,'old-full','https://www.xiaohongshu.com/explore/test','正文','完整正文证据','hash','{"content_kind":"full_text"}','fixture',now()-interval '1 day') RETURNING id""",
            (ORG1, s.job["id"], item),
        )
        full_id = c.fetchone()["id"]
        c.execute(
            """INSERT INTO social_source_snapshots(org_id,job_id,item_id,generation,source_key,source_url,title,body,body_hash,coverage,adapter_version)
        SELECT %s,%s,%s,1,'comment-'||n,'https://www.xiaohongshu.com/explore/test','评论','评论需求证据','hash','{"content_kind":"comment"}','fixture' FROM generate_series(1,600) n""",
            (ORG1, s.job["id"], item),
        )
    assert s.repo.job(str(s.job["id"]))["source_count"] == 601
    first = s.repo.sources(str(s.job["id"]), limit=100)
    second = s.repo.sources(str(s.job["id"]), limit=100, offset=100)
    assert not {r["id"] for r in first} & {r["id"] for r in second}
    assert s.repo.snapshot(str(full_id))["body"] == "完整正文证据"
    with pytest.raises(ReachError):
        s.foreign.snapshot(str(full_id))
    monkeypatch.setattr(
        "services.social_ops.production.bind_scheduled_skills",
        AsyncMock(return_value={"version": 1, "skills": []}),
    )
    request = Produce(
        kind="plan",
        job_id=s.job["id"],
        stages=[{"skill_id": k, "revision": "fixture"} for k in STAGES["plan"]],
    )
    run = await create_run(SimpleNamespace(pool=s.pool), s.repo, request)
    with s.repo.cursor() as c:
        c.execute("SELECT input FROM social_production_runs WHERE id=%s", (run["id"],))
        payload = c.fetchone()["input"]
        assert any(r["id"] == str(full_id) for r in payload["sources"])
        assert (
            payload["coverage"]["available"] == 601
            and payload["coverage"]["input_subset"]
        )
        revision = s.repo.save_content(
            c,
            s.job["id"],
            {"title": "旧草稿", "body": "正文", "evidence_ids": [str(full_id)]},
            {},
            {},
        )
        c.execute(
            "UPDATE social_research_jobs SET generation=2 WHERE id=%s", (s.job["id"],)
        )
    request = Produce(
        kind="polish",
        job_id=s.job["id"],
        content_id=revision["content_id"],
        expected_revision=1,
        stages=[{"skill_id": STAGES["polish"][0], "revision": "fixture"}],
    )
    run = await create_run(SimpleNamespace(pool=s.pool), s.repo, request)
    with s.repo.cursor() as c:
        c.execute("SELECT input FROM social_production_runs WHERE id=%s", (run["id"],))
        payload = c.fetchone()["input"]
        assert [r["id"] for r in payload["sources"]] == [str(full_id)]


def test_known_not_sent_budget_release_is_idempotent(stores):
    s = stores
    b = AccessBudgetService(s.pool, s.scope)
    request = ReachRequest(task="q", platform="xiaohongshu", action="search", query="q")
    attempt = b.reserve(s.connection, request)
    assert b.status(s.connection)["consumed"] == 2
    b.release_not_sent(attempt)
    b.release_not_sent(attempt)
    assert b.status(s.connection)["consumed"] == 0
