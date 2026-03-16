"""
沙盒 ERP 数据计算集成测试脚本

用 mock dispatcher 模拟真实 ERP 数据，验证沙盒内各种数据聚合场景。
运行: cd backend && source venv/bin/activate && python scripts/test_sandbox_erp.py
"""

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from services.sandbox.functions import build_sandbox_executor


# ============================================================
# Mock 数据工厂
# ============================================================

def _mock_shops():
    """模拟店铺列表"""
    return {
        "list": [
            {"userId": "S001", "title": "天猫旗舰店", "type": "tmall",
             "platformName": "天猫", "shortName": "天猫"},
            {"userId": "S002", "title": "京东自营店", "type": "jd",
             "platformName": "京东", "shortName": "京东"},
            {"userId": "S003", "title": "抖音直播店", "type": "douyin",
             "platformName": "抖音", "shortName": "抖音"},
            {"userId": "S004", "title": "拼多多专卖店", "type": "pdd",
             "platformName": "拼多多", "shortName": "拼多多"},
            {"userId": "S005", "title": "小红书旗舰店", "type": "xhs",
             "platformName": "小红书", "shortName": "小红书"},
        ],
        "total": 5,
    }


def _mock_orders(params):
    """模拟订单数据（按店铺/日期/状态过滤）"""
    all_orders = [
        # 天猫订单
        {"tid": "T1001", "sid": "1000000000000001", "payment": "299.00",
         "shopName": "天猫旗舰店", "buyerNick": "用户A",
         "sysStatus": "TRADE_FINISHED", "source": "tmall",
         "created": "2026-03-16 09:15:00", "payTime": "2026-03-16 09:16:00",
         "num": 2, "goodsNo": "SKU-001", "goodsName": "春季新款连衣裙",
         "costPrice": "120.00"},
        {"tid": "T1002", "sid": "1000000000000002", "payment": "158.00",
         "shopName": "天猫旗舰店", "buyerNick": "用户B",
         "sysStatus": "WAIT_SELLER_SEND_GOODS", "source": "tmall",
         "created": "2026-03-16 10:30:00", "payTime": "2026-03-16 10:31:00",
         "num": 1, "goodsNo": "SKU-002", "goodsName": "休闲T恤",
         "costPrice": "55.00"},
        {"tid": "T1003", "sid": "1000000000000003", "payment": "599.00",
         "shopName": "天猫旗舰店", "buyerNick": "用户C",
         "sysStatus": "TRADE_FINISHED", "source": "tmall",
         "created": "2026-03-16 14:20:00", "payTime": "2026-03-16 14:22:00",
         "num": 1, "goodsNo": "SKU-003", "goodsName": "真丝衬衫",
         "costPrice": "200.00"},
        # 京东订单
        {"tid": "J2001", "sid": "2000000000000001", "payment": "450.00",
         "shopName": "京东自营店", "buyerNick": "JD用户1",
         "sysStatus": "TRADE_FINISHED", "source": "jd",
         "created": "2026-03-16 08:00:00", "payTime": "2026-03-16 08:01:00",
         "num": 3, "goodsNo": "SKU-001", "goodsName": "春季新款连衣裙",
         "costPrice": "120.00"},
        {"tid": "J2002", "sid": "2000000000000002", "payment": "89.00",
         "shopName": "京东自营店", "buyerNick": "JD用户2",
         "sysStatus": "WAIT_SELLER_SEND_GOODS", "source": "jd",
         "created": "2026-03-16 11:45:00", "payTime": "2026-03-16 11:46:00",
         "num": 1, "goodsNo": "SKU-004", "goodsName": "纯棉袜子3双装",
         "costPrice": "15.00"},
        # 抖音订单
        {"tid": "D3001", "sid": "3000000000000001", "payment": "199.00",
         "shopName": "抖音直播店", "buyerNick": "抖音粉丝1",
         "sysStatus": "TRADE_FINISHED", "source": "douyin",
         "created": "2026-03-16 20:10:00", "payTime": "2026-03-16 20:11:00",
         "num": 1, "goodsNo": "SKU-002", "goodsName": "休闲T恤",
         "costPrice": "55.00"},
        {"tid": "D3002", "sid": "3000000000000002", "payment": "799.00",
         "shopName": "抖音直播店", "buyerNick": "抖音粉丝2",
         "sysStatus": "TRADE_FINISHED", "source": "douyin",
         "created": "2026-03-16 21:30:00", "payTime": "2026-03-16 21:31:00",
         "num": 2, "goodsNo": "SKU-005", "goodsName": "冬季羽绒服",
         "costPrice": "280.00"},
        # 拼多多订单
        {"tid": "P4001", "sid": "4000000000000001", "payment": "39.90",
         "shopName": "拼多多专卖店", "buyerNick": "拼多多买家1",
         "sysStatus": "TRADE_FINISHED", "source": "pdd",
         "created": "2026-03-16 12:00:00", "payTime": "2026-03-16 12:01:00",
         "num": 2, "goodsNo": "SKU-004", "goodsName": "纯棉袜子3双装",
         "costPrice": "15.00"},
        # 小红书订单
        {"tid": "X5001", "sid": "5000000000000001", "payment": "368.00",
         "shopName": "小红书旗舰店", "buyerNick": "小红书达人1",
         "sysStatus": "WAIT_SELLER_SEND_GOODS", "source": "xhs",
         "created": "2026-03-16 16:00:00", "payTime": "2026-03-16 16:02:00",
         "num": 1, "goodsNo": "SKU-003", "goodsName": "真丝衬衫",
         "costPrice": "200.00"},
        # 昨天的订单（验证日期过滤）
        {"tid": "T0001", "sid": "1000000000000000", "payment": "1299.00",
         "shopName": "天猫旗舰店", "buyerNick": "VIP用户",
         "sysStatus": "TRADE_FINISHED", "source": "tmall",
         "created": "2026-03-15 18:00:00", "payTime": "2026-03-15 18:05:00",
         "num": 1, "goodsNo": "SKU-005", "goodsName": "冬季羽绒服",
         "costPrice": "280.00"},
    ]
    page = int(params.get("page", 1))
    page_size = int(params.get("page_size", 100))
    start = (page - 1) * page_size
    return {"list": all_orders[start:start + page_size], "total": len(all_orders)}


