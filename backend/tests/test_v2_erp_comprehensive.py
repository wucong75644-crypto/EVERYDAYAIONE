"""
v2 ERP 综合模拟测试 — 覆盖9个ERP工具 × 多种提问方式

覆盖维度：
A. 编码识别 erp_identify（裸编码/条形码/订单号/SKU编码/套件）
B. 库存查询 erp_product_query（当前库存/分仓库存/出入库流水/虚拟库存）
C. 商品查询 erp_product_query（商品列表/详情/多编码/SKU信息/标签/品牌）
D. 订单查询 erp_trade_query（各平台订单号/状态筛选/时间类型/归档订单）
E. 出库物流 erp_trade_query（出库查询/物流/波次/多包裹）
F. 售后查询 erp_aftersales_query（工单/退货入库/补款/维修/system_id跨工具）
G. 基础信息 erp_info_query（仓库/店铺/标签/客户）
H. 仓储操作 erp_warehouse_query（调拨/盘点/入出库/加工单）
I. 采购查询 erp_purchase_query（采购单/收货/上架/历史归档/采购建议）
J. 淘宝奇门 erp_taobao_query（淘宝订单/天猫退款）
K. 两步调用 Step1(只传action) → Step2(传params)
L. 写操作 erp_execute（需确认）
M. 搜索工具 erp_api_search
N. 口语化提问（各种真实业务表达）
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from schemas.message import GenerationType, TextPart
from services.agent_loop import AgentLoop


# ============================================================
# Helpers
# ============================================================


def _lp(has_image=False):
    lp = AgentLoop(db=None, user_id="erp_u", conversation_id="erp_c")
    lp._settings = MagicMock()
    lp._settings.agent_loop_v2_enabled = True
    lp._settings.agent_loop_max_turns = 8
    lp._settings.agent_loop_max_tokens = 80000
    lp._has_image = has_image
    lp._thinking_mode = None
    lp._user_location = None
    lp._task_id = None
    lp._phase1_model = ""
    return lp


def _p1(name, args, tokens=150):
    return {
        "choices": [{"message": {"tool_calls": [{
            "id": "p1", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }]}}],
        "usage": {"total_tokens": tokens},
    }


def _p2t(calls, tokens=400):
    """Phase 2 工具调用响应"""
    tcs = [{"id": f"t{i}", "type": "function",
            "function": {"name": n, "arguments": json.dumps(a)}}
           for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": {"tool_calls": tcs}}],
            "usage": {"total_tokens": tokens}}


def _p2x(text="", tokens=100):
    """Phase 2 纯文本响应（循环终止）"""
    return {"choices": [{"message": {"content": text}}],
            "usage": {"total_tokens": tokens}}


def _ctx(lp, brain, history=None, knowledge=None, executor_ret="{}"):
    return [
        patch.object(lp, "_get_recent_history",
                     new_callable=AsyncMock, return_value=history),
        patch.object(lp, "_fetch_knowledge",
                     new_callable=AsyncMock, return_value=knowledge),
        patch.object(lp, "_call_brain", brain),
        patch.object(lp, "_notify_progress", new_callable=AsyncMock),
        patch.object(lp, "_fire_and_forget_knowledge"),
        patch.object(lp, "_record_ask_user_context"),
        patch.object(lp.executor, "execute",
                     new_callable=AsyncMock, return_value=executor_ret),
    ]


async def _run(lp, text, brain, **kw):
    c = _ctx(lp, brain, **kw)
    with c[0], c[1], c[2], c[3], c[4], c[5], c[6]:
        return await lp._execute_loop_v2([TextPart(text=text)])


async def _run_multi_exec(lp, text, brain, exec_results):
    """支持多次 executor 返回不同结果"""
    idx = 0

    async def mock_exec(name, args):
        nonlocal idx
        ret = exec_results[min(idx, len(exec_results) - 1)]
        idx += 1
        return ret

    c = _ctx(lp, brain)
    with c[0], c[1], c[2], c[3], c[4], c[5], \
            patch.object(lp.executor, "execute",
                         new_callable=AsyncMock, side_effect=mock_exec):
        return await lp._execute_loop_v2([TextPart(text=text)])


# ============================================================
# A. 编码识别 erp_identify（5 场景）
# ============================================================


class TestERPIdentify:

    @pytest.mark.asyncio
    async def test_barcode_identify(self):
        """条形码 → erp_identify → stock_status"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_identify", {"code": "6901234567890"})]),
            _p2t([("erp_product_query", {
                "action": "stock_status",
                "params": {"outer_id": "PROD-001"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "库存助手"})]),
        ])
        r = await _run(lp, "6901234567890 这个条码还有多少库存",
                       brain, executor_ret='{"type":"barcode","outer_id":"PROD-001"}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_sku_code_identify(self):
        """SKU编码（带-后缀）→ erp_identify → sku_info"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_identify", {"code": "ABC-001-XL"})]),
            _p2t([("erp_product_query", {
                "action": "sku_info",
                "params": {"sku_outer_id": "ABC-001-XL"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "商品助手"})]),
        ])
        r = await _run(lp, "ABC-001-XL 这个SKU是什么商品",
                       brain, executor_ret='{"type":"sku","sku_outer_id":"ABC-001-XL"}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_kit_identify_then_child_stock(self):
        """套件编码 → erp_identify 返回子单品 → 逐个查库存 → 汇总"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_identify", {"code": "KIT-GIFT-01"})]),
            # 套件返回子单品列表后，逐个查库存
            _p2t([
                ("erp_product_query", {
                    "action": "stock_status",
                    "params": {"outer_id": "CHILD-A"},
                }),
                ("erp_product_query", {
                    "action": "stock_status",
                    "params": {"outer_id": "CHILD-B"},
                }),
            ]),
            _p2t([("route_to_chat", {"system_prompt": "套件库存助手"})]),
        ])
        r = await _run_multi_exec(
            lp, "KIT-GIFT-01 这个套装还有货吗", brain,
            exec_results=[
                '{"type":"kit","children":["CHILD-A","CHILD-B"]}',
                '{"available":50}',
                '{"available":30}',
            ],
        )
        assert r.generation_type == GenerationType.CHAT
        assert r.turns_used >= 3

    @pytest.mark.asyncio
    async def test_platform_order_identify(self):
        """平台订单号 → erp_identify → order_list"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_identify", {"code": "126036803257340376"})]),
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {"order_id": "126036803257340376"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "订单助手"})]),
        ])
        r = await _run(lp, "126036803257340376",
                       brain, executor_ret='{"type":"taobao_order","order_id":"126036803257340376"}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_system_id_identify(self):
        """系统单号（16位）→ erp_identify → order_list(system_id)"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_identify", {"code": "1234567890123456"})]),
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {"system_id": "1234567890123456"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "订单助手"})]),
        ])
        r = await _run(lp, "系统单号1234567890123456查一下",
                       brain, executor_ret='{"type":"system_id","system_id":"1234567890123456"}')
        assert r.generation_type == GenerationType.CHAT


