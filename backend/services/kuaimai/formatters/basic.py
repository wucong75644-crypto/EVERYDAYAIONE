"""
基础信息 格式化器

格式化仓库、店铺、标签、客户、分销商等基础查询结果。
"""

from typing import Any, Callable, Dict

from services.kuaimai.formatters.common import format_timestamp
from services.kuaimai.registry.base import ApiEntry


def format_warehouse_list(data: Any, entry: ApiEntry) -> str:
    """仓库列表"""
    items = data.get("list") or []
    if not items:
        return "暂无仓库信息"
    lines = [f"共 {len(items)} 个仓库：\n"]
    for w in items:
        name = w.get("name") or ""
        code = w.get("code") or ""
        status = "启用" if w.get("status") == 1 else "停用"
        addr = w.get("address") or ""
        contact = w.get("contact") or ""
        phone = w.get("contactPhone") or ""
        parts = [f"- {name}"]
        if code:
            parts.append(f"编码: {code}")
        parts.append(f"状态: {status}")
        if contact:
            parts.append(f"联系人: {contact}")
        if phone:
            parts.append(f"电话: {phone}")
        if addr:
            parts.append(f"地址: {addr}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_shop_list(data: Any, entry: ApiEntry) -> str:
    """店铺列表"""
    items = data.get("list") or []
    if not items:
        return "暂无店铺信息"
    lines = [f"共 {len(items)} 个店铺：\n"]
    for s in items:
        name = s.get("name") or ""
        short = s.get("shortTitle") or ""
        source = s.get("source") or ""
        active = "启用" if s.get("active") == 1 else "停用"
        nick = s.get("nick") or ""
        parts = [f"- {name}"]
        if short:
            parts.append(f"简称: {short}")
        if source:
            parts.append(f"平台: {source}")
        if nick:
            parts.append(f"昵称: {nick}")
        parts.append(f"状态: {active}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_tag_list(data: Any, entry: ApiEntry) -> str:
    """标签列表"""
    items = data.get("list") or []
    if not items:
        return "暂无标签信息"
    lines = [f"共 {len(items)} 个标签：\n"]
    for t in items:
        name = t.get("tagName") or t.get("name") or ""
        tag_id = t.get("id") or ""
        remark = t.get("remark") or ""
        line = f"- {name} (ID: {tag_id})"
        if remark:
            clean = remark.replace("<br/>", " ").replace("<br>", " ")
            if len(clean) > 60:
                clean = clean[:60] + "..."
            line += f" | {clean}"
        lines.append(line)
    return "\n".join(lines)


def format_customer_list(data: Any, entry: ApiEntry) -> str:
    """客户列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到客户信息"
    lines = [f"共 {total} 个客户：\n"]
    for c in items[:30]:
        name = c.get("name") or c.get("nick") or ""
        code = c.get("code") or ""
        contact = c.get("contact") or ""
        phone = c.get("contactPhone") or ""
        status = "正常" if c.get("status") == 1 else "停用"
        parts = [f"- {name}"]
        if code:
            parts.append(f"编码: {code}")
        if contact:
            parts.append(f"联系人: {contact}")
        if phone:
            parts.append(f"电话: {phone}")
        parts.append(f"状态: {status}")
        lines.append(" | ".join(parts))
    if int(total) > len(items):
        lines.append(f"\n（显示前{len(items)}个，共{total}个）")
    return "\n".join(lines)


def format_distributor_list(data: Any, entry: ApiEntry) -> str:
    """分销商列表"""
    items = data.get("list") or []
    if not items:
        return "暂无分销商信息"
    lines = [f"共 {len(items)} 个分销商：\n"]
    for d in items[:30]:
        name = d.get("distributorCompanyName") or ""
        did = d.get("distributorCompanyId") or ""
        level = d.get("distributorLevel") or ""
        staff = d.get("saleStaffName") or ""
        parts = [f"- {name}"]
        if did:
            parts.append(f"ID: {did}")
        if level:
            parts.append(f"等级: {level}")
        if staff:
            parts.append(f"业务员: {staff}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


BASIC_FORMATTERS: Dict[str, Callable] = {
    "format_warehouse_list": format_warehouse_list,
    "format_shop_list": format_shop_list,
    "format_tag_list": format_tag_list,
    "format_customer_list": format_customer_list,
    "format_distributor_list": format_distributor_list,
}
