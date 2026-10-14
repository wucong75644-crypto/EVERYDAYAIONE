"""Real disposable PostgreSQL/RLS; substituted platform/model, no external calls."""

import os, json
from types import SimpleNamespace
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from psycopg.types.json import Jsonb
from tests.test_social_ops_postgres import stores, worker, OWNER, ORG1, ORG2
from tests.test_social_ops_phase3 import package
from services.social_ops import publishing, metrics, metric_imports, reviews
from services.social_ops.contracts import (
    MetricCollect,
    PublishVerify,
    ReviewCreate,
    SuggestionDecision,
)
from services.agent.agent_reach.contracts import ReachError, ReachItem, ReachResult
from services.social_ops.production import ProductionWorker

pytestmark = pytest.mark.skipif(
    not os.getenv("SOCIAL_OPS_TEST_DSN"), reason="dedicated disposable database only"
)


@pytest.fixture
def published(package, monkeypatch):
    p = package
    p.settings.social_ops_analytics_enabled = True
    monkeypatch.setattr("services.social_ops.worker.get_settings", lambda: p.settings)
    intent = publishing.create_intent(p.s.repo, p.request)
    with p.s.repo.cursor() as c:
        c.execute(
            "UPDATE social_publish_intents SET state='submitted' WHERE id=%s",
            (intent["id"],),
        )
    remote = "a" * 24
    intent = publishing.verify_intent(
        p.s.repo,
        str(intent["id"]),
        PublishVerify(
            remote_id=remote,
            url="https://www.xiaohongshu.com/explore/" + remote,
            note="隔离平台替代核验，不访问真实账号",
        ),
    )
    p.intent = intent
    p.url = intent["verification"]["url"] + "?xsec_token=fixture"
    return p


def import_bytes(p, **changes):
    row = {
        "intent_id": str(p.intent["id"]),
        "account_id": p.s.connection["account_id"],
        "remote_id": "a" * 24,
        "observed_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "published_at": "",
        "content_type": "image",
        "scope": "creator_cumulative",
        "denominator": "views",
        "likes": "0",
        "comments": "2",
        "saves": "",
        "shares": "1",
        "views": "10",
        **changes,
    }
    return (",".join(row) + "\n" + ",".join(row.values()) + "\n").encode()


def test_import_preview_identity_zero_missing_idempotency_conflict(published):
    p = published
    r = p.s.repo
    raw = import_bytes(p)
    pre = metric_imports.preview(r, raw, "x.csv", "测试后台导出", {})
    assert (
        pre["valid"]
        and pre["rows"][0]["data"]["metrics"]["likes"] == 0
        and pre["rows"][0]["data"]["metrics"]["saves"] is None
    )
    assert metric_imports.confirm(r, pre["id"], pre["preview_hash"]) == {
        "status": "confirmed",
        "created": 1,
        "reused": 0,
    }
    assert (
        metric_imports.confirm(r, pre["id"], pre["preview_hash"])["status"]
        == "already_confirmed"
    )
    pre2 = metric_imports.preview(r, raw, "again.csv", "测试后台导出", {})
    assert pre2["duplicate_count"] == 1
    assert metric_imports.confirm(r, pre2["id"], pre2["preview_hash"])["reused"] == 1
    conflict = metric_imports.preview(
        r, raw.replace(b",0,2,", b",1,2,"), "x.csv", "冲突", {}
    )
    assert not conflict["valid"]
    bad = metric_imports.preview(
        r, import_bytes(p, account_id="wrong"), "x.csv", "错误账号", {}
    )
    assert not bad["valid"]
    with pytest.raises(ReachError):
        metric_imports.confirm(p.s.foreign, pre["id"], pre["preview_hash"])
    with pytest.raises(ReachError):
        metrics.snapshots(p.s.foreign, [str(p.intent["id"])])
    with p.s.foreign.cursor() as c:
        for table in (
            "social_metric_snapshots",
            "social_metric_imports",
            "social_metric_collections",
            "social_review_suggestions",
        ):
            c.execute("SELECT * FROM " + table + " WHERE org_id=%s", (ORG1,))
            assert not c.fetchall()


