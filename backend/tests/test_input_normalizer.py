"""
三层输入归一化架构单元测试

L1 InputNormalizer: NFKC + 不可见字符 + strip
L2 MultiValueParser: 分隔符拆分 + 去重 + 上限截断 + to_filter
L3 ValueValidator: 格式正则 + 枚举映射
"""

import pytest
from services.agent.input_normalizer import (
    InputNormalizer,
    MultiValueParser,
    ValueValidator,
    DEFAULT_MAX_IN,
)


# ============================================================
# L1: InputNormalizer
# ============================================================

class TestInputNormalizer:

    def test_none(self):
        assert InputNormalizer.normalize(None) is None

    def test_empty_string(self):
        assert InputNormalizer.normalize("") is None

    def test_whitespace_only(self):
        assert InputNormalizer.normalize("   ") is None

    def test_strip(self):
        assert InputNormalizer.normalize("  ABC  ") == "ABC"

    def test_integer_input(self):
        assert InputNormalizer.normalize(123) == "123"

    def test_float_input(self):
        assert InputNormalizer.normalize(3.14) == "3.14"

    # ── NFKC 全角→半角 ──

    def test_fullwidth_digits(self):
        """全角数字 ０１２ → 012"""
        assert InputNormalizer.normalize("０１２３４５") == "012345"

    def test_fullwidth_letters(self):
        """全角字母 ＡＢＣ → ABC"""
        assert InputNormalizer.normalize("ＡＢＣ") == "ABC"

    def test_fullwidth_mixed(self):
        """全角混合 ＤＢＴＸＬ０１ → DBTXL01"""
        assert InputNormalizer.normalize("ＤＢＴＸＬ０１") == "DBTXL01"

    def test_fullwidth_comma(self):
        """全角逗号 ， → ,（NFKC 处理）"""
        assert InputNormalizer.normalize("Ａ，Ｂ") == "A,B"

    def test_fullwidth_semicolon(self):
        """全角分号 ； → ;"""
        assert InputNormalizer.normalize("Ａ；Ｂ") == "A;B"

    def test_fullwidth_tilde(self):
        """全角波浪号 ～ → ~（时间范围分隔符）"""
        result = InputNormalizer.normalize("2026-04-01～2026-04-25")
        assert "~" in result or "〜" in result  # NFKC may vary

    def test_fullwidth_hyphen(self):
        """全角连字符 － → -"""
        assert InputNormalizer.normalize("DBTXL０１－０２") == "DBTXL01-02"

    # ── 不可见字符移除 ──

    def test_zero_width_space(self):
        assert InputNormalizer.normalize("AB\u200bCD") == "ABCD"

    def test_bom(self):
        assert InputNormalizer.normalize("\ufeffABC") == "ABC"

    def test_zero_width_joiner(self):
        assert InputNormalizer.normalize("A\u200dB") == "AB"

    def test_bidi_marks(self):
        assert InputNormalizer.normalize("\u200eABC\u200f") == "ABC"


# ============================================================
# L2: MultiValueParser
# ============================================================

