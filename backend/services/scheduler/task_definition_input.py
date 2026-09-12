"""Structured task input: validate fields, never reinterpret natural language.

ToolSpec owns the input schema. The existing ChangeSet adapter still owns
completion of business snapshots, authorization, risk, and commit.
"""
from copy import deepcopy
from datetime import datetime
import re
from typing import Any, Mapping


class TaskDefinitionInputError(ValueError):
    pass


def _validate(value: Any, schema: Mapping, label: str) -> None:
    kind = schema.get("type")
    valid_type = {"object": isinstance(value, dict), "array": isinstance(value, list),
                  "string": isinstance(value, str), "integer": type(value) is int}
    if not valid_type.get(kind, False):
        raise TaskDefinitionInputError(f"{label}格式不正确，请按工具字段类型填写。")
    if "enum" in schema and value not in schema["enum"]:
        raise TaskDefinitionInputError(f"{label}请选择：{'、'.join(schema['enum'])}。")
    if kind == "object":
        properties = schema["properties"]
        if set(value) - set(properties):
            raise TaskDefinitionInputError(f"{label}包含不支持的字段，不能修改身份、权限或执行策略。")
        for key, item in value.items():
            _validate(item, properties[key], properties[key].get("title", key))
    elif kind == "string":
        if not value.strip() or "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise TaskDefinitionInputError(f"{label}为空或格式不正确，请补齐有效值。")
    elif kind == "integer":
        if not schema["minimum"] <= value <= schema["maximum"]:
            raise TaskDefinitionInputError(f"{label}应在 {schema['minimum']}～{schema['maximum']} 之间。")
    elif kind == "array":
        if len(value) < schema.get("minItems", 0):
            raise TaskDefinitionInputError(f"请选择{label}。")
        for item in value:
            _validate(item, schema["items"], label)
        if schema.get("uniqueItems") and len(set(value)) != len(value):
            raise TaskDefinitionInputError(f"{label}不能重复。")


def task_definition_input(definition: Any, *, operation: str, base: Mapping | None = None,
                          recipient: Any = "") -> dict:
    from services.tools.catalog import build_tool_catalog
    schema = build_tool_catalog().require("manage_scheduled_task").to_schema()
    _validate(definition, schema["function"]["parameters"]["properties"]["definition"], "任务定义")
    if not isinstance(recipient, str):
        raise TaskDefinitionInputError("请明确收件人或通知渠道。")
    changes = deepcopy(definition)
    has_base = base is not None
    base = base or {}
    if "run_at" in changes:
        try:
            date = datetime.fromisoformat(changes["run_at"].replace("Z", "+00:00"))
            if date.tzinfo is None:
                raise ValueError("timezone missing")
        except ValueError:
            raise TaskDefinitionInputError("单次任务请提供含时区的完整执行日期和时间。") from None
    output_format = changes.pop("output_format", None)
    if output_format:
        if "prompt" in changes and operation == "update":
            raise TaskDefinitionInputError("只改输出形式请仅传 output_format；修改完整执行内容请仅传 prompt。")
        instruction = changes.get("prompt") or base.get("prompt")
        if operation == "update" and not has_base:
            changes["output_format"] = output_format  # Panel applies to its existing form once.
        elif instruction:
            changes["prompt"] = instruction + "\n输出格式：" + output_format
        else:
            raise TaskDefinitionInputError("请先补充实际执行内容，再指定输出格式。")
    effective = {**base, **changes}
    kind = effective.get("schedule_type")
    required = ["prompt", "schedule_type"] if operation == "create" else []
    # A patch may use the existing clock, but changing from once/complex cron
    # must not silently invent a new time. The adapter performs final merging.
    incompatible = {
        "daily": {"run_at", "weekdays", "day_of_month"},
        "weekly": {"run_at", "day_of_month"},
        "monthly": {"run_at", "weekdays"},
        "once": {"time_str", "weekdays", "day_of_month"},
        "cron": {"run_at", "time_str", "weekdays", "day_of_month"},
    }.get(kind, set()).intersection(changes)
    if incompatible:
        raise TaskDefinitionInputError("时间字段与执行频率不一致，请明确要修改的频率及对应时间。")
    if operation == "create" or has_base and set(changes) & {"schedule_type", "time_str", "weekdays", "day_of_month", "run_at"}:
        if kind == "once":
            required.append("run_at")
            if "time_str" in changes and "run_at" not in changes:
                raise TaskDefinitionInputError("修改单次任务时间请提供完整的 run_at 日期和时间。")
        else:
            if not effective.get("time_str") and not simple_task_time(base):
                required.append("time_str")
            required += {"weekly": ["weekdays"], "monthly": ["day_of_month"]}.get(kind, [])
    missing = [key for key in required if not effective.get(key)]
    from services.scheduler.task_submission import unfilled_shop_placeholder
    if unfilled_shop_placeholder(str(effective.get("prompt") or "")) and "prompt" not in missing:
        missing.append("prompt")
    if operation == "create":
        changes.setdefault("name", (changes.get("prompt") or "新建任务")[:20])
    return {"changes": changes, "missing_fields": missing, "recipient": recipient.strip(), "parsed": True}


def simple_task_time(task: Mapping) -> str | None:
    """Read an existing fixed clock; never turn a complex cron into a guess."""
    parts = str(task.get("cron_expr") or "").split()
    if len(parts) == 5 and parts[0].isdigit() and parts[1].isdigit():
        minute, hour = int(parts[0]), int(parts[1])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    return None