# ============================================================
# B. 库存查询 erp_product_query（6 场景）
# ============================================================


class TestERPStock:

    @pytest.mark.asyncio
    async def test_stock_status_by_outer_id(self):
        """商品编码查当前库存快照"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_product_query", {
                "action": "stock_status",
                "params": {"outer_id": "SHOE-001"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "库存助手"})]),
        ])
        r = await _run(lp, "SHOE-001 还有多少库存",
                       brain, executor_ret='{"total":200,"available":180}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_stock_status_by_sku(self):
        """SKU编码查库存（sku_outer_id）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_product_query", {
                "action": "stock_status",
                "params": {"sku_outer_id": "SHOE-001-42"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "库存助手"})]),
        ])
        r = await _run(lp, "SHOE-001-42 这个尺码还有库存吗",
                       brain, executor_ret='{"total":50,"available":45}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_warehouse_stock_distribution(self):
        """各仓库库存分布"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_product_query", {
                "action": "warehouse_stock",
                "params": {"outer_id": "COAT-100"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "仓库库存助手"})]),
        ])
        r = await _run(lp, "COAT-100 各仓库分别有多少",
                       brain, executor_ret='{"warehouses":[{"name":"北京仓","qty":100}]}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_stock_in_out_flow(self):
        """出入库流水（带时间范围 + order_type 筛选）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_product_query", {
                "action": "stock_in_out",
                "params": {
                    "outer_id": "DRESS-055",
                    "order_type": 2,
                    "start_date": "2026-03-01",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "销量分析师"})]),
        ])
        r = await _run(lp, "DRESS-055 这个月卖了多少件",
                       brain, executor_ret='{"records":[{"num":5},{"num":3}]}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_virtual_warehouse_query(self):
        """虚拟仓库/预售库存查询"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_product_query", {
                "action": "virtual_warehouse",
                "params": {"name": "618活动"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "预售助手"})]),
        ])
        r = await _run(lp, "618活动的虚拟库存情况",
                       brain, executor_ret='{"items":[{"name":"618活动","qty":500}]}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_history_cost_price(self):
        """历史成本价查询（必须传 item_id + sku_id）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_product_query", {
                "action": "history_cost_price",
                "params": {
                    "item_id": 12345,
                    "sku_id": 67890,
                    "start_date": "2026-01-01",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "成本分析师"})]),
        ])
        r = await _run(lp, "查一下商品12345的成本价变化",
                       brain, executor_ret='{"prices":[{"date":"2026-02","cost":35.5}]}')
        assert r.generation_type == GenerationType.CHAT


