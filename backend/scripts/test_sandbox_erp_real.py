"""
[已废弃] 沙盒 ERP 真实参数集成测试

⚠️ 本脚本已废弃（2026-04-15）。
原因：沙盒纯计算重构后，erp_query_all/erp_query 已从沙盒移除，
      数据获取统一走 Agent 工具层（fetch_all_pages → staging → code_execute）。
替代方案：backend/tests/test_paginate_erp.py（翻页引擎单元测试）

运行: cd backend && source venv/bin/activate && PYTHONPATH=. python scripts/test_sandbox_erp_real.py
"""

import asyncio
from typing import Any, Dict
from unittest.mock import AsyncMock

from services.sandbox.functions import build_sandbox_executor


# ============================================================
# 真实字段名 Mock 数据工厂
# ============================================================

def _mock_shops():
    """店铺列表 — 真实字段: userId, title, shortTitle, source, active, nick"""
    return {
        "list": [
            {
                "userId": "112358",
                "title": "美妆旗舰店",
                "shortTitle": "旗舰店",
                "source": "tmall",
                "active": 1,
                "nick": "meizhuang_official",
                "platformName": "天猫",
            },
            {
                "userId": "223469",
                "title": "京东自营专区",
                "shortTitle": "京东自营",
                "source": "jd",
                "active": 1,
                "nick": "jd_beauty",
                "platformName": "京东",
            },
            {
                "userId": "334570",
                "title": "抖音直播间",
                "shortTitle": "抖音",
                "source": "douyin",
                "active": 1,
                "nick": "douyin_live",
                "platformName": "抖音",
            },
            {
                "userId": "445681",
                "title": "拼多多百亿店",
                "shortTitle": "拼多多",
                "source": "pdd",
                "active": 1,
                "nick": "pdd_official",
                "platformName": "拼多多",
            },
            {
                "userId": "556792",
                "title": "小红书种草店",
                "shortTitle": "小红书",
                "source": "xhs",
                "active": 1,
                "nick": "xhs_beauty",
                "platformName": "小红书",
            },
        ],
        "total": 5,
    }


