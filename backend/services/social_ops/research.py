"""One leased, bounded public tool action per tick; snapshots commit atomically."""

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from psycopg.types.json import Jsonb
from services.agent.agent_reach.access_budget import AUTH_ERRORS, PLATFORM_ERRORS
from .repository import Repository, digest, display_url, source_key


class ResearchWorker:
    def __init__(self, db, scope):
        self.db = db
        self.repo = Repository(db.pool, scope)

    def claim(self, job_id):
        with self.repo.cursor() as c:
            c.execute(
                "SELECT * FROM social_research_jobs WHERE id=%s AND org_id=%s FOR UPDATE SKIP LOCKED",
                (job_id, self.repo.scope.org_id),
            )
            job = c.fetchone()
            if not job:
                return None
            if (
                job["status"] in {"completed", "partial", "failed"}
                and job["next_occurrence_at"]
                and job["next_occurrence_at"] <= datetime.now(timezone.utc)
            ):
                # Coalesce missed occurrences into one new generation.
                job["generation"] += 1
                self.repo.seed(c, job, job["generation"])
                c.execute(
                    "UPDATE social_research_jobs SET generation=%s,status='queued',next_occurrence_at=NULL WHERE id=%s",
                    (job["generation"], job_id),
                )
                job["status"] = "queued"
            if job["status"] not in {"queued", "running", "waiting_budget"}:
                return None
            c.execute(
                """SELECT * FROM social_research_items WHERE job_id=%s AND generation=%s AND
                ((status IN ('queued','waiting_budget') AND next_eligible_at<=now()) OR
                 (status='running' AND lease_expires_at<now()))
                ORDER BY CASE action WHEN 'search' THEN 0 WHEN 'read' THEN 1 ELSE 2 END,id
                FOR UPDATE SKIP LOCKED LIMIT 1""",
                (job_id, job["generation"]),
            )
            item = c.fetchone()
            if not item:
                self.finish(c, job)
                return None
            if job["plan"].get("purpose") == "metrics":
                from core.config import get_settings

                if not get_settings().social_ops_analytics_enabled:
                    return None
            token = str(uuid4())
            c.execute(
                "UPDATE social_research_items SET status='running',lease_token=%s,lease_expires_at=now()+interval '180 seconds',attempts=attempts+1 WHERE id=%s",
                (token, item["id"]),
            )
            c.execute(
                "UPDATE social_research_jobs SET status='running',next_eligible_at=now()+interval '180 seconds' WHERE id=%s",
                (job_id,),
            )
            item["lease_token"] = token
            item["attempts"] += 1
            return job, item

    def finish(self, c, job):
        c.execute(
            "SELECT status,count(*) AS n FROM social_research_items WHERE job_id=%s AND generation=%s GROUP BY status",
            (job["id"], job["generation"]),
        )
        counts = {r["status"]: r["n"] for r in c.fetchall()}
        if any(
            counts.get(s) for s in ("queued", "waiting_budget", "running", "paused")
        ):
            return
        c.execute(
            "SELECT count(*) AS n FROM social_source_snapshots WHERE job_id=%s AND generation=%s AND coverage->>'content_kind'='full_text'",
            (job["id"], job["generation"]),
        )
        full = c.fetchone()["n"]
        status = (
            "partial"
            if counts.get("failed")
            or (job["plan"]["max_items"] and not full and counts.get("completed"))
            else "completed"
        )
        # Successful empty discovery is a complete no-match result.
        c.execute(
            "SELECT count(*) AS n FROM social_source_snapshots WHERE job_id=%s AND generation=%s",
            (job["id"], job["generation"]),
        )
        if not c.fetchone()["n"] and not counts.get("failed"):
            status = "completed"
        c.execute(
            "SELECT count(*) AS n FROM social_source_snapshots WHERE job_id=%s AND generation=%s AND (coverage->>'truncated'='true' OR (coverage->>'content_kind'<>'snippet' AND coverage->>'status'='partial'))",
            (job["id"], job["generation"]),
        )
        if c.fetchone()["n"]:
            status = "partial"
        hours = job["plan"].get("interval_hours")
        c.execute(
            """UPDATE social_research_jobs SET status=%s,next_occurrence_at=%s WHERE id=%s""",
            (
                status,
                datetime.now(timezone.utc) + timedelta(hours=hours) if hours else None,
                job["id"],
            ),
        )

    def settle(self, job, item, result):
        with self.repo.cursor() as c:
            # Lock parent first, same order as claim/cancel; old workers cannot
            # commit after cancellation or after a lease is reissued.
            c.execute(
                "SELECT status FROM social_research_jobs WHERE id=%s FOR UPDATE",
                (job["id"],),
            )
            parent = c.fetchone()
            c.execute(
                """SELECT * FROM social_research_items WHERE id=%s AND lease_token=%s
                AND status='running' AND lease_expires_at>now() FOR UPDATE""",
                (item["id"], item["lease_token"]),
            )
            if (
                not c.fetchone()
                or not parent
                or parent["status"] not in {"running", "queued", "waiting_budget"}
            ):
                return
            code = result.error_message
            if result.status in {"error", "timeout"}:
                next_at = datetime.now(timezone.utc) + timedelta(
                    seconds=min(300, 30 * 2 ** item["attempts"])
                )
                if code == "ACCESS_BUDGET_WAIT":
                    state = "waiting_budget"
                    jobstate = "waiting_budget"
                    next_at = datetime.fromisoformat(
                        result.metadata["next_eligible_at"]
                    )
                elif code == "BUDGET_CONFIGURATION_REQUIRED":
                    state = "paused"
                    jobstate = "needs_input"
                elif code in AUTH_ERRORS:
                    state = "paused"
                    jobstate = "paused_auth"
                elif (
                    code in PLATFORM_ERRORS
                    or code == "UPSTREAM_CHANGED"
                    and item["attempts"] >= 3
                ):
                    state = "paused"
                    jobstate = "paused_platform"
                elif (
                    code in {"NETWORK_ERROR", "BUDGET_EXHAUSTED"}
                    and item["attempts"] < 3
                ):
                    state = "queued"
                    jobstate = "queued"
                else:
                    state = "failed"
                    jobstate = "queued"
                c.execute(
                    "UPDATE social_research_items SET status=%s,error=%s,next_eligible_at=%s,lease_token=NULL WHERE id=%s",
                    (state, code, next_at, item["id"]),
                )
                c.execute(
                    "UPDATE social_research_jobs SET status=%s,last_error=%s,next_eligible_at=%s WHERE id=%s",
                    (jobstate, code, next_at, job["id"]),
                )
                return
            sources = result.metadata.get("sources", [])
            if job["plan"].get("purpose") == "metrics":
                from .metrics import collect_snapshot, verified
                from services.agent.agent_reach.contracts import ReachError

                try:
                    if item["action"] == "read":
                        collect_snapshot(self.repo, c, job, item, sources)
                    elif item["action"] == "read_comments" and sources:
                        intent = verified(self.repo, c, job["plan"]["intent_id"])
                        if any(
                            s.get("metric_metadata", {}).get("remote_id")
                            != intent["verification"]["remote_id"]
                            or s.get("metric_metadata", {}).get("author_id")
                            != intent["account_id"]
                            for s in sources
                        ):
                            raise ReachError(
                                "ACCOUNT_MISMATCH", "评论返回的帖子或作者ID不匹配"
                            )
                except ReachError as error:
                    c.execute(
                        "UPDATE social_research_items SET status='paused',error=%s,lease_token=NULL WHERE id=%s",
                        (error.code, item["id"]),
                    )
                    c.execute(
                        "UPDATE social_research_jobs SET status='needs_input',last_error=%s WHERE id=%s",
                        (error.code, job["id"]),
                    )
                    return
            for source in sources:
                url = source["url"]
                key = source_key(url) + (
                    "#" + url.split("#", 1)[1] if "#" in url else ""
                )
                body = source.get("content", source.get("snippet", ""))
                coverage = {
                    k: source.get(k)
                    for k in ("content_kind", "truncated", "published_at")
                }
                coverage.update(
                    {
                        "warnings": result.metadata.get("warnings", []),
                        "pagination": result.metadata.get("pagination", {}),
                        "missing": ["完整主页与作品列表", "运营指标"],
                        "scope": "搜索发现/指定样本，非全量",
                        "status": result.status,
                    }
                )
                c.execute(
                    """INSERT INTO social_source_snapshots(org_id,job_id,item_id,generation,source_key,source_url,title,body,body_hash,coverage,adapter_version,metrics)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (
                        self.repo.scope.org_id,
                        job["id"],
                        item["id"],
                        job["generation"],
                        key,
                        display_url(url),
                        source["title"],
                        body,
                        hashlib.sha256(body.encode()).hexdigest(),
                        Jsonb(coverage),
                        result.metadata.get("backend", "unknown") + ":phase12-v1",
                        Jsonb(source.get("metrics", {})),
                    ),
                )
                if item["action"] == "search":
                    c.execute(
                        "SELECT count(*) AS n FROM social_research_items WHERE job_id=%s AND generation=%s AND action='read'",
                        (job["id"], job["generation"]),
                    )
                    if c.fetchone()["n"] < job["plan"]["max_items"]:
                        self.repo._enqueue(
                            c,
                            job,
                            job["generation"],
                            source_key(url),
                            "read",
                            {
                                "task": "读取搜索发现的研究样本",
                                "platform": "xiaohongshu",
                                "action": "read",
                                "url": url,
                                "connection_id": str(job["connection_id"]),
                            },
                        )
            if item["action"] == "read":
                for page in range(1, job["plan"]["comment_pages"] + 1):
                    self.repo._enqueue(
                        c,
                        job,
                        job["generation"],
                        item["item_key"],
                        "read_comments",
                        {
                            **item["request"],
                            "action": "read_comments",
                            "page": page,
                            "max_results": 10,
                        },
                        page,
                    )
            result_meta = {
                k: result.metadata.get(k)
                for k in ("warnings", "pagination", "access_cost")
            }
            result_meta["status"] = result.status
            result_meta["returned_sources"] = len(sources)
            c.execute(
                "UPDATE social_research_items SET status='completed',lease_token=NULL,error='',result_meta=%s WHERE id=%s",
                (Jsonb(result_meta), item["id"]),
            )
            c.execute(
                "UPDATE social_research_jobs SET next_eligible_at=now(),last_error='' WHERE id=%s",
                (job["id"],),
            )
            self.finish(c, job)

    async def tick(self, job_id):
        claimed = await asyncio.to_thread(self.claim, job_id)
        if not claimed:
            return
        job, item = claimed
        from services.agent.tool_executor import ToolExecutor

        executor = ToolExecutor(
            db=self.db,
            user_id=str(job["owner_id"]),
            org_id=str(job["org_id"]),
            conversation_id=None,
            task_id=str(job["id"]),
            allowed_tool_names={"agent_reach"},
            execution_mode="scheduled",
            permission_mode="auto",
            tool_policy_snapshot={"version": 1, "allowed_tools": ["agent_reach"]},
            tool_entrypoint="model",
        )
        result = await executor.execute(
            "agent_reach", item["request"], call_id=str(item["lease_token"])
        )
        await asyncio.to_thread(self.settle, job, item, result)