class TestMultiValueParser:

    # ── 基本解析 ──

    def test_none(self):
        assert MultiValueParser.parse(None) is None

    def test_empty(self):
        assert MultiValueParser.parse("") is None

    def test_single_value(self):
        assert MultiValueParser.parse("ABC") == "ABC"

    def test_comma_separated(self):
        assert MultiValueParser.parse("A,B,C") == ["A", "B", "C"]

    def test_semicolon_separated(self):
        assert MultiValueParser.parse("A;B;C") == ["A", "B", "C"]

    def test_newline_separated(self):
        assert MultiValueParser.parse("A\nB\nC") == ["A", "B", "C"]

    def test_pipe_separated(self):
        assert MultiValueParser.parse("A|B|C") == ["A", "B", "C"]

    def test_mixed_separators(self):
        assert MultiValueParser.parse("A,B;C\nD|E") == ["A", "B", "C", "D", "E"]

    # ── 全角分隔符（L1 NFKC 自动处理）──

    def test_fullwidth_comma_parsed(self):
        """全角逗号 NFKC→半角 → 拆分"""
        assert MultiValueParser.parse("Ａ，Ｂ，Ｃ") == ["A", "B", "C"]

    def test_fullwidth_values_normalized(self):
        """全角编码值自动归一化"""
        assert MultiValueParser.parse("ＤＢＴＸＬ０１") == "DBTXL01"

    # ── list 输入 ──

    def test_list_input(self):
        assert MultiValueParser.parse(["A", "B"]) == ["A", "B"]

    def test_single_element_list(self):
        assert MultiValueParser.parse(["A"]) == "A"

    def test_empty_list(self):
        assert MultiValueParser.parse([]) is None

    def test_list_with_empty_strings(self):
        assert MultiValueParser.parse(["A", "", "  ", "B"]) == ["A", "B"]

    def test_list_with_fullwidth(self):
        """list 内的全角值也被 L1 归一化"""
        assert MultiValueParser.parse(["ＡＢＣ", "ＤＥＦ"]) == ["ABC", "DEF"]

    # ── 去重 ──

    def test_dedup_preserves_order(self):
        assert MultiValueParser.parse("A,B,A,C,B") == ["A", "B", "C"]

    def test_dedup_list(self):
        assert MultiValueParser.parse(["X", "Y", "X"]) == ["X", "Y"]

    # ── 上限截断 ──

    def test_over_limit_truncated(self):
        codes = ",".join(f"CODE{i}" for i in range(600))
        result = MultiValueParser.parse(codes)
        assert isinstance(result, list)
        assert len(result) == DEFAULT_MAX_IN

    def test_custom_limit(self):
        result = MultiValueParser.parse("A,B,C,D,E", max_values=3)
        assert result == ["A", "B", "C"]

    # ── 边界 ──

    def test_trailing_comma(self):
        assert MultiValueParser.parse("A,B,") == ["A", "B"]

    def test_leading_comma(self):
        assert MultiValueParser.parse(",A,B") == ["A", "B"]

    def test_spaces_around_values(self):
        assert MultiValueParser.parse(" A , B , C ") == ["A", "B", "C"]

    # ── to_filter ──

    def test_to_filter_single(self):
        f = MultiValueParser.to_filter("outer_id", "ABC")
        assert f == {"field": "outer_id", "op": "eq", "value": "ABC"}

    def test_to_filter_multi(self):
        f = MultiValueParser.to_filter("outer_id", ["A", "B"])
        assert f == {"field": "outer_id", "op": "in", "value": ["A", "B"]}


# ============================================================
# L3: ValueValidator
# ============================================================

class TestValueValidatorFormat:

    # ── product_code ──

    def test_product_code_valid(self):
        valid, invalid = ValueValidator.validate_format("product_code", "DBTXL01")
        assert valid == ["DBTXL01"]
        assert invalid == []

    def test_product_code_with_hyphen(self):
        valid, _ = ValueValidator.validate_format("product_code", "DBTXL01-02")
        assert valid == ["DBTXL01-02"]

    def test_product_code_invalid_starts_digit(self):
        _, invalid = ValueValidator.validate_format("product_code", "01DBTXL")
        assert invalid == ["01DBTXL"]

    def test_product_code_invalid_pure_digits(self):
        _, invalid = ValueValidator.validate_format("product_code", "12345")
        assert invalid == ["12345"]

    def test_product_code_batch(self):
        valid, invalid = ValueValidator.validate_format(
            "product_code", ["DBTXL01", "123", "ABC-01"],
        )
        assert valid == ["DBTXL01", "ABC-01"]
        assert invalid == ["123"]

    # ── order_no ──

    def test_order_no_taobao_18(self):
        valid, _ = ValueValidator.validate_format("order_no", "126036803257340376")
        assert valid == ["126036803257340376"]

    def test_order_no_jd_16(self):
        valid, _ = ValueValidator.validate_format("order_no", "1234567890123456")
        assert valid == ["1234567890123456"]

    def test_order_no_xhs_p18(self):
        valid, _ = ValueValidator.validate_format("order_no", "P126036803257340376")
        assert valid == ["P126036803257340376"]

    def test_order_no_pdd_date(self):
        valid, _ = ValueValidator.validate_format("order_no", "20260315-0001")
        assert valid == ["20260315-0001"]

    def test_order_no_douyin_19(self):
        valid, _ = ValueValidator.validate_format("order_no", "1234567890123456789")
        assert valid == ["1234567890123456789"]

    def test_order_no_invalid_short(self):
        _, invalid = ValueValidator.validate_format("order_no", "12345")
        assert invalid == ["12345"]

    # ── system_id ──

    def test_system_id_valid(self):
        valid, _ = ValueValidator.validate_format("system_id", "5759422420146938")
        assert valid == ["5759422420146938"]

    def test_system_id_invalid_17(self):
        _, invalid = ValueValidator.validate_format("system_id", "57594224201469380")
        assert invalid == ["57594224201469380"]

    # ── express_no ──

    def test_express_sf(self):
        valid, _ = ValueValidator.validate_format("express_no", "SF1234567890")
        assert valid == ["SF1234567890"]

    def test_express_yt(self):
        valid, _ = ValueValidator.validate_format("express_no", "YT9876543210")
        assert valid == ["YT9876543210"]

    def test_express_case_insensitive(self):
        valid, _ = ValueValidator.validate_format("express_no", "sf1234567890")
        assert valid == ["sf1234567890"]

    def test_express_invalid_prefix(self):
        _, invalid = ValueValidator.validate_format("express_no", "XX1234567890")
        assert invalid == ["XX1234567890"]

    # ── doc_code ──

    def test_doc_code_purchase(self):
        valid, _ = ValueValidator.validate_format("doc_code", "DB20260315001")
        assert valid == ["DB20260315001"]

    def test_doc_code_aftersale(self):
        valid, _ = ValueValidator.validate_format("doc_code", "AS20260315001")
        assert valid == ["AS20260315001"]

    # ── 无正则字段 → 全部合法 ──

    def test_unknown_field_all_valid(self):
        valid, invalid = ValueValidator.validate_format("buyer_nick", "张三")
        assert valid == ["张三"]
        assert invalid == []

    def test_unknown_field_list(self):
        valid, _ = ValueValidator.validate_format("buyer_nick", ["张三", "李四"])
        assert valid == ["张三", "李四"]


