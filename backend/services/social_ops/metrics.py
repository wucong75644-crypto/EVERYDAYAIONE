"""Verified publication identity, collection orchestration and immutable observations."""

from datetime import datetime, timezone
from uuid import uuid4
from psycopg.types.json import Jsonb
from services.agent.agent_reach.contracts import ReachError, platform_for_url
from services.agent.agent_reach.xhs_adapter import XHSAdapter
from .repository import digest, display_url
from .analytics import METRICS, nullable_metrics, timestamp


def verified(repo, c, intent_id):
    c.execute(
        """SELECT i.*,j.profile_revision_id,j.id AS original_job_id,c.account_id,c.platform,r.data AS content,r.id AS content_revision_id
        FROM social_publish_intents i JOIN social_contents s ON s.id=i.content_id AND s.org_id=i.org_id
        JOIN social_research_jobs j ON j.id=s.job_id AND j.org_id=i.org_id
        JOIN reach_connections c ON c.id=i.connection_id AND c.org_id=i.org_id
        JOIN social_content_revisions r ON r.content_id=i.content_id AND r.org_id=i.org_id AND r.revision=i.revision
        WHERE i.id=%s AND i.org_id=%s""",
        (intent_id, repo.scope.org_id),
    )
    row = c.fetchone()
    if not row:
        raise ReachError("NOT_FOUND", "发布内容不存在或无权访问")
    if row["state"] != "verified" or not (row.get("verification") or {}).get(
        "remote_id"
    ):
        raise ReachError("NEEDS_INPUT", "内容没有可信平台ID，请先完成平台核验")
    c.execute(
        "SELECT id FROM social_publish_intents WHERE org_id=%s AND connection_id=%s AND state='verified' AND verification->>'remote_id'=%s AND id<>%s LIMIT 1",
        (
            repo.scope.org_id,
            row["connection_id"],
            row["verification"]["remote_id"],
            row["id"],
        ),
    )
    if c.fetchone():
        raise ReachError(
            "NEEDS_INPUT",
            "同一账号平台ID关联了多条发布意图，请先纠正核验，不能猜测归属",
        )
    return row


def create_collection(repo, intent_id, data, *, cursor=None):
    def create(c):
        intent = verified(repo, c, intent_id)
        if intent["platform"] != "xiaohongshu":
            raise ReachError("UNSUPPORTED_OPERATION", "当前仅启用小红书结构化指标回收")
        if (
            platform_for_url(data.url) != intent["platform"]
            or XHSAdapter.target(data.url)["feed_id"]
            != intent["verification"]["remote_id"]
        ):
            raise ReachError("INVALID_ARGUMENTS", "采集链接与已核验帖子ID不一致")
        plan = {
            "keywords": [],
            "sample_urls": [data.url],
            "max_items": 1,
            "comment_pages": data.comment_pages,
            "interval_hours": None,
            "purpose": "metrics",
            "intent_id": str(intent_id),
            "published_at": (
                data.published_at.isoformat() if data.published_at else None
            ),
            "content_type": data.content_type,
        }
        c.execute(
            """INSERT INTO social_research_jobs(org_id,owner_id,client_request_id,connection_id,profile_revision_id,plan)
          VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(org_id,client_request_id) DO NOTHING RETURNING *""",
            (
                repo.scope.org_id,
                repo.scope.actor_user_id,
                data.client_request_id,
                intent["connection_id"],
                intent["profile_revision_id"],
                Jsonb(plan),
            ),
        )
        job = c.fetchone()
        if not job:
            c.execute(
                "SELECT * FROM social_research_jobs WHERE org_id=%s AND client_request_id=%s",
                (repo.scope.org_id, data.client_request_id),
            )
            job = c.fetchone()
            if job["plan"] != plan:
                raise ReachError("VERSION_CONFLICT", "相同请求编号已绑定其他回收输入")
        else:
            repo.seed(c, job, 1)
            c.execute(
                """INSERT INTO social_metric_collections(org_id,job_id,intent_id,published_at,content_type)
                VALUES(%s,%s,%s,%s,%s)""",
                (
                    repo.scope.org_id,
                    job["id"],
                    intent_id,
                    data.published_at,
                    data.content_type,
                ),
            )
        return repo.safe_job(job)

    if cursor is not None:
        return create(cursor)
    repo.load(str(_connection(repo, intent_id)), "xiaohongshu")
    with repo.cursor() as c:
        return create(c)