# ============================================================
# C. 商品查询 erp_product_query（4 场景）
# ============================================================


class TestERPProduct:

    @pytest.mark.asyncio
    async def test_product_list_with_filters(self):
        """商品列表 + 状态筛选 + 时间范围"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_product_query", {
                "action": "product_list",
                "params": {
                    "status": "on_sale",
                    "start_date": "2026-03-01",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "商品助手"})]),
        ])
        r = await _run(lp, "这个月新上架了哪些商品",
                       brain, executor_ret='{"total":15,"items":[]}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_product_detail_single(self):
        """单个商品详情（outer_id）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_product_query", {
                "action": "product_detail",
                "params": {"outer_id": "JACKET-200"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "商品助手"})]),
        ])
        r = await _run(lp, "JACKET-200 的详细信息",
                       brain, executor_ret='{"name":"冬季夹克","price":299}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_multi_product_batch(self):
        """批量商品查询（≤20 个编码）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_product_query", {
                "action": "multi_product",
                "params": {"outer_ids": "A001,A002,A003"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "商品助手"})]),
        ])
        r = await _run(lp, "A001、A002、A003 这三个商品的信息",
                       brain, executor_ret='{"items":[{},{},{}]}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_brand_and_category_list(self):
        """品牌列表 + 分类列表（无参数调用）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([
                ("erp_product_query", {"action": "brand_list"}),
                ("erp_product_query", {"action": "cat_list"}),
            ]),
            _p2t([("route_to_chat", {"system_prompt": "商品助手"})]),
        ])
        r = await _run_multi_exec(
            lp, "我们有哪些品牌和分类", brain,
            exec_results=['{"brands":["A","B"]}', '{"cats":["服装","鞋靴"]}'],
        )
        assert r.generation_type == GenerationType.CHAT


# ============================================================
# D. 订单查询 erp_trade_query（6 场景）
# ============================================================


class TestERPOrder:

    @pytest.mark.asyncio
    async def test_taobao_order_by_tid(self):
        """淘宝18位订单号"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {"order_id": "126036803257340376"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "订单助手"})]),
        ])
        r = await _run(lp, "淘宝单126036803257340376什么状态",
                       brain, executor_ret='{"status":"SELLER_SEND_GOODS"}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_pdd_order(self):
        """拼多多订单号格式（日期-数字串）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {"order_id": "260305-1234567890"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "订单助手"})]),
        ])
        r = await _run(lp, "拼多多260305-1234567890发货了没",
                       brain, executor_ret='{"status":"WAIT_SEND_GOODS"}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_xhs_order(self):
        """小红书订单号（P+18位）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {"order_id": "P123456789012345678"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "订单助手"})]),
        ])
        r = await _run(lp, "小红书P123456789012345678这单什么情况",
                       brain, executor_ret='{"status":"FINISHED"}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_order_list_with_status_and_time(self):
        """带状态+时间类型+时间范围的组合查询"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {
                    "status": "WAIT_SEND_GOODS",
                    "time_type": "created",
                    "start_date": "2026-03-19",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "订单助手"})]),
        ])
        r = await _run(lp, "今天有多少待发货订单",
                       brain, executor_ret='{"total":42}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_order_by_buyer_nick(self):
        """按买家昵称查询"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {"buyer": "小明同学"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "订单助手"})]),
        ])
        r = await _run(lp, "小明同学的订单",
                       brain, executor_ret='{"total":3,"orders":[]}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_archived_order(self):
        """归档订单查询（query_type=1）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {"order_id": "100099887766554433"},
            })]),
            # 第一次查不到，LLM 加 query_type=1 查归档
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {
                    "order_id": "100099887766554433",
                    "query_type": 1,
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "订单助手"})]),
        ])
        r = await _run_multi_exec(
            lp, "100099887766554433 这单查不到啊", brain,
            exec_results=['{"total":0}', '{"total":1,"orders":[{"status":"FINISHED"}]}'],
        )
        assert r.generation_type == GenerationType.CHAT
        assert r.turns_used >= 3


# ============================================================
# E. 出库物流 erp_trade_query（4 场景）
# ============================================================