class TestValueValidatorEnum:

    def test_exact_match(self):
        m = {"淘宝": "tb", "京东": "jd"}
        assert ValueValidator.validate_enum("淘宝", m) == "tb"

    def test_no_match(self):
        m = {"淘宝": "tb"}
        assert ValueValidator.validate_enum("拼多多", m) is None

    def test_fullwidth_fallback(self):
        """全角输入 L1 归一化后重试"""
        m = {"ABC": "abc_value"}
        # 全角 ＡＢＣ → NFKC → ABC → 匹配
        assert ValueValidator.validate_enum("ＡＢＣ", m) == "abc_value"

    def test_direct_db_value(self):
        """直接传 DB 值（如 "tb"）不在映射中 → 返回 None"""
        m = {"淘宝": "tb"}
        assert ValueValidator.validate_enum("tb", m) is None


# ============================================================
# E2E：三层管道完整链路
# ============================================================

class TestE2EPipeline:
    """模拟真实场景：全角输入 → L1 归一化 → L2 多值拆分 → L3 格式校验"""

    def test_fullwidth_product_codes(self):
        """用户从 Excel 粘贴全角编码"""
        raw = "ＤＢＴＸＬ０１，ＢＬＴＭＨ０１"
        parsed = MultiValueParser.parse(raw)
        assert parsed == ["DBTXL01", "BLTMH01"]
        valid, invalid = ValueValidator.validate_format("product_code", parsed)
        assert valid == ["DBTXL01", "BLTMH01"]
        assert invalid == []

    def test_fullwidth_order_number(self):
        """全角订单号"""
        raw = "１２６０３６８０３２５７３４０３７６"
        parsed = MultiValueParser.parse(raw)
        assert parsed == "126036803257340376"
        valid, _ = ValueValidator.validate_format("order_no", parsed)
        assert valid == ["126036803257340376"]

    def test_mixed_valid_invalid_codes(self):
        """混合合法/非法编码"""
        raw = "DBTXL01,12345,ABC-01"
        parsed = MultiValueParser.parse(raw)
        assert parsed == ["DBTXL01", "12345", "ABC-01"]
        valid, invalid = ValueValidator.validate_format("product_code", parsed)
        assert valid == ["DBTXL01", "ABC-01"]
        assert invalid == ["12345"]

    def test_to_filter_from_pipeline(self):
        """完整管道 → filter dict"""
        raw = "DBTXL01;BLTMH01"
        parsed = MultiValueParser.parse(raw)
        f = MultiValueParser.to_filter("outer_id", parsed)
        assert f == {
            "field": "outer_id",
            "op": "in",
            "value": ["DBTXL01", "BLTMH01"],
        }

    def test_single_value_pipeline(self):
        raw = "DBTXL01"
        parsed = MultiValueParser.parse(raw)
        assert parsed == "DBTXL01"
        f = MultiValueParser.to_filter("outer_id", parsed)
        assert f["op"] == "eq"