def _mock_orders(params: Dict[str, Any]):
    """订单列表 — 真实字段 + 真实平台订单号格式 + orders 子订单"""
    all_orders = [
        # ── 天猫订单（tid=18位数字）──
        {
            "tid": "126036803257340376",
            "sid": "5759422420146938",
            "sysStatus": "TRADE_FINISHED",
            "buyerNick": "时尚达人小美",
            "payment": "299.00",
            "postFee": "0.00",
            "totalFee": "299.00",
            "shopName": "美妆旗舰店",
            "source": "tmall",
            "created": 1742083200000,   # 2026-03-16 09:00:00
            "payTime": 1742083260000,
            "consignTime": 1742090400000,
            "warehouseName": "杭州仓",
            "sellerMemo": "老客户，优先发货",
            "receiverName": "张**",
            "receiverState": "浙江省",
            "receiverCity": "杭州市",
            "orders": [
                {
                    "sysTitle": "兰蔻小黑瓶精华50ml",
                    "outerId": "LK-XHP-50",
                    "skuOuterId": "LK-XHP-50-01",
                    "num": 1,
                    "price": "299.00",
                    "totalFee": "299.00",
                    "propertiesName": "规格:50ml",
                },
            ],
        },
        {
            "tid": "126036803257340512",
            "sid": "5759422420146939",
            "sysStatus": "WAIT_SELLER_SEND_GOODS",
            "buyerNick": "护肤控小王",
            "payment": "458.00",
            "postFee": "0.00",
            "totalFee": "458.00",
            "shopName": "美妆旗舰店",
            "source": "tmall",
            "created": 1742090400000,   # 2026-03-16 11:00:00
            "payTime": 1742090460000,
            "consignTime": None,
            "warehouseName": "杭州仓",
            "sellerMemo": "",
            "receiverName": "王**",
            "receiverState": "上海市",
            "receiverCity": "上海市",
            "orders": [
                {
                    "sysTitle": "SK-II神仙水230ml",
                    "outerId": "SK2-SSW-230",
                    "skuOuterId": "SK2-SSW-230-01",
                    "num": 1,
                    "price": "458.00",
                    "totalFee": "458.00",
                    "propertiesName": "规格:230ml",
                },
            ],
        },
        {
            "tid": "126036803257340801",
            "sid": "5759422420146940",
            "sysStatus": "TRADE_FINISHED",
            "buyerNick": "妈妈的化妆台",
            "payment": "1099.00",
            "postFee": "0.00",
            "totalFee": "1099.00",
            "shopName": "美妆旗舰店",
            "source": "tmall",
            "created": 1742101200000,   # 2026-03-16 14:00:00
            "payTime": 1742101260000,
            "consignTime": 1742108400000,
            "warehouseName": "杭州仓",
            "sellerMemo": "",
            "receiverName": "李**",
            "receiverState": "广东省",
            "receiverCity": "深圳市",
            "orders": [
                {
                    "sysTitle": "兰蔻小黑瓶精华50ml",
                    "outerId": "LK-XHP-50",
                    "skuOuterId": "LK-XHP-50-01",
                    "num": 2,
                    "price": "299.00",
                    "totalFee": "598.00",
                    "propertiesName": "规格:50ml",
                },
                {
                    "sysTitle": "雅诗兰黛眼霜15ml",
                    "outerId": "EL-YS-15",
                    "skuOuterId": "EL-YS-15-01",
                    "num": 1,
                    "price": "501.00",
                    "totalFee": "501.00",
                    "propertiesName": "规格:15ml",
                },
            ],
        },
        # ── 京东订单（tid=16位数字）──
        {
            "tid": "2860315678901234",
            "sid": "6759422420146941",
            "sysStatus": "TRADE_FINISHED",
            "buyerNick": "JD_beauty_fan",
            "payment": "680.00",
            "postFee": "0.00",
            "totalFee": "680.00",
            "shopName": "京东自营专区",
            "source": "jd",
            "created": 1742076000000,   # 2026-03-16 07:00:00
            "payTime": 1742076060000,
            "consignTime": 1742083200000,
            "warehouseName": "北京仓",
            "sellerMemo": "",
            "receiverName": "赵**",
            "receiverState": "北京市",
            "receiverCity": "北京市",
            "orders": [
                {
                    "sysTitle": "SK-II神仙水230ml",
                    "outerId": "SK2-SSW-230",
                    "skuOuterId": "SK2-SSW-230-01",
                    "num": 1,
                    "price": "458.00",
                    "totalFee": "458.00",
                    "propertiesName": "规格:230ml",
                },
                {
                    "sysTitle": "资生堂红腰子精华30ml",
                    "outerId": "SSD-HYZ-30",
                    "skuOuterId": "SSD-HYZ-30-01",
                    "num": 1,
                    "price": "222.00",
                    "totalFee": "222.00",
                    "propertiesName": "规格:30ml",
                },
            ],
        },
        {
            "tid": "2860315678901298",
            "sid": "6759422420146942",
            "sysStatus": "SELLER_SEND_GOODS",
            "buyerNick": "京东PLUS会员",
            "payment": "149.00",
            "postFee": "0.00",
            "totalFee": "149.00",
            "shopName": "京东自营专区",
            "source": "jd",
            "created": 1742097600000,   # 2026-03-16 13:00:00
            "payTime": 1742097660000,
            "consignTime": 1742104800000,
            "warehouseName": "北京仓",
            "sellerMemo": "",
            "receiverName": "孙**",
            "receiverState": "天津市",
            "receiverCity": "天津市",
            "orders": [
                {
                    "sysTitle": "妮维雅男士洗面奶150ml",
                    "outerId": "NV-NS-150",
                    "skuOuterId": "NV-NS-150-01",
                    "num": 3,
                    "price": "49.90",
                    "totalFee": "149.70",
                    "propertiesName": "规格:150ml 3支装",
                },
            ],
        },
        # ── 抖音订单（tid=19位数字）──
        {
            "tid": "7260316123456789012",
            "sid": "7759422420146943",
            "sysStatus": "TRADE_FINISHED",
            "buyerNick": "直播间铁粉001",
            "payment": "199.00",
            "postFee": "0.00",
            "totalFee": "199.00",
            "shopName": "抖音直播间",
            "source": "douyin",
            "created": 1742119200000,   # 2026-03-16 19:00:00
            "payTime": 1742119260000,
            "consignTime": None,
            "warehouseName": "杭州仓",
            "sellerMemo": "直播秒杀款",
            "receiverName": "周**",
            "receiverState": "江苏省",
            "receiverCity": "南京市",
            "orders": [
                {
                    "sysTitle": "兰蔻小黑瓶精华50ml",
                    "outerId": "LK-XHP-50",
                    "skuOuterId": "LK-XHP-50-01",
                    "num": 1,
                    "price": "199.00",
                    "totalFee": "199.00",
                    "propertiesName": "规格:50ml（直播特惠）",
                },
            ],
        },
        {
            "tid": "7260316123456789098",
            "sid": "7759422420146944",
            "sysStatus": "WAIT_AUDIT",
            "buyerNick": "直播间铁粉002",
            "payment": "899.00",
            "postFee": "0.00",
            "totalFee": "899.00",
            "shopName": "抖音直播间",
            "source": "douyin",
            "created": 1742126400000,   # 2026-03-16 21:00:00
            "payTime": 1742126460000,
            "consignTime": None,
            "warehouseName": "杭州仓",
            "sellerMemo": "",
            "receiverName": "吴**",
            "receiverState": "四川省",
            "receiverCity": "成都市",
            "orders": [
                {
                    "sysTitle": "兰蔻小黑瓶精华50ml",
                    "outerId": "LK-XHP-50",
                    "skuOuterId": "LK-XHP-50-01",
                    "num": 2,
                    "price": "199.00",
                    "totalFee": "398.00",
                    "propertiesName": "规格:50ml",
                },
                {
                    "sysTitle": "雅诗兰黛眼霜15ml",
                    "outerId": "EL-YS-15",
                    "skuOuterId": "EL-YS-15-01",
                    "num": 1,
                    "price": "501.00",
                    "totalFee": "501.00",
                    "propertiesName": "规格:15ml",
                },
            ],
        },
        # ── 拼多多订单（tid=日期-数字串）──
        {
            "tid": "260316-088765432101",
            "sid": "8759422420146945",
            "sysStatus": "TRADE_FINISHED",
            "buyerNick": None,  # 拼多多隐私保护，buyerNick=null
            "payment": "69.90",
            "postFee": "0.00",
            "totalFee": "69.90",
            "shopName": "拼多多百亿店",
            "source": "pdd",
            "created": 1742086800000,   # 2026-03-16 10:00:00
            "payTime": 1742086860000,
            "consignTime": 1742094000000,
            "warehouseName": "杭州仓",
            "sellerMemo": "",
            "receiverName": "郑**",
            "receiverState": "湖北省",
            "receiverCity": "武汉市",
            "orders": [
                {
                    "sysTitle": "妮维雅男士洗面奶150ml",
                    "outerId": "NV-NS-150",
                    "skuOuterId": "NV-NS-150-01",
                    "num": 2,
                    "price": "34.95",
                    "totalFee": "69.90",
                    "propertiesName": "规格:150ml",
                },
            ],
        },
        # ── 小红书订单（tid=P+18位数字）──
        {
            "tid": "P126036803257340901",
            "sid": "9759422420146946",
            "sysStatus": "WAIT_SELLER_SEND_GOODS",
            "buyerNick": "小红书种草达人",
            "payment": "520.00",
            "postFee": "0.00",
            "totalFee": "520.00",
            "shopName": "小红书种草店",
            "source": "xhs",
            "created": 1742112000000,   # 2026-03-16 17:00:00
            "payTime": 1742112060000,
            "consignTime": None,
            "warehouseName": "杭州仓",
            "sellerMemo": "网红推荐款",
            "receiverName": "陈**",
            "receiverState": "浙江省",
            "receiverCity": "杭州市",
            "orders": [
                {
                    "sysTitle": "雅诗兰黛眼霜15ml",
                    "outerId": "EL-YS-15",
                    "skuOuterId": "EL-YS-15-01",
                    "num": 1,
                    "price": "520.00",
                    "totalFee": "520.00",
                    "propertiesName": "规格:15ml",
                },
            ],
        },
        # ── 1688 分销订单（tid=19位数字）──
        {
            "tid": "6912345678901234567",
            "sid": "1059422420146947",
            "sysStatus": "TRADE_FINISHED",
            "buyerNick": "华东分销商A",
            "payment": "3600.00",
            "postFee": "15.00",
            "totalFee": "3600.00",
            "shopName": "美妆旗舰店",
            "source": "1688",
            "created": 1742094000000,   # 2026-03-16 12:00:00
            "payTime": 1742094060000,
            "consignTime": 1742101200000,
            "warehouseName": "杭州仓",
            "sellerMemo": "分销批发",
            "receiverName": "分销商A仓库",
            "receiverState": "浙江省",
            "receiverCity": "义乌市",
            "orders": [
                {
                    "sysTitle": "兰蔻小黑瓶精华50ml",
                    "outerId": "LK-XHP-50",
                    "skuOuterId": "LK-XHP-50-01",
                    "num": 10,
                    "price": "180.00",
                    "totalFee": "1800.00",
                    "propertiesName": "规格:50ml 批发",
                },
                {
                    "sysTitle": "资生堂红腰子精华30ml",
                    "outerId": "SSD-HYZ-30",
                    "skuOuterId": "SSD-HYZ-30-01",
                    "num": 10,
                    "price": "180.00",
                    "totalFee": "1800.00",
                    "propertiesName": "规格:30ml 批发",
                },
            ],
        },
    ]
    page = int(params.get("page", 1))
    page_size = int(params.get("page_size", 100))
    start = (page - 1) * page_size
    return {
        "list": all_orders[start:start + page_size],
        "total": len(all_orders),
        "pageNo": page,
        "pageSize": page_size,
    }


