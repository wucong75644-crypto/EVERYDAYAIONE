"""查询预检防御层单测

覆盖：EXPLAIN 估算 + 超阈值拒绝 + 降级逻辑
"""

import pytest
from unittest.mock import MagicMock

from services.kuaimai.erp_query_preflight import (
    EXPORT_ROW_LIMIT,
    PreflightResult,
    _explain_estimate,
    preflight_check,
)


def _make_db(plan_rows: int):
    """构造 mock db，EXPLAIN 返回指定 plan_rows"""
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


class TestPreflightCheck:
    """预检门卫：能做就做，做不了就拒绝"""

    def test_summary_always_ok(self):
        """summary 模式不拦截，不调 EXPLAIN"""
        db = MagicMock()  # 不需要 pool
        result = preflight_check(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-1", "summary",
        )
        assert result.ok is True
        # summary 不调 EXPLAIN
        db.pool.connection.assert_not_called()

    def test_small_export_ok(self):
        """export 预估 < 阈值 → 放行"""
        db = _make_db(10_000)
        result = preflight_check(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-1", "export",
        )
        assert result.ok is True
        assert result.estimated_rows == 10_000

    def test_boundary_at_limit_ok(self):
        """恰好等于阈值 → 放行"""
        db = _make_db(EXPORT_ROW_LIMIT)
        result = preflight_check(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-1", "export",
        )
        assert result.ok is True

    def test_above_limit_rejected(self):
        """超过阈值 → 拒绝 + 原因 + 建议"""
        db = _make_db(EXPORT_ROW_LIMIT + 1)
        result = preflight_check(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-1", "export",
        )
        assert result.ok is False
        assert "数据量过大" in result.reject_reason
        assert len(result.suggestions) > 0

    def test_large_data_rejected(self):
        """30 万行 → 拒绝"""
        db = _make_db(300_000)
        result = preflight_check(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-1", "export",
        )
        assert result.ok is False
        assert result.estimated_rows == 300_000

    def test_explain_failure_allows_execution(self):
        """EXPLAIN 失败 → 静默放行（防御层不能成为新故障点）"""
        db = MagicMock()
        db.pool.connection.side_effect = Exception("connection lost")
        result = preflight_check(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-1", "export",
        )
        assert result.ok is True
        assert result.estimated_rows == -1

    def test_null_org_id(self):
        """org_id=None → EXPLAIN 用 IS NULL"""
        db = _make_db(5_000)
        result = preflight_check(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", None, "export",
        )
        assert result.ok is True


class TestExplainEstimate:
    """EXPLAIN 估算行数"""

    def test_returns_plan_rows(self):
        db = _make_db(12345)
        result = _explain_estimate(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", "org-1",
        )
        assert result == 12345

    def test_invalid_time_col_raises(self):
        """非白名单时间列 → 拒绝（防 SQL 注入）"""
        db = _make_db(0)
        with pytest.raises(ValueError, match="invalid time_col"):
            _explain_estimate(
                db, "order", "'; DROP TABLE --",
                "2026-04-01", "2026-05-01", "org-1",
            )

    def test_null_org_id_uses_is_null(self):
        db = _make_db(100)
        _explain_estimate(
            db, "order", "pay_time",
            "2026-04-01", "2026-05-01", None,
        )
        call_args = db.pool.connection().__enter__().cursor().__enter__().execute.call_args
        sql = call_args[0][0]
        assert "org_id IS NULL" in sql
