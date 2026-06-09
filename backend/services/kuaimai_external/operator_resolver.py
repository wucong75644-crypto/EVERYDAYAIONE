"""
运营 → 企微账号自动匹配（async 版）

匹配规则：wecom_employees.name 完全匹配 + status=1（在职），唯一匹配 → 自动绑定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


MatchStatus = Literal["matched", "not_found", "multiple", "skipped"]


@dataclass
class ResolveResult:
    operator_name: str
    status: MatchStatus
    wecom_userid: str | None = None
    matched_count: int = 0
    candidates: list[str] | None = None


async def resolve_operator(
    db: Any,
    *,
    org_id: str,
    operator_name: str,
) -> ResolveResult:
    """根据运营姓名查企微账号。"""
    if not operator_name or not operator_name.strip():
        return ResolveResult(operator_name=operator_name or "", status="skipped")

    name = operator_name.strip()

    resp = await (
        db.table("wecom_employees")
        .select("wecom_userid, name, status")
        .eq("org_id", org_id)
        .eq("name", name)
        .eq("status", 1)
        .execute()
    )
    rows = resp.data or []

    if len(rows) == 0:
        return ResolveResult(operator_name=name, status="not_found", matched_count=0)

    if len(rows) == 1:
        return ResolveResult(
            operator_name=name,
            status="matched",
            wecom_userid=rows[0]["wecom_userid"],
            matched_count=1,
        )

    return ResolveResult(
        operator_name=name,
        status="multiple",
        matched_count=len(rows),
        candidates=[r["wecom_userid"] for r in rows],
    )


async def verify_binding_still_valid(
    db: Any,
    *,
    org_id: str,
    wecom_userid: str,
) -> bool:
    """验证已绑定的 wecom_userid 是否还有效（员工还在职）。"""
    if not wecom_userid:
        return False

    resp = await (
        db.table("wecom_employees")
        .select("wecom_userid")
        .eq("org_id", org_id)
        .eq("wecom_userid", wecom_userid)
        .eq("status", 1)
        .limit(1)
        .execute()
    )
    return bool(resp.data)
