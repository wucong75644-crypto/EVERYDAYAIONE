"""Pinned review input and confirmed, traceable strategy proposals."""

import json
import re
from datetime import datetime, timezone
from uuid import uuid4
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from services.agent.agent_reach.contracts import ReachError
from services.skills.scheduled import bind_scheduled_skills
from .analytics import statistics
from .metrics import verified, snapshots
from .repository import digest
from .validation import Evidence, EvidenceReference

REVIEW_POLICY_VERSION = "observational-no-causes-v2"
REVIEW_INSTRUCTIONS = (
    "当前输入仅为观察数据，无实验或因果证据。possible_causes必须为空数组，"
    "不得因为引用存在就推断原因、粉丝基础、流量分发或平台机制。"
    "insufficient_evidence明确说明无法判断原因；不把未知写成事实。"
    "建议只能提出待验证的后续实验，不陈述未经证据支持的现状或效果。"
)

REVIEW_SKILLS = {
    "postmortem": "easel-content-postmortem",
    "comment_insights": "easel-comment-insights",
    "strategy": "easel-strategy-advisor",
}


class Conclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)
    evidence: list[Evidence] = Field(min_length=1, max_length=10)


class ReferencedConclusion(Conclusion):
    evidence: list[EvidenceReference] = Field(min_length=1, max_length=10)


class Suggestion(Conclusion):
    kind: Literal["experience", "topic"]


class ReferencedSuggestion(ReferencedConclusion):
    kind: Literal["experience", "topic"]


class CommentInsight(Conclusion):
    category: Literal["demand", "question", "complaint", "praise", "other"]


class ReferencedCommentInsight(ReferencedConclusion):
    category: Literal["demand", "question", "complaint", "praise", "other"]


class ReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observed_fact_ids: list[str] = Field(max_length=30)
    possible_causes: list[Conclusion] = Field(max_length=0)
    insufficient_evidence: list[str] = Field(max_length=20)
    comment_insights: list[CommentInsight] = Field(max_length=10)
    suggestions: list[Suggestion] = Field(max_length=10)


class ReferencedReviewOutput(ReviewOutput):
    possible_causes: list[ReferencedConclusion] = Field(max_length=0)
    comment_insights: list[ReferencedCommentInsight] = Field(max_length=10)
    suggestions: list[ReferencedSuggestion] = Field(max_length=10)


def validate_review(output, payload):
    if output["possible_causes"]:
        raise ReachError(
            "MODEL_CLAIM_INVALID",
            "当前仅有观察数据，不能生成原因判断；请将缺少因果证据列入证据不足",
        )
    facts = {f["id"]: f for f in payload["statistics"]["facts"]}
    if any(i not in facts for i in output["observed_fact_ids"]):
        raise ReachError("EVIDENCE_REFERENCE_INVALID", "报告引用了不存在的统计事实")
    # Numeric claims are rendered from server facts only. Model interpretation
    # cannot introduce a fabricated metric, percentage, ID or uplift promise.
    texts = [
        s["text"]
        for k in ("possible_causes", "comment_insights", "suggestions")
        for s in output[k]
    ] + output["insufficient_evidence"]
    if any(
        re.search(
            r"[0-9０-９%％]|[零一二三四五六七八九十百千万亿]+(?:万|亿|次|人|率|成|倍|百分)|保证爆款|必然爆款|导致|造成|归因于|因此|证明|必然|必定|caus(?:e|es|ed)|result(?:s|ed)? in",
            t,
        )
        for t in texts
    ):
        raise ReachError(
            "MODEL_CLAIM_INVALID",
            "解释不得编写数字指标、确定因果或爆款保证；数值仅由程序事实展示",
        )
    by_id = {str(s["id"]): s for s in payload["sources"]}
    for item in output["comment_insights"]:
        if any(
            by_id.get(e["snapshot_id"], {}).get("coverage", {}).get("content_kind")
            != "comment"
            for e in item["evidence"]
        ):
            raise ReachError(
                "EVIDENCE_REFERENCE_INVALID", "评论结论必须引用输入中的评论样本"
            )
    return output


