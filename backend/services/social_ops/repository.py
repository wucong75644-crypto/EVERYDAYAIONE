"""Short scoped transactions and immutable editorial revisions."""

import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
from psycopg.types.json import Jsonb
from services.agent.agent_reach.connections import Connections
from services.agent.agent_reach.contracts import ReachError

TERMINAL = {"completed", "partial", "failed", "cancelled"}


def digest(data):
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def display_url(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", p.fragment))


def source_key(url):
    match = re.search(r"/(?:explore|discovery/item)/([A-Za-z0-9]+)", urlsplit(url).path)
    return "xiaohongshu:" + match[1] if match else digest(display_url(url))


class Repository(Connections):
    def profiles(self):
        with self.cursor() as c:
            c.execute(
                """SELECT p.*,r.id AS revision_id,r.data FROM social_profiles p JOIN social_profile_revisions r
                ON r.profile_id=p.id AND r.revision=p.current_revision WHERE p.org_id=%s ORDER BY p.created_at DESC""",
                (self.scope.org_id,),
            )
            return c.fetchall()

    def save_profile(self, data, profile_id=None):
        payload = data.model_dump(exclude={"expected_revision"})
        with self.cursor() as c:
            if profile_id:
                c.execute(
                    "SELECT * FROM social_profiles WHERE id=%s AND org_id=%s FOR UPDATE",
                    (profile_id, self.scope.org_id),
                )
                old = c.fetchone()
                if not old:
                    raise ReachError("NOT_FOUND", "画像不存在")
                if old["current_revision"] != data.expected_revision:
                    raise ReachError("VERSION_CONFLICT", "画像已更新")
                revision = old["current_revision"] + 1
                c.execute(
                    "UPDATE social_profiles SET name=%s,current_revision=%s WHERE id=%s",
                    (data.name, revision, profile_id),
                )
            else:
                profile_id = str(uuid4())
                revision = 1
                c.execute(
                    "INSERT INTO social_profiles(id,org_id,name,created_by) VALUES(%s,%s,%s,%s)",
                    (
                        profile_id,
                        self.scope.org_id,
                        data.name,
                        self.scope.actor_user_id,
                    ),
                )
            c.execute(
                """INSERT INTO social_profile_revisions(org_id,profile_id,revision,data,sha256,created_by)
                VALUES(%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    self.scope.org_id,
                    profile_id,
                    revision,
                    Jsonb(payload),
                    digest(payload),
                    self.scope.actor_user_id,
                ),
            )
            return c.fetchone()

    def _enqueue(self, c, job, generation, key, action, request, page=1):
        c.execute(
            """INSERT INTO social_research_items(org_id,job_id,generation,item_key,action,page,request)
            VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (
                self.scope.org_id,
                job["id"],
                generation,
                key,
                action,
                page,
                Jsonb(request),
            ),
        )

    def seed(self, c, job, generation):
        plan = job["plan"]
        base = {
            "task": "根据已保存采集规则读取研究资料",
            "platform": "xiaohongshu",
            "connection_id": str(job["connection_id"]),
            "max_results": min(plan["max_items"], 10),
        }
        for query in plan["keywords"]:
            self._enqueue(
                c,
                job,
                generation,
                digest(query),
                "search",
                {**base, "action": "search", "query": query},
            )
        for url in plan["sample_urls"][: plan["max_items"]]:
            self._enqueue(
                c,
                job,
                generation,
                source_key(url),
                "read",
                {**base, "action": "read", "url": url},
            )

    def create_job(self, data):
        if not data.keywords and not data.sample_urls:
            raise ReachError("INVALID_ARGUMENTS", "需要关键词或样本链接")
        self.load(str(data.connection_id), "xiaohongshu")
        with self.cursor() as c:
            c.execute(
                """SELECT r.id FROM social_profiles p JOIN social_profile_revisions r
                ON p.id=r.profile_id AND p.current_revision=r.revision WHERE p.id=%s AND p.org_id=%s""",
                (data.profile_id, self.scope.org_id),
            )
            profile = c.fetchone()
            if not profile:
                raise ReachError("NOT_FOUND", "画像不存在")
            c.execute(
                """INSERT INTO social_research_jobs(org_id,owner_id,client_request_id,connection_id,profile_revision_id,plan)
                VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(org_id,client_request_id) DO NOTHING RETURNING *""",
                (
                    self.scope.org_id,
                    self.scope.actor_user_id,
                    data.client_request_id,
                    data.connection_id,
                    profile["id"],
                    Jsonb(data.model_dump(mode="json")),
                ),
            )
            row = c.fetchone()
            if row:
                self.seed(c, row, 1)
            else:
                c.execute(
                    "SELECT * FROM social_research_jobs WHERE org_id=%s AND client_request_id=%s",
                    (self.scope.org_id, data.client_request_id),
                )
                row = c.fetchone()
                if row["plan"] != data.model_dump(mode="json"):
                    raise ReachError("VERSION_CONFLICT", "同一请求标识对应不同采集规则")
            return self.safe_job(row)

    @staticmethod
    def safe_job(row):
        row = dict(row)
        row["plan"] = {
            **row["plan"],
            "sample_urls": [display_url(u) for u in row["plan"]["sample_urls"]],
        }
        return row

    def jobs(self):
        with self.cursor() as c:
            c.execute(
                "SELECT * FROM social_research_jobs WHERE org_id=%s ORDER BY created_at DESC LIMIT 100",
                (self.scope.org_id,),
            )
            return [self.safe_job(r) for r in c.fetchall()]

    def job(self, id):
        with self.cursor() as c:
            c.execute(
                "SELECT * FROM social_research_jobs WHERE id=%s AND org_id=%s",
                (id, self.scope.org_id),
            )
            row = c.fetchone()
            if not row:
                raise ReachError("NOT_FOUND", "研究任务不存在")
            c.execute(
                """SELECT id,generation,action,page,status,attempts,error,next_eligible_at,result_meta FROM social_research_items
                WHERE job_id=%s AND org_id=%s AND generation=%s ORDER BY action,page""",
                (id, self.scope.org_id, row["generation"]),
            )
            row = self.safe_job(row)
            row["items"] = c.fetchall()
            c.execute(
                "SELECT count(*) AS n FROM social_source_snapshots WHERE job_id=%s AND org_id=%s",
                (id, self.scope.org_id),
            )
            row["source_count"] = c.fetchone()["n"]
            return row

    def sources(self, id, *, limit=500, offset=0, generation=None, evidence_ids=None):
        self.job(id)
        query = "SELECT * FROM social_source_snapshots WHERE job_id=%s AND org_id=%s"
        params = [id, self.scope.org_id]
        if generation is not None:
            query += " AND generation=%s"
            params.append(generation)
        if evidence_ids is not None:
            query += " AND id=ANY(%s::uuid[])"
            params.append([str(i) for i in evidence_ids])
        query += " ORDER BY fetched_at DESC,id DESC LIMIT %s OFFSET %s"
        params.extend([min(limit, 2000), offset])
        with self.cursor() as c:
            c.execute(query, params)
            return c.fetchall()

    def snapshot(self, id):
        with self.cursor() as c:
            c.execute(
                "SELECT * FROM social_source_snapshots WHERE id=%s AND org_id=%s",
                (id, self.scope.org_id),
            )
            row = c.fetchone()
            if not row:
                raise ReachError("NOT_FOUND", "来源快照不存在")
            return row

    def transition(self, id, action):
        with self.cursor() as c:
            c.execute(
                "SELECT * FROM social_research_jobs WHERE id=%s AND org_id=%s FOR UPDATE",
                (id, self.scope.org_id),
            )
            job = c.fetchone()
            if not job:
                raise ReachError("NOT_FOUND", "任务不存在")
            if action == "resume":
                if job["status"] not in {
                    "paused_auth",
                    "paused_platform",
                    "needs_input",
                }:
                    raise ReachError("VERSION_CONFLICT", "任务当前不可恢复")
                # Validate live access; a breaker must be cleared by an admin separately.
                connection = self.load(str(job["connection_id"]), "xiaohongshu")
                from services.agent.agent_reach.access_budget import AccessBudgetService

                if AccessBudgetService(self.pool, self.scope).status(connection)[
                    "breaker"
                ]:
                    raise ReachError("ACCESS_DENIED", "请先处理账号异常并恢复访问")
                c.execute(
                    "UPDATE social_research_items SET status='queued',next_eligible_at=now(),lease_token=NULL WHERE job_id=%s AND status IN ('paused','running')",
                    (id,),
                )
                status = "queued"
            elif action == "pause":
                if job["status"] in TERMINAL:
                    raise ReachError("VERSION_CONFLICT", "任务已结束")
                status = "needs_input"
            elif action == "cancel":
                status = "cancelled"
            else:
                raise ReachError("INVALID_ARGUMENTS", "不支持此状态操作")
            c.execute(
                "UPDATE social_research_jobs SET status=%s,next_eligible_at=now() WHERE id=%s",
                (status, id),
            )
            return {"status": status}

    def contents(self, job_id):
        self.job(job_id)
        with self.cursor() as c:
            c.execute(
                """SELECT c.id,c.job_id,c.current_revision,r.data,r.manifest,r.checks FROM social_contents c
                JOIN social_content_revisions r ON r.content_id=c.id AND r.revision=c.current_revision
                WHERE c.org_id=%s AND c.job_id=%s ORDER BY c.created_at DESC""",
                (self.scope.org_id, job_id),
            )
            return c.fetchall()

    def save_content(
        self, c, job_id, data, manifest, checks, content_id=None, expected=None
    ):
        if content_id:
            c.execute(
                "SELECT * FROM social_contents WHERE id=%s AND org_id=%s AND job_id=%s FOR UPDATE",
                (content_id, self.scope.org_id, job_id),
            )
            old = c.fetchone()
            if not old:
                raise ReachError("NOT_FOUND", "草稿不存在")
            if old["current_revision"] != expected:
                raise ReachError("VERSION_CONFLICT", "草稿版本冲突")
            revision = expected + 1
            c.execute(
                "UPDATE social_contents SET current_revision=%s WHERE id=%s",
                (revision, content_id),
            )
        else:
            content_id = str(uuid4())
            revision = 1
            c.execute(
                "INSERT INTO social_contents(id,org_id,job_id) VALUES(%s,%s,%s)",
                (content_id, self.scope.org_id, job_id),
            )
        c.execute(
            """INSERT INTO social_content_revisions(org_id,content_id,revision,data,manifest,checks,created_by)
            VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (
                self.scope.org_id,
                content_id,
                revision,
                Jsonb(data),
                Jsonb(manifest),
                Jsonb(checks),
                self.scope.actor_user_id,
            ),
        )
        return c.fetchone()

    def retry_item(self, item_id):
        with self.cursor() as c:
            c.execute(
                "SELECT job_id FROM social_research_items WHERE id=%s AND org_id=%s",
                (item_id, self.scope.org_id),
            )
            found = c.fetchone()
            if not found:
                raise ReachError("NOT_FOUND", "研究项不存在")
            c.execute(
                "SELECT * FROM social_research_jobs WHERE id=%s FOR UPDATE",
                (found["job_id"],),
            )
            job = c.fetchone()
            if job["status"] == "cancelled":
                raise ReachError("VERSION_CONFLICT", "已取消任务不可重新派发")
            connection = self.load(str(job["connection_id"]), "xiaohongshu")
            from services.agent.agent_reach.access_budget import AccessBudgetService

            if AccessBudgetService(self.pool, self.scope).status(connection)["breaker"]:
                raise ReachError("ACCESS_DENIED", "请先由管理员处理账号异常并恢复访问")
            c.execute(
                """UPDATE social_research_items SET status='queued',attempts=0,error='',
                next_eligible_at=now(),lease_token=NULL WHERE id=%s AND job_id=%s AND generation=%s
                AND status='failed' RETURNING id""",
                (item_id, job["id"], job["generation"]),
            )
            if not c.fetchone():
                raise ReachError("VERSION_CONFLICT", "只能重试本轮失败的只读项")
            c.execute(
                "UPDATE social_research_jobs SET status='queued',next_eligible_at=now(),next_occurrence_at=NULL WHERE id=%s",
                (job["id"],),
            )
            return {"status": "queued"}
