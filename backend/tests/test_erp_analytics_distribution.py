"""erp_analytics_distribution.py 单元测试。"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.kuaimai.erp_unified_schema import TimeRange


def _tr():
    return TimeRange(
        start_iso="2026-04-01", end_iso="2026-04-28", time_col="doc_created_at",
        label="04-01 ~ 04-28", date_range="2026-04-01 ~ 2026-04-28",
    )


def _mock_db(rpc_data):
    db = MagicMock()
    rpc_resp = MagicMock(); rpc_resp.data = rpc_data
    db.rpc = MagicMock(return_value=MagicMock(execute=MagicMock(return_value=rpc_resp)))
    return db


class TestQueryDistribution:

    @pytest.mark.asyncio
    async def test_success(self):
        from services.kuaimai.erp_analytics_distribution import query_distribution
        db = _mock_db([
            {"bucket": "0-50", "count": 120, "bucket_total": 3500},
            {"bucket": "50-100", "count": 85, "bucket_total": 6200},
        ])
        result = await query_distribution(db, "org-1", "order", tr=_tr(), metrics=["amount"])
        assert result.data is not None
        assert len(result.data) == 2
        db.rpc.assert_called_once()
        assert db.rpc.call_args[0][0] == "erp_distribution_query"

    @pytest.mark.asyncio
    async def test_empty_returns_empty(self):
        from services.kuaimai.erp_analytics_distribution import query_distribution
        db = _mock_db([])
        result = await query_distribution(db, "org-1", "order", tr=_tr())
        assert str(result.status) in ("empty", "OutputStatus.EMPTY")

    @pytest.mark.asyncio
    async def test_rpc_params_correct(self):
        from services.kuaimai.erp_analytics_distribution import query_distribution
        db = _mock_db([{"bucket": "0-100", "count": 10, "bucket_total": 500}])
        await query_distribution(db, "org-1", "order", tr=_tr(), metrics=["amount"])
        params = db.rpc.call_args[0][1]
        assert params["p_doc_type"] == "order"
        assert params["p_org_id"] == "org-1"


class TestFormatDistributionSummary:

    def test_format(self):
        from services.kuaimai.erp_analytics_distribution import format_distribution_summary
        rows = [
            {"bucket": "0-50", "count": 120, "bucket_total": 3500},
            {"bucket": "50-100", "count": 85, "bucket_total": 6200},
        ]
        text = format_distribution_summary(rows, "order", "amount")
        assert "120" in text or "0-50" in text