def test_collection_concurrent_checkpoint_and_author_mismatch(published):
    p = published
    r = p.s.repo
    data = MetricCollect(client_request_id=uuid4(), url=p.url, comment_pages=0)

    def create(_):
        return metrics.create_collection(r, str(p.intent["id"]), data)["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert len(set(pool.map(create, range(2)))) == 1
    job_id = create(0)
    w = worker(p.s)
    job, item = w.claim(job_id)
    assert w.claim(job_id) is None
    result = ReachResult(
        "xiaohongshu",
        "xhs-rest",
        [
            ReachItem(
                "标题",
                p.url,
                "真实正文",
                metrics={"likes": 0, "comments": None},
                metric_metadata={
                    "remote_id": "a" * 24,
                    "author_id": p.s.connection["account_id"],
                    "raw_metrics": {"likes": "0"},
                    "adapter_version": "fixture:v1",
                    "source": "fixture",
                },
            )
        ],
    ).to_agent_result()
    w.settle(job, item, result)
    w.settle(job, item, result)
    saved = metrics.snapshots(r, [str(p.intent["id"])])
    assert (
        len(saved) == 1
        and saved[0]["metrics"]["likes"] == 0
        and saved[0]["metrics"]["comments"] is None
    )
    # Preserve checkpoint on retry/interrupt; a stale token cannot duplicate it.
    assert r.job(job_id)["status"] == "completed"
    second = metrics.create_collection(
        r, str(p.intent["id"]), data.model_copy(update={"client_request_id": uuid4()})
    )
    job, item = w.claim(second["id"])
    result.metadata["sources"][0]["metric_metadata"]["author_id"] = "wrong"
    w.settle(job, item, result)
    assert (
        r.job(second["id"])["status"] == "needs_input"
        and len(metrics.snapshots(r, [str(p.intent["id"])])) == 1
    )
    with pytest.raises(ReachError):
        metrics.create_collection(
            r,
            str(p.intent["id"]),
            data.model_copy(update={"url": p.url.replace("a" * 24, "b" * 24)}),
        )


async def test_report_pins_dedupe_and_suggestion_new_version_no_authorization_change(
    published, monkeypatch
):
    p = published
    r = p.s.repo
    pre = metric_imports.preview(r, import_bytes(p), "x.csv", "后台", {})
    metric_imports.confirm(r, pre["id"], pre["preview_hash"])
    pins = {"skills": [{"skill_id": "easel-content-postmortem", "revision": "fixed"}]}
    calls = []

    async def bind(*args, **kwargs):
        calls.append(kwargs)
        return pins

    monkeypatch.setattr(reviews, "bind_scheduled_skills", bind)
    data = ReviewCreate(
        client_request_id=uuid4(),
        intent_ids=[p.intent["id"]],
        start=datetime.now(timezone.utc) - timedelta(days=2),
        end=datetime.now(timezone.utc),
        stages=[{"skill_id": "easel-content-postmortem", "revision": "fixed"}],
    )
    run = await reviews.create_review(SimpleNamespace(pool=p.s.pool), r, data)
    duplicate = await reviews.create_review(
        SimpleNamespace(pool=p.s.pool),
        r,
        data.model_copy(update={"client_request_id": uuid4()}),
    )
    assert duplicate["id"] == run["id"] and len(calls) == 1
    pw = ProductionWorker(SimpleNamespace(pool=p.s.pool), p.s.scope)
    pw.repo = r
    claim = pw.claim(run["id"])
    src = claim["input"]["sources"][0]
    output = {
        "observed_fact_ids": [claim["input"]["statistics"]["facts"][0]["id"]],
        "possible_causes": [],
        "insufficient_evidence": ["样本不足，不能判断整体表现"],
        "comment_insights": [],
        "suggestions": [
            {
                "kind": "topic",
                "text": "尝试教程选题并观察反馈",
                "evidence": [{"snapshot_id": src["id"], "quote": src["body"][:100]}],
            },
            {
                "kind": "experience",
                "text": "有限样本提示可能需要更清楚的说明",
                "evidence": [{"snapshot_id": src["id"], "quote": src["body"][:100]}],
            },
        ],
    }
    pw.settle(
        claim, output, {"skill_snapshot": pins, "model": "fixture", "tokens": 100}
    )
    with r.cursor() as c:
        c.execute(
            "SELECT * FROM social_review_suggestions WHERE run_id=%s ORDER BY kind",
            (run["id"],),
        )
        sugs = c.fetchall()
    before = publishing.get_intent(r, str(p.intent["id"]))
    topic = next(s for s in sugs if s["kind"] == "topic")
    experience = next(s for s in sugs if s["kind"] == "experience")
    accepted = reviews.decide(
        r,
        str(topic["id"]),
        SuggestionDecision(
            decision="accept", text="编辑后的教程计划", expected_profile_revision=1
        ),
    )
    assert accepted["result"]["topic_id"]
    rejected = reviews.decide(
        r, str(experience["id"]), SuggestionDecision(decision="reject")
    )
    assert rejected["decision"] == "reject" and r.profiles()[0]["current_revision"] == 1
    with pytest.raises(ReachError):
        reviews.decide(
            r,
            str(topic["id"]),
            SuggestionDecision(decision="accept", expected_profile_revision=1),
        )
    with pytest.raises(ReachError):
        reviews.decide(
            p.s.foreign, str(experience["id"]), SuggestionDecision(decision="reject")
        )
    after = publishing.get_intent(r, str(p.intent["id"]))
    for k in ("state", "approval_hash", "revision", "payload", "verification"):
        assert before[k] == after[k]
    # A new suggestion can append only experience, preserving profile identity.
    with r.cursor() as c:
        c.execute(
            "INSERT INTO social_review_suggestions(org_id,run_id,profile_revision_id,kind,original_text,evidence) VALUES(%s,%s,%s,'experience','待验证经验','[]') RETURNING id",
            (ORG1, run["id"], p.s.profile["id"]),
        )
        sid = c.fetchone()["id"]
    updated = reviews.decide(
        r, str(sid), SuggestionDecision(decision="accept", expected_profile_revision=1)
    )
    assert updated["result"]["revision"] == 2
    new = r.profiles()[0]
    assert (
        new["data"]["positioning"] == p.s.profile["data"]["positioning"]
        and new["data"]["prohibited"] == p.s.profile["data"]["prohibited"]
    )


def test_direct_database_identity_and_snapshot_immutable(published):
    p = published
    r = p.s.repo
    pre = metric_imports.preview(r, import_bytes(p), "x.csv", "后台", {})
    metric_imports.confirm(r, pre["id"], pre["preview_hash"])
    import psycopg

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with r.cursor() as c:
            c.execute("UPDATE social_metric_snapshots SET metrics='{}'")
    with pytest.raises(psycopg.errors.RaiseException):
        with r.cursor() as c:
            intent = metrics.verified(r, c, p.intent["id"])
            intent["account_id"] = "wrong"
            metrics.save_snapshot(
                r,
                c,
                intent,
                {
                    "metrics": {"likes": 1},
                    "observed_at": datetime.now(timezone.utc),
                    "content_type": "image",
                    "coverage": {},
                    "source": "fixture",
                    "adapter_version": "fixture",
                },
                "wrong",
            )


async def test_schedule_pinned_bounded_host_occurrence_and_no_model_repeat(
    published, monkeypatch
):
    from services.social_ops import analytics_schedule
    from services.social_ops.contracts import AnalyticsSchedule

    p = published
    r = p.s.repo

    async def bind(*args, **kwargs):
        return {
            "skills": [{"skill_id": "easel-content-postmortem", "revision": "fixed"}]
        }

    monkeypatch.setattr(analytics_schedule, "bind_scheduled_skills", bind)
    data = AnalyticsSchedule(
        intent_id=p.intent["id"],
        collection={"client_request_id": uuid4(), "url": p.url},
        enabled=True,
        interval_hours=24,
        review_frequency="monthly",
        review_stage={"skill_id": "easel-content-postmortem", "revision": "fixed"},
        max_credits=5,
    )
    saved = await analytics_schedule.save(SimpleNamespace(pool=p.s.pool), r, data)
    assert saved["next_review_at"] > datetime.now(timezone.utc) + timedelta(days=20)
    with r.cursor() as c:
        c.execute(
            "SELECT config,skill_pin FROM social_analytics_schedules WHERE id=%s",
            (saved["id"],),
        )
        stored = c.fetchone()
    assert stored["skill_pin"]["skills"][0]["revision"] == "fixed"
    await analytics_schedule.tick(
        SimpleNamespace(pool=p.s.pool), p.s.scope, str(saved["id"])
    )
    with r.cursor() as c:
        c.execute(
            "SELECT last_job_id,next_at FROM social_analytics_schedules WHERE id=%s",
            (saved["id"],),
        )
        first = c.fetchone()
        assert first["last_job_id"] and first["next_at"] > datetime.now(timezone.utc)
        c.execute("SELECT count(*) AS n FROM social_metric_collections")
        assert c.fetchone()["n"] == 1
    await analytics_schedule.tick(
        SimpleNamespace(pool=p.s.pool), p.s.scope, str(saved["id"])
    )
    with r.cursor() as c:
        c.execute("SELECT count(*) AS n FROM social_metric_collections")
        assert c.fetchone()["n"] == 1
    # Disable preserves evidence and prevents new occurrences.
    await analytics_schedule.save(
        SimpleNamespace(pool=p.s.pool), r, data.model_copy(update={"enabled": False})
    )
    await analytics_schedule.tick(
        SimpleNamespace(pool=p.s.pool), p.s.scope, str(saved["id"])
    )
    with r.cursor() as c:
        c.execute("SELECT count(*) AS n FROM social_metric_collections")
        assert c.fetchone()["n"] == 1


async def test_review_api_rls_and_input_dependencies(published, monkeypatch):
    p = published
    from api.routes.social_analytics import review_detail, decision, history
    from fastapi import Response, HTTPException
    from services.social_ops.contracts import SuggestionDecision

    # No metrics -> no bound model run. Comments -> requires actual samples.
    request = ReviewCreate(
        client_request_id=uuid4(),
        intent_ids=[p.intent["id"]],
        start=datetime.now(timezone.utc) - timedelta(days=1),
        end=datetime.now(timezone.utc),
        stages=[{"skill_id": "easel-content-postmortem", "revision": "fixed"}],
    )
    with pytest.raises(ReachError, match="没有真实指标"):
        await reviews.create_review(SimpleNamespace(pool=p.s.pool), p.s.repo, request)
    pre = metric_imports.preview(p.s.repo, import_bytes(p), "x.csv", "后台", {})
    metric_imports.confirm(p.s.repo, pre["id"], pre["preview_hash"])
    req = request.model_copy(
        update={
            "kind": "comment_insights",
            "stages": [
                SimpleNamespace(skill_id="easel-comment-insights", revision="fixed")
            ],
        }
    )
    with pytest.raises(ReachError, match="没有评论"):
        await reviews.create_review(SimpleNamespace(pool=p.s.pool), p.s.repo, req)
    req = request.model_copy(
        update={
            "kind": "strategy",
            "stages": [
                SimpleNamespace(skill_id="easel-strategy-advisor", revision="fixed")
            ],
        }
    )
    with pytest.raises(ReachError, match="已完成的复盘"):
        await reviews.create_review(SimpleNamespace(pool=p.s.pool), p.s.repo, req)
    ctx = SimpleNamespace(
        user_id=p.s.foreign.scope.actor_user_id, org_id=p.s.foreign.scope.org_id
    )
    with pytest.raises(HTTPException) as e:
        history(p.intent["id"], ctx, SimpleNamespace(pool=p.s.pool), Response())
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        review_detail(uuid4(), ctx, SimpleNamespace(pool=p.s.pool), Response())
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        decision(
            uuid4(),
            SuggestionDecision(decision="reject"),
            ctx,
            SimpleNamespace(pool=p.s.pool),
            Response(),
        )
    assert e.value.status_code == 404


def test_concurrent_import_same_observation_and_cross_org_comments(published):
    p = published
    r = p.s.repo
    raw = import_bytes(p)
    a = metric_imports.preview(r, raw, "a.csv", "后台", {})
    b = metric_imports.preview(r, raw, "b.csv", "后台", {})

    def confirm(batch):
        return metric_imports.confirm(r, batch["id"], batch["preview_hash"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(confirm, [a, b]))
    assert (
        sum(v["created"] for v in result) == 1 and sum(v["reused"] for v in result) == 1
    )
    data = MetricCollect(client_request_id=uuid4(), url=p.url, comment_pages=1)
    job = metrics.create_collection(r, str(p.intent["id"]), data)
    w = worker(p.s)
    claimed, item = w.claim(job["id"])
    source = ReachItem(
        "t",
        p.url,
        "正文",
        metrics={"likes": 0},
        metric_metadata={
            "remote_id": "a" * 24,
            "author_id": p.s.connection["account_id"],
            "source": "fixture",
            "adapter_version": "fixture",
        },
    )
    w.settle(
        claimed, item, ReachResult("xiaohongshu", "fixture", [source]).to_agent_result()
    )
    claimed, item = w.claim(job["id"])
    comment = ReachItem(
        "comment",
        p.url + "#comment-c",
        "需要教程吗？",
        "comment",
        metric_metadata={
            "remote_id": "a" * 24,
            "author_id": p.s.connection["account_id"],
        },
    )
    w.settle(
        claimed,
        item,
        ReachResult(
            "xiaohongshu",
            "fixture",
            [comment],
            pagination={"page": 1, "loaded_count": 1, "platform_has_more": True},
        ).to_agent_result(),
    )
    comments = [
        s for s in r.sources(job["id"]) if s["coverage"]["content_kind"] == "comment"
    ]
    assert (
        len(comments) == 1
        and comments[0]["coverage"]["pagination"]["platform_has_more"]
    )
    with pytest.raises(ReachError):
        p.s.foreign.snapshot(str(comments[0]["id"]))
    assert r.job(job["id"])["status"] == "completed"


def test_profile_strategy_update_leaves_live_schedule_grant_unchanged(
    package, monkeypatch
):
    from tests.test_social_ops_phase4 import scheduled
    from services.social_ops import publish_worker

    p = package
    monkeypatch.setattr(publish_worker, "get_settings", lambda: p.settings)
    # Explicitly use the existing canonical phase-four authorization fixture.
    row = scheduled(p)
    with p.s.repo.cursor() as c:
        c.execute(
            "INSERT INTO social_review_suggestions(org_id,run_id,profile_revision_id,kind,original_text,evidence) VALUES(%s,%s,%s,'experience','仅记录待验证的表达经验','[]') RETURNING id",
            (ORG1, p.run, p.s.profile["id"]),
        )
        suggestion = c.fetchone()["id"]
    # Old reports remain auditable/rejectable, but cannot change a profile.
    with pytest.raises(ReachError, match="旧解释校验规则"):
        reviews.decide(p.s.repo, str(suggestion), SuggestionDecision(decision="accept", expected_profile_revision=1))
    with p.s.repo.cursor() as c:
        c.execute("UPDATE social_production_runs SET input=input || %s WHERE id=%s AND org_id=%s",
                  (Jsonb({"review_policy_version": reviews.REVIEW_POLICY_VERSION}), p.run, ORG1))
    reviews.decide(
        p.s.repo,
        str(suggestion),
        SuggestionDecision(decision="accept", expected_profile_revision=1),
    )
    after = publishing.get_intent(p.s.repo, row["id"])
    for key in (
        "state",
        "grant_hash",
        "grant_consumed_at",
        "payload",
        "approval_hash",
        "revision",
        "scheduled_at",
    ):
        assert after[key] == row[key]


def test_analytics_switch_off_does_not_starve_existing_research(published):
    from services.social_ops.worker import SocialOpsWorker

    p = published
    r = p.s.repo
    job = metrics.create_collection(
        r, str(p.intent["id"]), MetricCollect(client_request_id=uuid4(), url=p.url)
    )
    from psycopg_pool import ConnectionPool

    host_pool = ConnectionPool(p.s.dsn, kwargs={"options": "-c role=everydayai"})
    host = SocialOpsWorker(SimpleNamespace(pool=host_pool))
    p.settings.social_ops_analytics_enabled = False
    assert str(job["id"]) not in {str(row["job_id"]) for row in host.discover()}
    p.settings.social_ops_analytics_enabled = True
    assert str(job["id"]) in {str(row["job_id"]) for row in host.discover()}
    host_pool.close()


async def test_pause_schedule_needs_no_token_and_rejects_cross_org(published):
    from services.social_ops.analytics_schedule import save
    from services.social_ops.contracts import AnalyticsSchedule
    from api.routes.social_analytics import pause_schedule
    from fastapi import Response, HTTPException

    p = published
    row = await save(
        SimpleNamespace(pool=p.s.pool),
        p.s.repo,
        AnalyticsSchedule(
            intent_id=p.intent["id"],
            collection={"client_request_id": uuid4(), "url": p.url},
            enabled=True,
        ),
    )
    foreign = SimpleNamespace(
        org_id=p.s.foreign.scope.org_id, user_id=p.s.foreign.scope.actor_user_id
    )
    with pytest.raises(HTTPException) as error:
        pause_schedule(row["id"], foreign, SimpleNamespace(pool=p.s.pool), Response())
    assert error.value.status_code == 404
    own = SimpleNamespace(org_id=ORG1, user_id=OWNER)
    result = pause_schedule(row["id"], own, SimpleNamespace(pool=p.s.pool), Response())
    assert not result["enabled"]
