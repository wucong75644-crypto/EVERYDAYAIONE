"""file_ai_decision.py 单元测试。

覆盖：
  - dataclass 实例化与默认值
  - 枚举常量集合
  - validate_decision 校验逻辑（必填字段 / 类型合法性 / 行号关系）
  - 嵌套结构序列化
"""
from __future__ import annotations

from dataclasses import asdict

import pytest

from services.agent.file_ai_decision import (
    AIDecision,
    ColumnSemantic,
    DataQualityNote,
    EmptyRowDecision,
    HEADER_TYPES,
    MERGED_CELL_ACTIONS,
    MIXED_TYPE_ACTIONS,
    MergedCellAction,
    MixedTypeAction,
    NOTE_SEVERITIES,
    REGION_ROLES,
    RegionDecision,
    SEMANTIC_TYPES,
    SHEET_ROLES,
    SheetDecision,
    validate_decision,
)


class TestEnumConstants:
    """枚举常量集合内容正确。"""

    def test_semantic_types(self):
        assert "id" in SEMANTIC_TYPES
        assert "amount" in SEMANTIC_TYPES
        assert "other" in SEMANTIC_TYPES
        assert "invalid_xxx" not in SEMANTIC_TYPES

    def test_merged_cell_actions(self):
        assert MERGED_CELL_ACTIONS == frozenset({
            "treat_as_header", "fill_down", "preserve_as_group", "skip"
        })

    def test_mixed_type_actions(self):
        assert "extract_unit_number" in MIXED_TYPE_ACTIONS
        assert "extract_currency_amount" in MIXED_TYPE_ACTIONS

    def test_region_and_sheet_roles(self):
        assert REGION_ROLES == frozenset({"primary", "secondary", "metadata", "skip"})
        assert SHEET_ROLES == frozenset({"data", "meta", "aggregated", "skip"})

    def test_header_types_and_severities(self):
        assert HEADER_TYPES == frozenset({"single", "multi_level"})
        assert NOTE_SEVERITIES == frozenset({"info", "warning", "error"})


class TestSubObjectDefaults:
    """子对象默认值。"""

    def test_column_semantic_minimal(self):
        cs = ColumnSemantic(letter="A", business_name="序号", semantic_type="id")
        assert cs.is_order_level is False
        assert cs.is_id_column is False
        assert cs.notes == ""

    def test_merged_cell_action(self):
        m = MergedCellAction(range_str="A1:H1", action="treat_as_header")
        assert m.reason == ""

    def test_mixed_type_action(self):
        mta = MixedTypeAction(col_letter="F", action="force_str")
        assert mta.unit == ""

    def test_region_decision(self):
        rd = RegionDecision(region_id=1, range_str="A1:H100", role="primary")
        assert rd.relation_to_primary == ""

    def test_sheet_decision(self):
        sd = SheetDecision(name="2024-01", role="data")
        assert sd.merge_group == ""

    def test_empty_row_decision(self):
        erd = EmptyRowDecision(row=105)
        assert erd.reason == ""

    def test_data_quality_note(self):
        n = DataQualityNote(severity="info", note="退款金额负数属正常业务")
        assert n.affected_rows == []
        assert n.affected_cols == []


class TestAIDecisionDefaults:
    """AIDecision 默认值与最小实例化。"""

    def test_minimal_construction(self):
        d = AIDecision(
            header_row=1,
            data_start_row=2,
            column_semantics=[ColumnSemantic(letter="A", business_name="序号", semantic_type="id")],
        )
        assert d.header_type == "single"
        assert d.header_note == ""
        assert d.summary_rows == []
        assert d.regions == []
        assert d.sheets == []
        assert d.overall_summary == ""
        assert d.attempt_count == 1

    def test_with_all_fields(self):
        d = AIDecision(
            header_row=2,
            data_start_row=3,
            header_type="single",
            header_note="Row 1 是标题",
            column_semantics=[
                ColumnSemantic(letter="A", business_name="订单号", semantic_type="id", is_id_column=True),
                ColumnSemantic(letter="B", business_name="金额", semantic_type="amount", is_order_level=True),
            ],
            summary_rows=[501],
            data_quality_notes=[
                DataQualityNote(severity="warning", note="街道列空值率高")
            ],
            overall_summary="50 万行销售明细",
            model_used="qwen-turbo",
            attempt_count=1,
            elapsed_ms=1240,
        )
        assert len(d.column_semantics) == 2
        assert d.column_semantics[0].is_id_column is True
        assert d.summary_rows == [501]
        assert d.model_used == "qwen-turbo"


# ── validate_decision 校验测试 ──

