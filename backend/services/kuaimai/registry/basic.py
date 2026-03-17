"""
基础信息 API注册表

包含：仓库、店铺、标签、客户、分销商 相关API
"""

from services.kuaimai.registry.base import ApiEntry

BASIC_REGISTRY = {
    # ── 查询 ──────────────────────────────────────────
    "warehouse_list": ApiEntry(
        method="erp.warehouse.list.query",
        description="查询仓库列表（实体仓库信息：名称/编码/地址）。按名称/编码/ID筛选。查虚拟仓用virtual_warehouse",
        param_map={
            "name": "name",
            "code": "code",
            "warehouse_id": "id",
        },
        param_docs={
            "name": "仓库名称（精确匹配）。示例: 默认仓库",
            "code": "仓库编码（精确匹配）。示例: A",
            "warehouse_id": "仓库ID。示例: 1001",
        },
        fetch_all=True,
        page_size=500,
        formatter="format_warehouse_list",
    ),
    "shop_list": ApiEntry(
        method="erp.shop.list.query",
        description="查询店铺列表（各平台店铺信息：名称/平台/状态）。按名称/简称/ID筛选。获取店铺ID用于其他查询的shop_ids参数",
        param_map={
            "name": "name",
            "shop_id": "id",
            "short_name": "shortName",
        },
        param_docs={
            "name": "店铺显示名称（精确匹配title字段，非平台账号名）。示例: 蓝恩集美优品",
            "shop_id": "店铺ID（API实测id参数不生效，建议用name筛选后取shopId）。示例: 12345",
            "short_name": "店铺简称。示例: 旗舰店",
        },
        fetch_all=True,
        page_size=500,
        formatter="format_shop_list",
    ),
    "tag_list": ApiEntry(
        method="erp.trade.query.tag.list",
        description="查询订单/售后标签列表（订单打标分类用）。这是订单标签，商品标签在erp_product_query的tag_list",
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
        description="查询客户基础资料列表（B2B客户管理）。按名称/编码/昵称/等级筛选。不是查买家信息，买家信息在order_list中",
        param_map={
            "name": "name",
            "code": "code",
            "nick": "nick",
            "level": "level",
            "status": "enableStatus",
        },
        param_docs={
            "name": "客户名称（API实测不生效，建议用nick代替）。示例: 张三",
            "code": "客户编码（精确匹配）。示例: 529125636438528",
            "nick": "客户昵称（精确匹配）。示例: 王总",
            "level": "客户等级。可选值: 1(一级), 2(二级), 3(三级), 4(四级), 5(五级)。示例: 1",
            "status": "启用状态。可选值: 0(停用), 1(正常)。示例: 1",
        },
        formatter="format_customer_list",
    ),
    "distributor_list": ApiEntry(
        method="erp.distributor.list.query",
        description="查询分销商列表（分销合作伙伴信息）。按名称/状态/ID筛选",
        param_map={
            "name": "distributorName",
            "state": "state",
            "ids": "distributorCompanyIds",
        },
        param_docs={
            "name": "分销商名称（模糊搜索，支持前缀/后缀/包含匹配）。示例: 华东分销",
            "state": "分销商状态。可选值: 1(查询所有状态), 2(查询有效状态)。示例: 2",
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