class TestERPShipment:

    @pytest.mark.asyncio
    async def test_outstock_by_order(self):
        """按订单号查出库详情"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_trade_query", {
                "action": "outstock_query",
                "params": {"order_id": "126036803257340376"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "物流助手"})]),
        ])
        r = await _run(lp, "126036803257340376 发货了吗",
                       brain, executor_ret='{"status":"SHIPPED","express_no":"SF1234"}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_express_multi_packs(self):
        """多包裹查询"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_trade_query", {
                "action": "express_query",
                "params": {"system_id": "1234567890123456"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "物流助手"})]),
        ])
        r = await _run(lp, "1234567890123456这单分了几个包裹",
                       brain, executor_ret='{"packs":[{"express":"SF1"},{"express":"SF2"}]}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_outstock_order_warehouse_status(self):
        """仓库作业状态查询（outstock_order_query）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_trade_query", {
                "action": "outstock_order_query",
                "params": {
                    "status_list": "10,20",
                    "time_type": 1,
                    "start_date": "2026-03-19",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "仓库助手"})]),
        ])
        r = await _run(lp, "今天有多少待拣货的",
                       brain, executor_ret='{"total":15}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_order_then_waybill(self):
        """订单 → 获取电子面单号"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {"order_id": "126036803257340376"},
            })]),
            _p2t([("erp_trade_query", {
                "action": "waybill_get",
                "params": {"system_ids": "1234567890123456"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "面单助手"})]),
        ])
        r = await _run_multi_exec(
            lp, "126036803257340376 的面单号是多少", brain,
            exec_results=[
                '{"system_id":"1234567890123456"}',
                '{"waybill":"YT1234567890"}',
            ],
        )
        assert r.generation_type == GenerationType.CHAT


# ============================================================
# F. 售后查询 erp_aftersales_query（5 场景）
# ============================================================


