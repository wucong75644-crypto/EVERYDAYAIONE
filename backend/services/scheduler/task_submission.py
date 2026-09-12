"""Task-specific submission facts; tool capability facts still come from ToolSpec."""
from datetime import datetime, timezone
from typing import Mapping, Any
import re

from services.changeset.risk import RiskAssessment, RiskLevel


SUBMISSION_VERSION = "scheduled_task.submit.v1"


def unfilled_shop_placeholder(text: str) -> bool:
    """Only explicit template markers, never infer which real shop was intended."""
    return bool(re.search(
        r"(?:【\s*(?:实际店铺名|店铺名|店铺名称|指定店铺)\s*】|"
        r"\[\s*(?:实际店铺名|店铺名|店铺名称|指定店铺)\s*\]|"
        r"\{\s*(?:实际店铺名|店铺名|店铺名称|指定店铺)\s*\})", text,
    ))


def submission_receipt(row: Mapping) -> str:
    labels = {"create": "创建", "update": "修改", "pause": "暂停", "resume": "恢复", "delete": "删除"}
    action = labels.get(row.get("operation"), "变更")
    status = row.get("status")
    if status == "applied":
        return f"任务已{action}。" + ("本次若已开始会继续完成，之后不再自动运行。" if action == "暂停" else "")
    if status == "awaiting_approval":
        return f"{action}方案已准备好，请在卡片中确认。"
    if status in {"failed", "rejected", "conflicted", "cancelled", "expired"}:
        return f"本次{action}未生效，请查看卡片中的原因。"
    return f"正在检查{action}请求，结果将在卡片中更新。"


def valid_execution_policy(value: Any) -> bool:
    return (isinstance(value, Mapping) and value.get("version") == 1
            and isinstance(value.get("allowed_tools"), (list, tuple))
            and bool(value["allowed_tools"])
            and all(isinstance(name, str) and name for name in value["allowed_tools"]))


def readonly_tool_scope(policy: Mapping[str, Any]) -> bool:
    from services.tools.catalog import build_tool_catalog
    if not valid_execution_policy(policy):
        return False
    registry = build_tool_catalog()
    for name in policy["allowed_tools"]:
        spec = registry.get(name)
        if (spec is None or spec.risk_level == "dangerous"
                or spec.policy_rules.operation not in {"read", "analysis"}
                or "preflight" not in spec.policy_rules.execution_modes):
            return False
    return True


def plan_inputs_unchanged(base: Mapping, proposed: Mapping) -> bool:
    return all(base.get(key) == proposed.get(key) for key in (
        "prompt", "data_scope", "template_file", "push_target",
    )) and valid_execution_policy(base.get("execution_policy")) and bool(base.get("plan_snapshot"))


def _frequency(snapshot: Mapping) -> float:
    """Conservative daily frequency; complex cron changes retain confirmation."""
    kind = snapshot.get("schedule_type")
    if kind == "once":
        return 0
    cron = str(snapshot.get("cron_expr") or "").split()
    if cron:
        # Stored cron is the actual trigger, regardless of the UI frequency label.
        if len(cron) != 5 or not cron[0].isdigit() or not cron[1].isdigit():
            return float("inf")
        if cron[2:] == ["*", "*", "*"]:
            return 1
        if cron[2] == cron[3] == "*" and all(day.isdigit() for day in cron[4].split(",")):
            return len(set(cron[4].split(","))) / 7
        if cron[2].isdigit() and cron[3:] == ["*", "*"]:
            return 1 / 28
        return float("inf")
    if kind == "daily":
        return 1
    if kind == "weekly":
        return len(set(snapshot.get("weekdays") or [])) / 7
    if kind == "monthly":
        return 1 / 28
    return float("inf")


def instruction_scope_preserved(base: Mapping, proposed: Mapping) -> bool:
    """Recognize presentation-only additions while retaining every original instruction.

    Legacy prompt-only tasks have no independently enforceable shop scope. An
    arbitrary rewrite cannot be certified equivalent just by matching tool names.
    """
    before, after = str(base.get("prompt") or "").strip(), str(proposed.get("prompt") or "").strip()
    if before == after:
        return True
    if not before or not after.startswith(before):
        return False
    suffix = after[len(before):].strip("\n ，,。;；")
    return bool(re.fullmatch(r"(?:(?:输出要求|输出格式)[：:]\s*)?(?:请|并)?(?:以|用|使用|改用)?(?:Markdown表格|表格|列表|项目符号|文字|CSV)(?:格式)?(?:输出|展示|呈现|汇总)?[。.]?", suffix, re.IGNORECASE))


def submission_assessment(operation: str, base: Mapping, proposed: Mapping,
                          *, self_target: bool) -> RiskAssessment:
    reasons = []
    if operation == "pause":
        return RiskAssessment(RiskLevel.MEDIUM, False, ("pause_future_schedule",))
    if operation == "delete":
        return RiskAssessment(RiskLevel.HIGH, True, ("destructive_operation",))
    policy = proposed.get("execution_policy") or base.get("execution_policy") or {}
    if not valid_execution_policy(policy):
        reasons.append("execution_authorization_required")
    if operation == "resume":
        return RiskAssessment(RiskLevel.HIGH if reasons else RiskLevel.MEDIUM, bool(reasons),
                              tuple(reasons or ["resume_existing_definition"]))
    if not self_target and (operation == "create" or proposed.get("push_target") != base.get("push_target")):
        reasons.append("recipient_changed")
    if not readonly_tool_scope(policy):
        reasons.append("execution_requires_review")
    if operation == "create":
        if (int(proposed.get("max_credits") or 0) > 10 or _frequency(proposed) > 1
                or int(proposed.get("retry_count") or 0) > 1
                or int(proposed.get("timeout_sec") or 0) > 180):
            reasons.append("usage_requires_review")
    else:
        if not instruction_scope_preserved(base, proposed):
            reasons.append("instruction_scope_changed")
        if proposed.get("data_scope") != base.get("data_scope"):
            reasons.append("data_scope_changed")
        if set(policy.get("allowed_tools") or []) - set((base.get("execution_policy") or {}).get("allowed_tools") or []):
            reasons.append("tool_scope_expanded")
        if any(int(proposed.get(k) or 0) > int(base.get(k) or 0)
               for k in ("max_credits", "retry_count", "timeout_sec")):
            reasons.append("usage_increased")
        if (_frequency(proposed) > _frequency(base)
                or proposed.get("cron_expr") != base.get("cron_expr")
                and (proposed.get("schedule_type") == "cron" or _frequency(proposed) == float("inf"))):
            reasons.append("frequency_increased")
    if proposed.get("template_file") and proposed.get("template_file") != base.get("template_file"):
        reasons.append("template_changed")
    return RiskAssessment(RiskLevel.HIGH if reasons else RiskLevel.MEDIUM, bool(reasons),
                          tuple(reasons or ["explicit_request_within_scope"]))


def resume_time(task: Mapping, *, now: datetime | None = None) -> str:
    from services.scheduler.cron_utils import calc_next_run
    now = now or datetime.now(timezone.utc)
    if task.get("schedule_type") != "once":
        return calc_next_run(task["cron_expr"], task.get("timezone") or "Asia/Shanghai", now).isoformat()
    try:
        run_at = datetime.fromisoformat(str(task.get("run_at") or "").replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("这项一次性任务缺少有效执行时间，请先修改时间。") from None
    if run_at.tzinfo is None or run_at <= now:
        raise ValueError("这项一次性任务的时间已过，请修改执行时间，或选择立即运行一次。")
    return run_at.isoformat()
