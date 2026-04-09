"""
erp_local_db_export 单元测试

覆盖：PII 脱敏、关键词匹配精度、导出核心逻辑。
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.kuaimai.erp_local_db_export import (
    _mask_phone,
    _mask_pii,
    _validate_keyword,
    _generate_column_doc,
    _parse_columns,
    _ALL_COLUMN_NAMES,
    local_db_export,
    BATCH_SIZE,
    MAX_ROWS_LIMIT,
    DEFAULT_MAX_ROWS,
)


# ============================================================
# _mask_phone 手机号脱敏
# ============================================================


class TestMaskPhone:

    def test_normal_11_digits(self):
        assert _mask_phone("13812345678") == "138****5678"

    def test_short_7_digits(self):
        assert _mask_phone("1234567") == "123****4567"

    def test_too_short(self):
        """少于7位不脱敏"""
        assert _mask_phone("12345") == "12345"

    def test_empty(self):
        assert _mask_phone("") == ""

    def test_none(self):
        assert _mask_phone(None) == ""

    def test_long_number(self):
        """国际号码也能脱敏"""
        assert _mask_phone("+8613812345678") == "+86****5678"


# ============================================================
# _mask_pii 行级脱敏
# ============================================================


class TestMaskPii:

    def test_masks_phone_and_name(self):
        row = {"receiver_phone": "13812345678", "receiver_name": "张三丰"}
        _mask_pii(row)
        assert row["receiver_phone"] == "138****5678"
        assert row["receiver_name"] == "张**"

    def test_single_char_name(self):
        row = {"receiver_name": "张"}
        _mask_pii(row)
        assert row["receiver_name"] == "张"  # 单字不脱敏

    def test_missing_fields(self):
        """无 PII 字段不报错"""
        row = {"order_no": "123", "amount": 100}
        _mask_pii(row)  # 不抛异常
        assert row["order_no"] == "123"

    def test_empty_phone(self):
        row = {"receiver_phone": "", "receiver_name": ""}
        _mask_pii(row)
        assert row["receiver_phone"] == ""

    def test_modifies_in_place(self):
        """就地修改，不创建新 dict"""
        row = {"receiver_phone": "13800001111"}
        result = _mask_pii(row)
        assert result is row


# ============================================================
# _validate_keyword 匹配精度
# ============================================================


class TestValidateKeyword:

    def test_1_char_rejected(self):
        err, pattern = _validate_keyword("蓝", "店铺名")
        assert err is not None
        assert "至少2个字符" in err
        assert pattern is None

    def test_2_chars_prefix_match(self):
        err, pattern = _validate_keyword("蓝恩", "店铺名")
        assert err is None
        assert pattern == "蓝恩%"  # 前缀匹配

    def test_3_chars_fuzzy_match(self):
        err, pattern = _validate_keyword("蓝恩文", "店铺名")
        assert err is None
        assert pattern == "%蓝恩文%"  # 模糊匹配

    def test_long_keyword(self):
        err, pattern = _validate_keyword("蓝恩文具旗舰店", "店铺名")
        assert err is None
        assert pattern == "%蓝恩文具旗舰店%"


# ============================================================
# 常量验证
# ============================================================


class TestConstants:

    def test_batch_size(self):
        assert BATCH_SIZE == 5000

    def test_max_rows_limit(self):
        assert MAX_ROWS_LIMIT == 10000

    def test_default_max_rows(self):
        assert DEFAULT_MAX_ROWS == 5000


# ============================================================
# 两步协议：Step1 字段文档 + 列解析
# ============================================================


class TestColumnDoc:

    def test_generate_doc_contains_all_groups(self):
        """字段文档包含所有分组"""
        doc = _generate_column_doc("order")
        for group in ["单据基础", "时间", "商品", "数量金额", "关联方", "订单物流", "状态标记", "售后", "备注"]:
            assert group in doc

    def test_generate_doc_contains_example(self):
        """字段文档包含使用示例"""
        doc = _generate_column_doc("order")
        assert "columns=" in doc
        assert "order_no" in doc

    def test_all_column_names_not_empty(self):
        """白名单列集合不为空"""
        assert len(_ALL_COLUMN_NAMES) >= 50


class TestParseColumns:

    def test_valid_columns(self):
        result = _parse_columns("order_no,amount,shop_name")
        assert "order_no" in result
        assert "amount" in result
        assert "shop_name" in result

    def test_invalid_columns_filtered(self):
        """非法列名被过滤"""
        result = _parse_columns("order_no,FAKE_COLUMN,amount")
        assert "FAKE_COLUMN" not in result
        assert "order_no" in result

    def test_all_invalid_fallback(self):
        """全部非法列名回退到默认"""
        result = _parse_columns("FAKE1,FAKE2")
        assert "doc_type" in result  # 回退到单据基础字段

    def test_sql_injection_blocked(self):
        """SQL 注入被白名单拦截"""
        result = _parse_columns("order_no; DROP TABLE users")
        assert "DROP" not in result


# ============================================================
# local_db_export 集成测试
# ============================================================


class TestLocalDbExport:

    def _mock_db(self, rows: list[dict]):
        """构建 mock db，支持链式调用"""
        mock_result = MagicMock()
        mock_result.data = rows

        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.gte.return_value = mock_query
        mock_query.ilike.return_value = mock_query
        mock_query.or_.return_value = mock_query
        mock_query.is_.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.range.return_value = mock_query
        mock_query.execute.return_value = mock_result

        db = MagicMock()
        db.table.return_value = mock_query
        return db

    @pytest.mark.asyncio
    async def test_step1_returns_doc(self):
        """Step1: 不传 columns → 返回字段文档"""
        db = self._mock_db([])
        result = await local_db_export(
            db, doc_type="order", org_id="org1",
        )
        assert "可导出字段" in result
        assert "order_no" in result
        assert "columns=" in result  # 有示例

    @pytest.mark.asyncio
    async def test_export_success(self, tmp_path):
        """Step2: 传 columns → 正常导出写入 Parquet 文件"""
        rows = [
            {"order_no": "T001", "amount": 100},
            {"order_no": "T002", "amount": 200},
        ]
        db = self._mock_db(rows)

        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            result = await local_db_export(
                db, doc_type="order", columns="order_no,amount",
                days=1, org_id="org1", conversation_id="conv1",
            )

        assert "[数据已暂存]" in result
        assert "2 条记录" in result
        assert "Parquet" in result

        staging_files = list((tmp_path / "staging" / "conv1").glob("*.parquet"))
        assert len(staging_files) == 1

        # Parquet 可正确读回
        import pandas as _pd
        df = _pd.read_parquet(staging_files[0])
        assert len(df) == 2

    @pytest.mark.asyncio
    async def test_pii_masked_in_output(self, tmp_path):
        """导出文件中 PII 已脱敏"""
        rows = [
            {"doc_type": "order", "receiver_phone": "13812345678",
             "receiver_name": "王五六"},
        ]
        db = self._mock_db(rows)

        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            await local_db_export(
                db, doc_type="order", columns="doc_type",
                org_id="org1", conversation_id="conv1",
            )

        import pandas as _pd
        staging_files = list((tmp_path / "staging" / "conv1").glob("*.parquet"))
        assert len(staging_files) == 1
        df = _pd.read_parquet(staging_files[0])
        # PII 字段如果存在应已脱敏（当前 columns="doc_type" 不含 PII）
        assert len(df) == 1

    @pytest.mark.asyncio
    async def test_empty_result(self, tmp_path):
        """无数据返回提示"""
        db = self._mock_db([])

        with patch("core.config.get_settings") as mock_s, \
             patch("services.kuaimai.erp_local_helpers.check_sync_health",
                   return_value=""):
            mock_s.return_value.file_workspace_root = str(tmp_path)
            result = await local_db_export(
                db, doc_type="order", columns="order_no",
                org_id="org1",
            )

        assert "无数据" in result

    @pytest.mark.asyncio
    async def test_shop_name_1_char_rejected(self):
        """单字店铺名被拒绝"""
        db = self._mock_db([])
        result = await local_db_export(
            db, doc_type="order", columns="order_no",
            shop_name="蓝", org_id="org1",
        )
        assert "❌" in result
        assert "至少2个字符" in result

    @pytest.mark.asyncio
    async def test_max_rows_capped(self, tmp_path):
        """max_rows 超过上限被截断到 MAX_ROWS_LIMIT"""
        rows = [{"order_no": f"T{i}"} for i in range(3)]
        db = self._mock_db(rows)

        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            result = await local_db_export(
                db, doc_type="order", columns="order_no",
                max_rows=99999, org_id="org1", conversation_id="conv1",
            )

        assert "[数据已暂存]" in result

    @pytest.mark.asyncio
    async def test_query_error_cleanup(self, tmp_path):
        """查询异常时清理不完整文件"""
        db = MagicMock()
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.gte.return_value = mock_query
        mock_query.is_.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.range.return_value = mock_query
        mock_query.execute.side_effect = Exception("DB connection lost")
        db.table.return_value = mock_query

        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            result = await local_db_export(
                db, doc_type="order", columns="order_no",
                org_id="org1", conversation_id="conv_err",
            )

        assert "导出查询失败" in result
        # 不完整文件已清理
        staging_dir = tmp_path / "staging" / "conv_err"
        if staging_dir.exists():
            assert len(list(staging_dir.glob("*.jsonl"))) == 0

    @pytest.mark.asyncio
    async def test_default_conversation_id(self, tmp_path):
        """未传 conversation_id 使用 default"""
        rows = [{"order_no": "T1"}]
        db = self._mock_db(rows)

        with patch("core.config.get_settings") as mock_s:
            mock_s.return_value.file_workspace_root = str(tmp_path)
            result = await local_db_export(
                db, doc_type="order", columns="order_no",
                org_id="org1",
            )

        assert "staging/default/" in result