def _mock_stock_status(params: Dict[str, Any]):
    """库存状态 — 真实 response_key = stockStatusVoList"""
    items = [
        {
            "title": "兰蔻小黑瓶精华50ml",
            "shortTitle": "小黑瓶50ml",
            "mainOuterId": "LK-XHP-50",
            "outerId": "LK-XHP-50-01",
            "propertiesName": "规格:50ml",
            "totalAvailableStockSum": 320,
            "sellableNum": 298,
            "totalLockStock": 22,
            "wareHouseId": "1001",
            "stockStatus": 1,
            "purchasePrice": "150.00",
        },
        {
            "title": "兰蔻小黑瓶精华50ml",
            "shortTitle": "小黑瓶50ml",
            "mainOuterId": "LK-XHP-50",
            "outerId": "LK-XHP-50-01",
            "propertiesName": "规格:50ml",
            "totalAvailableStockSum": 85,
            "sellableNum": 80,
            "totalLockStock": 5,
            "wareHouseId": "1002",
            "stockStatus": 1,
            "purchasePrice": "150.00",
        },
        {
            "title": "SK-II神仙水230ml",
            "shortTitle": "神仙水230ml",
            "mainOuterId": "SK2-SSW-230",
            "outerId": "SK2-SSW-230-01",
            "propertiesName": "规格:230ml",
            "totalAvailableStockSum": 42,
            "sellableNum": 35,
            "totalLockStock": 7,
            "wareHouseId": "1001",
            "stockStatus": 2,  # 警戒
            "purchasePrice": "280.00",
        },
        {
            "title": "雅诗兰黛眼霜15ml",
            "shortTitle": "眼霜15ml",
            "mainOuterId": "EL-YS-15",
            "outerId": "EL-YS-15-01",
            "propertiesName": "规格:15ml",
            "totalAvailableStockSum": 18,
            "sellableNum": 12,
            "totalLockStock": 6,
            "wareHouseId": "1001",
            "stockStatus": 2,  # 警戒
            "purchasePrice": "320.00",
        },
        {
            "title": "资生堂红腰子精华30ml",
            "shortTitle": "红腰子30ml",
            "mainOuterId": "SSD-HYZ-30",
            "outerId": "SSD-HYZ-30-01",
            "propertiesName": "规格:30ml",
            "totalAvailableStockSum": 560,
            "sellableNum": 530,
            "totalLockStock": 30,
            "wareHouseId": "1001",
            "stockStatus": 1,
            "purchasePrice": "120.00",
        },
        {
            "title": "妮维雅男士洗面奶150ml",
            "shortTitle": "男士洗面奶",
            "mainOuterId": "NV-NS-150",
            "outerId": "NV-NS-150-01",
            "propertiesName": "规格:150ml",
            "totalAvailableStockSum": 1500,
            "sellableNum": 1420,
            "totalLockStock": 80,
            "wareHouseId": "1001",
            "stockStatus": 1,
            "purchasePrice": "18.00",
        },
    ]
    page = int(params.get("page", 1))
    page_size = int(params.get("page_size", 50))
    start = (page - 1) * page_size
    # ★ 真实 API 返回的 key 是 stockStatusVoList，不是 list
    return {
        "stockStatusVoList": items[start:start + page_size],
        "total": len(items),
        "pageNo": page,
        "pageSize": page_size,
    }