async def create_review(db, repo, data, *, pinned=None, schedule_id=None):
    if [s.skill_id for s in data.stages] != [REVIEW_SKILLS[data.kind]]:
        raise ReachError("INVALID_ARGUMENTS", "复盘阶段Skill不匹配")
    with repo.cursor() as c:
        intents = [verified(repo, c, i) for i in data.intent_ids]
        if (
            len({str(i["connection_id"]) for i in intents}) != 1
            or len({str(i["profile_revision_id"]) for i in intents}) != 1
        ):
            raise ReachError(
                "INVALID_ARGUMENTS",
                "复盘需选择同账号、同画像版本的内容；其他内容请分开复盘",
            )
        c.execute(
            "SELECT * FROM social_profile_revisions WHERE id=%s AND org_id=%s",
            (intents[0]["profile_revision_id"], repo.scope.org_id),
        )
        profile = c.fetchone()
        c.execute(
            """SELECT DISTINCT s.* FROM social_source_snapshots s
            JOIN social_research_jobs j ON j.id=s.job_id AND j.org_id=s.org_id
            JOIN social_publish_intents i ON i.org_id=s.org_id AND i.connection_id=j.connection_id
              AND split_part(s.source_key,'#',1)='xiaohongshu:'||(i.verification->>'remote_id')
            WHERE s.org_id=%s AND i.id=ANY(%s::uuid[]) AND s.fetched_at>=%s AND s.fetched_at<=%s
            AND s.coverage->>'content_kind'='comment' ORDER BY s.fetched_at DESC LIMIT 101""",
            (
                repo.scope.org_id,
                [str(i) for i in data.intent_ids],
                data.start,
                data.end,
            ),
        )
        fetched_comments = c.fetchall()
        # Same remote comment may be observed on repeated collection dates/pages.
        unique_comments = {}
        for comment in fetched_comments:
            unique_comments.setdefault(comment["source_key"], comment)
        comments = list(unique_comments.values())
    rows = snapshots(repo, data.intent_ids, data.start, data.end)
    if not rows:
        raise ReachError("NEEDS_INPUT", "选定范围没有真实指标快照，请先回收或导入")
    if data.kind == "comment_insights" and not comments:
        raise ReachError("NEEDS_INPUT", "没有评论样本，不能启动评论分析")
    stats = statistics(rows)
    if data.kind != "comment_insights" and not stats["facts"]:
        raise ReachError(
            "NEEDS_INPUT", "所选快照全部缺失精确指标，请补充真实数据后复盘"
        )
    # Latest observations are the statistical sample; historical series stays in UI.
    latest_ids = {r["snapshot_id"] for r in stats["contents"]}
    selected = [r for r in rows if str(r["id"]) in latest_ids]
    sources = []
    for r in selected:
        sources.append(
            {
                "id": str(r["id"]),
                "body": json.dumps(
                    {
                        "metrics": r["metrics"],
                        "observed_at": str(r["observed_at"]),
                        "published_at": (
                            str(r["published_at"]) if r["published_at"] else None
                        ),
                        "coverage": r["coverage"],
                        "source": r["source"],
                    },
                    ensure_ascii=False,
                ),
                "coverage": {"content_kind": "metrics"},
                "source_url": "",
                "title": "真实指标快照",
            }
        )
    for intent in intents:
        content = intent["content"]
        text = "\n".join(
            [
                content.get("title", ""),
                content.get("body", ""),
                " ".join(content.get("tags", [])),
            ]
        )
        sources.append(
            {
                "id": str(intent["content_revision_id"]),
                "body": text[:2500],
                "coverage": {
                    "content_kind": "published_content",
                    "revision": intent["revision"],
                    "truncated": len(text) > 2500,
                    "scope": "原发布文字前2500字；没有读取素材图片字节",
                },
                "source_url": intent["verification"]["url"],
                "title": "已发布内容原版本",
            }
        )
    size = sum(len(json.dumps(s, ensure_ascii=False).encode()) for s in sources)
    if size > 48000:
        raise ReachError("NEEDS_INPUT", "来源证据超过48KB，请减少所选内容；未调用模型")
    loaded = []
    for s in comments[:100]:
        entry = {k: s[k] for k in ("id", "body", "coverage", "source_url", "title")}
        entry = json.loads(json.dumps(entry, default=str))
        n = len(json.dumps(entry, ensure_ascii=False).encode())
        if size + n > 48000:
            continue
        loaded.append(entry)
        sources.append(entry)
        size += n
    previous_reviews = []
    if data.kind == "strategy":
        with repo.cursor() as c:
            c.execute(
                "SELECT id,result,input FROM social_production_runs WHERE org_id=%s AND kind='postmortem' AND status='completed' ORDER BY created_at DESC LIMIT 100",
                (repo.scope.org_id,),
            )
            candidates = c.fetchall()
        wanted = {str(i) for i in data.intent_ids}
        for previous in candidates:
            if {
                i["intent_id"] for i in previous["input"].get("contents", [])
            } == wanted:
                previous_reviews = [
                    {
                        "id": str(previous["id"]),
                        "result": previous["result"],
                        "input_hash": previous["input"]["snapshot_hash"],
                    }
                ]
                break
        if not previous_reviews:
            raise ReachError(
                "NEEDS_INPUT", "策略建议需要所选内容已完成的复盘报告，请先运行内容复盘"
            )
    payload = {
        "review_policy_version": REVIEW_POLICY_VERSION,
        "request": {**data.model_dump(mode="json"), "max_credits": data.max_credits},
        "profile": profile["data"],
        "profile_revision_id": str(profile["id"]),
        "profile_id": str(profile["profile_id"]),
        "profile_hash": profile["sha256"],
        "contents": [
            {
                "intent_id": str(i["id"]),
                "content_id": str(i["content_id"]),
                "revision": i["revision"],
                "data": i["content"],
            }
            for i in intents
        ],
        "statistics": stats,
        "sources": sources,
        "previous_reviews": previous_reviews,
        "comment_coverage": {
            "selected": len(loaded),
            "available_bounded": len(fetched_comments),
            "unique_comments": len(comments),
            "query_capped": len(fetched_comments) == 101,
            "scope": "仅所选采集任务、时间范围及已保存分页样本；非全部评论",
            "truncated": len(loaded) < len(comments),
        },
        "schedule_id": str(schedule_id) if schedule_id else None,
        "coverage": {"missing": ["不可取得的后台指标", "未采样评论", "因果证据"]},
        "spent_reservations": 0,
    }
    # Exclude observation time from change signature; unchanged counters do not
    # trigger a new paid report. IDs selected by an existing report remain pinned.
    signature = {
        "review_policy_version": REVIEW_POLICY_VERSION,
        "kind": data.kind,
        "profile_hash": profile["sha256"],
        "contents": payload["contents"],
        "metrics": [
            {
                k: r[k]
                for k in (
                    "intent_id",
                    "metrics",
                    "coverage",
                    "published_at",
                    "content_type",
                )
            }
            for r in sorted(selected, key=lambda r: str(r["intent_id"]))
        ],
        "previous_reviews": [r["id"] for r in previous_reviews],
        "comments": sorted({s["body_hash"] for s in comments}),
        "skills": [s.model_dump() for s in data.stages],
    }
    payload["change_hash"] = digest(signature)
    payload["snapshot_hash"] = digest(
        {k: v for k, v in payload.items() if k != "spent_reservations"}
    )
    if len(json.dumps(payload, ensure_ascii=False, default=str).encode()) > 128000:
        raise ReachError(
            "NEEDS_INPUT", "完整复盘输入超过128KB，请减少所选内容；未调用模型"
        )
    run_id = str(uuid4())
    with repo.cursor() as c:
        c.execute(
            "SELECT id,status FROM social_production_runs WHERE org_id=%s AND kind=%s AND input->>'change_hash'=%s AND status IN ('completed','queued','running') ORDER BY created_at DESC LIMIT 1",
            (repo.scope.org_id, data.kind, payload["change_hash"]),
        )
        previous = c.fetchone()
        if previous:
            return {**previous, "reused": True}
    pins = pinned or [
        await bind_scheduled_skills(
            db,
            owner=repo.scope.actor_user_id,
            org=repo.scope.org_id,
            task_id=run_id,
            selections=[s.model_dump()],
        )
        for s in data.stages
    ]
    with repo.cursor() as c:
        if schedule_id:
            c.execute(
                "SELECT id,skill_pin FROM social_analytics_schedules WHERE id=%s AND org_id=%s AND enabled FOR SHARE",
                (schedule_id, repo.scope.org_id),
            )
            schedule = c.fetchone()
            if not schedule:
                raise ReachError("ACCESS_DENIED", "周期已停止，不能启动新复盘")
            if pinned != [schedule["skill_pin"]]:
                raise ReachError("VERSION_CONFLICT", "周期技能配置已变化")
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (str(repo.scope.org_id) + str(data.client_request_id),),
        )
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (str(repo.scope.org_id) + payload["change_hash"],),
        )
        c.execute(
            "SELECT id,status FROM social_production_runs WHERE org_id=%s AND kind=%s AND input->>'change_hash'=%s AND status IN ('completed','queued','running') LIMIT 1",
            (repo.scope.org_id, data.kind, payload["change_hash"]),
        )
        old = c.fetchone()
        if old:
            return {**old, "reused": True}
        c.execute(
            "SELECT id,status,input FROM social_production_runs WHERE org_id=%s AND client_request_id=%s",
            (repo.scope.org_id, data.client_request_id),
        )
        old = c.fetchone()
        if old:
            if old["input"]["request"] != payload["request"]:
                raise ReachError("VERSION_CONFLICT", "复盘请求编号已绑定不同输入")
            return {"id": old["id"], "status": old["status"], "reused": True}
        c.execute(
            """INSERT INTO social_production_runs(id,org_id,owner_id,job_id,kind,input,stages,client_request_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,status""",
            (
                run_id,
                repo.scope.org_id,
                repo.scope.actor_user_id,
                intents[0]["original_job_id"],
                data.kind,
                Jsonb(payload),
                Jsonb(pins),
                data.client_request_id,
            ),
        )
        return {**c.fetchone(), "reused": False}


