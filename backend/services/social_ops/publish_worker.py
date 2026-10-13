"""One approved original version, one durable send boundary, no automatic resend."""

import asyncio
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from core.config import get_settings
from services.agent.agent_reach.contracts import ReachError
from services.tools.spec import thaw
from .repository import Repository, digest
from . import publishing, cards

PENDING = {
    "awaiting_confirmation",
    "scheduled",
    "waiting_budget",
    "paused",
    "preparing",
    "invalidated",
}


def enabled():
    if not get_settings().social_ops_scheduled_publish_enabled:
        raise ReachError(
            "CONFIG_REQUIRED", "定时发布尚未启用；不会派发未经验证的定时写入"
        )


def window(data):
    try:
        ZoneInfo(data.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ReachError("INVALID_ARGUMENTS", "时区必须是有效 IANA 时区") from None
    when = data.scheduled_at
    if when.tzinfo is None or when.utcoffset() is None:
        raise ReachError("INVALID_ARGUMENTS", "发布时间必须包含明确时区偏移")
    when = when.astimezone(timezone.utc)
    if when <= datetime.now(timezone.utc):
        raise ReachError("INVALID_ARGUMENTS", "发布时间必须在未来")
    return when, when + timedelta(seconds=data.grace_seconds)


def authorization(row):
    return digest(
        {
            k: row[k]
            for k in (
                "id",
                "org_id",
                "owner_id",
                "connection_id",
                "credential_version",
                "content_id",
                "revision",
                "approval_hash",
                "payload",
                "visibility",
                "scheduled_at",
                "schedule_timezone",
                "authorized_until",
            )
        }
    )


def prepare(repo, data, intent_id=None):
    enabled()
    when, until = window(data)
    # Editing must revoke the previous approval BEFORE potentially failing checks.
    if intent_id:
        cancel(repo, intent_id, invalidating=True)
    row = publishing.create_intent(repo, data, scheduled=True, intent_id=intent_id)
    with repo.cursor() as c:
        c.execute(
            "SELECT * FROM social_publish_intents WHERE id=%s AND org_id=%s FOR UPDATE",
            (row["id"], repo.scope.org_id),
        )
        row = c.fetchone()
        if str(row["owner_id"]) != str(repo.scope.actor_user_id):
            raise ReachError("ACCESS_DENIED", "只能修改本人排期")
        if row["state"] not in PENDING | {"cancelled", "expired"}:
            raise ReachError("VERSION_CONFLICT", "已进入发送或已结束，不能修改排期")
        if (
            row["state"] in {"scheduled", "waiting_budget"}
            and row["scheduled_at"] == when
            and row["authorized_until"] == until
            and row["schedule_timezone"] == data.timezone
            and row["visibility"] == data.visibility
            and str(row["check_id"]) == str(data.check_id)
        ):
            return row
        # Never turn a previously consumed authorization back into a new send.
        if row["grant_consumed_at"]:
            raise ReachError("VERSION_CONFLICT", "授权已消费，禁止重发")
        c.execute(
            """UPDATE social_publish_intents SET state='awaiting_confirmation',scheduled_at=%s,schedule_timezone=%s,
          authorized_until=%s,grant_hash=NULL,approved_by=NULL,lease_token=NULL,error='',created_at=now(),updated_at=now(),
          approval_hash=%s WHERE id=%s RETURNING *""",
            (
                when,
                data.timezone,
                until,
                digest(
                    {
                        "package": row["payload"],
                        "org": repo.scope.org_id,
                        "actor": repo.scope.actor_user_id,
                        "connection": str(row["connection_id"]),
                        "time": when,
                        "timezone": data.timezone,
                        "until": until,
                    }
                ),
                row["id"],
            ),
        )
        return c.fetchone()


def approve(repo, id, approval):
    enabled()
    with repo.cursor() as c:
        c.execute(
            "SELECT * FROM social_publish_intents WHERE id=%s AND org_id=%s FOR UPDATE",
            (id, repo.scope.org_id),
        )
        row = c.fetchone()
        if not row or str(row["owner_id"]) != str(repo.scope.actor_user_id):
            raise ReachError("ACCESS_DENIED", "需要原操作者确认")
        if row["approval_hash"] != approval:
            raise ReachError("VERSION_CONFLICT", "确认摘要已变化")
        if row["state"] == "scheduled":
            return row  # Idempotent duplicate HTTP confirmation.
        if (
            row["state"] != "awaiting_confirmation"
            or not row["scheduled_at"]
            or row["grant_consumed_at"]
            or row["created_at"] < datetime.now(timezone.utc) - timedelta(minutes=10)
            or row["scheduled_at"] <= datetime.now(timezone.utc)
        ):
            raise ReachError("VERSION_CONFLICT", "确认已过期或排期已处理，请重新审核")
        validate(repo, row, c)
        c.execute(
            "UPDATE social_publish_intents SET state='scheduled',grant_hash=%s,approved_by=%s,next_attempt_at=scheduled_at,updated_at=now() WHERE id=%s RETURNING *",
            (authorization(row), repo.scope.actor_user_id, id),
        )
        return c.fetchone()


def validate(repo, row, c=None):
    enabled()
    current = (
        cards.content(repo, row["content_id"], row["revision"], cursor=c)
        if c
        else cards.content(repo, row["content_id"], row["revision"])
    )
    if digest(current["data"]) != digest(row["payload"]["data"]):
        raise ReachError("RESOURCE_CHANGED", "最终内容已变化")
    check = cards.validate_package(repo, current)
    if check["hard_errors"]:
        raise ReachError("RESOURCE_CHANGED", "素材或图文包变化，请重新检查确认")
    conn = repo.load(str(row["connection_id"]), "xiaohongshu", write=True)
    if (
        conn["credential_version"] != row["credential_version"]
        or conn["account_id"] != row["payload"]["account_id"]
    ):
        raise ReachError("CREDENTIAL_CHANGED", "账号重新连接，旧授权失效")
    return current


def cancel(repo, id, invalidating=False):
    with repo.cursor() as c:
        c.execute(
            """UPDATE social_publish_intents SET state=%s,grant_hash=NULL,lease_token=NULL,error=%s,updated_at=now()
        WHERE id=%s AND org_id=%s AND owner_id=%s AND state=ANY(%s) AND grant_consumed_at IS NULL RETURNING *""",
            (
                "invalidated" if invalidating else "cancelled",
                "SCHEDULE_EDITED" if invalidating else "",
                id,
                repo.scope.org_id,
                repo.scope.actor_user_id,
                list(PENDING),
            ),
        )
        row = c.fetchone()
        if not row:
            raise ReachError(
                "VERSION_CONFLICT", "排期已进入发送或无权取消，无法保证撤回"
            )
        return row


def revise(repo, id):
    return cancel(repo, id, invalidating=True)


@dataclass
class ScheduledPublishGrant:
    """Created only by the trusted worker from a persisted human approval.

    Never serialized from a request, Skill output or authorization_snapshot.
    Policy checks exact tool arguments; handler consumes under a row lock after
    account admission and immutable media snapshot, immediately before send.
    """

    repo: Repository
    intent_id: str
    lease: str
    media: list = field(default_factory=list)
    snapshot: dict | None = field(default=None, init=False, repr=False)
    sent: bool = False
    last_error: str = ""
    next_eligible_at: str | None = None

    def valid_scope(self, context):
        return (
            context.entrypoint == "social_ops_scheduled"
            and context.execution_mode == "scheduled"
            and context.permission_mode == "ask"
            and context.org_id == str(self.repo.scope.org_id)
            and context.actor_user_id == str(self.repo.scope.actor_user_id)
            and context.workspace_owner_id == context.actor_user_id
            and context.context_scope == "user"
            and context.conversation_id is None
            and context.task_id == self.intent_id
            and context.call_id in (None, "social-publish:" + self.intent_id)
            and context.authorized_tool_names == frozenset({"agent_reach"})
        )

    def validate(self):
        with self.repo.cursor() as c:
            c.execute(
                "SELECT * FROM social_publish_intents WHERE id=%s AND org_id=%s",
                (self.intent_id, self.repo.scope.org_id),
            )
            row = c.fetchone()
            now = datetime.now(timezone.utc)
            if (
                not row
                or row["state"] != "preparing"
                or str(row["lease_token"]) != self.lease
                or row["lease_expires_at"] <= now
                or row["scheduled_at"] > now
                or row["authorized_until"] < now
                or row["grant_consumed_at"]
                or str(row["approved_by"]) != str(self.repo.scope.actor_user_id)
                or row["grant_hash"] != authorization(row)
            ):
                raise ReachError(
                    "ACCESS_DENIED", "定时发布授权不存在、过期、撤销或已消费"
                )
        validate(self.repo, row)
        self.snapshot = row
        return row

    def permits(self, name, args):
        if name != "agent_reach":
            return False
        try:
            # Policy is pure: the worker and refresh_context load trusted facts;
            # the handler revalidates and consumes them under locks before IO.
            row = self.snapshot
            now = datetime.now(timezone.utc)
            if (
                self.sent
                or not row
                or row["state"] != "preparing"
                or row["authorized_until"] < now
                or row["lease_expires_at"] <= now
                or row["grant_hash"] != authorization(row)
            ):
                return False
            data = row["payload"]["data"]
            if len(self.media) != len(data["assets"]):
                return False
            a = thaw(args)
            expected = {
                "task": "操作者确认后立即发布当前图文包",
                "platform": "xiaohongshu",
                "action": "create_post",
                "connection_id": str(row["connection_id"]),
                "title": data["title"],
                "content": data["body"],
                "tags": data["tags"],
                "visibility": row["visibility"],
                "media": self.media,
            }
            return a == expected
        except Exception:
            return False

    def begin_send(self, connection, request, media):
        row = self.validate()
        data = row["payload"]["data"]
        expected = {
            "platform": "xiaohongshu",
            "action": "create_post",
            "connection_id": str(row["connection_id"]),
            "title": data["title"],
            "content": data["body"],
            "tags": data["tags"],
            "visibility": row["visibility"],
        }
        if any(getattr(request, key, None) != value for key, value in expected.items()):
            raise ReachError("ACCESS_DENIED", "实际发布参数不符合精确授权")
        if (
            connection["credential_version"] != row["credential_version"]
            or str(connection["id"]) != str(row["connection_id"])
            or [m["sha256"] for m in media]
            != [m["sha256"] for m in row["payload"]["data"]["assets"]]
        ):
            raise ReachError("RESOURCE_CHANGED", "账号或媒体快照与授权不符")
        with self.repo.cursor() as c:
            # Locks the content and connection with the final consumption, so a
            # concurrent version/reconnect cannot cross the send boundary first.
            c.execute(
                "SELECT current_revision FROM social_contents WHERE id=%s FOR UPDATE",
                (row["content_id"],),
            )
            if c.fetchone()["current_revision"] != row["revision"]:
                raise ReachError("RESOURCE_CHANGED", "内容已变化")
            c.execute(
                "SELECT credential_version,status FROM reach_connections WHERE id=%s FOR UPDATE",
                (row["connection_id"],),
            )
            conn = c.fetchone()
            if (
                conn["status"] != "active"
                or conn["credential_version"] != row["credential_version"]
            ):
                raise ReachError("CREDENTIAL_CHANGED", "账号已变化")
            c.execute(
                """UPDATE social_publish_intents SET state='dispatching',grant_consumed_at=now(),lease_expires_at=now()+interval '210 seconds',updated_at=now()
            WHERE id=%s AND org_id=%s AND owner_id=%s AND state='preparing' AND lease_token=%s
            AND grant_consumed_at IS NULL AND grant_hash=%s AND authorized_until>=now() AND lease_expires_at>now() RETURNING id""",
                (
                    self.intent_id,
                    self.repo.scope.org_id,
                    self.repo.scope.actor_user_id,
                    self.lease,
                    authorization(row),
                ),
            )
            if not c.fetchone():
                raise ReachError("ACCESS_DENIED", "授权已取消、过期或被其他Worker消费")
        self.sent = True


class PublishWorker:
    def __init__(self, db, scope):
        self.db, self.repo = db, Repository(db.pool, scope)

    def claim(self, id):
        enabled()
        with self.repo.cursor() as c:
            c.execute(
                """UPDATE social_publish_intents SET state='preparing',lease_token=%s,lease_expires_at=now()+interval '210 seconds',updated_at=now()
            WHERE id=%s AND org_id=%s AND grant_hash IS NOT NULL AND grant_consumed_at IS NULL AND scheduled_at<=now() AND authorized_until>=now()
            AND ((state IN ('scheduled','waiting_budget') AND next_attempt_at<=now()) OR (state='preparing' AND lease_expires_at<now())) RETURNING *""",
                (str(uuid4()), id, self.repo.scope.org_id),
            )
            return c.fetchone()

    async def tick(self, id):
        row = await asyncio.to_thread(self.claim, id)
        if not row:
            return
        grant = ScheduledPublishGrant(self.repo, str(id), str(row["lease_token"]))
        try:
            await asyncio.to_thread(grant.validate)
            await publishing.confirm_intent(
                self.db, self.repo, str(id), row["approval_hash"], scheduled_grant=grant
            )
        except BaseException as e:
            if grant.sent:
                raise
            grant.last_error = getattr(e, "code", "PREPARATION_FAILED")
            if isinstance(e, asyncio.CancelledError):
                raise
        if not grant.sent:
            code = grant.last_error or "PREPARATION_FAILED"
            waiting = code == "ACCESS_BUDGET_WAIT"
            with self.repo.cursor() as c:
                c.execute(
                    """UPDATE social_publish_intents SET state=%s,error=%s,next_attempt_at=%s,
                grant_hash=CASE WHEN %s THEN grant_hash ELSE NULL END,lease_token=NULL,updated_at=now()
                WHERE id=%s AND org_id=%s AND state='preparing' AND lease_token=%s""",
                    (
                        "waiting_budget" if waiting else "paused",
                        code,
                        grant.next_eligible_at
                        or datetime.now(timezone.utc) + timedelta(seconds=60),
                        waiting,
                        id,
                        self.repo.scope.org_id,
                        grant.lease,
                    ),
                )
