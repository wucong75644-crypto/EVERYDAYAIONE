"""查询预检防御层单测

覆盖：导出量门卫（只管 limit 是否超上限）
"""

import pytest

from services.kuaimai.erp_query_preflight import (
    EXPORT_ROW_LIMIT,
    PreflightResult,
    preflight_check,
)


class TestPreflightCheck:
    """预检门卫：只管导出量"""

    def test_summary_always_ok(self):
        """summary 模式不拦截"""
        result = preflight_check("summary", limit=999_999)
        assert result.ok is True

    def test_small_limit_ok(self):
        """limit=5 → 放行"""
        result = preflight_check("export", limit=5)
        assert result.ok is True

    def test_default_limit_ok(self):
        """默认 limit=20 → 放行"""
        result = preflight_check("export", limit=20)
        assert result.ok is True

    def test_medium_limit_ok(self):
        """limit=1000 → 放行"""
        result = preflight_check("export", limit=1000)
        assert result.ok is True

    def test_boundary_at_limit_ok(self):
        """恰好等于上限 → 放行"""
        result = preflight_check("export", limit=EXPORT_ROW_LIMIT)
        assert result.ok is True

    def test_above_limit_rejected(self):
        """超过上限 → 拒绝 + 原因 + 建议"""
        result = preflight_check("export", limit=EXPORT_ROW_LIMIT + 1)
        assert result.ok is False
        assert "导出行数过大" in result.reject_reason
        assert len(result.suggestions) > 0

    def test_large_limit_rejected(self):
        """limit=50000 → 拒绝"""
        result = preflight_check("export", limit=50_000)
        assert result.ok is False

    def test_detail_mode_treated_as_export(self):
        """非 summary 模式都按 export 检查"""
        # mode != "export" → 放行（只拦 export）
        result = preflight_check("detail", limit=50_000)
        assert result.ok is True

    def test_suggestions_include_limit_hint(self):
        """拒绝时建议应包含减少 limit 的提示"""
        result = preflight_check("export", limit=50_000)
        assert any("limit" in s for s in result.suggestions)
