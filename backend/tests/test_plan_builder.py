"""
PlanBuilder 单元测试。

覆盖: _sanitize_params 参数透传 + _fill_platform L2 意图补全 + 域路由冲突检测
设计文档: docs/document/TECH_意图完整性校验层.md §3-4
"""
import json
import sys
from pathlib import Path

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.agent.execution_plan import ExecutionPlan, Round
from unittest.mock import MagicMock

from services.agent.plan_builder import (
    _fill_codes,
    _fill_platform,
    _sanitize_params,
    parse_llm_plan,
)


# ============================================================
# _sanitize_params 透传测试
# ============================================================


class TestSanitizeParamsPassthrough:
    """_sanitize_params 应透传 product_code/order_no/include_invalid。"""

    def test_product_code_passed_through(self):
        result = _sanitize_params({"product_code": "DBTXL01"})
        assert result["product_code"] == "DBTXL01"

    def test_order_no_passed_through(self):
        result = _sanitize_params({"order_no": "126036803257340376"})
        assert result["order_no"] == "126036803257340376"

    def test_include_invalid_true(self):
        result = _sanitize_params({"include_invalid": True})
        assert result["include_invalid"] is True

    def test_include_invalid_false(self):
        result = _sanitize_params({"include_invalid": False})
        assert result["include_invalid"] is False

    def test_include_invalid_non_bool_ignored(self):
        """非布尔值的 include_invalid 不透传"""
        result = _sanitize_params({"include_invalid": "yes"})
        assert "include_invalid" not in result

    def test_empty_product_code_not_passed(self):
        result = _sanitize_params({"product_code": ""})
        assert "product_code" not in result

    def test_empty_order_no_not_passed(self):
        result = _sanitize_params({"order_no": ""})
        assert "order_no" not in result

    def test_all_new_fields_together(self):
        """三个新字段同时存在"""
        result = _sanitize_params({
            "mode": "detail",
            "product_code": "ABC123",
            "order_no": "999888777666",
            "include_invalid": True,
        })
        assert result["product_code"] == "ABC123"
        assert result["order_no"] == "999888777666"
        assert result["include_invalid"] is True
        assert result["mode"] == "detail"

    def test_existing_fields_still_work(self):
        """已有字段（mode/doc_type/platform/group_by）不受影响"""
        result = _sanitize_params({
            "mode": "summary",
            "doc_type": "order",
            "platform": "taobao",
            "group_by": "shop",
            "time_range": "2026-04-17 ~ 2026-04-17",
            "time_col": "pay_time",
        })
        assert result["mode"] == "summary"
        assert result["doc_type"] == "order"
        assert result["platform"] == "taobao"
        assert result["group_by"] == "shop"
        assert result["time_range"] == "2026-04-17 ~ 2026-04-17"
        assert result["time_col"] == "pay_time"


# ============================================================
# _fill_platform L2 意图补全测试
# ============================================================


def _make_plan(agents: list[str], params: dict | None = None) -> ExecutionPlan:
    """构造单 Round 的 ExecutionPlan 供测试用。"""
    return ExecutionPlan(rounds=[Round(
        agents=agents,
        task="test",
        depends_on=[],
        params=params,
    )])


class TestFillPlatform:

    def test_single_cn_platform_fills(self):
        """查询含单个中文平台名 → 补全 DB 编码"""
        plan = _make_plan(["trade"], {})
        _fill_platform(plan, "昨天淘宝的订单统计")
        assert plan.rounds[0].params["platform"] == "tb"

    def test_douyin_alias(self):
        """抖店也映射到 fxg"""
        plan = _make_plan(["trade"], {})
        _fill_platform(plan, "抖店订单")
        assert plan.rounds[0].params["platform"] == "fxg"

    def test_tianmao_maps_to_tb(self):
        """天猫映射到 tb"""
        plan = _make_plan(["trade"], {})
        _fill_platform(plan, "天猫店铺的售后")
        assert plan.rounds[0].params["platform"] == "tb"

    def test_ai_already_extracted_not_overwritten(self):
        """AI 已提取 platform → 不覆盖（AI 优先）"""
        plan = _make_plan(["trade"], {"platform": "jd"})
        _fill_platform(plan, "淘宝的订单")
        assert plan.rounds[0].params["platform"] == "jd"

    def test_multi_platform_no_fill(self):
        """多平台匹配 → 不补全"""
        plan = _make_plan(["trade"], {})
        _fill_platform(plan, "淘宝和京东的订单")
        assert "platform" not in plan.rounds[0].params

    def test_no_platform_no_fill(self):
        """无平台关键词 → 不补全"""
        plan = _make_plan(["trade"], {})
        _fill_platform(plan, "昨天的订单统计")
        assert "platform" not in plan.rounds[0].params

    def test_compute_round_skipped(self):
        """compute 域不做补全"""
        plan = ExecutionPlan(rounds=[
            Round(agents=["trade"], task="查询", depends_on=[], params={}),
            Round(agents=["compute"], task="计算", depends_on=[0], params={}),
        ])
        _fill_platform(plan, "淘宝的订单汇总")
        assert plan.rounds[0].params["platform"] == "tb"
        assert "platform" not in plan.rounds[1].params

    def test_none_params_initialized(self):
        """params 为 None 时自动初始化 dict"""
        plan = _make_plan(["trade"], None)
        _fill_platform(plan, "拼多多的订单")
        assert plan.rounds[0].params["platform"] == "pdd"

    def test_1688_fills(self):
        """1688 关键词能被识别"""
        plan = _make_plan(["purchase"], {})
        _fill_platform(plan, "1688的采购单")
        assert plan.rounds[0].params["platform"] == "1688"

    def test_same_platform_aliases_count_as_one(self):
        """淘宝+天猫都映射到 tb，算单一平台，应补全"""
        plan = _make_plan(["trade"], {})
        _fill_platform(plan, "淘宝天猫的订单")
        assert plan.rounds[0].params["platform"] == "tb"


