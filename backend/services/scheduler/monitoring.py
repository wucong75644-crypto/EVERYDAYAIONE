"""Deterministic conditional notifications. Observations are data, never instructions."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal


class NotificationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["always", "conditional"] = "always"
    metric: str = Field(default="", max_length=80, pattern=r"^[a-zA-Z0-9_]*$")
    comparison: Literal["value", "delta", "percent"] = "value"
    threshold: float = Field(default=0, ge=0, le=1e12, allow_inf_nan=False)
    window_minutes: int | None = Field(default=None, ge=1, le=10080)
    repeat_increment: float | None = Field(default=None, gt=0, le=1e12, allow_inf_nan=False)
    cooldown_minutes: int = Field(default=0, ge=0, le=10080)

    @model_validator(mode="after")
    def complete(self):
        if self.mode == "conditional" and (not self.metric or self.threshold <= 0):
            raise ValueError("条件通知需要指标和大于零的门槛")
        if self.mode == "conditional" and self.comparison != "value" and not self.window_minutes:
            raise ValueError("增长量／增长率通知必须指定比较窗口（分钟）")
        return self


def validate_policy(value):
    try:
        policy = NotificationPolicy.model_validate(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("通知规则无效：请明确指标、正数门槛及增长比较窗口；再次增长门槛须大于零") from exc
    return {"mode": "always"} if policy.mode == "always" else policy.model_dump(exclude_none=True)


def parse_observations(text):
    """Exactly one explicitly labelled data block; malformed output stays incomplete."""
    blocks = list(re.finditer(r"```task_observations\s*\n(.*?)\n```", text or "", re.S))
    if len(blocks) != 1:
        return text, []
    match = blocks[0]
    clean = (text[:match.start()] + text[match.end():]).strip()
    try:
        raw = json.loads(match[1])
        if not isinstance(raw, list) or not 1 <= len(raw) <= 100:
            return clean, []
        return clean, raw
    except (ValueError, TypeError):
        return clean, []


def _date(value):
    try:
        date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return date.astimezone(timezone.utc) if date.tzinfo else None
    except (ValueError, TypeError):
        return None


def _number(value):
    try:
        return type(value) in (float, int) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def normalize_observations(raw, now, started_at=None):
    if not isinstance(raw, list) or len(raw) > 100:
        return []
    rows, seen = [], set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        platform = row.get("platform")
        if isinstance(platform, str):
            aliases = {"小红书": "xiaohongshu", "xhs": "xiaohongshu", "b站": "bilibili", "哔哩哔哩": "bilibili", "推特": "twitter", "x": "twitter"}
            platform = aliases.get(platform.strip().lower(), platform.strip().lower())
        entity_id = row.get("entity_id")
        identity = (platform, entity_id.strip() if isinstance(entity_id, str) else entity_id)
        if any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in identity):
            continue
        if identity in seen:
            return []  # ambiguous duplicate is not a valid baseline
        seen.add(identity)
        collected = _date(row.get("collected_at"))
        lower = started_at or now - timedelta(minutes=10)
        if not collected or not lower - timedelta(minutes=1) <= collected <= now + timedelta(minutes=1):
            continue
        metrics = row.get("metrics")
        if not isinstance(metrics, dict):
            continue
        metrics = {key: val for key, val in metrics.items()
                   if isinstance(key, str) and re.fullmatch(r"[a-zA-Z0-9_]{1,80}", key) and _number(val)}
        if not metrics:
            continue
        rows.append({"platform": identity[0], "entity_id": identity[1],
                     "collected_at": collected.isoformat(), "metrics": metrics,
                     "title": str(row.get("title") or "")[:500],
                     "url": str(row.get("url") or "")[:3000]})
    return rows


def evaluate(policy, observations, history, state, now):
    """Reserve notifications at enqueue, atomically committed with the run/outbox.

    Pending deliveries own the reservation and retry without rerunning the Agent.
    Invalid samples never rearm a previously matched entity.
    """
    if policy.get("mode", "always") == "always":
        return {"status": "always", "matches": [], "state": state or {}}
    policy = validate_policy(policy)
    signature = hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()
    state = deepcopy(state or {})
    if state.get("policy") != signature:
        state = {"policy": signature, "entities": {}}
    entities = state.setdefault("entities", {})
    # Per-entity state stays in the existing task JSON; history remains in runs.
    if len(entities) > 5000:
        return {"status": "incomplete", "matches": [], "state": state, "reason": "监测素材达到5000条，请缩小范围或新建任务"}
    metric, comparison = policy["metric"], policy["comparison"]
    matches, outcomes = [], []
    for item in observations:
        key = json.dumps([item["platform"], item["entity_id"]], ensure_ascii=False)
        current = item["metrics"].get(metric)
        if not _number(current):
            outcomes.append("incomplete")
            continue
        sampled = _date(item["collected_at"])
        prior = [row for row in history if row.get("platform") == item["platform"]
                 and row.get("entity_id") == item["entity_id"]
                 and _date(row.get("collected_at")) and _date(row["collected_at"]) < sampled
                 and _number((row.get("metrics") or {}).get(metric))]
        if not prior and not entities.get(key):
            outcomes.append("baseline")
            continue
        value = current
        if comparison != "value":
            target = sampled - timedelta(minutes=policy["window_minutes"])
            tolerance = timedelta(minutes=max(1, policy["window_minutes"] * .2))
            candidates = [row for row in prior if target - tolerance <= _date(row["collected_at"]) <= target]
            if not candidates:
                outcomes.append("incomplete")
                continue
            baseline = max(candidates, key=lambda row: _date(row["collected_at"]))["metrics"][metric]
            if comparison == "percent" and baseline == 0:
                outcomes.append("incomplete")
                continue
            value = current - baseline if comparison == "delta" else (current - baseline) * 100 / baseline
        if not math.isfinite(value):
            outcomes.append("incomplete")
            continue
        previous = entities.get(key, {})
        if value < policy["threshold"]:
            if previous:
                entities[key] = {**previous, "matched": False}
            outcomes.append("below_threshold")
            continue
        repeat = policy.get("repeat_increment")
        last_at = _date(previous.get("reserved_at"))
        new_trigger = not previous.get("matched")
        increased = repeat and _number(previous.get("value")) and current - previous["value"] >= repeat
        cooled = not last_at or sampled - last_at >= timedelta(minutes=policy["cooldown_minutes"])
        # Unchanged data cannot retrigger, even after a temporary fall below the rule.
        changed = not _number(previous.get("value")) or current != previous["value"]
        if key not in entities and len(entities) >= 5000:
            outcomes.append("incomplete")
            continue
        if cooled and changed and (new_trigger or increased):
            match = {**item, "metric": metric, "comparison_value": value}
            matches.append(match)
            entities[key] = {"matched": True, "value": current, "reserved_at": sampled.isoformat()}
            outcomes.append("matched")
        else:
            outcomes.append("duplicate")
    status = "matched" if matches else next((s for s in ("incomplete", "baseline", "below_threshold", "duplicate") if s in outcomes), "incomplete")
    return {"status": status, "matches": matches, "state": state,
            "counts": {s: outcomes.count(s) for s in set(outcomes)}}


def notification_text(task, decision):
    policy = task["notification_policy"]
    lines = [f"任务「{task['name']}」达到通知条件：{policy['metric']} / {policy['comparison']} ≥ {policy['threshold']}"]
    for item in decision["matches"]:
        lines.extend([f"\n{item['title'] or item['entity_id']}", item["url"],
                      f"采集时间：{item['collected_at']}",
                      f"{item['metric']}：{item['metrics'][item['metric']]}；判断值：{item['comparison_value']:g}"])
    return "\n".join(lines)