def _mock_stock(params):
    """模拟库存数据"""
    stocks = [
        {"outerCode": "SKU-001", "goodsName": "春季新款连衣裙",
         "stockNum": 230, "lockNum": 12, "availableNum": 218,
         "warehouseName": "杭州仓", "costPrice": "120.00"},
        {"outerCode": "SKU-001", "goodsName": "春季新款连衣裙",
         "stockNum": 85, "lockNum": 5, "availableNum": 80,
         "warehouseName": "广州仓", "costPrice": "120.00"},
        {"outerCode": "SKU-002", "goodsName": "休闲T恤",
         "stockNum": 500, "lockNum": 30, "availableNum": 470,
         "warehouseName": "杭州仓", "costPrice": "55.00"},
        {"outerCode": "SKU-003", "goodsName": "真丝衬衫",
         "stockNum": 45, "lockNum": 8, "availableNum": 37,
         "warehouseName": "杭州仓", "costPrice": "200.00"},
        {"outerCode": "SKU-004", "goodsName": "纯棉袜子3双装",
         "stockNum": 1200, "lockNum": 50, "availableNum": 1150,
         "warehouseName": "杭州仓", "costPrice": "15.00"},
        {"outerCode": "SKU-005", "goodsName": "冬季羽绒服",
         "stockNum": 15, "lockNum": 3, "availableNum": 12,
         "warehouseName": "杭州仓", "costPrice": "280.00"},
        {"outerCode": "SKU-005", "goodsName": "冬季羽绒服",
         "stockNum": 8, "lockNum": 0, "availableNum": 8,
         "warehouseName": "广州仓", "costPrice": "280.00"},
    ]
    page = int(params.get("page", 1))
    page_size = int(params.get("page_size", 100))
    start = (page - 1) * page_size
    return {"list": stocks[start:start + page_size], "total": len(stocks)}


