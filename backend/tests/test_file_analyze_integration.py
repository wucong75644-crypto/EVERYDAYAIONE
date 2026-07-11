"""file_analyze V2 端到端集成测试。

替代旧 test_prescan_integration.py。

覆盖：
  - ensure_parquet_cache 完整 V2 流程（make_scanner → adjudicate → clean_excel → render_xml）
  - meta.json 含 ai_decision / cleaning_strategy / xml_view
  - 缓存命中
  - Bug-1 真实回归（小发票文件 → XML 不再造谣 _is_summary）

标记：
  @pytest.mark.integration → 需要真实 DashScope API（CI 跳过）
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pandas as pd
import pytest


# 真实数据文件（仅本地有）
REAL_BUG_FILE = (
    "/Users/wucong/Documents/公摊/"
    "4月 销售主题分析-按订单商品明细-20260508134809_1d1705a783dab9d1-1.xlsx"
)


# ── V2 数据结构升级 ──

class TestFileMetaV2Fields:
    """FileMeta v2 新字段写入 meta.json。"""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not os.path.exists(REAL_BUG_FILE),
        reason="真实数据未提供，跳过 V2 端到端",
    )
    @pytest.mark.skipif(
        os.environ.get("RUN_LLM_INTEGRATION") != "1",
        reason="仅在 RUN_LLM_INTEGRATION=1 时运行真实 LLM 集成",
    )
    async def test_ai_decision_persisted(self, tmp_path):
        from services.agent.data_query_cache import ensure_parquet_cache
        from services.agent.file_meta import read_file_meta

        staging = tmp_path / "staging"
        staging.mkdir()
        cache_path, _ = await ensure_parquet_cache(
            REAL_BUG_FILE, None, str(staging),
        )
        meta = read_file_meta(cache_path)
        assert meta is not None
        # V2 字段必须存在
        assert isinstance(meta.ai_decision, dict)
        assert "column_semantics" in meta.ai_decision
        assert isinstance(meta.cleaning_strategy, dict)
        assert isinstance(meta.xml_view, str)
        assert "<file_analysis>" in meta.xml_view


# ── 缓存命中 ──

class TestCacheHit:
    """二次调用同一文件命中缓存（snapshot 匹配）。"""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not os.path.exists(REAL_BUG_FILE),
        reason="真实数据未提供",
    )
    @pytest.mark.skipif(
        os.environ.get("RUN_LLM_INTEGRATION") != "1",
        reason="仅在 RUN_LLM_INTEGRATION=1 时运行真实 LLM 集成",
    )
    async def test_second_call_hits_cache(self, tmp_path):
        import time
        from services.agent.data_query_cache import ensure_parquet_cache

        staging = tmp_path / "staging"
        staging.mkdir()

        t0 = time.monotonic()
        cache_path_1, _ = await ensure_parquet_cache(
            REAL_BUG_FILE, None, str(staging),
        )
        t_first = time.monotonic() - t0

        t0 = time.monotonic()
        cache_path_2, _ = await ensure_parquet_cache(
            REAL_BUG_FILE, None, str(staging),
        )
        t_second = time.monotonic() - t0

        assert cache_path_1 == cache_path_2
        # 二次调用应远快于首次（缓存命中）
        assert t_second < t_first * 0.1


# ── Bug-1 真实回归 ──

class TestBug1RegressionEndToEnd:
    """原报 bug：1,171 行发票文件不应再误报 _is_summary。"""

    def test_compress_issues_pure_unit_no_fabrication(self):
        """_compress_issues 单元层面不再造谣（无需 LLM）。"""
        from services.agent.file_meta.view import _compress_issues

        # 模拟用户原报场景：Row 2 多列缺失
        issues = [
            {"type": "missing_value", "severity": "warning",
             "location": {"row": 2, "col": col}, "action": f"{col} 列缺 N 个"}
            for col in ("D", "E", "F", "G", "H")
        ]
        out = _compress_issues(issues)
        text = "\n".join(out)
        assert "Row 2" in text
        assert "_is_summary" not in text
        assert "汇总行" not in text
        assert "WHERE" not in text


# ── V2 编排合成数据测试（不依赖 LLM）──

class TestV2OrchestrationSynthetic:
    """合成数据测试 V2 编排关键步骤（mock adjudicate 避免真实 LLM）。"""

    @pytest.mark.asyncio
    async def test_adapter_translates_decision_correctly(self):
        """_AIDecisionAdapter 把 AIDecision → 旧 PrescanResult 兼容形状。"""
        from services.agent.data_query_cache import _AIDecisionAdapter
        from services.agent.file_ai_decision import (
            AIDecision, ColumnSemantic, DataQualityNote,
        )

        d = AIDecision(
            header_row=2,
            data_start_row=3,
            column_semantics=[
                ColumnSemantic(letter="A", business_name="订单号",
                               semantic_type="id", is_id_column=True),
                ColumnSemantic(letter="B", business_name="金额",
                               semantic_type="amount", is_order_level=True),
            ],
            summary_rows=[501, 1002],
            data_quality_notes=[
                DataQualityNote(severity="info", note="退款负数正常",
                                affected_cols=["V"], affected_rows=[100, 200]),
            ],
            overall_summary="合成测试",
        )
        adapter = _AIDecisionAdapter(d)
        assert adapter.confidence == "high"
        assert adapter.header_rows == [2]
        assert adapter.data_start_row == 3
        assert adapter.column_mapping == {"A": "订单号", "B": "金额"}
        assert adapter.special_rows["summary"] == [501, 1002]
        assert len(adapter.anomalies) == 1
        assert adapter.anomalies[0]["column"] == "V"
        assert adapter.anomalies[0]["sample_rows"] == [100, 200]

        # to_dict 兼容 V1 meta.prescan 字段
        d_dict = adapter.to_dict()
        assert d_dict["confidence"] == "high"
        assert d_dict["header_rows"] == [2]


# ── 失败链端到端 ──

class TestFailureChainEndToEnd:
    """AI 全失败 → FileAnalyzeError 冒泡。"""

    @pytest.mark.asyncio
    async def test_ai_failure_raises_structured_error(self, tmp_path, monkeypatch):
        from services.agent.data_query_cache import ensure_parquet_cache
        from services.agent.file_ai_judge import FileAnalyzeError
        import services.agent.file_ai_judge as judge_mod
        import json

        # mock _call_llm 全失败
        async def fake_call(prompt, model, timeout):
            raise json.JSONDecodeError("bad", "doc", 0)

        monkeypatch.setattr(judge_mod, "_call_llm", fake_call)

        # 合成简单文件
        f = tmp_path / "simple.xlsx"
        pd.DataFrame({"a": [1, 2, 3]}).to_excel(f, index=False)
        staging = tmp_path / "staging"
        staging.mkdir()

        with pytest.raises(FileAnalyzeError) as exc:
            await ensure_parquet_cache(str(f), None, str(staging))
        # 3 次 llm_output_invalid → file_too_complex
        assert exc.value.error_category == "file_too_complex"
        assert exc.value.suggested_action == "ask_user"
        assert exc.value.user_message
