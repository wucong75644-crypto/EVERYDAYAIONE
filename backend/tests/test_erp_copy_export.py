"""erp_copy_export.py 单元测试——PII 脱敏 + 字段翻译 + WHERE 构建。"""
import sys
from pathlib import Path

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


class TestMaskPiiValue:

    def _mask(self, field, value):
        from services.kuaimai.erp_copy_export import _mask_pii_value
        return _mask_pii_value(field, value)

    def test_name_masked(self):
        assert self._mask("receiver_name", "张三丰") == "张**"

    def test_name_short(self):
        assert self._mask("receiver_name", "张") == "张"

    def test_mobile_masked(self):
        assert self._mask("receiver_mobile", "13800138000") == "138****8000"

    def test_phone_masked(self):
        assert self._mask("receiver_phone", "0571-88888888") == "057****8888"

    def test_address_masked(self):
        assert self._mask("receiver_address", "浙江省杭州市西湖区文三路100号") == "浙江省杭州市****"

    def test_none_passthrough(self):
        assert self._mask("receiver_name", None) is None

    def test_non_pii_field(self):
        assert self._mask("order_no", "12345") == "12345"

    def test_short_mobile_passthrough(self):
        assert self._mask("receiver_mobile", "12345") == "12345"


class TestTranslateRow:

    def _translate(self, row):
        from services.kuaimai.erp_copy_export import _translate_row
        return _translate_row(row)

    def test_platform(self):
        assert self._translate({"platform": "tb"})["platform"] == "淘宝"

    def test_order_type_composite(self):
        assert self._translate({"order_type": "0,14"})["order_type"] == "普通/补发"

    def test_bool_field(self):
        assert self._translate({"is_cancel": 1})["is_cancel"] == "是"
        assert self._translate({"is_cancel": 0})["is_cancel"] == "否"


class TestBuildCopyWhere:

    def _build(self, doc_type="order", org_id="org-1"):
        from services.kuaimai.erp_copy_export import _build_copy_where
        from services.kuaimai.erp_unified_schema import TimeRange, ValidatedFilter
        tr = TimeRange(
            start_iso="2026-04-01", end_iso="2026-04-28",
            time_col="doc_created_at", label="", date_range="",
        )
        filters = [ValidatedFilter(field="platform", op="eq", value="tb", col_type="text")]
        return _build_copy_where(doc_type, filters, tr, org_id)

    def test_contains_doc_type(self):
        where = self._build()
        assert "doc_type = 'order'" in where

    def test_contains_org_id(self):
        where = self._build()
        assert "org_id = 'org-1'" in where

    def test_contains_time_range(self):
        where = self._build()
        assert "doc_created_at >=" in where
        assert "doc_created_at <" in where

    def test_contains_filter(self):
        where = self._build()
        assert "platform = 'tb'" in where

    def test_sql_injection_escaped(self):
        """单引号转义防 SQL 注入。"""
        from services.kuaimai.erp_copy_export import _build_copy_where
        from services.kuaimai.erp_unified_schema import TimeRange, ValidatedFilter
        tr = TimeRange(
            start_iso="2026-04-01", end_iso="2026-04-28",
            time_col="doc_created_at", label="", date_range="",
        )
        filters = [ValidatedFilter(field="shop_name", op="eq", value="O'Reilly", col_type="text")]
        where = _build_copy_where("order", filters, tr, "org-1")
        assert "O''Reilly" in where


class TestNeedArchive:

    def test_recent_no_archive(self):
        from services.kuaimai.erp_copy_export import _need_archive
        from services.kuaimai.erp_unified_schema import TimeRange
        tr = TimeRange(
            start_iso="2026-04-01", end_iso="2026-04-28",
            time_col="doc_created_at", label="", date_range="",
        )
        assert not _need_archive(tr)

    def test_old_needs_archive(self):
        from services.kuaimai.erp_copy_export import _need_archive
        from services.kuaimai.erp_unified_schema import TimeRange
        tr = TimeRange(
            start_iso="2025-01-01", end_iso="2025-02-01",
            time_col="doc_created_at", label="", date_range="",
        )
        assert _need_archive(tr)