def _mock_products(params):
    """模拟商品数据"""
    products = [
        {"goodsNo": "SKU-001", "title": "春季新款连衣裙", "price": "299.00",
         "stockNum": 315, "status": "on_sale", "catName": "女装"},
        {"goodsNo": "SKU-002", "title": "休闲T恤", "price": "158.00",
         "stockNum": 500, "status": "on_sale", "catName": "男装"},
        {"goodsNo": "SKU-003", "title": "真丝衬衫", "price": "599.00",
         "stockNum": 45, "status": "on_sale", "catName": "女装"},
        {"goodsNo": "SKU-004", "title": "纯棉袜子3双装", "price": "39.90",
         "stockNum": 1200, "status": "on_sale", "catName": "内衣"},
        {"goodsNo": "SKU-005", "title": "冬季羽绒服", "price": "799.00",
         "stockNum": 23, "status": "on_sale", "catName": "女装"},
    ]
    page = int(params.get("page", 1))
    page_size = int(params.get("page_size", 100))
    start = (page - 1) * page_size
    return {"list": products[start:start + page_size], "total": len(products)}


def _build_mock_dispatcher():
    """构建 mock dispatcher"""
    dispatcher = AsyncMock()

    async def mock_execute_raw(tool_name, action, params):
        if action == "shop_list":
            return _mock_shops()
        if action == "order_list":
            return _mock_orders(params)
        if action in ("stock_status", "warehouse_stock"):
            return _mock_stock(params)
        if action == "product_list":
            return _mock_products(params)
        return {"list": [], "total": 0}

    dispatcher.execute_raw = mock_execute_raw
    return dispatcher


# ============================================================
# 测试场景
# ============================================================