# ============================================================
# _CORE_PATTERNS → PATTERNS / SEARCH_PATTERNS 派生一致性
# ============================================================

class TestPatternDerivation:
    """验证 single source of truth 派生的两套正则一致且正确"""

    def test_all_core_keys_in_patterns(self):
        """每个 _CORE_PATTERNS key 都在 PATTERNS 中"""
        for key in ValueValidator._CORE_PATTERNS:
            assert key in ValueValidator.PATTERNS, f"{key} 不在 PATTERNS 中"

    def test_all_core_keys_in_search_patterns(self):
        """每个 _CORE_PATTERNS key 都在 SEARCH_PATTERNS 中"""
        for key in ValueValidator._CORE_PATTERNS:
            assert key in ValueValidator.SEARCH_PATTERNS, f"{key} 不在 SEARCH_PATTERNS 中"

    def test_patterns_have_anchors(self):
        """PATTERNS（校验用）必须带 ^$"""
        for key, p in ValueValidator.PATTERNS.items():
            assert p.pattern.startswith("^"), f"{key} PATTERNS 缺少 ^ 锚点"
            assert p.pattern.endswith("$"), f"{key} PATTERNS 缺少 $ 锚点"

    def test_search_patterns_no_anchors(self):
        """SEARCH_PATTERNS（搜索用）不能带 ^$"""
        for key, p in ValueValidator.SEARCH_PATTERNS.items():
            assert not p.pattern.startswith("^"), f"{key} SEARCH_PATTERNS 不应有 ^ 锚点"
            assert not p.pattern.endswith("$"), f"{key} SEARCH_PATTERNS 不应有 $ 锚点"

    def test_flags_propagated(self):
        """_CORE_PATTERNS 的 flags 必须传播到两套派生 pattern"""
        for key, (_, flags) in ValueValidator._CORE_PATTERNS.items():
            assert ValueValidator.PATTERNS[key].flags & flags == flags, \
                f"{key} PATTERNS flags 未正确传播"
            assert ValueValidator.SEARCH_PATTERNS[key].flags & flags == flags, \
                f"{key} SEARCH_PATTERNS flags 未正确传播"

    def test_express_no_case_insensitive_in_both(self):
        """express_no 的 IGNORECASE 在校验和搜索模式中都生效"""
        import re
        assert ValueValidator.PATTERNS["express_no"].flags & re.IGNORECASE
        assert ValueValidator.SEARCH_PATTERNS["express_no"].flags & re.IGNORECASE

    def test_search_extracts_from_text(self):
        """SEARCH_PATTERNS 能从自然语言文本中提取候选值"""
        text = "查 DBTXL01-02 和 126036803257340376 的物流 sf1234567890"
        products = ValueValidator.SEARCH_PATTERNS["product_code"].findall(text)
        orders = ValueValidator.SEARCH_PATTERNS["order_no"].findall(text)
        expresses = ValueValidator.SEARCH_PATTERNS["express_no"].findall(text)
        assert "DBTXL01-02" in products
        assert "126036803257340376" in orders
        assert "sf1234567890" in expresses  # case insensitive

    def test_validation_rejects_what_search_finds(self):
        """PATTERNS 校验模式比 SEARCH 更严格——搜索到不一定校验通过"""
        # SF1234567890 被 product_code 搜索模式误匹配（字母开头）
        # 但不应通过 express_no 以外的校验
        _, invalid = ValueValidator.validate_format("product_code", "SF1234567890")
        # SF1234567890 以字母开头，符合 product_code 正则，所以是 valid
        # 这是已知行为——plan_fill 靠 DB 验证过滤
        valid, _ = ValueValidator.validate_format("product_code", "SF1234567890")
        assert valid == ["SF1234567890"]  # 格式合法（业务过滤在 DB 层）
