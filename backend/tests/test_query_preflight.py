"""查询预检防御层单测

覆盖：EXPLAIN 估算 + 三级路由决策 + 降级逻辑
"""

import pytest
from unittest.mock import MagicMock, patch

from services.kuaimai.erp_query_preflight import (
    BATCH_THRESHOLD,
    FAST_THRESHOLD,
    REJECT_THRESHOLD,
    PreflightResult,
    QueryRoute,
    _decide_route,
    _explain_estimate,
    preflight_check,
)


# ── _decide_route 路由决策 ─────────────────────────


class TestDecideRoute:
    """三级路由决策逻辑"""

    def test_small_summary_goes_fast(self):
        assert _decide_route(500, "summary") == QueryRoute.FAST

    def test_small_export_goes_fast(self):
        assert _decide_route(500, "export") == QueryRoute.FAST

    def test_boundary_fast_threshold(self):
        """恰好等于 FAST_THRESHOLD → STANDARD（不走快路径）"""
        assert _decide_route(FAST_THRESHOLD, "export") == QueryRoute.STANDARD

    def test_below_fast_threshold(self):
        assert _decide_route(FAST_THRESHOLD - 1, "export") == QueryRoute.FAST

    def test_medium_summary_goes_standard(self):
        """summary 模式不管行数多大都走 STANDARD（RPC 在 PG 侧执行）"""
        assert _decide_route(50_000, "summary") == QueryRoute.STANDARD

    def test_large_summary_still_standard(self):
        """summary 即使超过 BATCH_THRESHOLD 也走 STANDARD"""
        assert _decide_route(100_000, "summary") == QueryRoute.STANDARD

    def test_medium_export_goes_standard(self):
        assert _decide_route(20_000, "export") == QueryRoute.STANDARD

    def test_boundary_batch_threshold(self):
        """恰好等于 BATCH_THRESHOLD → STANDARD"""
        assert _decide_route(BATCH_THRESHOLD, "export") == QueryRoute.STANDARD

    def test_above_batch_threshold_export_goes_batch(self):
        assert _decide_route(BATCH_THRESHOLD + 1, "export") == QueryRoute.BATCH

    def test_very_large_export_goes_batch(self):
        assert _decide_route(300_000, "export") == QueryRoute.BATCH

    def test_zero_rows_goes_fast(self):
        assert _decide_route(0, "export") == QueryRoute.FAST

    def test_negative_rows_goes_fast(self):
        """异常的负数 → FAST（保守处理）"""
        assert _decide_route(-1, "export") == QueryRoute.FAST


# ── _explain_estimate EXPLAIN 预检 ────────────────


class TestExplainEstimate:
    """EXPLAIN 估算行数（mock db.pool）"""

    def _make_db(self, plan_rows: int):
        """构造 mock db，返回指定 plan_rows"""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {
            "QUERY PLAN": [{"Plan": {"Plan Rows": plan_rows}}]
        }
        mock_cur.__enter__ = lambda s: s
        mock_cur.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)

        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn

        db = MagicMock()
        db.pool = mock_pool
        return db

    def test_returns_plan_rows(self):
        db = self._make_db(12345)
        result = _explain_estimate(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-123",
        )
        assert result == 12345

    def test_null_org_id(self):
        """org_id=None 时 WHERE 用 IS NULL"""
        db = self._make_db(100)
        result = _explain_estimate(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", None,
        )
        assert result == 100
        # 验证 SQL 包含 IS NULL
        call_args = db.pool.connection().__enter__().cursor().__enter__().execute.call_args
        sql = call_args[0][0]
        assert "org_id IS NULL" in sql

    def test_sql_injection_safe(self):
        """time_col 直接拼入 SQL，但 doc_type/start/end 走参数化"""
        db = self._make_db(0)
        _explain_estimate(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-123",
        )
        call_args = db.pool.connection().__enter__().cursor().__enter__().execute.call_args
        params = call_args[0][1]
        assert params["doc_type"] == "order"
        assert params["start"] == "2026-04-01"


# ── preflight_check 完整预检 ─────────────────────


class TestPreflightCheck:
    """完整预检流程"""

    def _make_db(self, plan_rows: int):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {
            "QUERY PLAN": [{"Plan": {"Plan Rows": plan_rows}}]
        }
        mock_cur.__enter__ = lambda s: s
        mock_cur.__exit__ = MagicMock(return_value=False)

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)

        mock_pool = MagicMock()
        mock_pool.connection.return_value = mock_conn

        db = MagicMock()
        db.pool = mock_pool
        return db

    def test_small_export_routes_fast(self):
        db = self._make_db(500)
        result = preflight_check(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-123", "export",
        )
        assert result.route == QueryRoute.FAST
        assert result.estimated_rows == 500

    def test_medium_export_routes_standard(self):
        db = self._make_db(15_000)
        result = preflight_check(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-123", "export",
        )
        assert result.route == QueryRoute.STANDARD

    def test_large_export_routes_batch(self):
        db = self._make_db(100_000)
        result = preflight_check(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-123", "export",
        )
        assert result.route == QueryRoute.BATCH

    def test_reject_threshold_has_suggestions(self):
        db = self._make_db(REJECT_THRESHOLD + 1)
        result = preflight_check(
            db, "order", "pay_time",
            "2026-01-01", "2026-12-31", "org-123", "export",
        )
        assert result.route == QueryRoute.BATCH
        assert result.reject_reason != ""
        assert len(result.suggestions) > 0

    def test_explain_failure_fallback_standard(self):
        """EXPLAIN 失败 → 静默降级走 STANDARD"""
        db = MagicMock()
        db.pool.connection.side_effect = Exception("connection failed")
        result = preflight_check(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-123", "export",
        )
        assert result.route == QueryRoute.STANDARD
        assert result.estimated_rows == -1

    def test_large_summary_stays_standard(self):
        """summary 即使行数很大也走 STANDARD"""
        db = self._make_db(300_000)
        result = preflight_check(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-123", "summary",
        )
        assert result.route == QueryRoute.STANDARD