# ============================================================
# L2 域路由冲突检测测试
# ============================================================


class TestDomainRouteConflict:

    def test_trade_with_wrong_doc_type_auto_corrected(self):
        """trade agent 收到 doc_type=purchase → 自动纠正为 order"""
        raw = json.dumps({"rounds": [
            {"agents": ["trade"], "task": "查询",
             "depends_on": [],
             "params": {"doc_type": "purchase", "mode": "summary",
                        "time_range": "2026-04-17 ~ 2026-04-17"}},
        ]})
        plan = parse_llm_plan(raw)
        assert plan.rounds[0].params["doc_type"] == "order"

    def test_purchase_with_order_doc_type_corrected(self):
        """purchase agent 收到 doc_type=order → 纠正为 purchase"""
        raw = json.dumps({"rounds": [
            {"agents": ["purchase"], "task": "查询",
             "depends_on": [],
             "params": {"doc_type": "order", "mode": "summary",
                        "time_range": "2026-04-17 ~ 2026-04-17"}},
        ]})
        plan = parse_llm_plan(raw)
        assert plan.rounds[0].params["doc_type"] in {"purchase", "purchase_return"}

    def test_correct_routing_unchanged(self):
        """trade + doc_type=order → 不纠正"""
        raw = json.dumps({"rounds": [
            {"agents": ["trade"], "task": "查询",
             "depends_on": [],
             "params": {"doc_type": "order", "mode": "summary",
                        "time_range": "2026-04-17 ~ 2026-04-17"}},
        ]})
        plan = parse_llm_plan(raw)
        assert plan.rounds[0].params["doc_type"] == "order"

    def test_compute_no_doc_type_check(self):
        """compute 域不做 doc_type 校验"""
        raw = json.dumps({"rounds": [
            {"agents": ["trade"], "task": "查数据",
             "depends_on": [],
             "params": {"doc_type": "order", "mode": "summary",
                        "time_range": "2026-04-17 ~ 2026-04-17"}},
            {"agents": ["compute"], "task": "汇总",
             "depends_on": [0],
             "params": {"doc_type": "order", "mode": "summary"}},
        ]})
        plan = parse_llm_plan(raw)
        # compute round 的 doc_type 不被纠正
        assert plan.rounds[1].params.get("doc_type") == "order"

    def test_no_doc_type_no_error(self):
        """没有 doc_type 时不报错"""
        raw = json.dumps({"rounds": [
            {"agents": ["trade"], "task": "查询",
             "depends_on": [],
             "params": {"mode": "summary",
                        "time_range": "2026-04-17 ~ 2026-04-17"}},
        ]})
        plan = parse_llm_plan(raw)
        assert "doc_type" not in plan.rounds[0].params


# ============================================================
# L2 product_code / order_no DB 验证补全测试
# ============================================================


def _mock_db_with_products(codes: list[str]) -> MagicMock:
    """创建一个 mock DB，erp_products 表中包含指定 outer_id。"""
    db = MagicMock()

    def _mock_table(table_name):
        table = MagicMock()
        chain = MagicMock()
        table.select.return_value = chain
        chain.eq.return_value = chain
        chain.limit.return_value = chain

        def _execute():
            result = MagicMock()
            # 从最近一次 eq 调用中提取查询值
            eq_calls = chain.eq.call_args_list
            val = None
            for call in eq_calls:
                args = call[0] if call[0] else ()
                if len(args) >= 2 and args[0] in ("outer_id", "order_no"):
                    val = args[1]
            if table_name == "erp_products":
                result.data = [{"outer_id": val}] if val in codes else []
            elif table_name == "erp_document_items":
                result.data = [{"order_no": val}] if val in codes else []
            else:
                result.data = []
            return result

        chain.execute = _execute
        return table

    db.table = _mock_table
    return db


