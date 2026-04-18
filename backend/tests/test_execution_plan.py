"""
ERP 参数提取工具函数 单元测试。

覆盖: plan_builder.py（简化后）
- quick_classify 关键词降级
- _sanitize_params 参数宽容校验
- _build_fallback_params 降级路径参数构造
- build_extract_prompt / parse_extract_response LLM 提取
- build_plan_prompt 向后兼容

设计文档: docs/document/TECH_ERPAgent架构简化.md
"""
import sys
from pathlib import Path

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.agent.plan_builder import (
    build_extract_prompt,
    build_plan_prompt,
    parse_extract_response,
    quick_classify,
)


# ============================================================
# quick_classify — 关键词降级
# ============================================================


class TestQuickClassify:

    def test_warehouse_keywords(self):
        assert quick_classify("查一下A001库存") == "warehouse"
        assert quick_classify("哪些缺货") == "warehouse"
        assert quick_classify("仓库列表") == "warehouse"

    def test_purchase_keywords(self):
        assert quick_classify("采购单到货了吗") == "purchase"
        assert quick_classify("供应商列表") == "purchase"

    def test_trade_keywords(self):
        assert quick_classify("今天多少订单") == "trade"
        assert quick_classify("发货情况") == "trade"
        assert quick_classify("物流查询") == "trade"

    def test_aftersale_keywords(self):
        assert quick_classify("退货率多少") == "aftersale"
        assert quick_classify("售后单查询") == "aftersale"

    def test_no_match(self):
        assert quick_classify("hello world") is None
        assert quick_classify("天气怎么样") is None

    def test_highest_score_wins(self):
        assert quick_classify("订单退货退款") == "trade"

    def test_ambiguous_tie_returns_none(self):
        assert quick_classify("采购入库") is None


# ============================================================
# parse_extract_response — LLM 响应解析
# ============================================================


class TestParseExtractResponse:

    def test_valid_json(self):
        raw = '{"domain": "trade", "params": {"doc_type": "order", "mode": "summary"}}'
        domain, params = parse_extract_response(raw)
        assert domain == "trade"
        assert params["doc_type"] == "order"

    def test_with_markdown_fence(self):
        raw = '```json\n{"domain": "warehouse", "params": {"mode": "detail"}}\n```'
        domain, params = parse_extract_response(raw)
        assert domain == "warehouse"
        assert params["mode"] == "detail"

    def test_invalid_json(self):
        with pytest.raises(ValueError, match="JSON"):
            parse_extract_response("not json at all")

    def test_missing_domain(self):
        with pytest.raises(ValueError, match="domain"):
            parse_extract_response('{"params": {}}')

    def test_unknown_domain_rejected(self):
        with pytest.raises(ValueError, match="未知域"):
            parse_extract_response('{"domain": "finance", "params": {}}')

    def test_compute_domain_rejected(self):
        """compute 不再是有效域"""
        with pytest.raises(ValueError, match="未知域"):
            parse_extract_response('{"domain": "compute", "params": {}}')

    def test_missing_params_defaults_empty(self):
        raw = '{"domain": "trade"}'
        domain, params = parse_extract_response(raw)
        assert domain == "trade"
        assert params == {}

    def test_params_not_dict_defaults_empty(self):
        raw = '{"domain": "trade", "params": "invalid"}'
        domain, params = parse_extract_response(raw)
        assert params == {}


# ============================================================
# _sanitize_params — params 宽容校验
# ============================================================


class TestSanitizeParams:

    def test_valid_params_pass_through(self):
        from services.agent.plan_builder import _sanitize_params
        params = {
            "mode": "summary",
            "doc_type": "order",
            "time_range": "2026-04-17 ~ 2026-04-17",
            "time_col": "pay_time",
            "platform": "taobao",
        }
        clean = _sanitize_params(params)
        assert clean["mode"] == "summary"
        assert clean["doc_type"] == "order"
        assert clean["time_range"] == "2026-04-17 ~ 2026-04-17"
        assert clean["platform"] == "taobao"

    def test_invalid_mode_defaults_to_summary(self):
        from services.agent.plan_builder import _sanitize_params
        clean = _sanitize_params({"mode": "garbage"})
        assert clean["mode"] == "summary"

    def test_invalid_time_range_dropped(self):
        from services.agent.plan_builder import _sanitize_params
        clean = _sanitize_params({"time_range": "今天到明天"})
        assert "time_range" not in clean


# ============================================================
# _build_fallback_params — 降级路径参数构造
# ============================================================


class TestBuildFallbackParams:

    def test_default_summary_today(self):
        from services.agent.plan_builder import _build_fallback_params
        params = _build_fallback_params("查订单")
        assert params["mode"] == "summary"
        assert "~" in params["time_range"]
        assert params["_degraded"] is True

    def test_detail_keywords_override_mode(self):
        from services.agent.plan_builder import _build_fallback_params
        for kw in ("明细", "列表", "导出"):
            params = _build_fallback_params(f"查订单{kw}")
            assert params["mode"] == "detail", f"'{kw}' should trigger detail"

    def test_domain_time_col_mapping(self):
        from services.agent.plan_builder import _build_fallback_params
        assert _build_fallback_params("x", domain="trade")["time_col"] == "pay_time"
        assert _build_fallback_params("x", domain="purchase")["time_col"] == "doc_created_at"
        assert _build_fallback_params("x", domain="warehouse")["time_col"] == "doc_created_at"
        assert _build_fallback_params("x", domain="aftersale")["time_col"] == "doc_created_at"


# ============================================================
# build_extract_prompt / build_plan_prompt
# ============================================================


class TestBuildExtractPrompt:

    def test_contains_query(self):
        prompt = build_extract_prompt("查库存")
        assert "查库存" in prompt

    def test_contains_domains(self):
        prompt = build_extract_prompt("x")
        assert "warehouse" in prompt
        assert "purchase" in prompt
        assert "trade" in prompt
        assert "aftersale" in prompt

    def test_no_compute_domain(self):
        """简化后不包含 compute 域"""
        prompt = build_extract_prompt("x")
        assert "compute" not in prompt

    def test_output_format_is_flat(self):
        """输出格式应为扁平结构，不是 DAG"""
        prompt = build_extract_prompt("x")
        assert '"domain"' in prompt
        assert "rounds" not in prompt

    def test_contains_time_when_provided(self):
        prompt = build_extract_prompt("x", now_str="2026-04-18 10:00 周五")
        assert "2026-04-18" in prompt

    def test_build_plan_prompt_compat(self):
        """build_plan_prompt 是向后兼容别名"""
        p1 = build_extract_prompt("查库存", "2026-04-18")
        p2 = build_plan_prompt("查库存", "2026-04-18")
        assert p1 == p2
