"""Real isolated PostgreSQL and canonical tools; no platform writes/model fees."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import psycopg
import pytest
from tests.test_social_ops_phase3 import package
from tests.test_social_ops_postgres import stores
from services.social_ops import publishing, publish_worker as pw, cards
from services.social_ops.contracts import PublishSchedule
from services.agent.agent_reach.contracts import ReachError, ReachResult
from core.local_db import LocalDBClient
from core.db_scope import DatabaseScope, DatabaseAccessKind, SET_DATABASE_SCOPE_SQL


@pytest.fixture(autouse=True)
def phase4_settings(package, monkeypatch):
    monkeypatch.setattr(pw, "get_settings", lambda: package.settings)


def scheduled(p):
    p.settings.social_ops_scheduled_publish_enabled = True
    request = PublishSchedule(
        **p.request.model_dump(),
        scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        timezone="Asia/Shanghai",
        grace_seconds=600,
    )
    row = pw.prepare(p.s.repo, request)
    row = pw.approve(p.s.repo, str(row["id"]), row["approval_hash"])
    # Fixture moves the already human-approved schedule to now and recalculates
    # server grant; production code never accepts these DB mutations from clients.
    with p.s.repo.cursor() as c:
        c.execute(
            "UPDATE social_publish_intents SET scheduled_at=now()-interval '1 second',authorized_until=now()+interval '5 minutes',next_attempt_at=now() WHERE id=%s RETURNING *",
            (row["id"],),
        )
        row = c.fetchone()
        c.execute(
            "UPDATE social_publish_intents SET grant_hash=%s WHERE id=%s",
            (pw.authorization(row), row["id"]),
        )
    return publishing.get_intent(p.s.repo, row["id"])


def db_for(p, monkeypatch):
    monkeypatch.setattr(
        "services.permissions.checker.PermissionChecker.check",
        AsyncMock(return_value=True),
    )
    with psycopg.connect(p.s.dsn) as conn:
        conn.execute(
            "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS features jsonb DEFAULT '{}'::jsonb"
        )
    db = object.__new__(LocalDBClient)
    db._pool = p.s.pool
    return db


def discover(p):
    with psycopg.connect(p.s.dsn) as conn:
        scope = DatabaseScope(
            actor_user_id=None, org_id=None, access_kind=DatabaseAccessKind.WORKER
        )
        conn.execute(SET_DATABASE_SCOPE_SQL, scope.settings)
        return conn.execute("SELECT * FROM social_publish_due()").fetchall()


def test_time_windows_switch_and_scope(package):
    p = package
    request = PublishSchedule(
        **p.request.model_dump(),
        scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    with pytest.raises(ReachError):
        pw.prepare(p.s.repo, request)
    p.settings.social_ops_scheduled_publish_enabled = True
    for change in (
        {"scheduled_at": datetime.now()},
        {"scheduled_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
        {"timezone": "invalid/zone"},
    ):
        with pytest.raises(ReachError):
            pw.prepare(p.s.repo, request.model_copy(update=change))
    row = pw.prepare(p.s.repo, request)
    with pytest.raises(ReachError):
        pw.approve(p.s.foreign, str(row["id"]), row["approval_hash"])
    with p.s.foreign.cursor() as c:
        for table, field in [
            ("social_publish_intents", "id"),
            ("social_publish_events", "intent_id"),
        ]:
            c.execute(f"SELECT * FROM {table} WHERE {field}=%s", (row["id"],))
            assert not c.fetchall()
    first = pw.approve(p.s.repo, str(row["id"]), row["approval_hash"])
    assert (
        pw.approve(p.s.repo, str(row["id"]), row["approval_hash"])["id"] == first["id"]
    )
    with pytest.raises(ReachError):
        publishing.consume(p.s.repo, row["id"], row["approval_hash"])


def test_competing_claim_cancel_and_stale_fencing(package):
    p = package
    row = scheduled(p)
    w = pw.PublishWorker(SimpleNamespace(pool=p.s.pool), p.s.scope)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: w.claim(row["id"]), range(2)))
    claimed = next(r for r in results if r)
    assert sum(r is not None for r in results) == 1
    old = pw.ScheduledPublishGrant(
        p.s.repo, str(row["id"]), str(claimed["lease_token"])
    )
    with p.s.repo.cursor() as c:
        c.execute(
            "UPDATE social_publish_intents SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
            (row["id"],),
        )
    fresh = w.claim(row["id"])
    assert fresh and fresh["lease_token"] != claimed["lease_token"]
    with pytest.raises(ReachError):
        old.validate()
    pw.cancel(p.s.repo, str(row["id"]))
    grant = pw.ScheduledPublishGrant(
        p.s.repo, str(row["id"]), str(fresh["lease_token"])
    )
    with pytest.raises(ReachError):
        grant.validate()
    assert not w.claim(row["id"])


@pytest.mark.parametrize(
    "change",
    [
        "time",
        "visibility",
        "account",
        "content",
        "media",
        "credentials",
        "disconnect",
        "member",
    ],
)
def test_approval_revocation(package, change):
    p = package
    row = scheduled(p)
    if change in {"time", "visibility", "account"}:
        request = PublishSchedule(
            **p.request.model_dump(),
            scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=20),
            timezone="UTC",
            grace_seconds=60,
        )
        if change == "visibility":
            request.visibility = "public"
        if change == "account":
            request.connection_id = p.s.repo.save_verified(
                "xiaohongshu", "other", "Other", {"service_token": "fixture-only"}
            )["id"]
        edited = pw.prepare(p.s.repo, request, str(row["id"]))
        with pytest.raises(ReachError):
            pw.approve(p.s.repo, str(edited["id"]), row["approval_hash"])
        assert edited["grant_hash"] is None
        return
    if change == "media":
        a = p.row["data"]["assets"][0]
        _, path = cards.asset(p.s.repo, a["id"])
        path.write_bytes(path.read_bytes() + b"changed")
    else:
        with psycopg.connect(p.s.dsn) as c:
            if change == "content":
                c.execute(
                    "UPDATE social_contents SET current_revision=current_revision+1 WHERE id=%s",
                    (row["content_id"],),
                )
            if change == "credentials":
                c.execute(
                    "UPDATE reach_connections SET credential_version=credential_version+1 WHERE id=%s",
                    (row["connection_id"],),
                )
            if change == "disconnect":
                c.execute(
                    "UPDATE reach_connections SET status='revoked' WHERE id=%s",
                    (row["connection_id"],),
                )
            if change == "member":
                c.execute(
                    "UPDATE org_members SET status='inactive' WHERE org_id=%s AND user_id=%s",
                    (p.s.scope.org_id, p.s.scope.actor_user_id),
                )
    try:
        discover(p)
        w = pw.PublishWorker(SimpleNamespace(pool=p.s.pool), p.s.scope)
        try:
            claimed = w.claim(row["id"])
        except ReachError:
            claimed = None
        if claimed:
            with pytest.raises(ReachError):
                pw.ScheduledPublishGrant(
                    p.s.repo, str(row["id"]), str(claimed["lease_token"])
                ).validate()
    finally:
        if change == "member":
            with psycopg.connect(p.s.dsn) as c:
                c.execute(
                    "UPDATE org_members SET status='active' WHERE org_id=%s AND user_id=%s",
                    (p.s.scope.org_id, p.s.scope.actor_user_id),
                )


@pytest.mark.parametrize("outcome", ["submitted", "uncertain", "crash", "budget"])
async def test_canonical_scheduled_send_once_and_budget_wait(
    package, monkeypatch, outcome
):
    p = package
    row = scheduled(p)
    db = db_for(p, monkeypatch)
    upstream = AsyncMock(
        return_value=ReachResult(
            "xiaohongshu",
            "fixture",
            status="partial",
            receipt={"stage": "backend_submitted", "remote_id": ""},
        )
    )
    if outcome == "uncertain":
        upstream.side_effect = ReachError("WRITE_UNCERTAIN", "模拟发送后未知")
    if outcome == "crash":
        upstream.side_effect = asyncio.CancelledError()
    if outcome == "budget":
        with p.s.repo.cursor() as c:
            from services.agent.agent_reach.access_budget import (
                AccessBudgetService,
                account_key,
            )

            AccessBudgetService(p.s.pool, p.s.scope).status(p.s.connection)
            c.execute(
                "UPDATE reach_access_budgets SET next_allowed_at=now()+interval '2 minutes' WHERE account_key=%s",
                (account_key(p.s.connection),),
            )
    monkeypatch.setattr("services.agent.agent_reach.handler.write", upstream)
    w = pw.PublishWorker(db, p.s.scope)
    if outcome == "crash":
        with pytest.raises(asyncio.CancelledError):
            await w.tick(str(row["id"]))
    else:
        await asyncio.gather(w.tick(str(row["id"])), w.tick(str(row["id"])))
    saved = publishing.get_intent(p.s.repo, row["id"])
    expected = {
        "submitted": "submitted",
        "uncertain": "uncertain",
        "crash": "uncertain",
        "budget": "waiting_budget",
    }[outcome]
    assert saved["state"] == expected, saved
    assert bool(saved["grant_consumed_at"]) == (outcome != "budget")
    assert upstream.await_count == (0 if outcome == "budget" else 1)
    await w.tick(str(row["id"]))
    assert upstream.await_count == (0 if outcome == "budget" else 1)
    if outcome == "budget":
        assert saved["grant_hash"] and not saved["operation_id"]
        with p.s.repo.cursor() as c:
            c.execute("UPDATE reach_access_budgets SET next_allowed_at=now()")
            c.execute(
                "UPDATE social_publish_intents SET next_attempt_at=now() WHERE id=%s",
                (row["id"],),
            )
        await w.tick(str(row["id"]))
        assert upstream.await_count == 1
        assert publishing.get_intent(p.s.repo, row["id"])["state"] == "submitted"


def test_restart_expiry_and_dispatch_crash(package):
    p = package
    row = scheduled(p)
    with p.s.repo.cursor() as c:
        c.execute(
            "UPDATE social_publish_intents SET authorized_until=now()-interval '1 millisecond' WHERE id=%s",
            (row["id"],),
        )
    discover(p)
    assert publishing.get_intent(p.s.repo, row["id"])["state"] == "expired"
    # A consumed sending row is never reclaimed even without a platform receipt.
    with p.s.repo.cursor() as c:
        c.execute(
            "UPDATE social_publish_intents SET state='dispatching',grant_consumed_at=now(),lease_expires_at=now()-interval '1 second' WHERE id=%s",
            (row["id"],),
        )
    discover(p)
    assert publishing.get_intent(p.s.repo, row["id"])["state"] == "uncertain"
    assert not pw.PublishWorker(SimpleNamespace(pool=p.s.pool), p.s.scope).claim(
        row["id"]
    )


@pytest.mark.parametrize("winner", ["cancel", "send"])
def test_cancel_send_boundary_race(package, winner):
    p = package
    row = scheduled(p)
    w = pw.PublishWorker(SimpleNamespace(pool=p.s.pool), p.s.scope)
    claimed = w.claim(row["id"])
    grant = pw.ScheduledPublishGrant(
        p.s.repo, str(row["id"]), str(claimed["lease_token"])
    )
    from services.agent.agent_reach.contracts import ReachRequest

    request = ReachRequest(
        task="fixture",
        platform="xiaohongshu",
        action="create_post",
        connection_id=str(row["connection_id"]),
        title=row["payload"]["data"]["title"],
        content=row["payload"]["data"]["body"],
        tags=row["payload"]["data"]["tags"],
        visibility=row["visibility"],
        media=[{"resource_ref": "fref1_fixture", "name": "fixture", "role": "image"}],
    )
    media = [{"sha256": a["sha256"]} for a in row["payload"]["data"]["assets"]]
    if winner == "cancel":
        pw.cancel(p.s.repo, str(row["id"]))
        with pytest.raises(ReachError):
            grant.begin_send(p.s.connection, request, media)
    else:
        grant.begin_send(p.s.connection, request, media)
        with pytest.raises(ReachError):
            pw.cancel(p.s.repo, str(row["id"]))
        with pytest.raises(ReachError):
            grant.begin_send(p.s.connection, request, media)
        assert publishing.get_intent(p.s.repo, row["id"])["grant_consumed_at"]


async def test_crash_after_receipt_and_stale_result_cannot_overwrite(
    package, monkeypatch
):
    p = package
    row = scheduled(p)
    db = db_for(p, monkeypatch)
    upstream = AsyncMock(
        return_value=ReachResult(
            "xiaohongshu",
            "fixture",
            status="partial",
            receipt={"stage": "backend_submitted", "remote_id": ""},
        )
    )
    monkeypatch.setattr("services.agent.agent_reach.handler.write", upstream)
    real_update = publishing.update_result

    def crash(*args, **kwargs):
        raise RuntimeError("simulated DB boundary crash")

    monkeypatch.setattr(publishing, "update_result", crash)
    with pytest.raises(RuntimeError):
        await pw.PublishWorker(db, p.s.scope).tick(str(row["id"]))
    with p.s.repo.cursor() as c:
        c.execute(
            "SELECT state,receipt FROM reach_operations WHERE call_id=%s",
            ("social-publish:" + str(row["id"]),),
        )
        operation = c.fetchone()
        assert (
            operation["state"] == "succeeded"
            and operation["receipt"]["stage"] == "backend_submitted"
        )
        c.execute(
            "UPDATE social_publish_intents SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
            (row["id"],),
        )
    discover(p)
    assert publishing.get_intent(p.s.repo, row["id"])["state"] == "submitted"
    real_update(p.s.repo, row["id"], "failed", {}, "late-worker")
    assert publishing.get_intent(p.s.repo, row["id"])["state"] == "submitted"
    await pw.PublishWorker(db, p.s.scope).tick(str(row["id"]))
    assert upstream.await_count == 1


def test_exact_tool_grant_rejects_forgery_and_argument_changes(package):
    p = package
    row = scheduled(p)
    w = pw.PublishWorker(SimpleNamespace(pool=p.s.pool), p.s.scope)
    claimed = w.claim(row["id"])
    grant = pw.ScheduledPublishGrant(
        p.s.repo, str(row["id"]), str(claimed["lease_token"])
    )
    grant.validate()
    grant.media = [
        {"resource_ref": "fref1_fixture", "name": str(i), "role": "image"}
        for i in range(len(row["payload"]["data"]["assets"]))
    ]
    data = row["payload"]["data"]
    args = {
        "task": "操作者确认后立即发布当前图文包",
        "platform": "xiaohongshu",
        "action": "create_post",
        "connection_id": str(row["connection_id"]),
        "title": data["title"],
        "content": data["body"],
        "tags": data["tags"],
        "visibility": row["visibility"],
        "media": grant.media,
    }
    assert grant.permits("agent_reach", args)
    for change in (
        {"action": "post_comment"},
        {"visibility": "public"},
        {"title": "changed"},
        {"media": list(reversed(grant.media))},
        {"approved": True},
    ):
        assert not grant.permits("agent_reach", {**args, **change})
    assert not grant.permits("erp_write", args)
    fake = pw.ScheduledPublishGrant(
        p.s.foreign, str(row["id"]), str(claimed["lease_token"])
    )
    assert not fake.permits("agent_reach", args)


def test_cancel_and_consume_actual_concurrent_race(package):
    from threading import Barrier

    p = package
    row = scheduled(p)
    w = pw.PublishWorker(SimpleNamespace(pool=p.s.pool), p.s.scope)
    claim = w.claim(row["id"])
    grant = pw.ScheduledPublishGrant(
        p.s.repo, str(row["id"]), str(claim["lease_token"])
    )
    gate = Barrier(2)

    def run(kind):
        gate.wait()
        try:
            if kind == "cancel":
                pw.cancel(p.s.repo, str(row["id"]))
            else:
                grant.begin_send(
                    p.s.connection,
                    SimpleNamespace(
                        platform="xiaohongshu",
                        action="create_post",
                        connection_id=str(row["connection_id"]),
                        title=row["payload"]["data"]["title"],
                        content=row["payload"]["data"]["body"],
                        tags=row["payload"]["data"]["tags"],
                        visibility=row["visibility"],
                    ),
                    [{"sha256": a["sha256"]} for a in row["payload"]["data"]["assets"]],
                )
            return kind
        except ReachError:
            return "rejected"

    with ThreadPoolExecutor(2) as pool:
        result = list(pool.map(run, ["cancel", "send"]))
    assert result.count("rejected") == 1
    saved = publishing.get_intent(p.s.repo, row["id"])
    assert (saved["state"] == "dispatching" and saved["grant_consumed_at"]) or (
        saved["state"] == "cancelled" and saved["grant_consumed_at"] is None
    )


def test_schedule_edits_keep_identity_and_cancel_restores_immediate(package):
    p = package
    p.settings.social_ops_scheduled_publish_enabled = True
    req = PublishSchedule(
        **p.request.model_dump(),
        scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    row = pw.prepare(p.s.repo, req)
    row = pw.approve(p.s.repo, str(row["id"]), row["approval_hash"])
    assert pw.prepare(p.s.repo, req)["state"] == "scheduled"
    edited = pw.prepare(
        p.s.repo, req.model_copy(update={"visibility": "public"}), str(row["id"])
    )
    assert edited["id"] == row["id"] and edited["grant_hash"] is None
    pw.cancel(p.s.repo, str(row["id"]))
    immediate = publishing.create_intent(p.s.repo, p.request)
    assert (
        immediate["id"] == row["id"]
        and immediate["scheduled_at"] is None
        and immediate["state"] == "awaiting_confirmation"
    )


def test_schedule_http_review_edit_cancel_and_tenant_boundary(package, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routes import social_ops as routes
    from api.deps import get_db, get_org_context

    p = package
    p.settings.social_ops_scheduled_publish_enabled = True
    monkeypatch.setattr(routes, "get_settings", lambda: p.settings)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(pool=p.s.pool)
    app.dependency_overrides[get_org_context] = lambda: SimpleNamespace(
        org_id=p.s.scope.org_id, user_id=p.s.scope.actor_user_id
    )
    request = {
        **p.request.model_dump(mode="json"),
        "scheduled_at": (
            datetime.now(timezone.utc) + timedelta(minutes=20)
        ).isoformat(),
        "timezone": "Asia/Shanghai",
        "grace_seconds": 60,
    }
    with TestClient(app) as client:
        created = client.post("/social-ops/publish-schedules", json=request)
        assert created.status_code == 201, created.text
        row = created.json()
        url = "/social-ops/publish-schedules/" + row["id"]
        approval = {"approval_hash": row["approval_hash"], "acknowledge_risks": True}
        assert (
            client.post(url + "/confirm", json=approval).json()["state"] == "scheduled"
        )
        assert (
            client.post(url + "/confirm", json=approval).json()["state"] == "scheduled"
        )
        assert (
            client.get(
                "/social-ops/publish-schedules?month="
                + datetime.now(timezone.utc).strftime("%Y-%m")
            ).json()["total"]
            == 1
        )
        assert (
            client.get("/social-ops/publish-schedules?month=invalid").status_code == 422
        )
        assert client.get(url).json()["events"]
        assert client.post(url + "/revise").json()["state"] == "invalidated"
        edited = client.put(url, json={**request, "visibility": "public"}).json()
        assert edited["id"] == row["id"]
        assert client.post(url + "/confirm", json=approval).status_code == 409
        assert (
            client.post("/social-ops/publish-intents/" + row["id"] + "/cancel").json()[
                "state"
            ]
            == "cancelled"
        )
        app.dependency_overrides[get_org_context] = lambda: SimpleNamespace(
            org_id=p.s.foreign.scope.org_id, user_id=p.s.foreign.scope.actor_user_id
        )
        assert client.get("/social-ops/publish-schedules").json()["total"] == 0
        assert client.get(url).status_code == 404
        assert client.post(url + "/confirm", json=approval).status_code == 403


async def test_short_publish_scan_independent_of_host_fallback(monkeypatch):
    from services.background_task_worker import BackgroundTaskWorker

    host = object.__new__(BackgroundTaskWorker)
    host.is_running = True
    host.poll_interval = 120

    async def poll():
        host.is_running = False

    host._social_ops = SimpleNamespace(poll_publish=AsyncMock(side_effect=poll))
    sleep = AsyncMock()
    monkeypatch.setattr("services.background_task_worker.asyncio.sleep", sleep)
    await host._social_publish_loop()
    host._social_ops.poll_publish.assert_awaited_once()
    sleep.assert_awaited_once_with(15)
