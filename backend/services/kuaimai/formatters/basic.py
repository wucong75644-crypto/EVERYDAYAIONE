"""
基础信息 格式化器（Phase 5B 标签映射表模式）

格式化仓库、店铺、标签、客户、分销商等基础查询结果。
"""

from typing import Any, Callable, Dict

from services.kuaimai.formatters.common import format_item_with_labels, format_platform, format_timestamp
from services.kuaimai.registry.base import ApiEntry

# ---------------------------------------------------------------------------
# 仓库列表 — erp.warehouse.list.query
# ---------------------------------------------------------------------------
_WAREHOUSE_LABELS = {
    "name": "名称", "code": "编码",
    "type": "类型",
    "status": "状态",
    "contact": "联系人", "contactPhone": "电话",
    "state": "省", "city": "市", "district": "区",
    "address": "地址",
    "externalCode": "外部编码",
}
_WAREHOUSE_TRANSFORMS: Dict[str, Callable] = {
    "type": lambda v: {0: "自有", 1: "第三方", 2: "门店"}.get(v, str(v)),
    "status": lambda v: {0: "停用", 1: "正常", 2: "禁止发货"}.get(v, str(v)),
}

# ---------------------------------------------------------------------------
# 店铺列表 — erp.shop.list.query
# 修正: active(2态) → state(4态)
# ---------------------------------------------------------------------------
_SHOP_LABELS = {
    "title": "名称", "shortTitle": "简称",
    "userId": "店铺编码", "shopId": "店铺ID",
    "source": "平台", "nick": "昵称",
    "state": "状态",
    "deadline": "到期时间",
    "groupName": "店铺组",
}
_SHOP_TRANSFORMS: Dict[str, Callable] = {
    "source": format_platform,
    "state": lambda v: {1: "停用", 2: "未初始化", 3: "启用",
                        4: "会话失效"}.get(v, str(v)),
    "deadline": format_timestamp,
}

# ---------------------------------------------------------------------------
# 标签列表 — erp.trade.query.tag.list / erp.item.tag.list
# ---------------------------------------------------------------------------
_TAG_LABELS = {
    "tagName": "标签名", "name": "标签名", "id": "ID",
    "type": "类型",
    "remark": "说明",
}
_TAG_TRANSFORMS: Dict[str, Callable] = {
    "type": lambda v: {0: "普通", 1: "自定义异常", 3: "系统",
                       -1: "系统异常"}.get(v, str(v)),
}

# ---------------------------------------------------------------------------
# 客户列表 — erp.query.customers.list
# ---------------------------------------------------------------------------
_CUSTOMER_LABELS = {
    "name": "名称", "code": "编码",
    "type": "类型",
    "level": "等级",
    "contact": "联系人", "contactPhone": "电话",
    "discountRate": "折扣率",
    "status": "状态",
    "remark": "备注",
    "invoiceTitle": "发票抬头",
}
_CUSTOMER_TRANSFORMS: Dict[str, Callable] = {
    "type": lambda v: {0: "分销商", 1: "经销商", 2: "线下渠道",
                       3: "其他", 4: "线上代发"}.get(v, str(v)),
    "status": lambda v: "正常" if v == 1 else "停用",
}

# ---------------------------------------------------------------------------
# 分销商列表 — erp.distributor.list.query
# ---------------------------------------------------------------------------
_DISTRIBUTOR_LABELS = {
    "distributorCompanyName": "公司名称",
    "distributorCompanyId": "公司ID",
    "distributorLevel": "等级",
    "saleStaffName": "业务员",
    "showState": "状态",
    "purchaseAccount": "采购账户",
    "helpMsg": "助记符",
    "remark": "备注",
    "autoSyncStock": "自动同步库存",
}
_DISTRIBUTOR_TRANSFORMS: Dict[str, Callable] = {
    "showState": lambda v: {1: "待审核", 2: "已生效", 3: "已作废",
                            4: "已拒绝"}.get(v, str(v)),
    "autoSyncStock": lambda v: "是" if v else "否",
}


# ===== 公开 formatter 函数 =====

def format_warehouse_list(data: Any, entry: ApiEntry) -> str:
    """仓库列表"""
    items = data.get("list") or []
    if not items:
        return "暂无仓库信息"
    lines = [f"共 {len(items)} 个仓库：\n"]
    for w in items[:50]:
        lines.append("- " + format_item_with_labels(
            w, _WAREHOUSE_LABELS, transforms=_WAREHOUSE_TRANSFORMS))
    return "\n".join(lines)


def format_shop_list(data: Any, entry: ApiEntry) -> str:
    """店铺列表"""
    items = data.get("list") or []
    if not items:
        return "暂无店铺信息"
    lines = [f"共 {len(items)} 个店铺：\n"]
    for s in items[:50]:
        lines.append("- " + format_item_with_labels(
            s, _SHOP_LABELS, transforms=_SHOP_TRANSFORMS))
    return "\n".join(lines)


def format_tag_list(data: Any, entry: ApiEntry) -> str:
    """标签列表"""
    items = data.get("list") or []
    if not items:
        return "暂无标签信息"
    lines = [f"共 {len(items)} 个标签：\n"]
    for t in items[:50]:
        # 特殊处理: remark 包含 HTML，需要清理
        remark = t.get("remark") or ""
        if remark:
            remark = remark.replace("<br/>", " ").replace("<br>", " ")
            if len(remark) > 60:
                remark = remark[:60] + "..."
            t = {**t, "remark": remark}
        lines.append("- " + format_item_with_labels(
            t, _TAG_LABELS, transforms=_TAG_TRANSFORMS))
    return "\n".join(lines)


def format_customer_list(data: Any, entry: ApiEntry) -> str:
    """客户列表"""
    items = data.get("list") or []
    total = data.get("total", len(items))
    if not items:
        return "未找到客户信息"
    lines = [f"共 {total} 个客户：\n"]
    for c in items[:30]:
        lines.append("- " + format_item_with_labels(
            c, _CUSTOMER_LABELS, transforms=_CUSTOMER_TRANSFORMS))
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
        lines.append("- " + format_item_with_labels(
            d, _DISTRIBUTOR_LABELS, transforms=_DISTRIBUTOR_TRANSFORMS))
    return "\n".join(lines)


BASIC_FORMATTERS: Dict[str, Callable] = {
    "format_warehouse_list": format_warehouse_list,
    "format_shop_list": format_shop_list,
    "format_tag_list": format_tag_list,
    "format_customer_list": format_customer_list,
    "format_distributor_list": format_distributor_list,
}

# 返回字段注册表（供 erp_api_search 生成文档）
BASIC_RESPONSE_FIELDS: Dict[str, Dict] = {
    "format_warehouse_list": {"main": _WAREHOUSE_LABELS},
    "format_shop_list": {"main": _SHOP_LABELS},
    "format_tag_list": {"main": _TAG_LABELS},
    "format_customer_list": {"main": _CUSTOMER_LABELS},
    "format_distributor_list": {"main": _DISTRIBUTOR_LABELS},
}
