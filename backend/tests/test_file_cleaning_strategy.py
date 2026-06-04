"""file_cleaning_strategy.py 单元测试。

覆盖：
  - 默认值
  - from_decision 映射规则（is_id_column → id_columns / column_semantics → column_mapping）
  - empty_row_policy 自动升级
  - 边界场景
"""
from __future__ import annotations

from dataclasses import asdict

import pytest

from services.agent.file_ai_decision import (
    AIDecision,
    ColumnSemantic,
    EmptyRowDecision,
    MergedCellAction,
    MixedTypeAction,
)
from services.agent.file_cleaning_strategy import (
    EMPTY_ROW_POLICIES,
    CleaningStrategy,
)


class TestDefaults:
    """空 CleaningStrategy 默认行为。"""

    def test_empty_strategy(self):
        s = CleaningStrategy()
        assert s.merged_cell_actions == []
        assert s.empty_row_policy == "strict_all_empty"
        assert s.preserve_empty_rows == []
        assert s.mixed_type_handling == []
        assert s.id_columns == []
        assert s.summary_rows == []
        assert s.column_mapping == {}
        assert s.skip_columns == []

    def test_policy_constants(self):
        assert "strict_all_empty" in EMPTY_ROW_POLICIES
        assert "preserve_section_separators" in EMPTY_ROW_POLICIES


class TestFromDecisionMapping:
    """from_decision 的映射规则。"""

    def test_id_columns_derived(self):
        """is_id_column=True 的列应映射到 id_columns。"""
        decision = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[
                ColumnSemantic(letter="A", business_name="平台订单号",
                               semantic_type="id", is_id_column=True),
                ColumnSemantic(letter="B", business_name="店铺名称",
                               semantic_type="name", is_id_column=False),
                ColumnSemantic(letter="C", business_name="规格商家编码",
                               semantic_type="id", is_id_column=True),
            ],
        )
        s = CleaningStrategy.from_decision(decision)
        assert s.id_columns == ["平台订单号", "规格商家编码"]

    def test_column_mapping_derived(self):
        """所有列都进 column_mapping（letter→business_name）。"""
        decision = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[
                ColumnSemantic(letter="A", business_name="订单号", semantic_type="id"),
                ColumnSemantic(letter="B", business_name="商品名", semantic_type="name"),
                ColumnSemantic(letter="C", business_name="金额", semantic_type="amount"),
            ],
        )
        s = CleaningStrategy.from_decision(decision)
        assert s.column_mapping == {"A": "订单号", "B": "商品名", "C": "金额"}

    def test_summary_rows_derived(self):
        decision = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[ColumnSemantic("A", "x", "id")],
            summary_rows=[501, 1002],
        )
        s = CleaningStrategy.from_decision(decision)
        assert s.summary_rows == [501, 1002]

    def test_merged_cell_actions_passthrough(self):
        decision = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[ColumnSemantic("A", "x", "id")],
            merged_cell_actions=[
                MergedCellAction(range_str="A2:H2", action="treat_as_header"),
                MergedCellAction(range_str="C5:C10", action="fill_down"),
            ],
        )
        s = CleaningStrategy.from_decision(decision)
        assert len(s.merged_cell_actions) == 2
        assert s.merged_cell_actions[0].action == "treat_as_header"

    def test_mixed_type_passthrough(self):
        decision = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[ColumnSemantic("A", "x", "id")],
            mixed_type_handling=[
                MixedTypeAction(col_letter="F", action="extract_unit_number", unit="kg"),
            ],
        )
        s = CleaningStrategy.from_decision(decision)
        assert len(s.mixed_type_handling) == 1
        assert s.mixed_type_handling[0].unit == "kg"

    def test_empty_row_policy_upgrade(self):
        """当 AI 决策保留某些空行时，policy 自动升级。"""
        decision = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[ColumnSemantic("A", "x", "id")],
            preserve_empty_rows=[
                EmptyRowDecision(row=105, reason="章节分隔"),
            ],
        )
        s = CleaningStrategy.from_decision(decision)
        assert s.empty_row_policy == "preserve_section_separators"
        assert s.preserve_empty_rows[0].row == 105

    def test_empty_row_policy_default_when_no_preserves(self):
        """无 preserve 时保持默认 strict_all_empty。"""
        decision = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[ColumnSemantic("A", "x", "id")],
            preserve_empty_rows=[],
        )
        s = CleaningStrategy.from_decision(decision)
        assert s.empty_row_policy == "strict_all_empty"


class TestFromDecisionEdgeCases:
    """边界场景。"""

    def test_all_columns_are_ids(self):
        decision = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[
                ColumnSemantic("A", "ID1", "id", is_id_column=True),
                ColumnSemantic("B", "ID2", "id", is_id_column=True),
            ],
        )
        s = CleaningStrategy.from_decision(decision)
        assert s.id_columns == ["ID1", "ID2"]

    def test_no_id_columns(self):
        decision = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[
                ColumnSemantic("A", "金额", "amount"),
                ColumnSemantic("B", "数量", "quantity"),
            ],
        )
        s = CleaningStrategy.from_decision(decision)
        assert s.id_columns == []

    def test_business_name_empty_skipped(self):
        """business_name 为空的列不进 column_mapping。"""
        decision = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[
                ColumnSemantic(letter="A", business_name="正常", semantic_type="id"),
                ColumnSemantic(letter="B", business_name="", semantic_type="other"),
            ],
        )
        s = CleaningStrategy.from_decision(decision)
        assert s.column_mapping == {"A": "正常"}

    def test_independent_lists(self):
        """from_decision 返回的列表是新的（修改不影响原 decision）。"""
        decision = AIDecision(
            header_row=1, data_start_row=2,
            column_semantics=[ColumnSemantic("A", "x", "id")],
            summary_rows=[100],
        )
        s = CleaningStrategy.from_decision(decision)
        s.summary_rows.append(200)
        # decision 原始数据不能被改
        assert decision.summary_rows == [100]


class TestSerialization:
    """asdict 序列化。"""

    def test_strategy_asdict(self):
        decision = AIDecision(
            header_row=2, data_start_row=3,
            column_semantics=[
                ColumnSemantic("A", "订单号", "id", is_id_column=True),
            ],
            summary_rows=[501],
        )
        s = CleaningStrategy.from_decision(decision)
        d = asdict(s)
        assert d["empty_row_policy"] == "strict_all_empty"
        assert d["id_columns"] == ["订单号"]
        assert d["column_mapping"] == {"A": "订单号"}
        assert d["summary_rows"] == [501]