def _mock_products(params: Dict[str, Any]):
    """商品列表 — 真实 response_key = items"""
    products = [
        {
            "id": "90001",
            "title": "兰蔻小黑瓶精华50ml",
            "outerId": "LK-XHP-50",
            "barcode": "3614271256263",
            "activeStatus": 1,
            "isSkuItem": 1,
            "weight": 280,
            "unit": "瓶",
            "sellingPrice": "299.00",
            "purchasePrice": "150.00",
            "catName": "护肤精华",
        },
        {
            "id": "90002",
            "title": "SK-II神仙水230ml",
            "outerId": "SK2-SSW-230",
            "barcode": "4979006065731",
            "activeStatus": 1,
            "isSkuItem": 1,
            "weight": 420,
            "unit": "瓶",
            "sellingPrice": "458.00",
            "purchasePrice": "280.00",
            "catName": "护肤精华",
        },
        {
            "id": "90003",
            "title": "雅诗兰黛眼霜15ml",
            "outerId": "EL-YS-15",
            "barcode": "0887167316478",
            "activeStatus": 1,
            "isSkuItem": 0,
            "weight": 85,
            "unit": "瓶",
            "sellingPrice": "520.00",
            "purchasePrice": "320.00",
            "catName": "眼部护理",
        },
        {
            "id": "90004",
            "title": "资生堂红腰子精华30ml",
            "outerId": "SSD-HYZ-30",
            "barcode": "4901872963081",
            "activeStatus": 1,
            "isSkuItem": 0,
            "weight": 150,
            "unit": "瓶",
            "sellingPrice": "222.00",
            "purchasePrice": "120.00",
            "catName": "护肤精华",
        },
        {
            "id": "90005",
            "title": "妮维雅男士洗面奶150ml",
            "outerId": "NV-NS-150",
            "barcode": "4005808223916",
            "activeStatus": 1,
            "isSkuItem": 1,
            "weight": 180,
            "unit": "瓶",
            "sellingPrice": "49.90",
            "purchasePrice": "18.00",
            "catName": "男士护理",
        },
    ]
    page = int(params.get("page", 1))
    page_size = int(params.get("page_size", 20))
    start = (page - 1) * page_size
    # ★ 真实 API 返回的 key 是 items，不是 list
    return {
        "items": products[start:start + page_size],
        "total": len(products),
        "pageNo": page,
        "pageSize": page_size,
    }


