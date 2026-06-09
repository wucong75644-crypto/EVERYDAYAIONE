"""
运营 → 企微账号自动匹配

输入运营姓名（来自 viperp.shopGroupName），输出企微 wecom_userid。

匹配优先级：
  1. wecom_employees.name 完全匹配 + status=1（在职）
  2. 匹配到唯一一人 → 返回 wecom_userid
  3. 匹配到 0 人 / 多人 → 返回 None + 状态标记

注意：不做模糊匹配（"廖晴宇" != "廖晴予"），保护数据准确性。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


MatchStatus = Literal["matched", "not_found", "multiple", "skipped"]


@dataclass
class ResolveResult:
    """匹配结果"""
    operator_name: str
    status: MatchStatus
    wecom_userid: str | None = None
    matched_count: int = 0   # 匹配到几个人
    candidates: list[str] | None = None  # 多匹配时的候选 wecom_userid 列表


def resolve_operator(
    db: Any,
    *,
    org_id: str,
    operator_name: str,
) -> ResolveResult:
    """
    根据运营姓名查企微账号。

    Args:
        db: 同步 DB
        org_id: 企业 ID（多租户隔离）
        operator_name: viperp.shopGroupName 提取的姓名

    Returns:
        ResolveResult
    """
    if not operator_name or not operator_name.strip():
        return ResolveResult(
            operator_name=operator_name or "",
            status="skipped",
        )

    name = operator_name.strip()

    # 查同 org 下在职员工
    resp = (
        db.table("wecom_employees")
        .select("wecom_userid, name, status")
        .eq("org_id", org_id)
        .eq("name", name)
        .eq("status", 1)
        .execute()
    )
    rows = resp.data or []

    if len(rows) == 0:
        return ResolveResult(
            operator_name=name,
            status="not_found",
            matched_count=0,
        )

    if len(rows) == 1:
        return ResolveResult(
            operator_name=name,
            status="matched",
            wecom_userid=rows[0]["wecom_userid"],
            matched_count=1,
        )

    # 多匹配（同名）
    return ResolveResult(
        operator_name=name,
        status="multiple",
        matched_count=len(rows),
        candidates=[r["wecom_userid"] for r in rows],
    )


def verify_binding_still_valid(
    db: Any,
    *,
    org_id: str,
    wecom_userid: str,
) -> bool:
    """
    验证一个已绑定的 wecom_userid 是否还有效（员工还在职）。

    用于"绑定健康检查"——sync 后跑一遍所有 is_bound=TRUE 的运营，
    如果对应的企微账号被删/离职 → 自动解绑 + 告警。

    Returns:
        True 表示账号还存在且在职；False 表示账号已失效
    """
    if not wecom_userid:
        return False

    resp = (
        db.table("wecom_employees")
        .select("wecom_userid")
        .eq("org_id", org_id)
        .eq("wecom_userid", wecom_userid)
        .eq("status", 1)
        .limit(1)
        .execute()
    )
    return bool(resp.data)