class TestERPAftersales:

    @pytest.mark.asyncio
    async def test_aftersale_by_order_id(self):
        """按平台订单号查售后"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_aftersales_query", {
                "action": "aftersale_list",
                "params": {"order_id": "126036803257340376"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "售后助手"})]),
        ])
        r = await _run(lp, "126036803257340376 有没有售后",
                       brain, executor_ret='{"total":1,"items":[{"type":2}]}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_aftersale_by_type_and_date(self):
        """按售后类型+时间范围查"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_aftersales_query", {
                "action": "aftersale_list",
                "params": {
                    "type": 2,
                    "start_date": "2026-03-17",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "退货分析师"})]),
        ])
        r = await _run(lp, "最近三天有多少退货",
                       brain, executor_ret='{"total":12}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_system_id_to_aftersale_cross_tool(self):
        """system_id → order_list拿order_id → aftersale_list
        （aftersale_list 不支持 system_id，必须跨工具）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            # 先用 system_id 查订单拿到 order_id
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {"system_id": "1234567890123456"},
            })]),
            # 再用 order_id 查售后
            _p2t([("erp_aftersales_query", {
                "action": "aftersale_list",
                "params": {"order_id": "126036803257340376"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "售后助手"})]),
        ])
        r = await _run_multi_exec(
            lp, "系统单号1234567890123456的售后情况", brain,
            exec_results=[
                '{"order_id":"126036803257340376"}',
                '{"total":1,"items":[{"type":1}]}',
            ],
        )
        assert r.generation_type == GenerationType.CHAT
        assert r.turns_used >= 3

    @pytest.mark.asyncio
    async def test_refund_warehouse_query(self):
        """退货入库查询（必须传 time_type）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_aftersales_query", {
                "action": "refund_warehouse",
                "params": {
                    "time_type": "created",
                    "start_date": "2026-03-18",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "退货入库助手"})]),
        ])
        r = await _run(lp, "昨天到今天退回来多少件货",
                       brain, executor_ret='{"total":8}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_repair_list_and_detail(self):
        """维修单查询 → 查详情"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_aftersales_query", {
                "action": "repair_list",
                "params": {"status": 1, "query_type": 1, "query_text": "WX2026031900001"},
            })]),
            _p2t([("erp_aftersales_query", {
                "action": "repair_detail",
                "params": {"repair_no": "WX2026031900001"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "维修助手"})]),
        ])
        r = await _run_multi_exec(
            lp, "维修单WX2026031900001的情况", brain,
            exec_results=[
                '{"items":[{"repair_no":"WX2026031900001"}]}',
                '{"detail":{"status":"processing","items":["SKU-A"]}}',
            ],
        )
        assert r.generation_type == GenerationType.CHAT


# ============================================================
# G. 基础信息 erp_info_query（4 场景）
# ============================================================


class TestERPBasicInfo:

    @pytest.mark.asyncio
    async def test_warehouse_list(self):
        """查仓库列表"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_info_query", {"action": "warehouse_list"})]),
            _p2t([("route_to_chat", {"system_prompt": "仓库助手"})]),
        ])
        r = await _run(lp, "我们有几个仓库",
                       brain, executor_ret='{"warehouses":[{"name":"北京仓"},{"name":"上海仓"}]}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_shop_list(self):
        """查店铺列表"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_info_query", {"action": "shop_list"})]),
            _p2t([("route_to_chat", {"system_prompt": "店铺助手"})]),
        ])
        r = await _run(lp, "所有店铺列出来",
                       brain, executor_ret='{"shops":[{"name":"天猫旗舰"},{"name":"京东自营"}]}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_order_tag_list(self):
        """订单标签列表（tag_type=1）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_info_query", {
                "action": "tag_list",
                "params": {"tag_type": 1},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "标签助手"})]),
        ])
        r = await _run(lp, "有哪些订单标签",
                       brain, executor_ret='{"tags":["VIP","加急","赠品"]}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_customer_list_with_filter(self):
        """按客户等级查询"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_info_query", {
                "action": "customer_list",
                "params": {"level": "VIP"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "客户助手"})]),
        ])
        r = await _run(lp, "VIP客户有哪些",
                       brain, executor_ret='{"total":5,"customers":[]}')
        assert r.generation_type == GenerationType.CHAT


# ============================================================
# H. 仓储操作 erp_warehouse_query（4 场景）
# ============================================================


class TestERPWarehouse:

    @pytest.mark.asyncio
    async def test_allocate_transfer_list(self):
        """调拨单列表（按状态筛选）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_warehouse_query", {
                "action": "allocate_list",
                "params": {
                    "status": "OUTING",
                    "start_date": "2026-03-01",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "调拨助手"})]),
        ])
        r = await _run(lp, "这个月有多少在途调拨",
                       brain, executor_ret='{"total":3}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_inventory_sheet(self):
        """盘点单查询 + 详情"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_warehouse_query", {
                "action": "inventory_sheet_list",
                "params": {"status": 2},
            })]),
            _p2t([("erp_warehouse_query", {
                "action": "inventory_sheet_detail",
                "params": {"code": "PD20260319001"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "盘点助手"})]),
        ])
        r = await _run_multi_exec(
            lp, "正在盘点的单子有哪些", brain,
            exec_results=[
                '{"items":[{"code":"PD20260319001"}]}',
                '{"detail":{"total_items":50,"counted":30}}',
            ],
        )
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_process_order_assembly(self):
        """加工单查询（组装类型）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_warehouse_query", {
                "action": "process_order_list",
                "params": {
                    "type": 1,
                    "status": "PRODUCING",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "加工助手"})]),
        ])
        r = await _run(lp, "正在生产的组装单",
                       brain, executor_ret='{"total":2}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_other_in_out_list(self):
        """其他入库/出库记录"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([
                ("erp_warehouse_query", {
                    "action": "other_in_list",
                    "params": {"start_date": "2026-03-01", "end_date": "2026-03-19"},
                }),
                ("erp_warehouse_query", {
                    "action": "other_out_list",
                    "params": {"start_date": "2026-03-01", "end_date": "2026-03-19"},
                }),
            ]),
            _p2t([("route_to_chat", {"system_prompt": "出入库助手"})]),
        ])
        r = await _run_multi_exec(
            lp, "这个月的手工出入库记录", brain,
            exec_results=['{"total":5}', '{"total":3}'],
        )
        assert r.generation_type == GenerationType.CHAT


# ============================================================
# I. 采购查询 erp_purchase_query（5 场景）
# ============================================================


class TestERPPurchase:

    @pytest.mark.asyncio
    async def test_purchase_order_list(self):
        """采购单列表（带状态+时间）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_purchase_query", {
                "action": "purchase_order_list",
                "params": {
                    "status": "GOODS_NOT_ARRIVED",
                    "time_type": 2,
                    "start_date": "2026-03-01",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "采购助手"})]),
        ])
        r = await _run(lp, "这个月有多少待到货的采购单",
                       brain, executor_ret='{"total":8}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_purchase_detail_then_entry(self):
        """采购单详情 → 收货单"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_purchase_query", {
                "action": "purchase_order_detail",
                "params": {"purchase_id": 10086},
            })]),
            _p2t([("erp_purchase_query", {
                "action": "warehouse_entry_list",
                "params": {
                    "status": "NOT_FINISH",
                    "start_date": "2026-03-01",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "采购助手"})]),
        ])
        r = await _run_multi_exec(
            lp, "采购单10086到货了吗", brain,
            exec_results=[
                '{"status":"GOODS_NOT_ARRIVED","supplier":"供应商A"}',
                '{"total":0}',
            ],
        )
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_purchase_history_archived(self):
        """历史归档采购单查询（≥3个月前）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_purchase_query", {
                "action": "purchase_order_history",
                "params": {
                    "start_date": "2025-12-01",
                    "end_date": "2025-12-31",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "采购助手"})]),
        ])
        r = await _run(lp, "去年12月的采购单",
                       brain, executor_ret='{"total":15}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_purchase_strategy_recommendation(self):
        """采购建议（按商品搜索）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_purchase_query", {
                "action": "purchase_strategy",
                "params": {"query_key": "运动鞋"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "采购建议助手"})]),
        ])
        r = await _run(lp, "运动鞋需要补货吗",
                       brain, executor_ret='{"suggestions":[{"sku":"SHOE-001","recommended_qty":100}]}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_supplier_list(self):
        """供应商列表"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_purchase_query", {
                "action": "supplier_list",
                "params": {"status": 1},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "供应商助手"})]),
        ])
        r = await _run(lp, "现在合作的供应商有哪些",
                       brain, executor_ret='{"total":10,"suppliers":[]}')
        assert r.generation_type == GenerationType.CHAT


