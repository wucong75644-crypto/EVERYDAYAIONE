"""从可信结构化证据提取可直接引用的数值。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from services.agent.runtime.artifact_ledger import (
    ArtifactKind,
    ArtifactSnapshot,
    ArtifactStatus,
)

_NUMBER_IN_TEXT = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")


@dataclass(frozen=True)
class EvidenceValue:
    value: Decimal
    terms: frozenset[str]


@dataclass(frozen=True)
class EvidenceValueSet:
    values: tuple[EvidenceValue, ...]
    evidence_count: int

    def supports(
        self,
        value: Decimal,
        unit: str | None,
        context: str,
    ) -> bool:
        candidates = {value}
        if unit in {"%", "％"}:
            candidates.add(value / Decimal("100"))
        return any(
            item.value in candidates
            and (not item.terms or _mentions_term(context, item.terms))
            for item in self.values
        )


def collect_evidence_values(snapshot: ArtifactSnapshot) -> EvidenceValueSet:
    values: set[EvidenceValue] = set()
    count = 0
    for evidence in snapshot.evidence:
        if (
            evidence.kind != ArtifactKind.DATA_RESULT
            or evidence.status != ArtifactStatus.READY
        ):
            continue
        count += 1
        payload = evidence.payload or {}
        aliases = _column_aliases(payload.get("columns"))
        rows = payload.get("data")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for field, value in row.items():
                    terms = aliases.get(str(field), {str(field)})
                    _collect(value, values, terms=frozenset(terms))
        _collect(payload.get("metadata"), values)
        if isinstance(rows, list):
            values.add(
                EvidenceValue(
                    Decimal(len(rows)),
                    frozenset({"行", "组", "条", "rows", "count"}),
                )
            )
    return EvidenceValueSet(tuple(values), count)


def _collect(
    value: Any,
    output: set[EvidenceValue],
    *,
    terms: frozenset[str] = frozenset(),
) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float, Decimal)):
        parsed = _decimal(value)
        if parsed is not None:
            output.add(EvidenceValue(parsed, terms))
        return
    if isinstance(value, str):
        for match in _NUMBER_IN_TEXT.finditer(value):
            parsed = _decimal(match.group(0).replace(",", ""))
            if parsed is not None:
                output.add(EvidenceValue(parsed, terms))
                output.add(EvidenceValue(abs(parsed), terms))
        return
    if isinstance(value, dict):
        for field, item in value.items():
            _collect(item, output, terms=frozenset({str(field)}))
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect(item, output, terms=terms)


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _column_aliases(value: Any) -> dict[str, set[str]]:
    if not isinstance(value, list):
        return {}
    aliases: dict[str, set[str]] = {}
    for column in value:
        if not isinstance(column, dict) or not column.get("name"):
            continue
        name = str(column["name"])
        aliases[name] = {
            name,
            str(column.get("label") or name),
        }
    return aliases


def _mentions_term(context: str, terms: frozenset[str]) -> bool:
    normalized = context.lower().replace("_", "")
    return any(
        _usable_term(term)
        and term.lower().replace("_", "") in normalized
        for term in terms
    )


def _usable_term(term: str) -> bool:
    return len(term) >= 2 or any("\u4e00" <= char <= "\u9fff" for char in term)
