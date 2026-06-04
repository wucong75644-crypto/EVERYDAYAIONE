"""file_evidence.py 单元测试。

覆盖：
  - dataclass 实例化默认值
  - dataclass 字段类型
  - 序列化/反序列化（asdict 双向）
  - EvidencePool 4 条路径形态
"""
from __future__ import annotations

from dataclasses import asdict

import pytest

from services.agent.file_evidence import (
    CellSample,
    ColumnEvidence,
    EvidencePool,
    FormulaEvidence,
    RegionEvidence,
    SheetEvidence,
    SuspiciousRow,
)


class TestDataclassDefaults:
    """所有 dataclass 在最小参数下能正确实例化，默认值合理。"""

    def test_cell_sample_minimal(self):
        cs = CellSample(row=1, col="A", raw_value="hello")
        assert cs.row == 1
        assert cs.col == "A"
        assert cs.raw_value == "hello"
        assert cs.classified == ""

    def test_suspicious_row_minimal(self):
        sr = SuspiciousRow(row=10, reason="multi_null")
        assert sr.row == 10
        assert sr.reason == "multi_null"
        assert sr.keywords == []
        assert sr.null_ratio == 0.0
        assert sr.raw_values == []
        assert sr.surrounding == {}

    def test_column_evidence_minimal(self):
        ce = ColumnEvidence(col_letter="A", raw_header="销售金额")
        assert ce.col_letter == "A"
        assert ce.raw_header == "销售金额"
        assert ce.sample_values == []
        assert ce.classified_dist == {}
        assert ce.null_ratio == 0.0
        assert ce.is_long_id_candidate is False

    def test_region_evidence_minimal(self):
        re_obj = RegionEvidence(
            region_id=1, range_str="A1:H100", header_row=0,
        )
        assert re_obj.region_id == 1
        assert re_obj.range_str == "A1:H100"
        assert re_obj.suspected_type == "unknown"

    def test_sheet_evidence_minimal(self):
        se = SheetEvidence(name="Sheet1", rows=100, cols=10)
        assert se.name == "Sheet1"
        assert se.rows == 100
        assert se.cols == 10
        assert se.column_names == []

    def test_sheet_evidence_unsampled(self):
        """rows=-1 表示该 sheet 未采样（多 sheet 超上限场景）。"""
        se = SheetEvidence(name="未采样的sheet", rows=-1, cols=10)
        assert se.rows == -1

    def test_formula_evidence_minimal(self):
        fe = FormulaEvidence(cell="Sheet1!H501", expression="=SUM(H3:H500)", value=156890.50)
        assert fe.cell == "Sheet1!H501"
        assert fe.col_name == ""


class TestEvidencePoolPaths:
    """EvidencePool 在 4 条路径下的形态。"""

    def _base_pool(self, path_type: str) -> EvidencePool:
        return EvidencePool(
            file_path="/tmp/test.xlsx",
            file_name="test.xlsx",
            file_size_bytes=1024,
            total_rows=100,
            total_cols=10,
            sheet_names=["Sheet1"],
            target_sheet="Sheet1",
            path_type=path_type,
        )

    def test_path_a_minimal(self):
        pool = self._base_pool("A")
        assert pool.path_type == "A"
        assert pool.regions == []
        assert pool.sheets == []
        assert pool.suspicious_rows == []
        assert pool.formulas == []

    def test_path_b_with_suspicious(self):
        pool = self._base_pool("B")
        pool.suspicious_rows.append(
            SuspiciousRow(row=500001, reason="multi_null", keywords=["合计"])
        )
        assert len(pool.suspicious_rows) == 1
        assert pool.suspicious_rows[0].reason == "multi_null"

    def test_path_c_with_regions(self):
        pool = self._base_pool("C")
        pool.regions.extend([
            RegionEvidence(region_id=1, range_str="A1:H100", header_row=0,
                           suspected_type="primary"),
            RegionEvidence(region_id=2, range_str="J1:M50", header_row=0,
                           suspected_type="summary"),
        ])
        assert len(pool.regions) == 2
        assert pool.regions[0].suspected_type == "primary"

    def test_path_d_with_sheets(self):
        pool = self._base_pool("D")
        pool.sheet_names = ["2024-01", "2024-02", "说明"]
        pool.target_sheet = "*"
        pool.sheets.extend([
            SheetEvidence(name="2024-01", rows=1000, cols=15),
            SheetEvidence(name="2024-02", rows=1100, cols=15),
            SheetEvidence(name="说明", rows=10, cols=3),
        ])
        assert len(pool.sheets) == 3


class TestSerialization:
    """asdict 序列化 / 反序列化。"""

    def test_evidence_pool_serializable(self):
        pool = EvidencePool(
            file_path="/tmp/test.xlsx",
            file_name="test.xlsx",
            file_size_bytes=1024,
            total_rows=100,
            total_cols=10,
            sheet_names=["Sheet1"],
            target_sheet="Sheet1",
            path_type="A",
        )
        pool.columns.append(
            ColumnEvidence(
                col_letter="A", raw_header="序号",
                sample_values=[1, 2, 3], classified_dist={"numeric": 3},
            )
        )
        pool.suspicious_rows.append(
            SuspiciousRow(row=2, reason="multi_null", null_ratio=0.7)
        )

        d = asdict(pool)
        assert d["file_name"] == "test.xlsx"
        assert d["path_type"] == "A"
        assert len(d["columns"]) == 1
        assert d["columns"][0]["col_letter"] == "A"
        assert d["columns"][0]["classified_dist"] == {"numeric": 3}
        assert len(d["suspicious_rows"]) == 1
        assert d["suspicious_rows"][0]["null_ratio"] == 0.7

    def test_nested_round_trip(self):
        """dataclass 嵌套结构序列化后能完整还原。"""
        original = SuspiciousRow(
            row=42,
            reason="multi_null",
            keywords=["合计", "总计"],
            null_ratio=0.85,
            raw_values=[None, None, "合计", 1234.5],
            surrounding={"prev_row": [1, 2, 3]},
        )
        d = asdict(original)
        rebuilt = SuspiciousRow(**d)
        assert rebuilt == original


class TestEvidencePoolBoundaries:
    """边界场景。"""

    def test_empty_file(self):
        """0 行文件能创建 pool（清洗层会 raise）。"""
        pool = EvidencePool(
            file_path="/tmp/empty.xlsx",
            file_name="empty.xlsx",
            file_size_bytes=0,
            total_rows=0,
            total_cols=0,
            sheet_names=[],
            target_sheet="",
            path_type="A",
        )
        assert pool.total_rows == 0
        assert pool.columns == []

    def test_long_sheet_list_path_d(self):
        """200 个 sheet 进 sheet_names，但 sheets 只放采样的。"""
        pool = EvidencePool(
            file_path="/tmp/multi.xlsx",
            file_name="multi.xlsx",
            file_size_bytes=10_000_000,
            total_rows=0,
            total_cols=10,
            sheet_names=[f"sheet_{i}" for i in range(200)],
            target_sheet="*",
            path_type="D",
        )
        # 假设只采样前 20 个
        for i in range(20):
            pool.sheets.append(SheetEvidence(name=f"sheet_{i}", rows=100, cols=10))
        # 剩余 180 个用 rows=-1 占位
        for i in range(20, 200):
            pool.sheets.append(SheetEvidence(name=f"sheet_{i}", rows=-1, cols=10))

        sampled = [s for s in pool.sheets if s.rows != -1]
        unsampled = [s for s in pool.sheets if s.rows == -1]
        assert len(sampled) == 20
        assert len(unsampled) == 180
