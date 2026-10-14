"""Deterministic nullable metrics; no estimated exposure or weighted rankings."""

import re
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median

METRICS = (
    "likes",
    "comments",
    "saves",
    "shares",
    "views",
    "impressions",
    "conversions",
    "followers",
    "completion_rate",
)
PUBLIC_METRICS = ("likes", "comments", "saves", "shares")
CAPABILITIES = {
    "xiaohongshu": {
        "adapter_version": "xhs-rest:metrics-v1",
        "public_fields": dict(
            zip(
                PUBLIC_METRICS,
                (
                    "interactInfo.likedCount",
                    "interactInfo.commentCount",
                    "interactInfo.collectedCount",
                    "interactInfo.sharedCount",
                ),
            )
        ),
        "requires": "已核验发布意图、相同帖子ID、完整xsec_token链接、返回作者ID与连接账号一致",
        "unavailable": [
            "阅读量",
            "曝光",
            "完播率",
            "转化",
            "粉丝归因",
            "创作者后台数据",
        ],
        "limits": "只解析返回的精确非负整数；万/千/+等缩写保留原值，统计值为null；有限评论分页，非全量",
    },
    "other": {
        "public_fields": {},
        "limits": "现有其他适配器没有已验证的结构化运营指标输出，本阶段不启用回收",
    },
}


def exact_count(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 10**12 else None
    if isinstance(value, str) and re.fullmatch(
        r"(?:0|[1-9][0-9]{0,12})", value.strip()
    ):
        n = int(value)
        return n if n <= 10**12 else None
    return None


def exact_metric(key, value):
    if key != "completion_rate":
        return exact_count(value)
    if isinstance(value, bool) or value is None:
        return None
    from decimal import Decimal, InvalidOperation

    try:
        number = Decimal(str(value))
        return float(number) if number.is_finite() and 0 <= number <= 1 else None
    except (InvalidOperation, ValueError):
        return None


def nullable_metrics(raw):
    return {k: exact_metric(k, raw.get(k)) for k in METRICS}


def timestamp(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def statistics(rows):
    """Latest snapshots per post; compare only homogeneous coverage/age groups."""
    latest = {}
    for r in rows:
        key = str(r["intent_id"])
        if key not in latest or (timestamp(r["observed_at"]), str(r["id"])) > (
            timestamp(latest[key]["observed_at"]),
            str(latest[key]["id"]),
        ):
            latest[key] = r
    groups = defaultdict(list)
    facts = []
    details = []
    for row in latest.values():
        values = nullable_metrics(row["metrics"])
        observed, published = timestamp(row["observed_at"]), timestamp(
            row.get("published_at")
        )
        age = (
            (observed - published).total_seconds() / 86400
            if observed and published
            else None
        )
        bucket = (
            "unknown"
            if age is None or age < 0
            else (
                "0-1d"
                if age < 1
                else (
                    "1-3d"
                    if age < 3
                    else (
                        "3-7d"
                        if age < 7
                        else (
                            "7-30d"
                            if age < 30
                            else f"{int(age//30)*30}-{int(age//30)*30+30}d"
                        )
                    )
                )
            )
        )
        present = tuple(k for k in METRICS if values[k] is not None)
        denominator = row.get("coverage", {}).get("denominator")
        numerator = (
            sum(values[k] for k in PUBLIC_METRICS)
            if all(values[k] is not None for k in PUBLIC_METRICS)
            else None
        )
        divisor = (
            values.get(denominator) if denominator in {"views", "impressions"} else None
        )
        rate = (
            numerator / divisor
            if numerator is not None and divisor is not None and divisor > 0
            else None
        )
        detail = {
            "intent_id": str(row["intent_id"]),
            "snapshot_id": str(row["id"]),
            "metrics": values,
            "age_days": age,
            "age_bucket": bucket,
            "engagement_rate": rate,
            "formula": (
                "(likes+comments+saves+shares)/" + denominator
                if rate is not None
                else None
            ),
            "rate_missing_reason": (
                None if rate is not None else "缺少完整互动计数或真实有效分母"
            ),
            "coverage": row.get("coverage", {}),
            "observed_at": str(row["observed_at"]),
            "published_at": str(published) if published else None,
        }
        details.append(detail)
        # Imported and public counters with different source scope never rank together.
        group_key = (
            str(row["connection_id"]),
            row["platform"],
            row["content_type"],
            bucket,
            present,
            row.get("coverage", {}).get("scope", "unknown"),
            denominator,
        )
        groups[group_key].append(detail)
        for k in present:
            facts.append(
                {
                    "id": f"{row['id']}:{k}",
                    "snapshot_id": str(row["id"]),
                    "metric": k,
                    "value": values[k],
                }
            )
    summaries = []
    for key, group in groups.items():
        summaries.append(
            {
                "account": key[0],
                "platform": key[1],
                "content_type": key[2],
                "age_bucket": key[3],
                "available_metrics": list(key[4]),
                "scope": key[5],
                "denominator": key[6],
                "sample_size": len(group),
                "insufficient": len(group) < 3 or key[3] == "unknown",
                "medians": {k: median(d["metrics"][k] for d in group) for k in key[4]},
                "snapshot_ids": [d["snapshot_id"] for d in group],
            }
        )
    n = len(details)
    return {
        "history_limit_per_content": 100,
        "history_truncated": any(r.get("available_snapshots", 0) > 100 for r in rows),
        "sample_size": n,
        "snapshot_count": len(rows),
        "coverage": {
            k: {
                "available": sum(d["metrics"][k] is not None for d in details),
                "total": n,
            }
            for k in METRICS
        },
        "groups": summaries,
        "contents": details,
        "facts": facts,
        "observed_range": {
            "start": str(min((r["observed_at"] for r in rows), default="")),
            "end": str(max((r["observed_at"] for r in rows), default="")),
        },
        "limitations": [
            "缺失不补零；中位数仅来自相同覆盖的分组；少于3条或发布时间未知为样本不足",
            "互动率为互动次数/所声明的真实分母，不代表独立用户转化",
            "不提供跨平台综合排名或因果判定",
        ],
    }