# ============================================================
# J. 淘宝奇门 erp_taobao_query（3 场景）
# ============================================================


class TestERPTaobao:

    @pytest.mark.asyncio
    async def test_taobao_order_by_date(self):
        """淘宝订单按日期查"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_taobao_query", {
                "action": "order_list",
                "params": {
                    "date_type": 1,
                    "start_date": "2026-03-19",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "淘宝订单助手"})]),
        ])
        r = await _run(lp, "今天淘宝店多少单",
                       brain, executor_ret='{"total":25}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_taobao_refund_list(self):
        """淘宝退款列表（按类型筛选）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_taobao_query", {
                "action": "refund_list",
                "params": {
                    "refund_type": 2,
                    "start_date": "2026-03-18",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "退款助手"})]),
        ])
        r = await _run(lp, "昨天到今天淘宝退货退款有多少",
                       brain, executor_ret='{"total":5}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_taobao_order_by_tid(self):
        """按淘宝订单号精确查"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_taobao_query", {
                "action": "order_list",
                "params": {"tid": "126036803257340376"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "淘宝助手"})]),
        ])
        r = await _run(lp, "淘宝奇门查一下126036803257340376",
                       brain, executor_ret='{"trades":[{"status":"paid"}]}')
        assert r.generation_type == GenerationType.CHAT


# ============================================================
# K. 两步调用模式（3 场景）
# ============================================================


class TestERPTwoStep:

    @pytest.mark.asyncio
    async def test_step1_get_docs_then_step2_query(self):
        """经典两步：Step1只传action → Step2传params"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            # Step 1: 只传 action 获取参数文档
            _p2t([("erp_trade_query", {"action": "order_list"})]),
            # Step 2: 根据文档传入参数
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {
                    "status": "WAIT_SEND_GOODS",
                    "time_type": "created",
                    "start_date": "2026-03-19",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "订单助手"})]),
        ])
        r = await _run_multi_exec(
            lp, "帮我查今天待发货的", brain,
            exec_results=[
                '{"_doc":"order_list参数文档: order_id, status, time_type..."}',
                '{"total":42,"orders":[]}',
            ],
        )
        assert r.generation_type == GenerationType.CHAT
        assert r.turns_used >= 3

    @pytest.mark.asyncio
    async def test_step1_skip_direct_params(self):
        """简单查询可直接跳过Step1带params"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_product_query", {
                "action": "stock_status",
                "params": {"outer_id": "ABC-001"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "库存助手"})]),
        ])
        r = await _run(lp, "ABC-001库存多少",
                       brain, executor_ret='{"available":100}')
        assert r.generation_type == GenerationType.CHAT
        assert r.turns_used == 2  # Phase1 + Phase2(直接查) + exit

    @pytest.mark.asyncio
    async def test_api_search_then_query(self):
        """不确定action → 先 erp_api_search → 再查"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_api_search", {"query": "退款"})]),
            _p2t([("erp_aftersales_query", {
                "action": "aftersale_list",
                "params": {"type": 1, "start_date": "2026-03-19"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "售后助手"})]),
        ])
        r = await _run_multi_exec(
            lp, "怎么查仅退款的", brain,
            exec_results=[
                '{"results":[{"tool":"erp_aftersales_query","action":"aftersale_list"}]}',
                '{"total":3}',
            ],
        )
        assert r.generation_type == GenerationType.CHAT
        assert r.turns_used >= 3


# ============================================================
# L. 写操作 erp_execute（2 场景）
# ============================================================


class TestERPExecute:

    @pytest.mark.asyncio
    async def test_order_memo_update(self):
        """修改订单备注"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_execute", {
                "category": "trade",
                "action": "seller_memo_update",
                "params": {
                    "system_id": "1234567890123456",
                    "memo": "客户要求周五发货",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "订单助手"})]),
        ])
        r = await _run(lp, "1234567890123456备注改成客户要求周五发货",
                       brain, executor_ret='{"success":true}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_stock_update(self):
        """手工调整库存"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_execute", {
                "category": "product",
                "action": "stock_update",
                "params": {
                    "outer_id": "SHOE-001-42",
                    "warehouse_id": 1,
                    "quantity": 50,
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "库存助手"})]),
        ])
        r = await _run(lp, "SHOE-001-42在北京仓库存调到50",
                       brain, executor_ret='{"success":true}')
        assert r.generation_type == GenerationType.CHAT


