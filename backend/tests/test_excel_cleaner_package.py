"""excel_cleaner 包契约测试。

防御性测试：拆分为包目录后，外部模块通过 `from services.agent.excel_cleaner import X`
依赖一组公共 + 私有 API。本测试锁定外部契约，防止未来修改 __init__.py 时
漏导出导致 7 处外部 import 立即破裂。

外部实际引用清单（来自全代码库 grep）：
  - 主流程: data_query_cache / file_meta/* / file_scanners / table_region_detector
  - 测试:   test_excel_cleaner / test_cleaning_strategy_integration / test_file_meta
"""
from __future__ import annotations

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest


class TestPackageContract:
    """__init__.py 必须导出全部被外部使用的名字。"""

    def test_public_api_exported(self):
        """公共 API（外部业务代码依赖）。"""
        from services.agent import excel_cleaner as ec

        # 数据结构
        assert hasattr(ec, "CleaningReport"), "CleaningReport 未导出"
        assert hasattr(ec, "ExcelStructure"), "ExcelStructure 未导出"
        # 入口
        assert hasattr(ec, "clean_excel"), "clean_excel 未导出"
        # IO
        assert hasattr(ec, "read_cleaning_report"), "read_cleaning_report 未导出"
        assert hasattr(ec, "write_cleaning_report"), "write_cleaning_report 未导出"

    def test_private_api_exported(self):
        """私有 API（_ 前缀，但仍被外部 import）。"""
        from services.agent import excel_cleaner as ec

        # data_query_cache 用
        assert hasattr(ec, "_detect_structure"), "_detect_structure 未导出"
        # file_meta/formulas.py 用
        assert hasattr(ec, "_parse_sheet_tags"), "_parse_sheet_tags 未导出"
        assert hasattr(ec, "_resolve_sheet_xml_path"), "_resolve_sheet_xml_path 未导出"
        # test_excel_cleaner 用
        assert hasattr(ec, "_col_letter_to_index"), "_col_letter_to_index 未导出"
        assert hasattr(ec, "_flatten_multi_header"), "_flatten_multi_header 未导出"

    def test_actions_are_internal(self):
        """清洗动作是 clean_excel 的内部实现，不在顶层导出。

        如需直接调用，必须走 actions 子模块路径：
            from services.agent.excel_cleaner.actions import _apply_merge_fill
        """
        from services.agent import excel_cleaner as ec
        from services.agent.excel_cleaner import actions

        internal_actions = (
            "_apply_merge_fill",
            "_coerce_object_columns",
            "_deduplicate_columns",
            "_fix_int_columns",
            "_mark_hidden_cols",
            "_mark_summary_rows",
            "_mark_summary_rows_from_strategy",
            "_remove_empty_rows_cols",
        )
        for name in internal_actions:
            # 顶层 namespace 不应有
            assert not hasattr(ec, name), \
                f"{name} 是内部 action，不应出现在 excel_cleaner 顶层"
            # 但 actions 子模块必须有（防止未来误删）
            assert hasattr(actions, name), \
                f"{name} 应在 excel_cleaner.actions 子模块中"


class TestExternalImportPatterns:
    """模拟外部模块的真实 import 模式，确保兼容。"""

    def test_data_query_cache_pattern(self):
        """data_query_cache.py 的 import 模式。"""
        from services.agent.excel_cleaner import (
            CleaningReport,
            clean_excel,
            write_cleaning_report,
        )
        from services.agent.excel_cleaner import _detect_structure
        assert all([CleaningReport, clean_excel, write_cleaning_report, _detect_structure])

    def test_file_meta_builders_pattern(self):
        """file_meta/builders.py 的 import 模式。"""
        from services.agent.excel_cleaner import CleaningReport
        assert CleaningReport is not None

    def test_file_meta_formulas_pattern(self):
        """file_meta/formulas.py 的 import 模式。"""
        from services.agent.excel_cleaner import _parse_sheet_tags, _resolve_sheet_xml_path
        assert all([_parse_sheet_tags, _resolve_sheet_xml_path])

    def test_file_scanners_pattern(self):
        """file_scanners.py 的 import 模式。"""
        from services.agent.excel_cleaner import _detect_structure
        assert _detect_structure is not None

    def test_table_region_detector_pattern(self):
        """table_region_detector.py 的 import 模式。"""
        from services.agent.excel_cleaner import (
            CleaningReport, clean_excel, write_cleaning_report,
        )
        assert all([CleaningReport, clean_excel, write_cleaning_report])


class TestSubmoduleDirectImports:
    """允许直接从子模块 import（actions.py / core.py 内部用）。"""

    def test_strategy_helpers_subpath(self):
        from services.agent.excel_cleaner._strategy_helpers import (
            _strategy_id_columns,
            _strategy_summary_rows,
            _strategy_mixed_handling,
            _strategy_preserve_rows,
            _strategy_merge_actions,
        )
        # 所有 5 个 helper 应可调用
        assert _strategy_summary_rows(None) == []
        assert _strategy_id_columns(None) == set()
        assert _strategy_mixed_handling(None) == {}
        assert _strategy_preserve_rows(None) == set()
        assert _strategy_merge_actions(None) == {}

    def test_report_submodule(self):
        from services.agent.excel_cleaner.report import (
            CleaningReport, ExcelStructure, _dedup_issues,
        )
        # 实例化
        r = CleaningReport()
        s = ExcelStructure()
        # _dedup_issues 工作正常
        assert _dedup_issues([]) == []

    def test_structure_submodule(self):
        from services.agent.excel_cleaner.structure import (
            _col_index_to_letter_local, _col_letter_to_index,
        )
        assert _col_index_to_letter_local(0) == "A"
        assert _col_letter_to_index("A") == 1

    def test_actions_submodule(self):
        from services.agent.excel_cleaner.actions import (
            _flatten_multi_header,
            _apply_merge_fill,
            _mark_summary_rows,
        )
        assert all([_flatten_multi_header, _apply_merge_fill, _mark_summary_rows])

    def test_core_submodule(self):
        from services.agent.excel_cleaner.core import clean_excel
        assert clean_excel is not None


class TestPackageHealth:
    """包健康检查：__all__ 完整、无循环导入。"""

    def test_all_attribute_present(self):
        """__init__.py 含 __all__，避免 import * 污染。"""
        from services.agent import excel_cleaner
        assert hasattr(excel_cleaner, "__all__"), "__init__.py 缺 __all__"
        assert isinstance(excel_cleaner.__all__, list)
        assert len(excel_cleaner.__all__) > 0

    def test_no_legacy_file_remains(self):
        """旧单文件 excel_cleaner.py 必须已删除。"""
        legacy = Path(backend_dir) / "services" / "agent" / "excel_cleaner.py"
        assert not legacy.exists(), \
            f"旧 excel_cleaner.py 仍存在于 {legacy}（应已迁移到包目录）"

    def test_package_is_dir(self):
        """excel_cleaner 必须是包目录（含 __init__.py）。"""
        pkg = Path(backend_dir) / "services" / "agent" / "excel_cleaner"
        assert pkg.is_dir()
        assert (pkg / "__init__.py").exists()

    def test_import_does_not_warn(self):
        """import 全包不应产生 DeprecationWarning（保证健康）。"""
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            # 触发完整 init 流程
            from services.agent import excel_cleaner as _ec
            assert _ec is not None