def _connection(repo, intent_id):
    with repo.cursor() as c:
        return verified(repo, c, intent_id)["connection_id"]


def save_snapshot(repo, c, intent, row, key):
    metrics = nullable_metrics(row["metrics"])
    c.execute(
        """INSERT INTO social_metric_snapshots(org_id,intent_id,connection_id,account_id,remote_id,platform,
        content_type,observed_at,published_at,metrics,raw_metrics,coverage,source,adapter_version,created_by,dedupe_key)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(org_id,dedupe_key) DO NOTHING RETURNING *""",
        (
            repo.scope.org_id,
            intent["id"],
            intent["connection_id"],
            intent["account_id"],
            intent["verification"]["remote_id"],
            intent["platform"],
            row["content_type"],
            row["observed_at"],
            row.get("published_at"),
            Jsonb(metrics),
            Jsonb(row.get("raw_metrics", {})),
            Jsonb(row["coverage"]),
            row["source"],
            row["adapter_version"],
            repo.scope.actor_user_id,
            key,
        ),
    )
    saved = c.fetchone()
    if saved:
        return saved, False
    c.execute(
        "SELECT * FROM social_metric_snapshots WHERE org_id=%s AND dedupe_key=%s",
        (repo.scope.org_id, key),
    )
    return c.fetchone(), True


def collect_snapshot(repo, c, job, item, sources):
    """Runs inside the research checkpoint transaction; never parses counts from prose."""
    intent = verified(repo, c, job["plan"]["intent_id"])
    source = next((s for s in sources if s.get("content_kind") == "full_text"), None)
    meta = (source or {}).get("metric_metadata", {})
    if (
        not source
        or meta.get("remote_id") != intent["verification"]["remote_id"]
        or meta.get("author_id") != intent["account_id"]
    ):
        raise ReachError(
            "ACCOUNT_MISMATCH", "返回帖子或作者ID缺失/不符，无法确认属于当前账号"
        )
    published = timestamp(job["plan"].get("published_at"))
    raw_time = meta.get("published_at_raw")
    if (
        published is None
        and isinstance(raw_time, (int, float))
        and not isinstance(raw_time, bool)
    ):
        try:
            published = datetime.fromtimestamp(raw_time / 1000, timezone.utc)
        except (ValueError, OverflowError, OSError):
            pass
    observed = timestamp(source.get("fetched_at")) or datetime.now(timezone.utc)
    if published and published > observed:
        raise ReachError("NEEDS_INPUT", "发布时间晚于采集时点，请核对原始来源")
    return save_snapshot(
        repo,
        c,
        intent,
        {
            "metrics": source.get("metrics", {}),
            "raw_metrics": meta.get("raw_metrics", {}),
            "content_type": job["plan"]["content_type"],
            "observed_at": timestamp(source.get("fetched_at"))
            or datetime.now(timezone.utc),
            "published_at": published,
            "coverage": {
                "scope": "public_cumulative",
                "denominator": None,
                "fields": [
                    k for k, v in source.get("metrics", {}).items() if v is not None
                ],
                "publication_source": (
                    "operator"
                    if job["plan"].get("published_at")
                    else "note.time" if published else "unknown"
                ),
            },
            "source": meta["source"] + " " + display_url(source["url"]),
            "adapter_version": meta["adapter_version"],
        },
        "collection:" + str(item["id"]),
    )


