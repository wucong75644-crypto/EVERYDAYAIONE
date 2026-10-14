"""Finite read-only collection and weekly/calendar-month review on the existing host."""

from datetime import datetime, timedelta, timezone
from calendar import monthrange
from uuid import uuid4, uuid5, UUID
from psycopg.types.json import Jsonb
from services.skills.scheduled import bind_scheduled_skills
from services.agent.agent_reach.contracts import ReachError
from .contracts import MetricCollect, ReviewCreate
from .metrics import create_collection, verified
from .reviews import create_review, REVIEW_SKILLS
from .repository import Repository


def next_review(now, frequency):
    if frequency == "weekly":
        return now + timedelta(days=7)
    month = now.month % 12 + 1
    year = now.year + int(now.month == 12)
    return now.replace(
        year=year, month=month, day=min(now.day, monthrange(year, month)[1])
    )


async def save(db, repo, data):
    with repo.cursor() as c:
        intent = verified(repo, c, data.intent_id)
    repo.load(str(intent["connection_id"]), "xiaohongshu")
    from services.agent.agent_reach.xhs_adapter import XHSAdapter

    if (
        XHSAdapter.target(data.collection.url)["feed_id"]
        != intent["verification"]["remote_id"]
    ):
        raise ReachError("INVALID_ARGUMENTS", "周期采集链接与核验ID不一致")
    pins = None
    if data.review_frequency != "none":
        if (
            not data.review_stage
            or data.review_stage.skill_id != REVIEW_SKILLS["postmortem"]
        ):
            raise ReachError("NEEDS_INPUT", "周期复盘需要固定已审核的内容复盘Skill版本")
        pins = await bind_scheduled_skills(
            db,
            owner=repo.scope.actor_user_id,
            org=repo.scope.org_id,
            task_id=str(uuid4()),
            selections=[data.review_stage.model_dump()],
        )
    with repo.cursor() as c:
        c.execute(
            """INSERT INTO social_analytics_schedules(org_id,owner_id,intent_id,config,skill_pin,enabled,next_review_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(org_id,intent_id) DO UPDATE
            SET next_review_at=CASE WHEN social_analytics_schedules.config->>'review_frequency' IS DISTINCT FROM EXCLUDED.config->>'review_frequency' OR social_analytics_schedules.skill_pin IS DISTINCT FROM EXCLUDED.skill_pin THEN EXCLUDED.next_review_at ELSE social_analytics_schedules.next_review_at END,
            config=EXCLUDED.config,skill_pin=EXCLUDED.skill_pin,enabled=EXCLUDED.enabled,owner_id=EXCLUDED.owner_id,error=''
            RETURNING id,enabled,next_at,next_review_at""",
            (
                repo.scope.org_id,
                repo.scope.actor_user_id,
                data.intent_id,
                Jsonb(data.model_dump(mode="json")),
                Jsonb(pins),
                data.enabled,
                (
                    next_review(datetime.now(timezone.utc), data.review_frequency)
                    if data.review_frequency != "none"
                    else datetime.now(timezone.utc)
                ),
            ),
        )
        return c.fetchone()


async def tick(db, scope, schedule_id):
    repo = Repository(db.pool, scope)
    with repo.cursor() as c:
        c.execute(
            "SELECT * FROM social_analytics_schedules WHERE id=%s AND org_id=%s AND enabled AND next_at<=now() FOR UPDATE SKIP LOCKED",
            (schedule_id, scope.org_id),
        )
        row = c.fetchone()
        if not row:
            return
        c.execute(
            "UPDATE social_analytics_schedules SET next_at=now()+interval '180 seconds' WHERE id=%s",
            (schedule_id,),
        )
    config = row["config"]
    now = datetime.now(timezone.utc)
    try:
        job = None
        if row["last_job_id"]:
            job = repo.job(str(row["last_job_id"]))
            if job["status"] in {"queued", "running", "waiting_budget"}:
                with repo.cursor() as c:
                    c.execute(
                        "UPDATE social_analytics_schedules SET next_at=now()+interval '60 seconds' WHERE id=%s",
                        (schedule_id,),
                    )
                return
            if job["status"] not in {"completed", "partial"}:
                raise ReachError(
                    "NEEDS_INPUT", job["last_error"] or "上轮回收需人工处理，周期已暂停"
                )
        if (
            job
            and config["review_frequency"] != "none"
            and row["next_review_at"] <= now
        ):
            data = ReviewCreate(
                client_request_id=uuid5(
                    UUID(str(row["id"])), str(row["next_review_at"])
                ),
                intent_ids=[row["intent_id"]],
                start=now
                - timedelta(days=7 if config["review_frequency"] == "weekly" else 31),
                end=now,
                kind="postmortem",
                stages=[config["review_stage"]],
                max_credits=config["max_credits"],
            )
            await create_review(
                db, repo, data, pinned=[row["skill_pin"]], schedule_id=schedule_id
            )
            with repo.cursor() as c:
                c.execute(
                    "UPDATE social_analytics_schedules SET next_review_at=%s WHERE id=%s",
                    (next_review(now, config["review_frequency"]), schedule_id),
                )
        # Advance only after checkpointing a finite collection; no catch-up burst.
        with repo.cursor() as c:
            c.execute(
                "SELECT * FROM social_analytics_schedules WHERE id=%s AND enabled FOR UPDATE",
                (schedule_id,),
            )
            current = c.fetchone()
            if (
                not current
                or current["config"] != config
                or current["owner_id"] != row["owner_id"]
            ):
                return
            collection = MetricCollect.model_validate(
                {
                    **config["collection"],
                    "client_request_id": str(
                        uuid5(UUID(str(row["id"])), str(row["next_at"]))
                    ),
                }
            )
            result = create_collection(
                repo, str(row["intent_id"]), collection, cursor=c
            )
            c.execute(
                "UPDATE social_analytics_schedules SET last_job_id=%s,next_at=%s WHERE id=%s",
                (
                    result["id"],
                    now + timedelta(hours=config["interval_hours"]),
                    schedule_id,
                ),
            )
    except Exception as e:
        code = e.code if isinstance(e, ReachError) else type(e).__name__
        with repo.cursor() as c:
            c.execute(
                "UPDATE social_analytics_schedules SET enabled=false,error=%s WHERE id=%s AND config=%s",
                (code, schedule_id, Jsonb(config)),
            )
        from services.scheduler.push_dispatcher import push_dispatcher

        await push_dispatcher.dispatch(
            str(scope.org_id),
            {"type": "web", "user_id": str(scope.actor_user_id)},
            "社媒周期回收已暂停：" + code + "。请查看社媒复盘配置。",
            [],
        )