def _mock_aftersales(params: Dict[str, Any]):
    """售后列表 — 真实字段"""
    items = [
        {
            "id": "AS20260316001",
            "tid": "126036803257340376",
            "sid": "5759422420146938",
            "type": 1,  # 退款
            "typeName": "仅退款",
            "status": "SUCCESS",
            "refundFee": "299.00",
            "reason": "不想要了",
            "buyerNick": "时尚达人小美",
            "shopName": "美妆旗舰店",
            "created": 1742130000000,
            "goodsName": "兰蔻小黑瓶精华50ml",
        },
        {
            "id": "AS20260316002",
            "tid": "260316-088765432101",
            "sid": "8759422420146945",
            "type": 2,  # 退货退款
            "typeName": "退货退款",
            "status": "WAIT_SELLER_AGREE",
            "refundFee": "69.90",
            "reason": "质量问题",
            "buyerNick": None,  # 拼多多隐私
            "shopName": "拼多多百亿店",
            "created": 1742133600000,
            "goodsName": "妮维雅男士洗面奶150ml",
        },
    ]
    return {"list": items, "total": len(items)}


def _mock_warehouses():
    """仓库列表"""
    return {
        "list": [
            {
                "id": "1001",
                "name": "杭州仓",
                "code": "WH-HZ",
                "type": "自有仓",
                "address": "浙江省杭州市余杭区",
            },
            {
                "id": "1002",
                "name": "北京仓",
                "code": "WH-BJ",
                "type": "自有仓",
                "address": "北京市大兴区",
            },
        ],
        "total": 2,
    }


def _build_mock_dispatcher():
    """构建模拟真实 API 响应的 mock dispatcher"""
    dispatcher = AsyncMock()

    async def mock_execute_raw(
        tool_name: str, action: str, params: Dict[str, Any],
    ) -> Dict[str, Any]:
        # 基础信息
        if action == "shop_list":
            return _mock_shops()
        if action == "warehouse_list":
            return _mock_warehouses()
        # 交易
        if action == "order_list":
            return _mock_orders(params)
        # 商品
        if action == "stock_status":
            return _mock_stock_status(params)
        if action == "product_list":
            return _mock_products(params)
        # 售后
        if action == "aftersale_list":
            return _mock_aftersales(params)
        # 写操作拦截
        if action in ("order_cancel", "receiver_update", "stock_update"):
            return {"error": f"沙盒内禁止写操作: {action}"}
        # 未知 action
        return {"list": [], "total": 0}

    dispatcher.execute_raw = mock_execute_raw
    return dispatcher


# ============================================================
# 测试场景
# ============================================================