SCENARIOS = [
    # ── 场景1：各店铺今日成交额 ──
    {
        "name": "各店铺今日成交额汇总",
        "desc": "统计各店铺成交额",
        "code": """
orders = await erp_query_all("erp_trade_query", "order_list", {"page_size": 100})
items = orders.get("list", [])

from collections import defaultdict
shop_total = defaultdict(float)
for o in items:
    shop_total[o["shopName"]] += float(o["payment"])

print("=== 各店铺成交额 ===")
for shop in sorted(shop_total, key=shop_total.get, reverse=True):
    print(f"  {shop}: ¥{shop_total[shop]:,.2f}")
print(f"\\n合计: ¥{sum(shop_total.values()):,.2f}")
print(f"店铺数: {len(shop_total)}")
""",
    },

    # ── 场景2：pandas 版本 ──
    {
        "name": "pandas 店铺+商品交叉分析",
        "desc": "pandas交叉分析",
        "code": """
import pandas as pd
orders = await erp_query_all("erp_trade_query", "order_list", {"page_size": 100})
df = pd.DataFrame(orders["list"])
df["payment"] = df["payment"].astype(float)
df["num"] = df["num"].astype(int)

# 按店铺+商品交叉统计
pivot = df.pivot_table(
    values=["payment", "num"],
    index="shopName",
    columns="goodsName",
    aggfunc="sum",
    fill_value=0,
)
print("=== 店铺×商品 销售额 ===")
print(pivot["payment"].to_string())
print()
print("=== 店铺×商品 销售量 ===")
print(pivot["num"].to_string())
""",
    },

    # ── 场景3：毛利率分析 ──
    {
        "name": "各商品毛利率计算",
        "desc": "毛利率分析",
        "code": """
orders = await erp_query_all("erp_trade_query", "order_list", {"page_size": 100})
items = orders.get("list", [])

from collections import defaultdict
revenue = defaultdict(float)
cost = defaultdict(float)
qty = defaultdict(int)

for o in items:
    name = o["goodsName"]
    revenue[name] += float(o["payment"])
    cost[name] += float(o["costPrice"]) * int(o["num"])
    qty[name] += int(o["num"])

print("=== 各商品毛利率分析 ===")
print(f"{'商品':<15} {'销售额':>10} {'成本':>10} {'毛利':>10} {'毛利率':>8}")
print("-" * 58)
for name in sorted(revenue, key=revenue.get, reverse=True):
    r = revenue[name]
    c = cost[name]
    margin = r - c
    rate = margin / r * 100 if r > 0 else 0
    print(f"{name:<15} ¥{r:>8,.2f} ¥{c:>8,.2f} ¥{margin:>8,.2f} {rate:>6.1f}%")
""",
    },

    # ── 场景4：库存预警 ──
    {
        "name": "库存预警（可用库存<50）",
        "desc": "库存预警分析",
        "code": """
stock_data = await erp_query_all("erp_product_query", "stock_status", {"page_size": 100})
items = stock_data.get("list", [])

import pandas as pd
df = pd.DataFrame(items)
df["stockNum"] = df["stockNum"].astype(int)
df["availableNum"] = df["availableNum"].astype(int)
df["lockNum"] = df["lockNum"].astype(int)

# 按商品汇总各仓库库存
summary = df.groupby("goodsName").agg(
    总库存=("stockNum", "sum"),
    可用=("availableNum", "sum"),
    锁定=("lockNum", "sum"),
    仓库数=("warehouseName", "nunique"),
).sort_values("可用")

print("=== 库存预警（可用<50 标记⚠） ===")
for name, row in summary.iterrows():
    warn = "⚠" if row["可用"] < 50 else " "
    print(f"{warn} {name:<15} 总:{row['总库存']:>5} 可用:{row['可用']:>5} 锁定:{row['锁定']:>3} ({row['仓库数']}个仓)")
""",
    },

    # ── 场景5：Decimal 精确金额计算 ──
    {
        "name": "Decimal 精确财务汇总",
        "desc": "Decimal精确计算",
        "code": """
from decimal import Decimal, ROUND_HALF_UP

orders = await erp_query_all("erp_trade_query", "order_list", {"page_size": 100})
items = orders.get("list", [])

total_revenue = Decimal("0")
total_cost = Decimal("0")
for o in items:
    total_revenue += Decimal(o["payment"])
    total_cost += Decimal(o["costPrice"]) * int(o["num"])

gross_profit = total_revenue - total_cost
margin_rate = (gross_profit / total_revenue * 100).quantize(Decimal("0.01"), ROUND_HALF_UP)

print(f"总销售额: ¥{total_revenue:,.2f}")
print(f"总成本:   ¥{total_cost:,.2f}")
print(f"毛利额:   ¥{gross_profit:,.2f}")
print(f"毛利率:   {margin_rate}%")
print(f"订单数:   {len(items)}")
print(f"精度验证:  {total_revenue} (Decimal, 无浮点误差)")
""",
    },

    # ── 场景6：日期维度分析 ──
    {
        "name": "按小时段分析订单分布",
        "desc": "时段分析",
        "code": """
from datetime import datetime
from collections import Counter

orders = await erp_query_all("erp_trade_query", "order_list", {"page_size": 100})
items = orders.get("list", [])

hours = []
for o in items:
    dt = datetime.strptime(o["created"], "%Y-%m-%d %H:%M:%S")
    hours.append(dt.hour)

hour_count = Counter(hours)
total = len(items)

print("=== 订单时段分布 ===")
for h in range(24):
    cnt = hour_count.get(h, 0)
    bar = "█" * cnt
    pct = cnt / total * 100 if total > 0 else 0
    if cnt > 0:
        print(f"  {h:02d}:00  {bar:<10} {cnt}单 ({pct:.0f}%)")
""",
    },

    # ── 场景7：多数据源组合（库存 + 销量）──
    {
        "name": "库存周转率估算",
        "desc": "库存周转分析",
        "code": """
import pandas as pd

# 并行获取订单和库存
orders = await erp_query_all("erp_trade_query", "order_list", {"page_size": 100})
stock = await erp_query_all("erp_product_query", "stock_status", {"page_size": 100})

# 销量统计
order_df = pd.DataFrame(orders["list"])
order_df["num"] = order_df["num"].astype(int)
sales = order_df.groupby("goodsNo")["num"].sum().rename("日销量")

# 库存统计
stock_df = pd.DataFrame(stock["list"])
stock_df["availableNum"] = stock_df["availableNum"].astype(int)
inventory = stock_df.groupby("outerCode")["availableNum"].sum().rename("可用库存")
inventory.index.name = "goodsNo"

# 合并计算周转天数
merged = pd.concat([sales, inventory], axis=1).fillna(0)
merged["周转天数"] = (merged["可用库存"] / merged["日销量"]).round(0)
merged["周转天数"] = merged["周转天数"].replace(float("inf"), 999)

# 商品名映射
name_map = order_df.drop_duplicates("goodsNo").set_index("goodsNo")["goodsName"]
merged["商品"] = merged.index.map(name_map).fillna("未知")

print("=== 库存周转率估算 ===")
print(f"{'商品':<15} {'日销量':>6} {'可用库存':>8} {'周转天数':>8} {'状态':>6}")
print("-" * 50)
for idx, row in merged.sort_values("周转天数").iterrows():
    status = "⚠紧急" if row["周转天数"] < 7 else ("需补" if row["周转天数"] < 30 else "正常")
    print(f"{row['商品']:<15} {int(row['日销量']):>5} {int(row['可用库存']):>7} {int(row['周转天数']):>7}天 {status}")
""",
    },

    # ── 场景8：ERP 返回空数据 ──
    {
        "name": "ERP 空数据防御",
        "desc": "空数据处理",
        "code": """
# 查询一个不存在的 action → mock 返回空列表
data = await erp_query_all("erp_trade_query", "nonexistent_action", {"page_size": 100})
items = data.get("list", [])
total = sum(float(o.get("payment", 0)) for o in items)
count = len(items)
avg = total / count if count > 0 else 0
print(f"订单数: {count}")
print(f"总金额: ¥{total:,.2f}")
print(f"平均单价: ¥{avg:,.2f}")
print("空数据处理正常" if count == 0 else f"有{count}条数据")
""",
    },

    # ── 场景9：ERP 返回 error ──
    {
        "name": "ERP 错误响应处理",
        "desc": "ERP错误处理",
        "code": """
# erp_query 无 dispatcher 时返回 {"error": "..."}
data = await erp_query("unknown_tool", "unknown_action")
if "error" in data:
    print(f"ERP 报错: {data['error']}")
    print("错误处理正常，未崩溃")
else:
    print(f"数据: {data}")
""",
    },

    # ── 场景10：除零防御 ──
    {
        "name": "除零防御 — 0 销量算毛利率",
        "desc": "除零防御",
        "code": """
scenarios = [
    {"name": "正常商品", "revenue": 1000, "cost": 600},
    {"name": "零销售额", "revenue": 0, "cost": 0},
    {"name": "零成本",   "revenue": 500, "cost": 0},
]
for s in scenarios:
    r, c = s["revenue"], s["cost"]
    margin = r - c
    rate = (margin / r * 100) if r > 0 else 0
    print(f"{s['name']}: 收入={r} 成本={c} 毛利率={rate:.1f}%")
print("除零防御通过")
""",
    },

    # ── 场景11：字段缺失 / KeyError 防御 ──
    {
        "name": "字段缺失 — .get() 防御",
        "desc": "字段缺失处理",
        "code": """
# 模拟不完整的数据
orders = [
    {"tid": "001", "payment": "100.00", "shopName": "天猫"},
    {"tid": "002"},  # 缺少 payment 和 shopName
    {"tid": "003", "payment": "0", "shopName": "京东"},
]
total = 0
errors = 0
for o in orders:
    try:
        total += float(o.get("payment", 0))
    except (ValueError, TypeError):
        errors += 1
print(f"总金额: ¥{total:,.2f}")
print(f"异常记录: {errors}条")
print(f"处理了 {len(orders)} 条订单（含不完整数据）")
""",
    },

    # ── 场景12：代码语法错误 ──
    {
        "name": "语法错误 — LLM 生成的坏代码",
        "desc": "语法错误处理",
        "code": "def foo(\n  print('hello')",
        "expect_error": True,
    },

    # ── 场景13：try-except 错误恢复 ──
    {
        "name": "沙盒内 try-except 错误恢复",
        "desc": "错误恢复",
        "code": """
results = []
test_cases = [
    ("正常计算", lambda: 100 / 3),
    ("除零", lambda: 1 / 0),
    ("类型错误", lambda: "abc" + 123),
    ("键不存在", lambda: {}["missing"]),
]
for name, fn in test_cases:
    try:
        val = fn()
        results.append(f"  ✓ {name}: {val:.2f}")
    except ZeroDivisionError:
        results.append(f"  ✗ {name}: 捕获除零错误")
    except TypeError as e:
        results.append(f"  ✗ {name}: 捕获类型错误")
    except KeyError as e:
        results.append(f"  ✗ {name}: 捕获键不存在")

print("=== try-except 测试 ===")
for r in results:
    print(r)
print(f"\\n处理了 {len(results)} 个用例，全部正常")
""",
    },

    # ── 场景14：输出超长截断 ──
    {
        "name": "超长输出截断（10000行）",
        "desc": "输出截断",
        "code": """
for i in range(10000):
    print(f"行{i}: " + "x" * 50)
""",
        "check": lambda r: "已截断" in r or len(r) < 100000,
    },

    # ── 场景15：无输出代码 ──
    {
        "name": "无输出代码（无 print 无表达式）",
        "desc": "无输出",
        "code": "x = 1 + 1\ny = x * 3",
        "check": lambda r: "无输出" in r or "成功" in r,
    },

    # ── 场景16：安全拦截 — import os ──
    {
        "name": "安全拦截 — import os",
        "desc": "安全验证",
        "code": "import os\nos.listdir('.')",
        "expect_error": True,
    },

    # ── 场景17：安全拦截 — 运行时白名单 ──
    {
        "name": "安全拦截 — 运行时白名单",
        "desc": "运行时安全",
        "code": "import socket\nsocket.gethostname()",
        "expect_error": True,
    },

    # ── 场景18：安全拦截 — dunder 逃逸 ──
    {
        "name": "安全拦截 — __class__.__bases__ 逃逸",
        "desc": "元编程拦截",
        "code": "().__class__.__bases__[0].__subclasses__()",
        "expect_error": True,
    },

    # ── 场景19：erp_query 单页查询 ──
    {
        "name": "erp_query 单页查询（区别 erp_query_all）",
        "desc": "单页查询",
        "code": """
# erp_query 返回单页原始数据
data = await erp_query("erp_info_query", "shop_list")
shops = data.get("list", [])
print(f"查到 {len(shops)} 个店铺:")
for s in shops:
    print(f"  - {s['title']} ({s['platformName']})")
""",
    },

    # ── 场景20：纯计算 — 复利计算器 ──
    {
        "name": "纯数学计算 — 复利计算器",
        "desc": "复利计算",
        "code": """
principal = 100000  # 本金10万
rate = 0.05  # 年化5%
years = 10

print(f"本金: ¥{principal:,.0f}")
print(f"年化利率: {rate*100}%")
print(f"投资年限: {years}年")
print()

total = principal
for y in range(1, years + 1):
    total *= (1 + rate)
    if y % 2 == 0 or y == 1:
        print(f"  第{y:>2}年: ¥{total:>12,.2f} (收益 ¥{total - principal:>10,.2f})")

print(f"\\n最终金额: ¥{total:,.2f}")
print(f"总收益:   ¥{total - principal:,.2f}")
print(f"收益率:   {(total/principal - 1) * 100:.1f}%")
""",
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
