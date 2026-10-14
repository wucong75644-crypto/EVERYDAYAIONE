"""Durable account-level admission; unknown dispatches remain charged."""

import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from uuid import uuid4
from .connections import Connections
from .contracts import ReachError

AUTH_ERRORS = {"AUTH_REQUIRED", "AUTH_EXPIRED", "ACCOUNT_MISMATCH"}
PLATFORM_ERRORS = {"PLATFORM_BLOCKED", "CAPTCHA_REQUIRED", "RATE_LIMITED"}


def account_key(connection):
    return hashlib.sha256(
        (connection["platform"] + ":" + connection["account_id"]).encode()
    ).hexdigest()


class AccessBudgetService(Connections):
    def status(self, connection):
        key = account_key(connection)
        with self.cursor() as c:
            c.execute(
                "INSERT INTO reach_access_budgets(account_key) VALUES(%s) ON CONFLICT DO NOTHING",
                (key,),
            )
            c.execute("SELECT * FROM reach_access_budgets WHERE account_key=%s", (key,))
            row = c.fetchone()
            if row["day"] != datetime.now(ZoneInfo(row["timezone"])).date():
                row["consumed"] = 0
            return row

    def configure(self, connection, data):
        with self.cursor(admin=True) as c:
            c.execute(
                """INSERT INTO reach_access_budgets(account_key,daily_limit,min_interval_seconds,timezone,allowed_start_hour,allowed_end_hour,max_comment_pages)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(account_key) DO UPDATE SET
                daily_limit=EXCLUDED.daily_limit,min_interval_seconds=EXCLUDED.min_interval_seconds,
                timezone=EXCLUDED.timezone,allowed_start_hour=EXCLUDED.allowed_start_hour,
                allowed_end_hour=EXCLUDED.allowed_end_hour,max_comment_pages=EXCLUDED.max_comment_pages RETURNING *""",
                (
                    account_key(connection),
                    data.daily_limit,
                    data.min_interval_seconds,
                    data.timezone,
                    data.allowed_start_hour,
                    data.allowed_end_hour,
                    data.max_comment_pages,
                ),
            )
            return c.fetchone()

    def reserve(self, connection, request):
        key = account_key(connection)
        # XHS checks identity then performs the action. Browser-internal traffic
        # is unobservable; comments incur an explicit conservative page estimate.
        units = (
            2 + (request.page if request.action == "read_comments" else 0)
            if request.platform == "xiaohongshu"
            else 1
        )
        with self.cursor() as c:
            c.execute(
                "INSERT INTO reach_access_budgets(account_key) VALUES(%s) ON CONFLICT DO NOTHING",
                (key,),
            )
            c.execute(
                "SELECT * FROM reach_access_budgets WHERE account_key=%s FOR UPDATE",
                (key,),
            )
            row = c.fetchone()
            now = datetime.now(timezone.utc)
            zone = ZoneInfo(row["timezone"])
            local = now.astimezone(zone)
            day = local.date()
            consumed = row["consumed"] if row["day"] == day else 0
            if units > row["daily_limit"]:
                raise ReachError(
                    "BUDGET_CONFIGURATION_REQUIRED",
                    "单项估计成本超过每日额度，请调整配置",
                )
            if (
                request.action == "read_comments"
                and request.page > row["max_comment_pages"]
            ):
                raise ReachError("COMMENT_PAGE_LIMIT", "请求超过账号评论页数上限")
            if row["breaker"]:
                raise ReachError(row["breaker"], "账号访问已暂停，请处理原因后手动恢复")
            wait_reason = "interval"
            if consumed + units > row["daily_limit"]:
                wait_reason = "daily_limit"
                next_at = datetime.combine(
                    day + timedelta(days=1), datetime.min.time(), zone
                ).astimezone(timezone.utc)
            else:
                next_at = row["next_allowed_at"]
            start, end = row["allowed_start_hour"], row["allowed_end_hour"]
            inside = (
                start <= local.hour < end
                if start < end
                else local.hour >= start or local.hour < end
            )
            if not inside:
                wait_reason = "access_window"
                candidate = local.replace(hour=start, minute=0, second=0, microsecond=0)
                if candidate <= local:
                    candidate += timedelta(days=1)
                next_at = max(next_at, candidate.astimezone(timezone.utc))
            if next_at > now:
                error = ReachError(
                    "ACCESS_BUDGET_WAIT", "账号访问额度或间隔尚未恢复", retryable=True
                )
                error.next_eligible_at = next_at.isoformat()
                error.wait_reason = wait_reason
                raise error
            attempt = str(uuid4())
            c.execute(
                """UPDATE reach_access_budgets SET day=%s,consumed=%s,next_allowed_at=%s,
                credential_version=%s WHERE account_key=%s""",
                (
                    day,
                    consumed + units,
                    now + timedelta(seconds=row["min_interval_seconds"]),
                    connection["credential_version"],
                    key,
                ),
            )
            c.execute(
                """INSERT INTO reach_access_attempts(id,org_id,connection_id,account_key,action,units,basis)
                VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (
                    attempt,
                    self.scope.org_id,
                    connection["id"],
                    key,
                    request.action,
                    units,
                    (
                        "estimated: identity+action HTTP; browser internal requests unknown; comment reload weighted"
                        if request.platform == "xiaohongshu"
                        else "estimated tool invocation"
                    ),
                ),
            )
            return attempt

    def settle(self, connection, attempt, code="success"):
        with self.cursor() as c:
            c.execute(
                "UPDATE reach_access_attempts SET outcome=%s WHERE id=%s AND org_id=%s",
                (code, attempt, self.scope.org_id),
            )
            breaker = code if code in AUTH_ERRORS | PLATFORM_ERRORS else ""
            if code == "UPSTREAM_CHANGED":
                c.execute(
                    """SELECT outcome FROM reach_access_attempts WHERE account_key=%s ORDER BY created_at DESC LIMIT 3""",
                    (account_key(connection),),
                )
                if [r["outcome"] for r in c.fetchall()] == ["UPSTREAM_CHANGED"] * 3:
                    breaker = code
            if breaker:
                c.execute(
                    "UPDATE reach_access_budgets SET breaker=%s WHERE account_key=%s",
                    (breaker, account_key(connection)),
                )

    def recover(self, connection):
        # No network probing or repeated captcha attempts here. Identity is
        # verified by the existing connection flow / next bounded read.
        with self.cursor(admin=True) as c:
            c.execute(
                "UPDATE reach_access_budgets SET breaker=%s WHERE account_key=%s",
                ("", account_key(connection)),
            )

    def release_not_sent(self, attempt):
        """Release only a caller-proven pre-network rejection, idempotently."""
        with self.cursor() as c:
            c.execute(
                "SELECT * FROM reach_access_attempts WHERE id=%s AND org_id=%s FOR UPDATE",
                (attempt, self.scope.org_id),
            )
            row = c.fetchone()
            if not row or row["outcome"] == "not_sent":
                return
            c.execute(
                "SELECT * FROM reach_access_budgets WHERE account_key=%s FOR UPDATE",
                (row["account_key"],),
            )
            budget = c.fetchone()
            if (
                row["created_at"].astimezone(ZoneInfo(budget["timezone"])).date()
                == budget["day"]
            ):
                c.execute(
                    "UPDATE reach_access_budgets SET consumed=GREATEST(0,consumed-%s) WHERE account_key=%s",
                    (row["units"], row["account_key"]),
                )
            c.execute(
                "UPDATE reach_access_attempts SET outcome='not_sent' WHERE id=%s",
                (attempt,),
            )
