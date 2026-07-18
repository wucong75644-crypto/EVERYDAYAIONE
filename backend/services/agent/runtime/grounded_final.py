"""从已验证 data_compute 产物生成不可篡改的最终文本。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from services.agent.runtime.artifact_ledger import ArtifactKind, ArtifactStatus


_FOLLOW_UP_TERMS = (
    "重新计算",
    "再计算",
    "重算",
    "共多少",
    "总和",
    "合计",
    "求和",
    "排除",
    "除了",
    "按照有效",
    "按有效",
    "recalculate",
    "recompute",
    "excluding",
    "exclude",
    "total",
    "sum",
)

GROUNDED_FINAL_BLOCKED = (
    "无法完成可信重算：本轮没有产生通过验证的计算结果。"
    "请重试，或重新查询原始数据后再计算。"
)


def is_data_compute_follow_up(text: str, *, has_data_context: bool) -> bool:
    if not has_data_context:
        return False
    normalized = text.replace(" ", "")
    return any(term in normalized for term in _FOLLOW_UP_TERMS)


def build_grounded_final(runtime_state: Any) -> str:
    evidence = _latest_compute_evidence(runtime_state)
    if evidence is None:
        return ""
    payload = evidence.payload or {}
    rows = payload.get("data")
    if not isinstance(rows, list):
        return ""
    typed_rows = [row for row in rows if isinstance(row, dict)]
    if len(typed_rows) != len(rows):
        return ""
    if not typed_rows:
        return "重新计算完成：没有符合条件的数据。"
    if len(typed_rows) == 1:
        values = "，".join(
            f"{key}：{_format_value(value)}"
            for key, value in typed_rows[0].items()
        )
        return f"重新计算结果：{values}。"
    return _render_table(typed_rows)


def _latest_compute_evidence(runtime_state: Any) -> Any:
    items = runtime_state.ledger.snapshot().evidence
    for evidence in reversed(items):
        payload = evidence.payload or {}
        if (
            evidence.kind == ArtifactKind.DATA_RESULT
            and evidence.status == ArtifactStatus.READY
            and payload.get("source") == "data_compute"
        ):
            return evidence
    return None


def _render_table(rows: list[dict[str, Any]]) -> str:
    columns = list(rows[0])
    header = "| " + " | ".join(_escape(column) for column in columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| "
        + " | ".join(_escape(_format_value(row.get(column))) for column in columns)
        + " |"
        for row in rows
    ]
    return (
        f"重新计算完成，共 {len(rows)} 组：\n\n"
        + "\n".join([header, divider, *body])
    )


def _format_value(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if not isinstance(value, (int, float, Decimal)):
        return str(value)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    if number == number.to_integral():
        return f"{int(number):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def _escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")
