"""运行时内部的数据校验、过滤与聚合；不暴露为模型工具。"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from services.agent.agent_result import AgentResult
from services.agent.runtime.artifact_collector import collect_tool_result
from services.agent.runtime.artifact_ledger import ArtifactKind, ArtifactStatus
from services.agent.tool_output import ColumnMeta, OutputFormat


_FOLLOW_UP_TERMS = (
    "重新计算", "再计算", "重算", "共多少", "总和", "合计", "求和",
    "排除", "除了", "按照有效", "按有效",
    "recalculate", "recompute", "excluding", "exclude", "total", "sum",
)
_GROUP_TERMS = ("按照平台", "按平台", "平台划分", "各平台")
_PLATFORM_CODES = {
    "淘宝": "tb", "京东": "jd", "拼多多": "pdd", "抖音": "fxg",
    "抖店": "fxg", "快手": "kuaishou", "小红书": "xhs",
    "1688": "1688", "系统": "sys",
}


def execute_validation_plan(
    runtime_state: Any,
    arguments: Mapping[str, Any],
) -> AgentResult:
    """按 artifact_id 取证据并计算；任何口径或字段错误都显式失败。"""
    if runtime_state is None:
        return _error("当前运行时没有可计算的数据证据", "RUNTIME_STATE_MISSING")
    artifact_id = str(arguments.get("artifact_id") or "")
    evidence = _find_data_evidence(runtime_state, artifact_id)
    if evidence is None:
        return _error("数据证据不存在或不可用", "ARTIFACT_NOT_FOUND")
    payload = evidence.payload or {}
    rows = payload.get("data")
    if not isinstance(rows, list):
        return _error("当前数据证据不是可内联计算的数据", "ROWS_UNAVAILABLE")
    try:
        filtered = _apply_filters(rows, arguments.get("filters"))
        output = _aggregate(
            filtered,
            arguments.get("group_by"),
            arguments.get("metrics"),
        )
    except ValueError as error:
        return _error(str(error), "INVALID_COMPUTE_REQUEST")
    columns = _result_columns(output)
    return AgentResult(
        status="success" if output else "empty",
        summary=_summary(output, len(filtered)),
        format=OutputFormat.TABLE,
        data=output,
        columns=columns,
        source="runtime_validator",
        metadata={
            "derived_from": [artifact_id],
            "input_rows": len(rows),
            "filtered_rows": len(filtered),
            "deterministic": True,
            "operation": {
                "filters": arguments.get("filters") or [],
                "group_by": arguments.get("group_by") or [],
                "metrics": arguments.get("metrics") or [],
            },
        },
    )


def has_computable_data(runtime_state: Any) -> bool:
    return _source_evidence(runtime_state) is not None


def requires_validation(text: str, *, has_data_context: bool) -> bool:
    """只识别可确定性表达的高置信数据追问。"""
    if not has_data_context:
        return False
    normalized = text.replace(" ", "").lower()
    return any(term in normalized for term in _FOLLOW_UP_TERMS)


def requires_source_validation(text: str) -> bool:
    """首轮明确要求分组展示时，校验并保持源结果形态。"""
    return any(term in text for term in _GROUP_TERMS)


def run_internal_validation(runtime_state: Any) -> bool:
    """执行当前请求的内部校验；结果只写账本，不写模型消息。"""
    source = _source_evidence(runtime_state)
    if source is None:
        runtime_state.validation_error = "没有可计算的结构化数据证据"
        return False
    plan = _build_validation_plan(runtime_state, source)
    if plan is None:
        runtime_state.validation_error = "无法确定当前问题的计算口径"
        return False
    result = execute_validation_plan(runtime_state, plan)
    if result.is_failure:
        runtime_state.validation_error = result.summary
        return False
    _localize_verified_result(result, source)
    evidence = collect_tool_result(result, tool_call_id=None)[0]
    runtime_state.ledger.record(evidence)
    runtime_state.validation_plan = dict(plan)
    runtime_state.validation_error = None
    runtime_state.request_verified_final()
    return True


def _build_validation_plan(runtime_state: Any, source: Any) -> dict[str, Any] | None:
    text = str(runtime_state.user_text or "")
    normalized = text.replace(" ", "").lower()
    previous = _latest_validation_operation(runtime_state)
    if any(term in normalized for term in ("重新计算", "再计算", "重算", "recalculate", "recompute")):
        return _rebase_plan(previous, source.fingerprint)
    if requires_source_validation(text):
        return _preserve_rows_plan(source)
    filters = list(previous.get("filters") or []) if previous else []
    exclusion = _exclusion_filter(text, source)
    if exclusion:
        filters = [exclusion]
    elif any(term in text for term in ("排除", "除了", "不含", "exclude")):
        return None
    metric = _metric_for_text(text, source, previous)
    if metric is None:
        return None
    alias = _metric_alias(metric)
    return {
        "artifact_id": source.fingerprint,
        "filters": filters,
        "group_by": [],
        "metrics": [{"field": metric, "operation": "sum", "alias": alias}],
    }


def _preserve_rows_plan(source: Any) -> dict[str, Any] | None:
    payload = source.payload or {}
    rows = payload.get("data")
    if not isinstance(rows, list):
        return None
    columns = _column_names(payload.get("columns"))
    group_field = next(
        (field for field in ("platform", "group_key") if field in columns),
        None,
    )
    metrics = [
        {"field": field, "operation": "sum", "alias": field}
        for field in columns
        if field != group_field and _is_numeric_field(rows, field)
    ]
    if not group_field or not metrics:
        return None
    return {
        "artifact_id": source.fingerprint,
        "filters": [],
        "group_by": [group_field],
        "metrics": metrics,
    }


def _source_evidence(runtime_state: Any) -> Any:
    items = runtime_state.ledger.snapshot().evidence
    for evidence in reversed(items):
        payload = evidence.payload or {}
        if (
            evidence.kind == ArtifactKind.DATA_RESULT
            and evidence.status == ArtifactStatus.READY
            and payload.get("source") not in {
                "runtime_validator",
                "data_compute",
            }
            and isinstance(payload.get("data"), list)
        ):
            return evidence
    return None


def _latest_validation_operation(runtime_state: Any) -> dict[str, Any]:
    for evidence in reversed(runtime_state.ledger.snapshot().evidence):
        payload = evidence.payload or {}
        metadata = payload.get("metadata")
        if (
            payload.get("source") in {"runtime_validator", "data_compute"}
            and isinstance(metadata, dict)
            and isinstance(metadata.get("operation"), dict)
        ):
            return dict(metadata["operation"])
    return {}


def _rebase_plan(plan: dict[str, Any], artifact_id: str) -> dict[str, Any] | None:
    if not plan:
        return None
    return {
        "artifact_id": artifact_id,
        "filters": list(plan.get("filters") or []),
        "group_by": list(plan.get("group_by") or []),
        "metrics": list(plan.get("metrics") or []),
    }


def _exclusion_filter(text: str, source: Any) -> dict[str, Any] | None:
    if not any(term in text for term in ("排除", "除了", "不含", "exclude")):
        return None
    payload = source.payload or {}
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        return None
    field = next(
        (name for name in ("platform", "group_key") if name in rows[0]),
        None,
    )
    if field is None:
        return None
    values = {row.get(field) for row in rows if isinstance(row, dict)}
    for label, code in _PLATFORM_CODES.items():
        if label in text:
            value = label if label in values else code
            return {"field": field, "operator": "ne", "value": value}
    return None


def _metric_for_text(
    text: str,
    source: Any,
    previous: dict[str, Any],
) -> str | None:
    columns = set(_column_names((source.payload or {}).get("columns")))
    candidates = (
        ("valid_amount", ("有效金额",)),
        ("valid_orders", ("有效订单", "有效单")),
        ("total_amount", ("总金额", "金额")),
        ("total_orders", ("总订单", "订单", "多少单")),
    )
    for field, terms in candidates:
        if field in columns and any(term in text for term in terms):
            return field
    metrics = previous.get("metrics") if previous else None
    if isinstance(metrics, list) and metrics:
        field = metrics[0].get("field") if isinstance(metrics[0], dict) else None
        if field in columns:
            return str(field)
    return "total_orders" if "total_orders" in columns else None


def _metric_alias(field: str) -> str:
    return {
        "valid_amount": "有效金额合计",
        "valid_orders": "有效订单合计",
        "total_amount": "总金额合计",
        "total_orders": "总订单合计",
    }.get(field, f"{field}合计")


def _is_numeric_field(rows: list[Any], field: str) -> bool:
    values = [row.get(field) for row in rows if isinstance(row, dict)]
    return bool(values) and all(
        isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
        for value in values
    )


def _localize_verified_result(result: AgentResult, source: Any) -> None:
    """只处理最终展示副本，不修改源证据。"""
    if not result.data:
        return
    payload = source.payload or {}
    labels = {
        str(column.get("name")): str(column.get("label") or column.get("name"))
        for column in payload.get("columns") or []
        if isinstance(column, dict) and column.get("name")
    }
    labels["group_key"] = "平台"
    labels["platform"] = "平台"
    from services.kuaimai.erp_unified_schema import PLATFORM_CN

    localized: list[dict[str, Any]] = []
    for row in result.data:
        output: dict[str, Any] = {}
        for key, value in row.items():
            label = labels.get(key, key)
            if key in {"group_key", "platform"}:
                value = PLATFORM_CN.get(str(value), value)
            output[label] = value
        localized.append(output)
    result.data = localized
    result.columns = _result_columns(localized)


def _find_data_evidence(runtime_state: Any, artifact_id: str) -> Any:
    for evidence in runtime_state.ledger.snapshot().evidence:
        if (
            evidence.fingerprint == artifact_id
            and evidence.kind == ArtifactKind.DATA_RESULT
            and evidence.status == ArtifactStatus.READY
        ):
            return evidence
    return None


def _apply_filters(
    rows: list[Any],
    raw_filters: Any,
) -> list[dict[str, Any]]:
    if raw_filters is None:
        raw_filters = []
    if not isinstance(raw_filters, list):
        raise ValueError("filters 必须是数组")
    typed_rows = _validate_rows(rows)
    for item in raw_filters:
        if not isinstance(item, dict):
            raise ValueError("每个 filter 必须是对象")
        field = _required_text(item, "field")
        operator = _required_text(item, "operator")
        _ensure_field(typed_rows, field)
        value = item.get("value")
        typed_rows = [
            row for row in typed_rows
            if _matches(row[field], operator, value)
        ]
    return typed_rows


def _matches(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "eq":
        return actual == expected
    if operator == "ne":
        return actual != expected
    if operator in {"in", "not_in"}:
        if not isinstance(expected, list):
            raise ValueError(f"{operator} 的 value 必须是数组")
        contained = actual in expected
        return contained if operator == "in" else not contained
    raise ValueError(f"不支持的过滤操作: {operator}")


def _aggregate(
    rows: list[dict[str, Any]],
    raw_group_by: Any,
    raw_metrics: Any,
) -> list[dict[str, Any]]:
    group_by = _text_list(raw_group_by, "group_by")
    if not isinstance(raw_metrics, list) or not raw_metrics:
        raise ValueError("metrics 至少需要一项")
    for field in group_by:
        _ensure_field(rows, field)
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[field] for field in group_by)].append(row)
    if not rows and not group_by:
        groups[()] = []
    return [
        _aggregate_group(key, grouped, group_by, raw_metrics)
        for key, grouped in groups.items()
    ]


def _aggregate_group(
    key: tuple[Any, ...],
    rows: list[dict[str, Any]],
    group_by: list[str],
    metrics: list[Any],
) -> dict[str, Any]:
    output = dict(zip(group_by, key))
    for metric in metrics:
        if not isinstance(metric, dict):
            raise ValueError("每个 metric 必须是对象")
        operation = _required_text(metric, "operation")
        alias = _required_text(metric, "alias")
        field = str(metric.get("field") or "")
        if alias in output:
            raise ValueError(f"重复输出字段: {alias}")
        if operation == "count":
            output[alias] = len(rows)
        elif operation == "sum":
            if not field:
                raise ValueError("sum 必须指定 field")
            _ensure_field(rows, field)
            output[alias] = _sum_values(row[field] for row in rows)
        else:
            raise ValueError(f"不支持的聚合操作: {operation}")
    return output


def _sum_values(values: Iterable[Any]) -> int | float:
    total = Decimal("0")
    for value in values:
        try:
            total += Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ValueError(f"sum 字段包含非数值: {value}") from error
    return int(total) if total == total.to_integral() else float(total)


def _validate_rows(rows: list[Any]) -> list[dict[str, Any]]:
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("数据行必须是对象")
    return [dict(row) for row in rows]


def _ensure_field(rows: list[dict[str, Any]], field: str) -> None:
    if rows and any(field not in row for row in rows):
        raise ValueError(f"字段不存在或部分数据缺失: {field}")


def _required_text(value: Mapping[str, Any], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{field} 必须是非空字符串")
    return result


def _text_list(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{name} 必须是字符串数组")
    return value


def _column_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item.get("name"))
        for item in value
        if isinstance(item, dict) and item.get("name")
    ]


def _result_columns(rows: list[dict[str, Any]]) -> list[ColumnMeta]:
    if not rows:
        return []
    return [
        ColumnMeta(name=name, dtype=type(value).__name__, label=name)
        for name, value in rows[0].items()
    ]


def _summary(rows: list[dict[str, Any]], filtered_count: int) -> str:
    if not rows:
        return f"确定性计算完成：筛选后 {filtered_count} 行，无聚合结果"
    if len(rows) == 1:
        values = "，".join(f"{key}={value}" for key, value in rows[0].items())
        return f"确定性计算完成：{values}"
    return f"确定性计算完成：筛选后 {filtered_count} 行，输出 {len(rows)} 组"


def _error(summary: str, code: str) -> AgentResult:
    return AgentResult(
        status="error",
        summary=summary,
        source="runtime_validator",
        error_message=code,
        metadata={"retryable": False, "deterministic": True},
    )
