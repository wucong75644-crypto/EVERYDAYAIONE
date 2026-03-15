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
        formatter="format_shop_list",
    ),
    "tag_list": ApiEntry(
        method="erp.trade.query.tag.list",
        description="获取标签列表",
        param_map={
            "tag_type": "tagType",
        },
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