def settle_review(repo, c, run, output):
    validate_review(output, run["input"])
    for s in output["suggestions"]:
        c.execute(
            """INSERT INTO social_review_suggestions(org_id,run_id,profile_revision_id,kind,original_text,evidence)
            VALUES(%s,%s,%s,%s,%s,%s)""",
            (
                repo.scope.org_id,
                run["id"],
                run["input"]["profile_revision_id"],
                s["kind"],
                s["text"],
                Jsonb(s["evidence"]),
            ),
        )
    return {
        **output,
        "observed_facts": [
            f
            for f in run["input"]["statistics"]["facts"]
            if f["id"] in output["observed_fact_ids"]
        ],
        "statistics": run["input"]["statistics"],
        "comment_coverage": run["input"]["comment_coverage"],
    }


def decide(repo, suggestion_id, data):
    with repo.cursor() as c:
        c.execute(
            "SELECT * FROM social_review_suggestions WHERE id=%s AND org_id=%s FOR UPDATE",
            (suggestion_id, repo.scope.org_id),
        )
        s = c.fetchone()
        if not s:
            raise ReachError("NOT_FOUND", "建议不存在")
        if s["decision"] != "pending":
            raise ReachError("VERSION_CONFLICT", "建议已处理，不能重复应用")
        text = (data.text if data.text is not None else s["original_text"]).strip()
        if not text:
            raise ReachError("INVALID_ARGUMENTS", "建议文字不能为空")
        result = None
        if data.decision == "accept":
            c.execute(
                "SELECT input FROM social_production_runs WHERE id=%s AND org_id=%s",
                (s["run_id"], repo.scope.org_id),
            )
            source_run = c.fetchone()
            if not source_run or source_run["input"].get("review_policy_version") != REVIEW_POLICY_VERSION:
                raise ReachError("VERSION_CONFLICT", "此报告使用旧解释校验规则，请重新复盘后接受建议")
            c.execute(
                "SELECT current.*,p.current_revision FROM social_profile_revisions r JOIN social_profiles p ON p.id=r.profile_id AND p.org_id=r.org_id JOIN social_profile_revisions current ON current.profile_id=p.id AND current.org_id=p.org_id AND current.revision=p.current_revision WHERE r.id=%s AND r.org_id=%s FOR UPDATE OF p",
                (s["profile_revision_id"], repo.scope.org_id),
            )
            profile = c.fetchone()
            if data.expected_profile_revision != profile["current_revision"]:
                raise ReachError(
                    "VERSION_CONFLICT",
                    "画像版本已变化，请刷新后确认当前版本，不能覆盖新画像",
                )
            if s["kind"] == "experience":
                payload = {
                    **profile["data"],
                    "experience": (
                        profile["data"].get("experience", "") + "\n" + text
                    ).strip(),
                }
                if len(payload["experience"]) > 4000:
                    raise ReachError(
                        "INVALID_ARGUMENTS", "经验字段超过4000字，请编辑建议"
                    )
                revision = profile["current_revision"] + 1
                c.execute(
                    "UPDATE social_profiles SET current_revision=%s WHERE id=%s",
                    (revision, profile["profile_id"]),
                )
                c.execute(
                    """INSERT INTO social_profile_revisions(org_id,profile_id,revision,data,sha256,created_by)
                    VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (
                        repo.scope.org_id,
                        profile["profile_id"],
                        revision,
                        Jsonb(payload),
                        digest(payload),
                        repo.scope.actor_user_id,
                    ),
                )
                result = {
                    "profile_revision_id": str(c.fetchone()["id"]),
                    "revision": revision,
                    "suggestion_id": str(s["id"]),
                }
            else:
                c.execute(
                    "SELECT job_id FROM social_production_runs WHERE id=%s AND org_id=%s",
                    (s["run_id"], repo.scope.org_id),
                )
                job = c.fetchone()
                topic = {
                    "title": text[:200],
                    "audience": profile["data"]["audience"],
                    "angle": text,
                    "evidence": s["evidence"],
                    "unknowns": ["复盘建议需在新一轮验证"],
                    "decision": "adopt",
                    "planned_date": None,
                    "format": "待规划",
                    "required_materials": [],
                    "suggestion_id": str(s["id"]),
                    "review_run_id": str(s["run_id"]),
                }
                c.execute(
                    """INSERT INTO social_topic_candidates(org_id,job_id,run_id,profile_revision_id,data)
                    VALUES(%s,%s,%s,%s,%s) RETURNING id""",
                    (
                        repo.scope.org_id,
                        job["job_id"],
                        s["run_id"],
                        profile["id"],
                        Jsonb(topic),
                    ),
                )
                result = {
                    "topic_id": str(c.fetchone()["id"]),
                    "suggestion_id": str(s["id"]),
                }
        c.execute(
            """UPDATE social_review_suggestions SET decision=%s,edited_text=%s,decided_by=%s,decided_at=now(),result=%s
            WHERE id=%s RETURNING *""",
            (data.decision, text, repo.scope.actor_user_id, Jsonb(result), s["id"]),
        )
        return c.fetchone()
