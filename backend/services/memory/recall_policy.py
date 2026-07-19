"""通用 Curated Memory 召回过滤、时间衰减与多样性策略。"""

from __future__ import annotations

from datetime import datetime, timezone
from math import exp
from typing import Any


def normalize_relevance(
    *,
    vector_score: float = 0.0,
    keyword_score: float = 0.0,
    matched_both: bool = False,
) -> float:
    """将不同检索通道的分数映射到稳定的 0..1 相关性。"""
    vector = _clamp(vector_score)
    keyword = 1.0 - exp(-max(0.0, keyword_score) * 4.0)
    agreement = 0.55 if matched_both else 0.0
    return max(vector, keyword, agreement)


def rank_for_recall(
    candidates: list[dict[str, Any]],
    *,
    max_results: int,
    score_threshold: float,
    now: datetime | None = None,
    mmr_lambda: float = 0.75,
) -> list[dict[str, Any]]:
    """先执行硬阈值与时间衰减，再用 MMR 抑制近重复结果。"""
    if max_results <= 0:
        return []
    current = now or datetime.now(timezone.utc)
    eligible: list[dict[str, Any]] = []
    for candidate in candidates:
        relevance = _clamp(float(candidate.get("relevance_score") or 0.0))
        if relevance < score_threshold:
            continue
        freshness = _freshness(candidate.get("updated_at"), current)
        priority = _clamp(float(candidate.get("priority") or 0) / 100.0)
        ranked = dict(candidate)
        ranked["score"] = (
            0.75 * relevance
            + 0.15 * freshness
            + 0.10 * priority
        )
        eligible.append(ranked)

    eligible.sort(
        key=lambda item: (
            -float(item["score"]),
            str(item.get("record_id") or ""),
        )
    )
    selected: list[dict[str, Any]] = []
    remaining = eligible
    while remaining and len(selected) < max_results:
        best = max(
            remaining,
            key=lambda item: (
                mmr_lambda * float(item["score"])
                - (1.0 - mmr_lambda) * _max_similarity(item, selected),
                str(item.get("record_id") or ""),
            ),
        )
        selected.append(best)
        remaining = [item for item in remaining if item is not best]
    return selected


def _freshness(value: Any, now: datetime) -> float:
    parsed = _as_datetime(value)
    if parsed is None:
        return 0.5
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - parsed).total_seconds() / 86400.0)
    return exp(-age_days / 180.0)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _max_similarity(
    candidate: dict[str, Any],
    selected: list[dict[str, Any]],
) -> float:
    if not selected:
        return 0.0
    content = str(candidate.get("content") or "")
    return max(
        _text_similarity(content, str(item.get("content") or ""))
        for item in selected
    )


def _text_similarity(left: str, right: str) -> float:
    left_parts = _bigrams(left)
    right_parts = _bigrams(right)
    if not left_parts or not right_parts:
        return float(left.strip().casefold() == right.strip().casefold())
    return len(left_parts & right_parts) / len(left_parts | right_parts)


def _bigrams(value: str) -> set[str]:
    normalized = "".join(value.casefold().split())
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {
        normalized[index:index + 2]
        for index in range(len(normalized) - 1)
    }


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