# ============================================================
# M. 口语化/多种提问方式（8 场景）
# ============================================================


class TestERPColloquial:

    @pytest.mark.asyncio
    async def test_today_orders_casual(self):
        """「今天多少单」→ 只取 total"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {
                    "time_type": "created",
                    "start_date": "2026-03-19",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "订单统计助手"})]),
        ])
        r = await _run(lp, "今天多少单", brain, executor_ret='{"total":85}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_shipped_today_casual(self):
        """「今天发了多少」→ time_type=consign_time"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {
                    "status": "SELLER_SEND_GOODS",
                    "time_type": "consign_time",
                    "start_date": "2026-03-19",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "发货统计助手"})]),
        ])
        r = await _run(lp, "今天发了多少", brain, executor_ret='{"total":60}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_paid_amount_casual(self):
        """「今日成交额」→ time_type=pay_time"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {
                    "time_type": "pay_time",
                    "start_date": "2026-03-19",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("route_to_chat", {"system_prompt": "成交分析师"})]),
        ])
        r = await _run(lp, "今日成交多少", brain, executor_ret='{"total":120}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_low_stock_alert(self):
        """「哪些快没货了」→ 库存预警"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_product_query", {
                "action": "stock_status",
                "params": {"stock_statuses": "warning"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "库存预警助手"})]),
        ])
        r = await _run(lp, "哪些商品快没货了",
                       brain, executor_ret='{"items":[{"outer_id":"A","available":2}]}')
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_per_platform_stats(self):
        """「每个平台各多少单」→ 先查店铺 → 逐个统计"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_info_query", {"action": "shop_list"})]),
            _p2t([
                ("erp_trade_query", {
                    "action": "order_list",
                    "params": {
                        "shop_ids": "1",
                        "time_type": "created",
                        "start_date": "2026-03-19",
                        "end_date": "2026-03-19",
                    },
                }),
                ("erp_trade_query", {
                    "action": "order_list",
                    "params": {
                        "shop_ids": "2",
                        "time_type": "created",
                        "start_date": "2026-03-19",
                        "end_date": "2026-03-19",
                    },
                }),
            ]),
            _p2t([("route_to_chat", {"system_prompt": "多店统计助手"})]),
        ])
        r = await _run_multi_exec(
            lp, "今天每个店铺各多少单", brain,
            exec_results=[
                '{"shops":[{"id":"1","name":"天猫"},{"id":"2","name":"京东"}]}',
                '{"total":50}',
                '{"total":30}',
            ],
        )
        assert r.generation_type == GenerationType.CHAT
        assert r.turns_used >= 3

    @pytest.mark.asyncio
    async def test_product_sales_ranking(self):
        """「这周卖的最好的是啥」→ 销量排行"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_product_query", {
                "action": "stock_in_out",
                "params": {
                    "order_type": 2,
                    "start_date": "2026-03-13",
                    "end_date": "2026-03-19",
                },
            })]),
            _p2t([("code_execute", {
                "code": "# 按商品聚合销量排序",
            })]),
            _p2t([("route_to_chat", {"system_prompt": "销量分析师"})]),
        ])
        r = await _run_multi_exec(
            lp, "这周卖的最好的商品是啥", brain,
            exec_results=[
                '{"records":[{"outer_id":"A","num":50},{"outer_id":"B","num":30}]}',
                '{"ranking":[{"outer_id":"A","total":50}]}',
            ],
        )
        assert r.generation_type == GenerationType.CHAT

    @pytest.mark.asyncio
    async def test_name_not_found_then_ask(self):
        """按名称查不到 → ask_user 确认"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_info_query", {
                "action": "warehouse_list",
                "params": {"name": "深圳仓"},
            })]),
            # 查不到，追问用户
            _p2t([("ask_user", {
                "message": "未找到名为'深圳仓'的仓库，请确认仓库名称",
                "reason": "need_info",
            })]),
        ])
        r = await _run_multi_exec(
            lp, "深圳仓的库存", brain,
            exec_results=['{"total":0,"warehouses":[]}'],
        )
        assert r.generation_type == GenerationType.CHAT
        assert r.direct_reply is not None

    @pytest.mark.asyncio
    async def test_raw_number_ambiguous(self):
        """纯数字 → erp_identify 先判断是什么"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_identify", {"code": "9876543210"})]),
            _p2t([("route_to_chat", {"system_prompt": "ERP助手"})]),
        ])
        r = await _run(lp, "9876543210",
                       brain, executor_ret='{"type":"unknown","message":"无法识别"}')
        assert r.generation_type == GenerationType.CHAT


# ============================================================
# N. 跨工具复杂联查（4 场景）
# ============================================================