SCENARIOS = [
    # ── 场景1：用真实字段名处理订单数据 ──
    {
        "name": "真实字段 — 各店铺成交额（含 orders 子订单）",
        "desc": "各店铺成交额",
        "code": """
orders = await erp_query_all("erp_trade_query", "order_list", {"page_size": 100})
items = orders.get("list", [])

from collections import defaultdict
shop_total = defaultdict(float)
shop_count = defaultdict(int)
for o in items:
    shop = o.get("shopName", "未知")
    shop_total[shop] += float(o.get("payment", 0))
    shop_count[shop] += 1

print("=== 各店铺成交额 ===")
for shop in sorted(shop_total, key=shop_total.get, reverse=True):
    print(f"  {shop}: ¥{shop_total[shop]:,.2f} ({shop_count[shop]}单)")
print(f"\\n合计: ¥{sum(shop_total.values()):,.2f} / {sum(shop_count.values())}单")
""",
    },

    # ── 场景2：解析 orders 子订单 (真实嵌套结构) ──
    {
        "name": "真实字段 — 解析 orders 子订单明细",
        "desc": "子订单明细",
        "code": """
data = await erp_query_all("erp_trade_query", "order_list", {"page_size": 100})
items = data.get("list", [])

from collections import defaultdict
sku_sales = defaultdict(lambda: {"qty": 0, "revenue": 0.0})

for order in items:
    for sub in order.get("orders", []):
        sku = sub.get("outerId", "未知")
        title = sub.get("sysTitle", "")
        qty = int(sub.get("num", 0))
        fee = float(sub.get("totalFee", 0))
        sku_sales[sku]["title"] = title
        sku_sales[sku]["qty"] += qty
        sku_sales[sku]["revenue"] += fee

print("=== SKU 销售排行（按金额）===")
for sku in sorted(sku_sales, key=lambda k: sku_sales[k]["revenue"], reverse=True):
    info = sku_sales[sku]
    print(f"  {info['title']} ({sku}): {info['qty']}件 / ¥{info['revenue']:,.2f}")
""",
    },

    # ── 场景3：按平台（source）统计 ──
    {
        "name": "真实字段 — 按平台 source 统计",
        "desc": "平台统计",
        "code": """
data = await erp_query_all("erp_trade_query", "order_list", {"page_size": 100})
items = data.get("list", [])

from collections import defaultdict
platform = defaultdict(lambda: {"count": 0, "total": 0.0})
for o in items:
    src = o.get("source", "unknown")
    platform[src]["count"] += 1
    platform[src]["total"] += float(o.get("payment", 0))

print("=== 平台成交分布 ===")
grand_total = sum(p["total"] for p in platform.values())
for src in sorted(platform, key=lambda k: platform[k]["total"], reverse=True):
    p = platform[src]
    pct = p["total"] / grand_total * 100 if grand_total else 0
    print(f"  {src:>8}: ¥{p['total']:>10,.2f} ({p['count']}单, {pct:.1f}%)")
""",
    },

    # ── 场景4：按订单状态统计 ──
    {
        "name": "真实字段 — sysStatus 状态分布",
        "desc": "状态分布",
        "code": """
data = await erp_query_all("erp_trade_query", "order_list", {"page_size": 100})
items = data.get("list", [])

from collections import Counter
status_map = {
    "WAIT_BUYER_PAY": "待付款",
    "WAIT_AUDIT": "待审核",
    "WAIT_SELLER_SEND_GOODS": "待发货",
    "SELLER_SEND_GOODS": "已发货",
    "TRADE_FINISHED": "已完成",
    "CLOSED": "已关闭",
}
counts = Counter(o.get("sysStatus", "") for o in items)

print("=== 订单状态分布 ===")
for status, cnt in counts.most_common():
    label = status_map.get(status, status)
    print(f"  {label}({status}): {cnt}单")
""",
    },

    # ── 场景5：拼多多隐私字段防御 (buyerNick=null) ──
    {
        "name": "真实字段 — 拼多多 buyerNick=null 防御",
        "desc": "null字段防御",
        "code": """
data = await erp_query_all("erp_trade_query", "order_list", {"page_size": 100})
items = data.get("list", [])

buyers = []
for o in items:
    buyer = o.get("buyerNick") or "（隐私保护）"
    buyers.append(f"{o['source']}: {buyer}")

print("=== 买家昵称（含隐私保护）===")
for b in buyers:
    print(f"  {b}")
print(f"\\n隐私保护订单数: {sum(1 for o in items if not o.get('buyerNick'))}")
""",
    },

    # ── 场景6：timestamp 时间戳处理 ──
    {
        "name": "真实字段 — 毫秒时间戳转可读时间",
        "desc": "时间戳处理",
        "code": """
from datetime import datetime

data = await erp_query_all("erp_trade_query", "order_list", {"page_size": 100})
items = data.get("list", [])

def ts_to_str(ts):
    if not ts:
        return "未发货"
    return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")

print("=== 订单时间线 ===")
for o in items[:5]:
    tid = o.get("tid", "")[:12] + "..."
    created = ts_to_str(o.get("created"))
    paid = ts_to_str(o.get("payTime"))
    shipped = ts_to_str(o.get("consignTime"))
    print(f"  {tid} | 下单:{created} | 付款:{paid} | 发货:{shipped}")
""",
    },

    # ── 场景7：erp_query 单页 + stockStatusVoList response_key ──
    {
        "name": "真实字段 — erp_query 库存（stockStatusVoList 键名）",
        "desc": "库存查询",
        "code": """
# erp_query 返回原始 dict，代码需知道正确的 response_key
data = await erp_query("erp_product_query", "stock_status", {"page_size": 50})

# 真实 API 返回 stockStatusVoList，不是 list
items = data.get("stockStatusVoList", [])

print(f"=== 库存状态（{len(items)}条）===")
status_labels = {1: "正常", 2: "警戒", 3: "无货", 4: "超卖"}
for s in items:
    name = s.get("title", "")
    sku = s.get("mainOuterId", "")
    available = s.get("sellableNum", 0)
    locked = s.get("totalLockStock", 0)
    wh = s.get("wareHouseId", "")
    status = status_labels.get(s.get("stockStatus", 0), "未知")
    price = s.get("purchasePrice", "0")
    print(f"  {name} ({sku}) | 可售:{available} 锁定:{locked} | 仓库:{wh} | {status} | 采购价:¥{price}")
""",
    },

    # ── 场景8：erp_query 单页 + items response_key ──
    {
        "name": "真实字段 — erp_query 商品列表（items 键名）",
        "desc": "商品列表",
        "code": """
data = await erp_query("erp_product_query", "product_list", {"page_size": 20})

# 真实 API 返回 items，不是 list
products = data.get("items", [])

print(f"=== 商品列表（{len(products)}个）===")
for p in products:
    title = p.get("title", "")
    code = p.get("outerId", "")
    sell = p.get("sellingPrice", "0")
    buy = p.get("purchasePrice", "0")
    margin = float(sell) - float(buy)
    rate = margin / float(sell) * 100 if float(sell) > 0 else 0
    print(f"  {title} ({code}) | 售价:¥{sell} 采购:¥{buy} | 毛利率:{rate:.1f}%")
""",
    },

    # ── 场景9：pandas + 真实字段名 ──
    {
        "name": "pandas — 真实字段名交叉分析",
        "desc": "pandas交叉分析",
        "code": """
import pandas as pd

data = await erp_query_all("erp_trade_query", "order_list", {"page_size": 100})
orders = data.get("list", [])

# 展开子订单为扁平行
rows = []
for o in orders:
    for sub in o.get("orders", []):
        rows.append({
            "shopName": o.get("shopName"),
            "source": o.get("source"),
            "sysStatus": o.get("sysStatus"),
            "goodsTitle": sub.get("sysTitle"),
            "outerId": sub.get("outerId"),
            "qty": int(sub.get("num", 0)),
            "revenue": float(sub.get("totalFee", 0)),
        })

df = pd.DataFrame(rows)
pivot = df.pivot_table(
    values="revenue",
    index="source",
    columns="outerId",
    aggfunc="sum",
    fill_value=0,
)
print("=== 平台×SKU 销售额交叉表 ===")
print(pivot.to_string())
print(f"\\n总行数: {len(rows)}条子订单")
""",
    },

    # ── 场景10：售后数据处理 ──
    {
        "name": "真实字段 — 售后列表分析",
        "desc": "售后分析",
        "code": """
data = await erp_query("erp_aftersales_query", "aftersale_list")
items = data.get("list", [])

print(f"=== 售后单（{len(items)}条）===")
total_refund = 0
for a in items:
    as_id = a.get("id", "")
    type_name = a.get("typeName", "")
    status = a.get("status", "")
    fee = float(a.get("refundFee", 0))
    reason = a.get("reason", "")
    buyer = a.get("buyerNick") or "（隐私保护）"
    shop = a.get("shopName", "")
    goods = a.get("goodsName", "")
    total_refund += fee
    print(f"  {as_id} | {type_name} | {status} | ¥{fee:.2f}")
    print(f"    商品: {goods} | 买家: {buyer} | 原因: {reason}")

print(f"\\n退款总额: ¥{total_refund:,.2f}")
""",
    },

    # ── 场景11：写操作拦截验证 ──
    {
        "name": "真实场景 — 沙盒内写操作被拦截",
        "desc": "写操作拦截",
        "code": """
# 尝试调 execute 类的写操作
result = await erp_query("erp_trade_query", "order_cancel", {"system_ids": "5759422420146938"})
if "error" in result:
    print(f"✅ 写操作拦截成功: {result['error']}")
else:
    print(f"❌ 写操作未被拦截!")
""",
    },

    # ── 场景12：真实平台订单号格式识别 ──
    {
        "name": "真实场景 — 平台订单号格式识别",
        "desc": "订单号格式",
        "code": """
import re

data = await erp_query_all("erp_trade_query", "order_list", {"page_size": 100})
items = data.get("list", [])

patterns = {
    "tmall": r"^\\d{18}$",
    "jd": r"^\\d{16}$",
    "douyin": r"^\\d{19}$",
    "pdd": r"^\\d{6}-\\d+$",
    "xhs": r"^P\\d{18}$",
    "1688": r"^\\d{19}$",
}

print("=== 平台订单号格式验证 ===")
for o in items:
    tid = o.get("tid", "")
    source = o.get("source", "")
    sid = o.get("sid", "")
    # sid 统一16位数字
    sid_ok = bool(re.match(r"^\\d{16}$", sid))

    matched = "未知格式"
    for platform, pat in patterns.items():
        if re.match(pat, tid):
            matched = platform
            break

    status = "✓" if matched == source else "✗"
    print(f"  {status} tid={tid[:20]:<20} source={source:<8} 匹配={matched:<8} sid={sid} ({'✓' if sid_ok else '✗'})")
""",
    },

    # ── 场景13：Decimal精确财务 + 真实字段 ──
    {
        "name": "Decimal 精确计算 — 采购价成本核算",
        "desc": "精确成本核算",
        "code": """
from decimal import Decimal, ROUND_HALF_UP

# 商品采购价
data = await erp_query("erp_product_query", "product_list", {"page_size": 20})
products = data.get("items", [])
cost_map = {}
for p in products:
    cost_map[p["outerId"]] = Decimal(p.get("purchasePrice", "0"))

# 订单
orders_data = await erp_query_all("erp_trade_query", "order_list", {"page_size": 100})
orders = orders_data.get("list", [])

total_revenue = Decimal("0")
total_cost = Decimal("0")
for order in orders:
    total_revenue += Decimal(order.get("payment", "0"))
    for sub in order.get("orders", []):
        sku = sub.get("outerId", "")
        qty = int(sub.get("num", 0))
        unit_cost = cost_map.get(sku, Decimal("0"))
        total_cost += unit_cost * qty

gross_profit = total_revenue - total_cost
margin = (gross_profit / total_revenue * 100).quantize(Decimal("0.01"), ROUND_HALF_UP)

print(f"总收入:   ¥{total_revenue:,.2f}")
print(f"总成本:   ¥{total_cost:,.2f}")
print(f"毛利额:   ¥{gross_profit:,.2f}")
print(f"毛利率:   {margin}%")
print(f"精度验证: {total_revenue} (Decimal)")
""",
    },

    # ── 场景14：erp_query_all + stockStatusVoList（已修复 response_key 自动探测）──
    {
        "name": "已修复 — erp_query_all 自动探测 stockStatusVoList",
        "desc": "response_key自动探测",
        "code": """
# erp_query_all 现在自动探测 response_key（list/items/stockStatusVoList/...）
data = await erp_query_all("erp_product_query", "stock_status", {"page_size": 50})
items = data.get("list", [])
total = data.get("total", 0)

if items and total > 0:
    print(f"✅ 自动探测成功: 获取到 {len(items)} 条库存数据 (total={total})")
    for s in items[:3]:
        print(f"  {s.get('title')} | 可售:{s.get('sellableNum')} | 仓库:{s.get('wareHouseId')}")
else:
    print(f"❌ 自动探测失败: {len(items)} 条 (total={total})")
""",
    },

    # ── 场景15：安全拦截 — 真实场景代码 ──
    {
        "name": "安全拦截 — 尝试读取文件（真实攻击向量）",
        "desc": "文件读取拦截",
        "code": "open('/etc/passwd').read()",
        "expect_error": True,
    },
]