class TestFillCodes:

    @pytest.mark.asyncio
    async def test_product_code_found_in_db(self):
        """查询含商品编码 + DB 验证通过 → 补全"""
        plan = _make_plan(["trade"], {})
        db = _mock_db_with_products(["DBTXL01"])
        await _fill_codes(plan, "查 DBTXL01 的订单", db, "org1")
        assert plan.rounds[0].params["product_code"] == "DBTXL01"

    @pytest.mark.asyncio
    async def test_product_code_not_in_db(self):
        """查询含字母数字组合但 DB 没有 → 不补全"""
        plan = _make_plan(["trade"], {})
        db = _mock_db_with_products([])
        await _fill_codes(plan, "查 XYZABC 的订单", db, "org1")
        assert "product_code" not in plan.rounds[0].params

    @pytest.mark.asyncio
    async def test_product_code_already_extracted(self):
        """LLM 已提取 product_code → 不覆盖"""
        plan = _make_plan(["trade"], {"product_code": "EXIST01"})
        db = _mock_db_with_products(["DBTXL01"])
        await _fill_codes(plan, "查 DBTXL01 的订单", db, "org1")
        assert plan.rounds[0].params["product_code"] == "EXIST01"

    @pytest.mark.asyncio
    async def test_order_no_found_in_db(self):
        """查询含订单号 + DB 验证通过 → 补全"""
        plan = _make_plan(["trade"], {})
        db = _mock_db_with_products(["126036803257340376"])
        await _fill_codes(
            plan, "查订单号 126036803257340376", db, "org1",
        )
        assert plan.rounds[0].params["order_no"] == "126036803257340376"

    @pytest.mark.asyncio
    async def test_order_no_not_in_db(self):
        """18 位数字但 DB 没有 → 不补全（避免误匹配手机号等）"""
        plan = _make_plan(["trade"], {})
        db = _mock_db_with_products([])
        await _fill_codes(
            plan, "联系方式 138001380001380", db, "org1",
        )
        assert "order_no" not in plan.rounds[0].params

    @pytest.mark.asyncio
    async def test_order_no_already_extracted(self):
        """LLM 已提取 order_no → 不覆盖"""
        plan = _make_plan(["trade"], {"order_no": "999888777666555444"})
        db = _mock_db_with_products(["126036803257340376"])
        await _fill_codes(
            plan, "查订单号 126036803257340376", db, "org1",
        )
        assert plan.rounds[0].params["order_no"] == "999888777666555444"

    @pytest.mark.asyncio
    async def test_no_db_no_fill(self):
        """db=None 时跳过，不报错"""
        plan = _make_plan(["trade"], {})
        await _fill_codes(plan, "查 DBTXL01 的订单", None, None)
        assert "product_code" not in plan.rounds[0].params

    @pytest.mark.asyncio
    async def test_compute_round_skipped(self):
        """compute 域不补全"""
        plan = ExecutionPlan(rounds=[
            Round(agents=["trade"], task="查询", depends_on=[], params={}),
            Round(agents=["compute"], task="计算", depends_on=[0], params={}),
        ])
        db = _mock_db_with_products(["DBTXL01"])
        await _fill_codes(plan, "查 DBTXL01 的订单", db, "org1")
        assert plan.rounds[0].params["product_code"] == "DBTXL01"
        assert "product_code" not in plan.rounds[1].params

    @pytest.mark.asyncio
    async def test_short_code_ignored(self):
        """短于 3 字符的候选被过滤"""
        plan = _make_plan(["trade"], {})
        db = _mock_db_with_products(["AB"])
        await _fill_codes(plan, "查 AB 的订单", db, "org1")
        assert "product_code" not in plan.rounds[0].params

    @pytest.mark.asyncio
    async def test_xhs_order_format(self):
        """小红书 P+18 位格式识别"""
        plan = _make_plan(["trade"], {})
        db = _mock_db_with_products(["P123456789012345678"])
        await _fill_codes(
            plan, "小红书订单 P123456789012345678", db, "org1",
        )
        assert plan.rounds[0].params["order_no"] == "P123456789012345678"

    @pytest.mark.asyncio
    async def test_both_code_and_order(self):
        """同时包含商品编码和订单号 → 都补全"""
        plan = _make_plan(["trade"], {})
        db = _mock_db_with_products(["DBTXL01", "126036803257340376"])
        await _fill_codes(
            plan, "商品 DBTXL01 订单 126036803257340376",
            db, "org1",
        )
        assert plan.rounds[0].params["product_code"] == "DBTXL01"
        assert plan.rounds[0].params["order_no"] == "126036803257340376"
