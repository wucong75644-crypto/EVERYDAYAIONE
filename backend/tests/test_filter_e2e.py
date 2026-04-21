"""
端到端过滤条件链路测试。

模拟：LLM 返回结构化参数 → _sanitize → DepartmentAgent._params_to_filters
     → _dispatch → UnifiedQueryEngine.execute 接收到正确的 filters。

验证完整链路：参数提取 → 子 Agent 接收 → filters 正确到达引擎。
不走 ERPAgent（避免 kuaimai 导入链），直接测 DepartmentAgent.execute 层。
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

# 预注入 kuaimai 模块 stub，避免 __init__.py 导入 KuaiMaiClient → pydantic 链
import types as _types
if "services.kuaimai" not in sys.modules:
    _stub = _types.ModuleType("services.kuaimai")
    _stub.__path__ = [str(_backend_dir / "services" / "kuaimai")]
    sys.modules["services.kuaimai"] = _stub

from services.agent.plan_builder import _sanitize_params
from services.agent.tool_output import OutputFormat, OutputStatus, ToolOutput


def _mock_engine_result():
    """引擎返回的标准 OK 结果"""
    return ToolOutput(
        summary="查询成功", source="erp", status=OutputStatus.OK,
        format=OutputFormat.TEXT,
    )


def _extract_engine_call_kwargs(mock_engine_cls) -> dict:
    """从 mock 的 UnifiedQueryEngine.execute 调用中提取全部 kwargs"""
    call_kwargs = mock_engine_cls.return_value.execute.call_args
    return dict(call_kwargs.kwargs) if call_kwargs else {}


async def _run_agent_chain(agent_cls, llm_raw_params: dict, task_hint: str = ""):
    """模拟 ERPAgent 链路：_sanitize → agent.execute(dag_mode, params) → 引擎。

    task_hint: 传给 _classify_action 的任务描述（影响子 Agent 路由）。
    返回引擎收到的 call kwargs。
    """
    # Step 1: _sanitize_params（和 ERPAgent._extract_params 一致）
    params = _sanitize_params(llm_raw_params)

    # Step 2: 构造 agent 并执行
    agent = agent_cls(db=MagicMock(), org_id="test_org")
    task_desc = task_hint or "test task"

    with patch("services.kuaimai.erp_unified_query.UnifiedQueryEngine") as MockEngine:
        MockEngine.return_value.execute = AsyncMock(return_value=_mock_engine_result())
        await agent.execute(task_desc, dag_mode=True, params=params)
        return _extract_engine_call_kwargs(MockEngine)


# ============================================================
# 场景 1: 快递单号查订单
# ============================================================


class TestExpressNoE2E:

    @pytest.mark.asyncio
    async def test_express_no_reaches_engine(self):
        from services.agent.departments.trade_agent import TradeAgent
        kw = await _run_agent_chain(TradeAgent, {
            "doc_type": "order", "mode": "export",
            "time_range": "2026-01-21 ~ 2026-04-21",
            "express_no": "SF1234567890",
        })
        filters = kw.get("filters", [])
        ef = [f for f in filters if f.get("field") == "express_no"]
        assert len(ef) == 1
        assert ef[0]["op"] == "eq"
        assert ef[0]["value"] == "SF1234567890"


# ============================================================
# 场景 2: 店铺 + 订单状态
# ============================================================


class TestShopStatusE2E:

    @pytest.mark.asyncio
    async def test_shop_and_status_reach_engine(self):
        from services.agent.departments.trade_agent import TradeAgent
        kw = await _run_agent_chain(TradeAgent, {
            "doc_type": "order", "mode": "summary",
            "time_range": "2026-04-21 ~ 2026-04-21",
            "shop_name": "蓝创旗舰店",
            "order_status": "WAIT_SEND_GOODS",
        })
        filters = kw.get("filters", [])

        shop = [f for f in filters if f.get("field") == "shop_name"]
        assert len(shop) == 1 and shop[0]["op"] == "like"
        assert "蓝创旗舰店" in shop[0]["value"]

        status = [f for f in filters if f.get("field") == "order_status"]
        assert len(status) == 1
        assert status[0]["value"] == "WAIT_SEND_GOODS"


# ============================================================
# 场景 3: 买家昵称查订单
# ============================================================


class TestBuyerNickE2E:

    @pytest.mark.asyncio
    async def test_buyer_nick_reaches_engine(self):
        from services.agent.departments.trade_agent import TradeAgent
        kw = await _run_agent_chain(TradeAgent, {
            "doc_type": "order", "mode": "export",
            "time_range": "2026-01-21 ~ 2026-04-21",
            "buyer_nick": "张三",
        })
        filters = kw.get("filters", [])
        bf = [f for f in filters if f.get("field") == "buyer_nick"]
        assert len(bf) == 1 and bf[0]["value"] == "张三"


# ============================================================
# 场景 4: 售后类型 + 退货原因
# ============================================================


class TestAftersaleTypeE2E:

    @pytest.mark.asyncio
    async def test_aftersale_filters_reach_engine(self):
        from services.agent.departments.aftersale_agent import AftersaleAgent
        kw = await _run_agent_chain(AftersaleAgent, {
            "doc_type": "aftersale", "mode": "export",
            "time_range": "2026-04-01 ~ 2026-04-21",
            "aftersale_type": "退货退款",
            "text_reason": "质量",
        })
        filters = kw.get("filters", [])

        tf = [f for f in filters if f.get("field") == "aftersale_type"]
        assert len(tf) == 1 and tf[0]["value"] == "退货退款"

        rf = [f for f in filters if f.get("field") == "text_reason"]
        assert len(rf) == 1 and rf[0]["op"] == "like" and "质量" in rf[0]["value"]


# ============================================================
# 场景 5: 供应商采购单
# ============================================================


class TestSupplierPurchaseE2E:

    @pytest.mark.asyncio
    async def test_supplier_reaches_engine(self):
        from services.agent.departments.purchase_agent import PurchaseAgent
        kw = await _run_agent_chain(PurchaseAgent, {
            "doc_type": "purchase", "mode": "export",
            "time_range": "2026-04-01 ~ 2026-04-21",
            "supplier_name": "深圳供应商",
        })
        filters = kw.get("filters", [])
        sf = [f for f in filters if f.get("field") == "supplier_name"]
        assert len(sf) == 1 and sf[0]["op"] == "like"
        assert "深圳供应商" in sf[0]["value"]


# ============================================================
# 场景 6: 布尔标记（异常订单）
# ============================================================


class TestFlagFilterE2E:

    @pytest.mark.asyncio
    async def test_exception_flag_reaches_engine(self):
        from services.agent.departments.trade_agent import TradeAgent
        kw = await _run_agent_chain(TradeAgent, {
            "doc_type": "order", "mode": "summary",
            "time_range": "2026-04-21 ~ 2026-04-21",
            "is_exception": True,
        })
        filters = kw.get("filters", [])
        ff = [f for f in filters if f.get("field") == "is_exception"]
        assert len(ff) == 1 and ff[0]["value"] == 1


# ============================================================
# 场景 7: fields 透传到引擎
# ============================================================


class TestFieldsForwardE2E:

    @pytest.mark.asyncio
    async def test_fields_reach_engine(self):
        from services.agent.departments.trade_agent import TradeAgent
        kw = await _run_agent_chain(TradeAgent, {
            "doc_type": "order", "mode": "export",
            "time_range": "2026-04-21 ~ 2026-04-21",
            "fields": ["remark", "express_no", "buyer_nick"],
        })
        fields = kw.get("fields")
        assert fields is not None
        assert "remark" in fields
        assert "express_no" in fields
        assert "buyer_nick" in fields


# ============================================================
# 场景 8: 收件地区过滤
# ============================================================


class TestReceiverRegionE2E:

    @pytest.mark.asyncio
    async def test_region_filters_reach_engine(self):
        from services.agent.departments.trade_agent import TradeAgent
        kw = await _run_agent_chain(TradeAgent, {
            "doc_type": "order", "mode": "summary",
            "time_range": "2026-04-01 ~ 2026-04-21",
            "receiver_state": "广东",
            "receiver_city": "深圳",
        })
        filters = kw.get("filters", [])

        sf = [f for f in filters if f.get("field") == "receiver_state"]
        assert len(sf) == 1 and "广东" in sf[0]["value"]

        cf = [f for f in filters if f.get("field") == "receiver_city"]
        assert len(cf) == 1 and "深圳" in cf[0]["value"]


# ============================================================
# 场景 9: 仓库收货记录 — WarehouseAgent 透传 group_by
# ============================================================


class TestWarehouseReceiptE2E:

    @pytest.mark.asyncio
    async def test_warehouse_receipt_filters(self):
        from services.agent.departments.warehouse_agent import WarehouseAgent
        kw = await _run_agent_chain(WarehouseAgent, {
            "doc_type": "receipt", "mode": "summary",
            "time_range": "2026-04-01 ~ 2026-04-21",
            "warehouse_name": "A仓",
            "group_by": ["warehouse"],
        }, task_hint="A仓的收货记录")
        filters = kw.get("filters", [])
        wf = [f for f in filters if f.get("field") == "warehouse_name"]
        assert len(wf) == 1 and "A仓" in wf[0]["value"]
        assert kw.get("group_by") == ["warehouse"]


# ============================================================
# 场景 10: 多条件组合（旧条件 + 新条件共存）
# ============================================================


class TestMixedFiltersE2E:

    @pytest.mark.asyncio
    async def test_mixed_old_and_new_filters(self):
        from services.agent.departments.trade_agent import TradeAgent
        kw = await _run_agent_chain(TradeAgent, {
            "doc_type": "order", "mode": "summary",
            "time_range": "2026-04-21 ~ 2026-04-21",
            "time_col": "pay_time",
            "order_no": "126036803257340376",
            "order_status": "WAIT_SEND_GOODS",
            "is_urgent": True,
        })
        filters = kw.get("filters", [])
        field_names = [f.get("field") for f in filters]

        assert "pay_time" in field_names       # 时间
        assert "order_no" in field_names       # 旧条件
        assert "order_status" in field_names   # 新条件
        assert "is_urgent" in field_names      # 新条件
        assert len(filters) >= 5  # 2时间 + order_no + order_status + is_urgent
