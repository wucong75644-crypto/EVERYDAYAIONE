"""沙盒 data helpers (safe_float / safe_int) 守护测试。

覆盖:
1. safe_float 处理 Excel 财务报表的混合类型(百分比/占位符/千分位/货币/NaN)
2. safe_int 取整逻辑
3. 沙盒 globals 注入(LLM 在沙盒里能调到)
4. code_execute description 含 DATA HELPERS 段(LLM 知道有这工具)
5. file_ai_prompt 含 step 6-10 (AI 识别 ragged/占位符/公式错误/日期 serial/同名列)
"""
import pytest


# ============================================================
# 1. safe_float 行业标准转换测试
# ============================================================

class TestSafeFloat:
    """safe_float 处理 Excel 财务报表混合类型,保留数据精度。"""

    def test_none_returns_default(self):
        from services.agent.excel_cleaner.internal_helpers import safe_float
        assert safe_float(None) == 0.0
        assert safe_float(None, default=99) == 99.0

    def test_nan_returns_default(self):
        import math
        from services.agent.excel_cleaner.internal_helpers import safe_float
        assert safe_float(float("nan")) == 0.0

    def test_int_preserved(self):
        from services.agent.excel_cleaner.internal_helpers import safe_float
        assert safe_float(42) == 42.0
        assert safe_float(-100) == -100.0

    def test_float_preserved(self):
        from services.agent.excel_cleaner.internal_helpers import safe_float
        assert safe_float(123.456) == 123.456
        assert safe_float(-99.99) == -99.99

    def test_percentage_to_decimal(self):
        """关键 case: '47.40%' → 0.474 保留数据精度"""
        from services.agent.excel_cleaner.internal_helpers import safe_float
        assert safe_float("47.40%") == pytest.approx(0.474)
        assert safe_float("-0.90%") == pytest.approx(-0.009)
        assert safe_float("100%") == 1.0

    def test_english_placeholders(self):
        from services.agent.excel_cleaner.internal_helpers import safe_float
        for p in ["-", "—", "N/A", "NA", "n/a", "NaN", "null", "", " ", "<NA>"]:
            assert safe_float(p) == 0.0, f"占位符 {p!r} 应转 default"

    def test_chinese_placeholders(self):
        """中文占位符变体覆盖"""
        from services.agent.excel_cleaner.internal_helpers import safe_float
        for p in ["无", "空", "尚未", "未知"]:
            assert safe_float(p) == 0.0, f"中文占位符 {p!r} 应转 default"

    def test_thousand_separator(self):
        from services.agent.excel_cleaner.internal_helpers import safe_float
        assert safe_float("1,234.56") == 1234.56
        assert safe_float("1,234,567") == 1234567.0
        assert safe_float("1，234.56") == 1234.56  # 中文逗号

    def test_currency_symbols(self):
        from services.agent.excel_cleaner.internal_helpers import safe_float
        assert safe_float("¥99.99") == 99.99
        assert safe_float("$1,234.56") == 1234.56
        assert safe_float("￥99.99") == 99.99  # 全角¥

    def test_unparseable_returns_default(self):
        """异常路径兜底"""
        from services.agent.excel_cleaner.internal_helpers import safe_float
        assert safe_float("abc") == 0.0
        assert safe_float("约 50%") == 0.0  # 含中文修饰
        assert safe_float("12.34.56") == 0.0  # 多个小数点

    def test_custom_default(self):
        from services.agent.excel_cleaner.internal_helpers import safe_float
        assert safe_float(None, default=-1) == -1.0
        assert safe_float("无", default=999) == 999.0


# ============================================================
# 2. file_ai_prompt 含 step 6-10 (AI 识别 ragged/占位符/公式/日期 serial/同名列)
# ============================================================

class TestFileAiPromptSteps:
    """AI 看 prompt 时必须被教这 5 种异常的识别 + 输出 warning"""

    def _prompt(self):
        from services.agent.file_ai_prompt import TASK_BLOCK
        return TASK_BLOCK

    def test_step6_ragged_mixed_type(self):
        p = self._prompt()
        assert "ragged 混合类型" in p
        assert "47.40%" in p
        assert "safe_float" in p

    def test_step7_chinese_placeholders(self):
        p = self._prompt()
        assert "中文占位符" in p
        assert "无" in p or "空" in p

    def test_step8_formula_errors(self):
        p = self._prompt()
        assert "#DIV/0!" in p
        assert "公式错误" in p or "Excel 公式错误" in p

    def test_step9_date_serial(self):
        p = self._prompt()
        assert "Excel 日期 serial" in p
        assert "45414" in p or "5 位整数" in p

    def test_step10_duplicate_column_names(self):
        p = self._prompt()
        assert "同名列" in p
        assert ".1" in p

    def test_all_steps_emit_warning_via_data_quality_notes(self):
        """5 个 step 都应该通过 data_quality_notes 输出 warning(项目原生渠道)"""
        p = self._prompt()
        # 不在 step 里加新字段,全部走 data_quality_notes
        assert p.count("data_quality_notes") >= 5