# ============================================================
# 执行器
# ============================================================

async def main():
    dispatcher = _build_mock_dispatcher()
    executor = build_sandbox_executor(
        dispatcher=dispatcher,
        timeout=30.0,
    )

    passed = 0
    failed = 0

    for i, scenario in enumerate(SCENARIOS, 1):
        name = scenario["name"]
        expect_error = scenario.get("expect_error", False)
        check_fn = scenario.get("check")

        print(f"\n{'='*60}")
        print(f"场景 {i}: {name}")
        print(f"{'='*60}")

        result = await executor.execute(
            scenario["code"].strip(),
            scenario["desc"],
        )

        is_error = result.startswith("❌")
        if expect_error:
            if is_error:
                print(f"✅ 预期拦截成功: {result.split(chr(10))[0]}")
                passed += 1
            else:
                print(f"❌ 应该被拦截但没有!")
                print(result)
                failed += 1
        elif check_fn:
            if check_fn(result):
                print(result)
                passed += 1
            else:
                print(f"❌ check 未通过!")
                print(result)
                failed += 1
        else:
            if is_error:
                print(f"❌ 执行失败!")
                print(result)
                failed += 1
            else:
                print(result)
                passed += 1

    print(f"\n{'='*60}")
    print(f"测试结果: {passed}/{passed + failed} 通过")
    if failed:
        print(f"⚠ {failed} 个场景失败")
    else:
        print("✅ 全部通过!")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
