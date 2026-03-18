"""
快麦通(分销) API注册表

包含：分销商品、分销商管理、同步 相关API
"""

from services.kuaimai.registry.base import ApiEntry

DISTRIBUTION_REGISTRY = {
    # ── 查询 ──────────────────────────────────────────
    "distributor_item_list": ApiEntry(
        method="kmt.api.dms.query.page.distributor.item",
        description="分页查询供销小店商品",
        param_map={
            "distributor_company_id": "distributorCompanyId",
            "supplier_company_id": "supplierCompanyId",
            "outer_ids": "outerIds",
            "sku_outer_ids": "skuOuterIds",
            "title": "title",
            "request_source": "requestSource",
        },
        param_docs={
            "distributor_company_id": "分销商公司ID（必填，从distributor_list获取）。示例: 100001",
            "supplier_company_id": "供应商公司ID（必填）。示例: 200001",
            "outer_ids": "商家编码（逗号隔开，最多20个）。示例: ABC123,DEF456",
            "sku_outer_ids": "SKU商家编码（逗号隔开，最多20个）。示例: SKU001,SKU002",
            "title": "商品标题（模糊搜索）。示例: 手机壳",
            "request_source": "请求来源（必填）。示例: erp",
        },
        required_params=["distributor_company_id", "supplier_company_id", "request_source"],
        formatter="format_generic_list",
        response_key="data",
    ),
    "distributor_item_detail": ApiEntry(
        method="kmt.api.dms.query.detail.distributor.item",
        description="查询供销小店商品详情",
        param_map={
            "distributor_company_id": "distributorCompanyId",
            "supplier_company_id": "supplierCompanyId",
            "base_item_id": "baseItemId",
        },
        param_docs={
            "distributor_company_id": "分销商公司ID（必填，从distributor_list获取）。示例: 100001",
            "supplier_company_id": "供应商公司ID（必填）。示例: 200001",
            "base_item_id": "基础商品ID（必填，从distributor_item_list获取）。示例: 300001",
        },
        required_params=[
            "distributor_company_id", "supplier_company_id", "base_item_id",
        ],
        formatter="format_generic_detail",
        response_key=None,
    ),
    "supplier_view_item_list": ApiEntry(
        method="kmt.api.dms.query.page.distributor.item.supplier.view",
        description="供销商视角分页供销小店商品",
        param_map={
            "supplier_company_id": "supplierCompanyId",
            "shop_id": "shopId",
            "outer_ids": "outerIds",
            "sku_outer_ids": "skuOuterIds",
            "title": "title",
            "start_date": "updateTimeBegin",
            "end_date": "updateTimeEnd",
            "request_source": "requestSource",
        },
        param_docs={
            "supplier_company_id": "供应商公司ID（必填）。示例: 200001",
            "shop_id": "店铺ID（必填，通过shop_list获取）。示例: 12345",
            "outer_ids": "商家编码（逗号隔开，最多20个）。示例: ABC123,DEF456",
            "sku_outer_ids": "SKU商家编码（逗号隔开，最多20个）。示例: SKU001,SKU002",
            "title": "商品标题（模糊搜索）。示例: 手机壳",
            "start_date": "更新起始日期。格式: YYYY-MM-DD。示例: 2026-03-01",
            "end_date": "更新结束日期。格式: YYYY-MM-DD。示例: 2026-03-15",
            "request_source": "请求来源。示例: erp",
        },
        required_params=["supplier_company_id", "shop_id"],
        formatter="format_generic_list",
    ),
    "supplier_view_item_detail": ApiEntry(
        method="kmt.api.dms.query.detail.distributor.item.supplier.view",
        description="供销商视角查询供销小店商品详情",
        param_map={
            "supplier_company_id": "supplierCompanyId",
            "base_item_id": "baseItemId",
        },
        param_docs={
            "supplier_company_id": "供应商公司ID（必填）。示例: 200001",
            "base_item_id": "基础商品ID（必填，从supplier_view_item_list获取）。示例: 300001",
        },
        required_params=["supplier_company_id", "base_item_id"],
        formatter="format_generic_detail",
        response_key=None,
    ),
    "distributor_list": ApiEntry(
        method="kmt.api.dms.query.distributor.list",
        description="分销商信息查询",
        param_map={
            "supplier_company_id": "supplierCompanyId",
            "request_state": "requestState",
            "start_date": "modifiedTimeStart",
            "end_date": "modifiedTimeEnd",
        },
        param_docs={
            "supplier_company_id": "供应商公司ID（必填）。示例: 200001",
            "request_state": "分销商申请状态。可选值: 0=待审核, 1=已通过, 2=已拒绝。示例: 1",
            "start_date": "修改起始日期。格式: YYYY-MM-DD。示例: 2026-03-01",
            "end_date": "修改结束日期。格式: YYYY-MM-DD。示例: 2026-03-15",
        },
        required_params=["supplier_company_id"],
        formatter="format_generic_list",
    ),
    "pay_prompt": ApiEntry(
        method="kmt.api.dms.pay.prompt",
        description="查询在线支付方式提示文案",
        param_map={
            "pay_type": "payType",
        },
        param_docs={
            "pay_type": "支付方式类型。可选值: 1=在线支付, 2=余额支付。示例: 1",
        },
        formatter="format_generic_detail",
        response_key=None,
    ),
    "item_video_info": ApiEntry(
        method="kmt.api.dms.query.item.video.info",
        description="获取最新的视频链接信息",
        param_map={
            "supplier_company_id": "supplierCompanyId",
            "base_item_id": "baseItemId",
            "video_id": "videoId",
        },
        param_docs={
            "supplier_company_id": "供应商公司ID（必填）。示例: 200001",
            "base_item_id": "基础商品ID（必填）。示例: 300001",
            "video_id": "视频ID（必填）。示例: 400001",
        },
        required_params=[
            "supplier_company_id", "base_item_id", "video_id",
        ],
        formatter="format_generic_detail",
        response_key=None,
    ),
    # ── 写入操作 ──────────────────────────────────────
    "add_distributor_money": ApiEntry(
        method="kmt.api.dms.add.distributor.money",
        description="增加分销余额",
        param_map={
            "supplier_company_id": "supplierCompanyId",
            "distributor_company_id": "distributorCompanyId",
            "payment_type": "paymentType",
            "amount": "amount",
        },
        required_params=[
            "supplier_company_id", "distributor_company_id",
            "payment_type", "amount",
        ],
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认增加分销余额？",
    ),
    "add_distribution_item": ApiEntry(
        method="kmt.api.dms.add.distribution.item.fromsupplier",
        description="添加分销商品",
        param_map={
            "supplier_company_id": "supplierCompanyId",
            "distributor_company_id": "distributorCompanyId",
            "item_outer_id": "itemOuterId",
            "item_sku_outer_id_list": "itemSkuOuterIdList",
        },
        required_params=[
            "supplier_company_id", "distributor_company_id", "item_outer_id",
        ],
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "add_distributor": ApiEntry(
        method="kmt.api.dms.add.distributor",
        description="注册分销商",
        param_map={
            "supplier_company_id": "supplierCompanyId",
            "distributor_company_name": "distributorCompanyName",
            "source": "source",
            "main_phone": "mainPhone",
            "default_user_name": "defaultUserName",
            "default_password": "defaultPassword",
            "version_number": "versionNumber",
        },
        required_params=[
            "supplier_company_id", "distributor_company_name",
            "source", "main_phone", "default_user_name", "default_password",
        ],
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
        confirm_template="确认注册新分销商？",
    ),
    "submit_sync_item": ApiEntry(
        method="kmt.api.dms.submit.sync.item",
        description="提交分销小店商品的同步",
        param_map={
            "supplier_company_id": "supplierCompanyId",
            "shop_id": "shopId",
            "item_type": "itemType",
        },
        required_params=["supplier_company_id", "shop_id"],
        is_write=True,
        response_key=None,
        formatter="format_generic_detail",
    ),
    "sync_status_item": ApiEntry(
        method="kmt.api.dms.sync.status.item",
        description="获取小店商品的同步状态",
        param_map={
            "supplier_company_id": "supplierCompanyId",
            "shop_id": "shopId",
        },
        param_docs={
            "supplier_company_id": "供应商公司ID（必填）。示例: 200001",
            "shop_id": "店铺ID（必填，通过shop_list获取）。示例: 12345",
        },
        required_params=["supplier_company_id", "shop_id"],
        formatter="format_generic_detail",
        response_key=None,
    ),
}
