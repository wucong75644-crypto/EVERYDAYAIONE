"""erp_analytics_ratio.py 单元测试——占比 + ABC 分类计算。"""
import sys
from pathlib import Path

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


class TestComputeRatio:

    def _compute(self, raw_data, metrics=None, doc_type="order"):
        from services.kuaimai.erp_analytics_ratio import compute_ratio
        return compute_ratio(raw_data, None, doc_type, metrics)

    def test_abc_classification(self):
        data = [
            {"group_key": "A", "total_amount": 80000},
            {"group_key": "B", "total_amount": 15000},
            {"group_key": "C", "total_amount": 5000},
        ]
        result = self._compute(data, ["amount"])
        assert result.data[0]["abc_class"] == "A"   # 80% → A
        assert result.data[1]["abc_class"] == "B"   # 95% → B
        assert result.data[2]["abc_class"] == "C"   # 100% → C

    def test_ratio_calculation(self):
        data = [
            {"group_key": "X", "total_amount": 60},
            {"group_key": "Y", "total_amount": 40},
        ]
        result = self._compute(data, ["amount"])
        assert result.data[0]["ratio"] == 60.0
        assert result.data[1]["ratio"] == 40.0
        assert result.data[0]["cumulative_ratio"] == 60.0
        assert result.data[1]["cumulative_ratio"] == 100.0

    def test_sorted_descending(self):
        data = [
            {"group_key": "small", "total_amount": 10},
            {"group_key": "big", "total_amount": 90},
        ]
        result = self._compute(data, ["amount"])
        assert result.data[0]["group_key"] == "big"
        assert result.data[1]["group_key"] == "small"

    def test_empty_data(self):
        result = self._compute([], ["amount"])
        assert str(result.status) in ("empty", "OutputStatus.EMPTY")

    def test_zero_total(self):
        """所有值为 0 时不除零。"""
        data = [{"group_key": "A", "total_amount": 0}]
        result = self._compute(data, ["amount"])
        assert result.data[0]["ratio"] == 0

    def test_metric_col_fallback(self):
        """指定的 metrics 不存在时回退到 total_amount。"""
        data = [{"group_key": "A", "total_amount": 100}]
        result = self._compute(data, ["nonexistent"])
        assert result.data[0]["ratio"] == 100.0

    def test_metadata_contains_query_type(self):
        data = [{"group_key": "A", "total_amount": 100}]
        result = self._compute(data, ["amount"])
        assert result.metadata["query_type"] == "ratio"
        assert result.metadata["total"] == 100

    def test_count_metric(self):
        """metrics=["count"] → 按 doc_count 列计算占比。"""
        data = [
            {"group_key": "A", "doc_count": 80, "total_amount": 999},
            {"group_key": "B", "doc_count": 20, "total_amount": 1},
        ]
        result = self._compute(data, ["count"])
        assert result.data[0]["ratio"] == 80.0