class TestERPCrossToolChain:

    @pytest.mark.asyncio
    async def test_order_to_logistics_to_aftersale(self):
        """订单 → 物流 → 售后（三步链路）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_trade_query", {
                "action": "order_list",
                "params": {"order_id": "126036803257340376"},
            })]),
            _p2t([("erp_trade_query", {
                "action": "outstock_query",
                "params": {"system_id": "1234567890123456"},
            })]),
            _p2t([("erp_aftersales_query", {
                "action": "aftersale_list",
                "params": {"order_id": "126036803257340376"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "综合客服助手"})]),
        ])
        r = await _run_multi_exec(
            lp, "126036803257340376 这单的订单详情、发货情况和售后情况都帮我查一下", brain,
            exec_results=[
                '{"system_id":"1234567890123456","status":"SELLER_SEND_GOODS"}',
                '{"express_no":"SF123","status":"shipped"}',
                '{"total":0}',
            ],
        )
        assert r.generation_type == GenerationType.CHAT
        assert r.turns_used >= 4

    @pytest.mark.asyncio
    async def test_purchase_to_entry_to_shelf(self):
        """采购 → 收货 → 上架（采购全链路）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_purchase_query", {
                "action": "purchase_order_detail",
                "params": {"purchase_id": 20001},
            })]),
            _p2t([("erp_purchase_query", {
                "action": "warehouse_entry_list",
                "params": {"code": "CG20260319001"},
            })]),
            _p2t([("erp_purchase_query", {
                "action": "shelf_list",
                "params": {"we_code": "RK20260319001"},
            })]),
            _p2t([("route_to_chat", {"system_prompt": "采购跟踪助手"})]),
        ])
        r = await _run_multi_exec(
            lp, "采购单20001到货上架了没", brain,
            exec_results=[
                '{"code":"CG20260319001","status":"FINISHED"}',
                '{"entry_id":500,"code":"RK20260319001","status":"FINISHED"}',
                '{"total":1,"items":[{"status":"done"}]}',
            ],
        )
        assert r.generation_type == GenerationType.CHAT
        assert r.turns_used >= 4

    @pytest.mark.asyncio
    async def test_identify_product_then_sales_and_stock(self):
        """编码识别 → 商品详情 + 库存 + 销量（并行查）"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([("erp_identify", {"code": "HOODIE-XL"})]),
            _p2t([
                ("erp_product_query", {
                    "action": "product_detail",
                    "params": {"outer_id": "HOODIE-XL"},
                }),
                ("erp_product_query", {
                    "action": "stock_status",
                    "params": {"outer_id": "HOODIE-XL"},
                }),
                ("erp_product_query", {
                    "action": "stock_in_out",
                    "params": {
                        "outer_id": "HOODIE-XL",
                        "order_type": 2,
                        "start_date": "2026-03-01",
                        "end_date": "2026-03-19",
                    },
                }),
            ]),
            _p2t([("route_to_chat", {"system_prompt": "商品分析师"})]),
        ])
        r = await _run_multi_exec(
            lp, "HOODIE-XL 这个商品的基本信息、库存和这个月销量", brain,
            exec_results=[
                '{"type":"product","outer_id":"HOODIE-XL"}',
                '{"name":"连帽卫衣XL","price":199}',
                '{"available":80}',
                '{"records":[{"num":20},{"num":15}]}',
            ],
        )
        assert r.generation_type == GenerationType.CHAT
        assert r.turns_used >= 3

    @pytest.mark.asyncio
    async def test_aftersales_analysis_with_code(self):
        """售后分析 → 淘宝退款 → code_execute 聚合"""
        lp = _lp()
        brain = AsyncMock(side_effect=[
            _p1("route_erp", {}),
            _p2t([
                ("erp_aftersales_query", {
                    "action": "aftersale_list",
                    "params": {
                        "start_date": "2026-03-13",
                        "end_date": "2026-03-19",
                    },
                }),
                ("erp_taobao_query", {
                    "action": "refund_list",
                    "params": {
                        "start_date": "2026-03-13",
                        "end_date": "2026-03-19",
                    },
                }),
            ]),
            _p2t([("code_execute", {
                "code": "# 聚合分析退货原因",
            })]),
            _p2t([("route_to_chat", {"system_prompt": "售后分析师"})]),
        ])
        r = await _run_multi_exec(
            lp, "这周的售后情况分析一下，按类型和原因统计", brain,
            exec_results=[
                '{"total":15,"items":[{"type":2},{"type":1}]}',
                '{"total":8,"workOrders":[]}',
                '{"analysis":{"return":10,"refund_only":5,"exchange":3}}',
            ],
        )
        assert r.generation_type == GenerationType.CHAT
        assert r.turns_used >= 3
