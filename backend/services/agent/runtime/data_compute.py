"""可信 DataResultArtifact 的确定性过滤、分组和聚合。"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from services.agent.agent_result import AgentResult
from services.agent.runtime.artifact_ledger import ArtifactKind, ArtifactStatus
from services.agent.tool_output import ColumnMeta, OutputFormat


class DataComputeToolMixin:
    """为统一 ToolExecutor 提供薄运行时适配。"""

    runtime_state: Any

    async def _execute_data_compute(
        self,
        args: dict[str, Any],
    ) -> AgentResult:
        return execute_data_compute(self.runtime_state, args)


def execute_data_compute(
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
        source="data_compute",
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


def build_data_context_prompt(runtime_state: Any) -> str:
    """向模型公开可计算证据目录，不注入整份数据副本。"""
    entries: list[str] = []
    for evidence in runtime_state.ledger.snapshot().evidence:
        if (
            evidence.kind != ArtifactKind.DATA_RESULT
            or evidence.status != ArtifactStatus.READY
        ):
            continue
        payload = evidence.payload or {}
        columns = _column_names(payload.get("columns"))
        rows = payload.get("data")
        row_count = len(rows) if isinstance(rows, list) else "file"
        entries.append(
            f"- artifact_id={evidence.fingerprint}; "
            f"rows={row_count}; columns={','.join(columns)}"
        )
    if not entries:
        return ""
    return (
        "[可信数据证据]\n"
        + "\n".join(entries)
        + "\n涉及过滤、排除、求和、计数、分组或重新计算时，"
        "必须调用 data_compute，不得从显示文本手工计算。"
    )


def has_computable_data(runtime_state: Any) -> bool:
    return bool(build_data_context_prompt(runtime_state))


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
        source="data_compute",
        error_message=code,
        metadata={"retryable": False, "deterministic": True},
    )
