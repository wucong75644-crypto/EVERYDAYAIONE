"""
基础信息 API注册表

包含：仓库、店铺、标签、客户、分销商 相关API
"""

from services.kuaimai.registry.base import ApiEntry

BASIC_REGISTRY = {
    # ── 查询 ──────────────────────────────────────────
    "warehouse_list": ApiEntry(
        method="erp.warehouse.list.query",
        description="仓库查询",
        param_map={
            "name": "name",
            "code": "code",
            "warehouse_id": "id",
        },
        param_docs={
            "name": "仓库名称（模糊搜索）。示例: 北京仓",
            "code": "仓库编码。示例: WH001",
            "warehouse_id": "仓库ID。示例: 1001",
        },
        fetch_all=True,
        page_size=500,
        formatter="format_warehouse_list",
    ),
    "shop_list": ApiEntry(
        method="erp.shop.list.query",
        description="店铺查询",
        param_map={
            "name": "name",
            "shop_id": "id",
            "short_name": "shortName",
        },
        param_docs={
            "name": "店铺名称（模糊搜索）。示例: 天猫旗舰店",
            "shop_id": "店铺ID。示例: 12345",
            "short_name": "店铺简称（模糊搜索）。示例: 旗舰店",
        },
        fetch_all=True,
        page_size=500,
        formatter="format_shop_list",
    ),
    "tag_list": ApiEntry(
        method="erp.trade.query.tag.list",
        description="获取标签列表",
        param_map={
            "tag_type": "tagType",
        },
        param_docs={
            "tag_type": "标签类型。可选值: 1(订单标签), 2(售后标签)。不传则返回所有类型。示例: 1",
        },
        fetch_all=True,
        page_size=500,
        formatter="format_tag_list",
    ),
    "customer_list": ApiEntry(
        method="erp.query.customers.list",
        description="客户基础资料查询",
        param_map={
            "name": "name",
            "code": "code",
            "nick": "nick",
            "level": "level",
            "status": "enableStatus",
        },
        param_docs={
            "name": "客户名称（模糊搜索）。示例: 张三",
            "code": "客户编码。示例: CM001",
            "nick": "客户昵称（模糊搜索）。示例: 小张",
            "level": "客户等级。可选值: 1(普通), 2(VIP), 3(SVIP)。示例: 2",
            "status": "启用状态。可选值: 1(启用), 2(停用)。示例: 1",
        },
        formatter="format_customer_list",
    ),
    "distributor_list": ApiEntry(
        method="erp.distributor.list.query",
        description="分销商查询",
        param_map={
            "name": "distributorName",
            "state": "state",
            "ids": "distributorCompanyIds",
        },
        param_docs={
            "name": "分销商名称（模糊搜索）。示例: 华东分销",
            "state": "分销商状态。可选值: 1(合作中), 2(已终止)。示例: 1",
            "ids": "分销商公司ID（多个逗号隔开）。示例: 100,200",
        },
        fetch_all=True,
        page_size=500,
        formatter="format_distributor_list",
    ),
    # ── 写入 ──────────────────────────────────────────
    "customer_create": ApiEntry(
        method="erp.customer.create",
        description="新增/修改客户基本信息",
        param_map={
            "customer_id": "customerId",
            "code": "cmCode",
            "nick": "cmNick",
            "name": "cmName",
            "type": "type",
            "level": "level",
            "province": "province",
            "city": "city",
            "area": "area",
            "address": "address",
            "remark": "remark",
            "email": "email",
            "payment_method": "paymentMethod",
            "discount_rate": "discountRate",
            "invoice_title": "invoiceTitle",
            "tax_number": "taxNumber",
            "bank_name": "bankName",
            "bank_account": "bankAccount",
            "type_code": "typeCode",
            "qq": "qqNumber",
            "fax": "fax",
            "url": "url",
            "zip_code": "zipCode",
        },
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认新增/修改客户「{name}」？",
    ),
}
