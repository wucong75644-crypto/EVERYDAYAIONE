"""erp_field_translator.py 单元测试——字段翻译 + PII 脱敏。"""
import sys
from pathlib import Path

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


class TestTranslateRow:

    def _translate(self, row: dict) -> dict:
        from services.kuaimai.erp_field_translator import translate_row
        return translate_row(row)

    def test_platform_tb_to_chinese(self):
        assert self._translate({"platform": "tb"})["platform"] == "淘宝"

    def test_platform_pdd(self):
        assert self._translate({"platform": "pdd"})["platform"] == "拼多多"

    def test_platform_unknown_passthrough(self):
        assert self._translate({"platform": "xyz"})["platform"] == "xyz"

    def test_platform_none_unchanged(self):
        assert self._translate({"platform": None})["platform"] is None

    def test_doc_type_order(self):
        assert self._translate({"doc_type": "order"})["doc_type"] == "订单"

    def test_doc_status_finished(self):
        assert self._translate({"doc_status": "FINISH"})["doc_status"] == "已完成"

    def test_order_status_wait_send(self):
        assert self._translate({"order_status": "WAIT_SEND"})["order_status"] == "待发货"

    def test_order_type_composite(self):
        """逗号分隔多值: "0,14" → "普通/补发"。"""
        row = self._translate({"order_type": "0,14"})
        assert row["order_type"] == "普通/补发"

    def test_order_type_single(self):
        assert self._translate({"order_type": "0"})["order_type"] == "普通"

    def test_order_type_99_is_delivery_note(self):
        """99 → 出库单（在映射表中有定义）。"""
        row = self._translate({"order_type": "99"})
        assert row["order_type"] == "出库单"

    def test_order_type_truly_unknown(self):
        """真正未知的数字保留原值。"""
        row = self._translate({"order_type": "999"})
        assert row["order_type"] == "999"

    def test_aftersale_type_refund(self):
        assert self._translate({"aftersale_type": 1})["aftersale_type"] == "退款"
        assert self._translate({"aftersale_type": "2"})["aftersale_type"] == "退货"

    def test_refund_status_success(self):
        assert self._translate({"refund_status": "2"})["refund_status"] == "退款成功"

    def test_good_status(self):
        assert self._translate({"good_status": "3"})["good_status"] == "卖家已收"

    def test_bool_fields(self):
        row = self._translate({"is_cancel": 1, "is_refund": 0, "is_urgent": True})
        assert row["is_cancel"] == "是"
        assert row["is_refund"] == "否"
        assert row["is_urgent"] == "是"

    def test_bool_string_one(self):
        assert self._translate({"is_halt": "1"})["is_halt"] == "是"

    def test_bool_none_unchanged(self):
        row = self._translate({"is_cancel": None})
        assert row["is_cancel"] is None

    def test_empty_row(self):
        assert self._translate({}) == {}

    def test_mixed_fields(self):
        row = self._translate({
            "platform": "jd", "doc_status": "SEND",
            "is_refund": 1, "amount": 100,
        })
        assert row["platform"] == "京东"
        assert row["doc_status"] == "已发货"
        assert row["is_refund"] == "是"
        assert row["amount"] == 100  # 非翻译字段不动


class TestTranslateRows:

    def test_batch(self):
        from services.kuaimai.erp_field_translator import translate_rows
        rows = [{"platform": "tb"}, {"platform": "pdd"}]
        result = translate_rows(rows)
        assert result[0]["platform"] == "淘宝"
        assert result[1]["platform"] == "拼多多"
        assert result is rows  # 就地修改