def published_contents(
    repo, connection_id=None, profile_id=None, content_type=None, start=None, end=None
):
    with repo.cursor() as c:
        c.execute(
            """SELECT i.id,i.content_id,i.revision,i.connection_id,i.state,i.verification,i.error,
            i.payload->'data'->>'title' AS title,c.account_id,c.display_name,j.profile_revision_id,p.profile_id,
            (SELECT row_to_json(m) FROM social_metric_snapshots m WHERE m.intent_id=i.id AND m.org_id=i.org_id ORDER BY m.observed_at DESC,m.id LIMIT 1) AS latest,
            (SELECT row_to_json(j2) FROM social_metric_collections mc JOIN social_research_jobs j2 ON j2.id=mc.job_id AND j2.org_id=mc.org_id
              WHERE mc.intent_id=i.id AND mc.org_id=i.org_id ORDER BY j2.created_at DESC LIMIT 1) AS collection
            FROM social_publish_intents i JOIN reach_connections c ON c.id=i.connection_id AND c.org_id=i.org_id
            JOIN social_contents s ON s.id=i.content_id AND s.org_id=i.org_id
            JOIN social_research_jobs j ON j.id=s.job_id AND j.org_id=i.org_id
            JOIN social_profile_revisions p ON p.id=j.profile_revision_id AND p.org_id=i.org_id
            WHERE i.org_id=%s AND i.state IN ('verified','submitted','uncertain') ORDER BY i.created_at DESC LIMIT 500""",
            (repo.scope.org_id,),
        )
        rows = c.fetchall()
    result = []
    for row in rows:
        latest = row["latest"]
        if connection_id and str(row["connection_id"]) != str(connection_id):
            continue
        if profile_id and str(row["profile_id"]) != str(profile_id):
            continue
        if content_type and (not latest or latest["content_type"] != content_type):
            continue
        if start and (not latest or timestamp(latest["observed_at"]) < start):
            continue
        if end and (not latest or timestamp(latest["observed_at"]) > end):
            continue
        if row["collection"]:
            job = row["collection"]
            row["collection"] = {
                "id": job["id"],
                "status": job["status"],
                "last_error": job["last_error"],
            }
        result.append(row)
    return result


def snapshots(repo, intent_ids, start=None, end=None):
    with repo.cursor() as c:
        # Explicitly validate every requested identity, including across accounts/orgs.
        for intent_id in intent_ids:
            verified(repo, c, intent_id)
        c.execute(
            """SELECT * FROM (SELECT *,row_number() OVER(PARTITION BY intent_id ORDER BY observed_at DESC,id DESC) AS history_index,
            count(*) OVER(PARTITION BY intent_id) AS available_snapshots FROM social_metric_snapshots WHERE org_id=%s AND intent_id=ANY(%s::uuid[])
            AND (%s::timestamptz IS NULL OR observed_at>=%s) AND (%s::timestamptz IS NULL OR observed_at<=%s)) h WHERE history_index<=100
            ORDER BY observed_at DESC,id DESC""",
            (repo.scope.org_id, [str(i) for i in intent_ids], start, start, end, end),
        )
        rows = c.fetchall()
    prior = {}
    for row in reversed(rows):
        signature = (
            str(row["intent_id"]),
            row["content_type"],
            row["coverage"].get("scope"),
            row["coverage"].get("denominator"),
            tuple(k for k, v in row["metrics"].items() if v is not None),
        )
        previous = prior.get(signature)
        row["metric_change"] = {
            "previous_snapshot_id": str(previous["id"]) if previous else None,
            "scope": "仅同内容、同形式、同来源范围与字段覆盖的相邻已返回快照；缺失或零基数不算变化率",
            "deltas": {
                k: (
                    row["metrics"].get(k) - previous["metrics"][k]
                    if previous
                    and row["metrics"].get(k) is not None
                    and previous["metrics"].get(k) is not None
                    else None
                )
                for k in METRICS
            },
            "ratios": {
                k: (
                    (row["metrics"].get(k) - previous["metrics"][k])
                    / previous["metrics"][k]
                    if previous
                    and row["metrics"].get(k) is not None
                    and previous["metrics"].get(k) is not None
                    and previous["metrics"][k] > 0
                    else None
                )
                for k in METRICS
            },
        }
        prior[signature] = row
    return rows
