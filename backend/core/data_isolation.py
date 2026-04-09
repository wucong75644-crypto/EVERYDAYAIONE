"""
数据隔离工具函数（已废弃）

已被 OrgScopedDB（core/org_scoped_db.py）全自动替代。
保留此文件仅为向后兼容旧测试，新代码不应引用此模块。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.deps import OrgContext


def apply_data_isolation(query: Any, ctx: OrgContext) -> Any:
    """
    通用数据隔离过滤（含 user_id 维度）。

    散客：WHERE org_id IS NULL AND user_id = ?
    企业：WHERE org_id = ? AND user_id = ?

    适用于：conversations, tasks, credits_history, image_generations 等
    按用户维度的业务表（企业成员各看各的）。
    """
    if ctx.org_id:
        return query.eq("org_id", ctx.org_id).eq("user_id", ctx.user_id)
    else:
        return query.is_("org_id", "null").eq("user_id", ctx.user_id)


def apply_org_filter(query: Any, ctx: OrgContext) -> Any:
    """
    纯企业维度过滤（不含 user_id）。

    散客：WHERE org_id IS NULL
    企业：WHERE org_id = ?

    适用于：erp_products, erp_stock_status 等
    全企业共享的数据表（企业内所有成员看同一份数据）。
    """
    if ctx.org_id:
        return query.eq("org_id", ctx.org_id)
    else:
        return query.is_("org_id", "null")


def get_org_id_for_insert(ctx: OrgContext) -> str | None:
    """
    获取写入数据时的 org_id 值。

    散客写入 None，企业写入 org_id。
    用于 INSERT 时填充 org_id 字段。
    """
    return ctx.org_id


def get_mem0_user_id(ctx: OrgContext) -> str:
    """
    获取 Mem0 记忆系统的 user_id。

    企业：org_{org_id}:{user_id}（企业内隔离）
    散客：personal:{user_id}
    """
    if ctx.org_id:
        return f"org_{ctx.org_id}:{ctx.user_id}"
    return f"personal:{ctx.user_id}"