class TestValidateDecisionPositive:
    """合法的 AIDecision 应通过校验（返回空列表）。"""

    def test_minimal_valid(self):
        d = AIDecision(
            header_row=1,
            data_start_row=2,
            column_semantics=[
                ColumnSemantic(letter="A", business_name="序号", semantic_type="id"),
            ],
        )
        assert validate_decision(d) == []

    def test_with_full_strategy(self):
        d = AIDecision(
            header_row=2,
            data_start_row=3,
            column_semantics=[
                ColumnSemantic(letter="A", business_name="订单号", semantic_type="id"),
            ],
            merged_cell_actions=[
                MergedCellAction(range_str="A1:H1", action="treat_as_header"),
            ],
            mixed_type_handling=[
                MixedTypeAction(col_letter="F", action="extract_unit_number", unit="kg"),
            ],
            regions=[
                RegionDecision(region_id=1, range_str="A1:H100", role="primary"),
            ],
            sheets=[
                SheetDecision(name="2024-01", role="data", merge_group="monthly"),
            ],
            data_quality_notes=[
                DataQualityNote(severity="info", note="可以聚合"),
            ],
        )
        assert validate_decision(d) == []


class TestValidateDecisionNegative:
    """非法 AIDecision 应返回明确错误列表。"""

    def test_header_row_below_one(self):
        d = AIDecision(header_row=0, data_start_row=2,
                       column_semantics=[ColumnSemantic("A", "x", "id")])
        errors = validate_decision(d)
        assert any("header_row" in e for e in errors)

    def test_data_start_row_not_greater_than_header(self):
        d = AIDecision(header_row=3, data_start_row=2,
                       column_semantics=[ColumnSemantic("A", "x", "id")])
        errors = validate_decision(d)
        assert any("必须 >" in e for e in errors)

    def test_empty_column_semantics(self):
        d = AIDecision(header_row=1, data_start_row=2, column_semantics=[])
        errors = validate_decision(d)
        assert any("column_semantics" in e for e in errors)

    def test_invalid_semantic_type(self):
        d = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[
                ColumnSemantic(letter="A", business_name="x", semantic_type="invalid_type"),
            ],
        )
        errors = validate_decision(d)
        assert any("semantic_type 非法" in e for e in errors)

    def test_empty_business_name_allowed(self):
        # 空列场景：LLM 老实输出 business_name="" 时不应被校验拒绝
        d = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[ColumnSemantic(letter="A", business_name="", semantic_type="other")],
        )
        errors = validate_decision(d)
        assert not any("business_name" in e for e in errors)

    def test_missing_letter(self):
        d = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[ColumnSemantic(letter="", business_name="x", semantic_type="id")],
        )
        errors = validate_decision(d)
        assert any("缺 letter" in e for e in errors)

    def test_invalid_merged_cell_action(self):
        d = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[ColumnSemantic("A", "x", "id")],
            merged_cell_actions=[
                MergedCellAction(range_str="A1:H1", action="invalid_action"),
            ],
        )
        errors = validate_decision(d)
        assert any("MergedCellAction.action 非法" in e for e in errors)

    def test_extract_unit_missing_unit_param(self):
        d = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[ColumnSemantic("A", "x", "id")],
            mixed_type_handling=[
                MixedTypeAction(col_letter="F", action="extract_unit_number", unit=""),
            ],
        )
        errors = validate_decision(d)
        assert any("extract_unit_number 缺 unit" in e for e in errors)

    def test_invalid_region_role(self):
        d = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[ColumnSemantic("A", "x", "id")],
            regions=[RegionDecision(region_id=1, range_str="A1:H100", role="invalid_role")],
        )
        errors = validate_decision(d)
        assert any("RegionDecision.role 非法" in e for e in errors)

    def test_invalid_sheet_role(self):
        d = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[ColumnSemantic("A", "x", "id")],
            sheets=[SheetDecision(name="s1", role="invalid_role")],
        )
        errors = validate_decision(d)
        assert any("SheetDecision.role 非法" in e for e in errors)

    def test_invalid_note_severity(self):
        d = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[ColumnSemantic("A", "x", "id")],
            data_quality_notes=[DataQualityNote(severity="critical_xxx", note="x")],
        )
        errors = validate_decision(d)
        assert any("DataQualityNote.severity 非法" in e for e in errors)

    def test_multiple_errors_accumulate(self):
        """多个错误应累加到一个列表，不是 raise 第一个。"""
        d = AIDecision(
            header_row=0,
            data_start_row=0,
            column_semantics=[ColumnSemantic("A", "", "invalid")],
        )
        errors = validate_decision(d)
        assert len(errors) >= 3


class TestSerialization:
    """完整 AIDecision 序列化。"""

    def test_full_decision_asdict(self):
        d = AIDecision(
            header_row=2,
            data_start_row=3,
            column_semantics=[
                ColumnSemantic(letter="A", business_name="订单号",
                               semantic_type="id", is_id_column=True),
            ],
            summary_rows=[501],
            data_quality_notes=[DataQualityNote(severity="info", note="OK")],
            overall_summary="测试摘要",
        )
        dct = asdict(d)
        assert dct["header_row"] == 2
        assert dct["column_semantics"][0]["business_name"] == "订单号"
        assert dct["column_semantics"][0]["is_id_column"] is True
        assert dct["summary_rows"] == [501]
        assert dct["data_quality_notes"][0]["severity"] == "info"
