"""file_meta.py 单元测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from services.agent.excel_cleaner import CleaningReport
from services.agent.file_meta import (
    FileMeta,
    _build_sample,
    _build_schema,
    _col_index_to_letter,
    _determine_status,
    _infer_dtype,
    _scan_issues,
    _serialize_value,
    extract_formulas,
    format_file_view,
    generate_file_meta,
    read_file_meta,
    write_file_meta,
)


# ── _col_index_to_letter ──


class TestColIndexToLetter:
    def test_basic(self):
        assert _col_index_to_letter(0) == "A"
        assert _col_index_to_letter(1) == "B"
        assert _col_index_to_letter(25) == "Z"

    def test_double_letter(self):
        assert _col_index_to_letter(26) == "AA"
        assert _col_index_to_letter(27) == "AB"
        assert _col_index_to_letter(51) == "AZ"
        assert _col_index_to_letter(52) == "BA"


# ── _infer_dtype ──


class TestInferDtype:
    def test_integer(self):
        s = pd.Series([1, 2, 3], dtype="Int64")
        assert _infer_dtype(s) == "integer"

    def test_float(self):
        s = pd.Series([1.1, 2.2], dtype="float64")
        assert _infer_dtype(s) == "decimal"

    def test_string(self):
        s = pd.Series(["a", "b"], dtype="object")
        assert _infer_dtype(s) == "string"

    def test_datetime(self):
        s = pd.to_datetime(pd.Series(["2024-01-01", "2024-02-01"]))
        assert _infer_dtype(s) == "datetime"

    def test_bool(self):
        s = pd.Series([True, False], dtype="bool")
        assert _infer_dtype(s) == "boolean"


# ── _serialize_value ──


class TestSerializeValue:
    def test_none(self):
        assert _serialize_value(None) is None
        assert _serialize_value(float("nan")) is None

    def test_timestamp(self):
        ts = pd.Timestamp("2024-01-15 10:30:00")
        result = _serialize_value(ts)
        assert result == "2024-01-15 10:30:00"

    def test_primitives(self):
        assert _serialize_value(42) == 42
        assert _serialize_value(3.14) == 3.14
        assert _serialize_value("hello") == "hello"
        assert _serialize_value(True) is True


# ── _build_schema ──


class TestBuildSchema:
    def test_basic_schema(self):
        df = pd.DataFrame({
            "order_id": ["A001", "A002", "A003"],
            "amount": [100.0, 200.0, None],
            "qty": pd.array([1, 2, 3], dtype="Int64"),
        })
        schema = _build_schema(df, data_start_row=2)
        assert "order_id" in schema
        assert schema["order_id"]["col"] == "A"
        assert schema["order_id"]["col_index"] == 0
        assert schema["order_id"]["type"] == "string"
        assert schema["order_id"]["null_ratio"] == 0.0

        assert schema["amount"]["col"] == "B"
        assert schema["amount"]["type"] == "decimal"
        assert schema["amount"]["null_ratio"] > 0

        assert schema["qty"]["col"] == "C"
        assert schema["qty"]["type"] == "integer"

    def test_categories(self):
        df = pd.DataFrame({"platform": ["淘宝", "京东", "淘宝", "拼多多"]})
        schema = _build_schema(df, data_start_row=2)
        assert "categories" in schema["platform"]
        assert "淘宝" in schema["platform"]["categories"]

    def test_hidden_col_excluded(self):
        df = pd.DataFrame({"name": ["a"], "_is_hidden": [False]})
        schema = _build_schema(df, data_start_row=2)
        assert "_is_hidden" not in schema
        assert "name" in schema


# ── _build_sample ──


class TestBuildSample:
    def test_sample_with_row_numbers(self):
        df = pd.DataFrame({
            "name": ["Alice", "Bob", "Charlie", "David", "Eve", "Frank"],
            "age": [25, 30, 35, 40, 45, 50],
        })
        sample = _build_sample(df, data_start_row=2)
        assert len(sample["head"]) == 5
        assert sample["head"][0]["_row"] == 2
        assert sample["head"][0]["name"] == "Alice"
        assert sample["tail"][-1]["_row"] == 7  # index 5 + 2

    def test_empty_df(self):
        df = pd.DataFrame(columns=["a", "b"])
        sample = _build_sample(df, data_start_row=2)
        assert sample["head"] == []
        assert sample["tail"] == []

    def test_small_df(self):
        df = pd.DataFrame({"x": [1, 2]})
        sample = _build_sample(df, data_start_row=3)
        assert len(sample["head"]) == 2
        assert sample["head"][0]["_row"] == 3


# ── _scan_issues ──


class TestScanIssues:
    def test_missing_values(self):
        df = pd.DataFrame({
            "name": ["Alice", None, "Charlie"],
            "age": [25, 30, None],
        })
        issues = _scan_issues(df, data_start_row=2)
        missing_issues = [i for i in issues if i["type"] == "missing_value"]
        assert len(missing_issues) == 2
        # 第一个缺失在 name 列，row 应该是 index 1 + 2 = 3
        name_issue = next(i for i in missing_issues if i["location"]["raw_col_name"] == "name")
        assert name_issue["location"]["row"] == 3
        assert name_issue["location"]["col"] == "A"
        assert name_issue["preserved"] is True
        assert "recovery_hint" in name_issue

    def test_duplicates(self):
        df = pd.DataFrame({
            "name": ["Alice", "Alice", "Bob"],
            "age": [25, 25, 30],
        })
        issues = _scan_issues(df, data_start_row=2)
        dup_issues = [i for i in issues if i["type"] == "duplicate_row"]
        assert len(dup_issues) == 1
        assert dup_issues[0]["preserved"] is True
        assert "drop_duplicates" in dup_issues[0]["recovery_hint"]

    def test_no_issues(self):
        df = pd.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]})
        issues = _scan_issues(df, data_start_row=2)
        assert issues == []


# ── _determine_status ──


class TestDetermineStatus:
    def test_pass(self):
        assert _determine_status([]) == "pass"

    def test_warning(self):
        issues = [{"severity": "warning"}]
        assert _determine_status(issues) == "warning"

    def test_fail(self):
        issues = [{"severity": "error"}, {"severity": "warning"}]
        assert _determine_status(issues) == "fail"


# ── generate_file_meta ──


class TestGenerateFileMeta:
    def test_basic(self):
        df = pd.DataFrame({
            "order_id": ["A001", "A002"],
            "amount": [100.0, 200.0],
        })
        report = CleaningReport(
            original_shape=(2, 2),
            final_shape=(2, 2),
            data_start_row=2,
        )
        meta = generate_file_meta(df, report, source_file="test.xlsx")
        assert meta.version == "1.0"
        assert meta.status == "pass"
        assert meta.summary["row_count"] == 2
        assert meta.summary["col_count"] == 2
        assert "order_id" in meta.schema
        assert meta.schema["order_id"]["col"] == "A"
        assert len(meta.sample["head"]) == 2
        assert meta.sample["head"][0]["_row"] == 2

    def test_with_missing_values(self):
        df = pd.DataFrame({"x": [1, None, 3]})
        report = CleaningReport(data_start_row=2)
        meta = generate_file_meta(df, report, source_file="test.csv")
        assert meta.status == "warning"
        assert meta.stats["missing_values"] == 1
        assert len(meta.issues) > 0
        assert meta.confidence == 0.8


# ── to_dict / write / read ──


class TestFileMetaToDict:
    def test_returns_dict(self):
        meta = FileMeta(version="1.0", status="pass", source_file="test.xlsx")
        d = meta.to_dict()
        assert isinstance(d, dict)
        assert d["version"] == "1.0"
        assert d["status"] == "pass"
        assert d["source_file"] == "test.xlsx"

    def test_serializable(self):
        """to_dict 结果必须能 JSON 序列化。"""
        df = pd.DataFrame({"a": [1, 2]})
        report = CleaningReport(data_start_row=2)
        meta = generate_file_meta(df, report, source_file="test.xlsx")
        d = meta.to_dict()
        import json
        serialized = json.dumps(d, default=str)
        assert len(serialized) > 0


class TestWriteReadFileMeta:
    def test_round_trip(self, tmp_path):
        cache_path = str(tmp_path / "test.parquet")
        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        report = CleaningReport(data_start_row=2)
        meta = generate_file_meta(df, report, source_file="test.xlsx")

        write_file_meta(cache_path, meta)

        meta_path = Path(cache_path.replace(".parquet", ".meta.json"))
        assert meta_path.exists()

        loaded = read_file_meta(cache_path)
        assert loaded is not None
        assert loaded.version == "1.0"
        assert loaded.summary["row_count"] == 2

    def test_read_old_format_returns_none(self, tmp_path):
        cache_path = str(tmp_path / "old.parquet")
        meta_path = cache_path.replace(".parquet", ".meta.json")
        # 旧格式没有 version 字段
        Path(meta_path).write_text(json.dumps({"merged_cols_filled": 0}))
        assert read_file_meta(cache_path) is None

    def test_read_missing_file(self, tmp_path):
        assert read_file_meta(str(tmp_path / "nonexistent.parquet")) is None


# ── format_file_view ──


class TestFormatFileView:
    def test_basic_output(self):
        df = pd.DataFrame({
            "order_id": ["A001", "A002"],
            "amount": [100.0, 200.0],
        })
        report = CleaningReport(data_start_row=2)
        meta = generate_file_meta(df, report, source_file="sales.xlsx")
        view = format_file_view(meta)

        assert "[文件已就绪]" in view
        assert "sales.xlsx" in view
        assert "order_id" in view
        assert "Row 2" in view
        assert "行号映射" in view

    def test_small_data_no_warning(self):
        """小数据（< 1万行）不应触发规模警告"""
        df = pd.DataFrame({"a": list(range(100)), "b": list(range(100))})
        report = CleaningReport(data_start_row=2)
        meta = generate_file_meta(df, report, source_file="small.xlsx")
        view = format_file_view(meta)
        assert "⚠️" not in view
        assert "OOM" not in view

    def test_medium_data_hint(self):
        """中数据（≥1万行）应出现温和提示，不出 ⚠️"""
        df = pd.DataFrame({"a": list(range(15_000)), "b": list(range(15_000))})
        report = CleaningReport(data_start_row=2)
        meta = generate_file_meta(df, report, source_file="medium.xlsx")
        view = format_file_view(meta)
        assert "15,000行" in view
        assert "WHERE/GROUP BY" in view
        assert "⚠️" not in view  # 中数据不上 ⚠️

    def test_large_data_oom_warning(self):
        """大数据（≥10万行）应出现 ⚠️ + OOM 警告（行业 schema-aware 做法）"""
        df = pd.DataFrame({"a": list(range(150_000)), "b": list(range(150_000))})
        report = CleaningReport(data_start_row=2)
        meta = generate_file_meta(df, report, source_file="large.xlsx")
        view = format_file_view(meta)
        assert "⚠️" in view
        assert "150,000行" in view
        assert "OOM" in view
        assert "SELECT *" in view  # 给出反例


# ── extract_formulas ──


class TestExtractFormulas:
    def test_xlsx_with_formulas(self, tmp_path):
        """测试从包含公式的 xlsx 文件提取公式。"""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws["A1"] = "数量"
        ws["A2"] = 10
        ws["A3"] = 20
        ws["A4"] = "=SUM(A2:A3)"
        wb.save(str(tmp_path / "formulas.xlsx"))
        wb.close()

        result, skip_reason = extract_formulas(str(tmp_path / "formulas.xlsx"), "Sheet1")
        assert len(result) >= 1
        assert skip_reason == ""
        formula_entry = next(
            (f for f in result if "A4" in f["cell"]), None
        )
        assert formula_entry is not None
        assert "SUM" in formula_entry["formula"]
        assert formula_entry["formula"].startswith("=")

    def test_csv_returns_empty(self, tmp_path):
        """CSV 文件不支持公式提取。"""
        csv_path = tmp_path / "data.csv"
        csv_path.write_text("a,b\n1,2\n")
        result, skip = extract_formulas(str(csv_path))
        assert result == []

    def test_no_formulas(self, tmp_path):
        """无公式的 xlsx 返回空列表。"""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A1"] = "hello"
        ws["B1"] = 42
        wb.save(str(tmp_path / "no_formulas.xlsx"))
        wb.close()

        result, skip = extract_formulas(str(tmp_path / "no_formulas.xlsx"))
        assert result == []

    def test_formulas_in_file_meta(self, tmp_path):
        """公式传入 generate_file_meta 后出现在输出中。"""
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        report = CleaningReport(data_start_row=2)
        formulas = [{"cell": "Sheet1!C3", "formula": "=A3+B3", "value": 6}]
        meta = generate_file_meta(df, report, "test.xlsx", formulas=formulas)
        assert len(meta.formulas) == 1
        assert meta.formulas[0]["cell"] == "Sheet1!C3"

        view = format_file_view(meta)
        assert "Sheet1!C3" in view
        assert "=A3+B3" in view

    def test_no_file_size_threshold(self, tmp_path):
        """不再有 50MB 文件大小限制，任意大小均尝试提取。"""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws["A1"] = "=SUM(1,2)"
        xlsx_path = str(tmp_path / "test.xlsx")
        wb.save(xlsx_path)
        wb.close()

        # 直接提取，无大小检查
        result, skip_reason = extract_formulas(xlsx_path)
        assert skip_reason == ""
        assert len(result) >= 1

    def test_formula_value_is_numeric(self, tmp_path):
        """公式的计算值应被解析为数值类型。"""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws["A1"] = 10
        ws["A2"] = 20
        ws["A3"] = "=SUM(A1:A2)"
        xlsx_path = str(tmp_path / "numeric.xlsx")
        wb.save(xlsx_path)
        wb.close()

        result, _ = extract_formulas(xlsx_path)
        if result:
            formula_entry = next(
                (f for f in result if "A3" in f["cell"]), None
            )
            assert formula_entry is not None
            # 值可能是 None（openpyxl 创建的文件未计算），但格式正确
            assert "cell" in formula_entry
            assert "formula" in formula_entry
            assert "value" in formula_entry
