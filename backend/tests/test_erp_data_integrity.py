"""
ERP 数据完整性验证测试

覆盖：
1. SQL 迁移脚本语法校验
2. 分类引擎端到端链路（mock RPC → classifier → ToolOutput）
3. PlanBuilder 语义区分（include_invalid vs filters）
4. import 链完整性
5. Row builder 新字段映射完整性
"""

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


# ══════════════════════════════════════════════════════════
# 1. SQL 迁移脚本语法校验
# ══════════════════════════════════════════════════════════


class TestMigrationSQLSyntax:
    """校验 081/082/083 迁移脚本的结构完整性"""

    def test_081_has_hot_and_archive_tables(self):
        sql = (MIGRATIONS_DIR / "081_expand_order_aftersale_fields.sql").read_text()
        # 热表和归档表都要加列
        assert "erp_document_items ADD COLUMN" in sql
        assert "erp_document_items_archive ADD COLUMN" in sql

    def test_081_column_count_matches_spec(self):
        """热表加列数应该 >= 90（93列，减去已存在的 sku_properties_name）"""
        sql = (MIGRATIONS_DIR / "081_expand_order_aftersale_fields.sql").read_text()
        hot_adds = re.findall(
            r"ALTER TABLE erp_document_items ADD COLUMN", sql
        )
        archive_adds = re.findall(
            r"ALTER TABLE erp_document_items_archive ADD COLUMN", sql
        )
        assert len(hot_adds) >= 90, f"热表加列数={len(hot_adds)}，预期>=90"
        assert len(archive_adds) >= 90, f"归档表加列数={len(archive_adds)}，预期>=90"

    def test_081_hot_archive_column_parity(self):
        """热表和归档表的新增列应完全一致"""
        sql = (MIGRATIONS_DIR / "081_expand_order_aftersale_fields.sql").read_text()

        def extract_columns(table_prefix: str) -> set[str]:
            pattern = (
                rf"ALTER TABLE {table_prefix}\s+"
                r"ADD COLUMN IF NOT EXISTS\s+(\w+)"
            )
            return set(re.findall(pattern, sql))

        hot_cols = extract_columns("erp_document_items")
        archive_cols = extract_columns("erp_document_items_archive")
        # 热表可能有索引相关的额外操作，但列应该是归档表的超集
        missing_in_archive = hot_cols - archive_cols
        assert not missing_in_archive, f"归档表缺少列: {missing_in_archive}"

    def test_081_all_if_not_exists(self):
        """所有 ADD COLUMN 都必须带 IF NOT EXISTS"""
        sql = (MIGRATIONS_DIR / "081_expand_order_aftersale_fields.sql").read_text()
        bare_adds = re.findall(
            r"ADD COLUMN\s+(?!IF NOT EXISTS)", sql
        )
        assert len(bare_adds) == 0, f"发现 {len(bare_adds)} 处 ADD COLUMN 缺少 IF NOT EXISTS"

    def test_081_has_scalping_index(self):
        sql = (MIGRATIONS_DIR / "081_expand_order_aftersale_fields.sql").read_text()
        assert "idx_doc_items_scalping" in sql

    def test_081_no_gin_index(self):
        """一期不加 GIN 索引（trade_tags 只写不查）"""
        sql = (MIGRATIONS_DIR / "081_expand_order_aftersale_fields.sql").read_text()
        # GIN 索引行应该被注释掉
        uncommented_gin = re.findall(r"^(?!\s*--)\s*.*USING GIN", sql, re.MULTILINE)
        assert len(uncommented_gin) == 0, "发现未注释的 GIN 索引"

    def test_082_classification_rules_table(self):
        sql = (MIGRATIONS_DIR / "082_classification_rules.sql").read_text()
        assert "erp_classification_rules" in sql
        assert "org_id UUID NOT NULL" in sql
        assert "shop_id UUID DEFAULT NULL" in sql
        assert "conditions JSONB NOT NULL" in sql
        assert "priority SMALLINT" in sql

    def test_083_has_both_rpcs(self):
        sql = (MIGRATIONS_DIR / "083_rpc_grouped_stats.sql").read_text()
        assert "erp_global_stats_query" in sql
        assert "erp_order_stats_grouped" in sql

    def test_083_has_not_like_operator(self):
        sql = (MIGRATIONS_DIR / "083_rpc_grouped_stats.sql").read_text()
        assert "not_like" in sql
        assert "NOT ILIKE" in sql

    def test_083_whitelist_sync_marker(self):
        """两个 RPC 都有 FILTER_WHITELIST_SYNC 注释"""
        sql = (MIGRATIONS_DIR / "083_rpc_grouped_stats.sql").read_text()
        sync_markers = re.findall(r"FILTER_WHITELIST_SYNC", sql)
        assert len(sync_markers) >= 2, f"期望 ≥2 处 FILTER_WHITELIST_SYNC，实际 {len(sync_markers)}"

    def test_083_whitelist_consistency(self):
        """两个 RPC 的白名单字段完全一致"""
        sql = (MIGRATIONS_DIR / "083_rpc_grouped_stats.sql").read_text()

        def extract_whitelist(block: str) -> set[str]:
            fields = re.findall(r"'(\w+)'", block)
            return set(fields)

        # 找到两个 IF field_name NOT IN 块
        blocks = re.findall(
            r"IF field_name NOT IN \((.*?)\) THEN",
            sql, re.DOTALL,
        )
        assert len(blocks) == 2, f"期望 2 个白名单块，实际 {len(blocks)}"
        wl1 = extract_whitelist(blocks[0])
        wl2 = extract_whitelist(blocks[1])
        assert wl1 == wl2, f"白名单不一致: {wl1.symmetric_difference(wl2)}"


# ══════════════════════════════════════════════════════════
# 2. 分类引擎端到端链路（mock RPC → classifier → ToolOutput）
# ══════════════════════════════════════════════════════════


class TestClassifierE2E:
    """模拟完整链路：RPC 返回 → 分类 → ToolOutput 输出"""

    @pytest.mark.asyncio
    async def test_order_summary_with_classification(self):
        """订单 summary 走分类引擎分支，返回 ToolOutput 三层推荐"""
        from config.default_classification_rules import DEFAULT_ORDER_RULES
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        from services.kuaimai.order_classifier import OrderClassifier

        # 模拟 erp_order_stats_grouped RPC 返回
        rpc_data = [
            {"order_type": "2,3,10,0", "order_status": "PAID", "is_scalping": 0,
             "doc_count": 2539, "total_qty": 5000, "total_amount": 8000},
            {"order_type": "2,14", "order_status": "PAID", "is_scalping": 0,
             "doc_count": 66, "total_qty": 100, "total_amount": 500},
            {"order_type": "2,3", "order_status": "CLOSED", "is_scalping": 0,
             "doc_count": 614, "total_qty": 1200, "total_amount": 2500},
            {"order_type": "2,3,0", "order_status": "PAID", "is_scalping": 0,
             "doc_count": 6057, "total_qty": 12000, "total_amount": 16059.77},
        ]

        mock_db = MagicMock()
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(data=rpc_data)
        mock_db.rpc.return_value = mock_rpc

        engine = UnifiedQueryEngine(db=mock_db, org_id="test-org")

        # Patch OrderClassifier.for_org 直接返回带默认规则的 classifier
        classifier = OrderClassifier(DEFAULT_ORDER_RULES)
        with patch(
            "services.kuaimai.order_classifier.OrderClassifier.for_org",
            return_value=classifier,
        ):
            result = await engine.execute(
                doc_type="order", mode="summary",
                filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-18"}],
                time_type="pay_time",
            )

        # 验证 ToolOutput 三层推荐协议
        assert result.data is not None
        data = result.data[0]
        assert "total" in data
        assert "valid" in data
        assert "categories" in data
        assert data["total"]["doc_count"] == 9276
        assert data["valid"]["doc_count"] == 6057
        assert result.metadata.get("recommended_key") == "valid"
        assert "后续计算请默认使用有效订单数据" in result.summary

    @pytest.mark.asyncio
    async def test_include_invalid_skips_classification(self):
        """include_invalid=True 跳过分类引擎，走原逻辑"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        mock_db = MagicMock()
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(
            data={"doc_count": 100, "total_qty": 200, "total_amount": 5000}
        )
        mock_db.rpc.return_value = mock_rpc
        mock_db.table.return_value.select.return_value.in_.return_value.execute.return_value.data = []

        engine = UnifiedQueryEngine(db=mock_db, org_id="test-org")
        result = await engine.execute(
            doc_type="order", mode="summary",
            filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-18"}],
            include_invalid=True,
        )

        # 应该只调用 erp_global_stats_query，不调用 erp_order_stats_grouped
        rpc_calls = [c[0][0] for c in mock_db.rpc.call_args_list]
        assert "erp_global_stats_query" in rpc_calls
        assert "erp_order_stats_grouped" not in rpc_calls
        assert result.metadata.get("recommended_key") is None

    @pytest.mark.asyncio
    async def test_grouped_query_skips_classification(self):
        """有 group_by 时跳过分类引擎"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        mock_db = MagicMock()
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(data=[
            {"group_key": "shop1", "doc_count": 50, "total_qty": 100, "total_amount": 2000},
        ])
        mock_db.rpc.return_value = mock_rpc

        engine = UnifiedQueryEngine(db=mock_db, org_id="test-org")
        result = await engine.execute(
            doc_type="order", mode="summary",
            filters=[{"field": "pay_time", "op": "gte", "value": "2026-04-18"}],
            group_by=["shop"],
        )

        rpc_calls = [c[0][0] for c in mock_db.rpc.call_args_list]
        assert "erp_global_stats_query" in rpc_calls
        assert "erp_order_stats_grouped" not in rpc_calls

    @pytest.mark.asyncio
    async def test_named_params_forwarded_to_grouped_rpc(self):
        """shop/platform 命名参数应转为 Filter DSL 传给分组 RPC"""
        from config.default_classification_rules import DEFAULT_ORDER_RULES
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine
        from services.kuaimai.order_classifier import OrderClassifier

        rpc_data = [
            {"order_type": "2,3,0", "order_status": "PAID", "is_scalping": 0,
             "doc_count": 100, "total_qty": 200, "total_amount": 5000},
        ]

        mock_db = MagicMock()
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(data=rpc_data)
        mock_db.rpc.return_value = mock_rpc

        engine = UnifiedQueryEngine(db=mock_db, org_id="test-org")
        classifier = OrderClassifier(DEFAULT_ORDER_RULES)
        with patch(
            "services.kuaimai.order_classifier.OrderClassifier.for_org",
            return_value=classifier,
        ):
            await engine.execute(
                doc_type="order", mode="summary",
                filters=[
                    {"field": "pay_time", "op": "gte", "value": "2026-04-18"},
                    {"field": "shop_name", "op": "like", "value": "蓝创"},
                    {"field": "platform", "op": "eq", "value": "taobao"},
                ],
                time_type="pay_time",
            )

        # 验证分组 RPC 被调用且 p_filters 包含 shop/platform
        grouped_calls = [
            c for c in mock_db.rpc.call_args_list
            if c[0][0] == "erp_order_stats_grouped"
        ]
        assert len(grouped_calls) == 1
        import json
        p_filters = json.loads(grouped_calls[0][0][1]["p_filters"])
        filter_fields = [f["field"] for f in p_filters]
        assert "shop_name" in filter_fields
        assert "platform" in filter_fields

    @pytest.mark.asyncio
    async def test_non_order_doctype_skips_classification(self):
        """非 order doc_type 跳过分类引擎"""
        from services.kuaimai.erp_unified_query import UnifiedQueryEngine

        mock_db = MagicMock()
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(
            data={"doc_count": 10, "total_qty": 20, "total_amount": 500}
        )
        mock_db.rpc.return_value = mock_rpc

        engine = UnifiedQueryEngine(db=mock_db, org_id="test-org")
        result = await engine.execute(
            doc_type="aftersale", mode="summary",
            filters=[{"field": "doc_created_at", "op": "gte", "value": "2026-04-18"}],
        )

        rpc_calls = [c[0][0] for c in mock_db.rpc.call_args_list]
        assert "erp_global_stats_query" in rpc_calls
        assert "erp_order_stats_grouped" not in rpc_calls


# ══════════════════════════════════════════════════════════
# 3. PlanBuilder 语义区分测试
# ══════════════════════════════════════════════════════════


class TestPlanBuilderSemanticParams:
    """PlanBuilder prompt 包含 include_invalid 语义区分指令"""

    def test_prompt_contains_include_invalid(self):
        from services.agent.plan_builder import build_plan_prompt
        prompt = build_plan_prompt("test", "2026-04-18")
        assert "include_invalid" in prompt

    def test_prompt_contains_product_code(self):
        from services.agent.plan_builder import build_plan_prompt
        prompt = build_plan_prompt("test", "2026-04-18")
        assert "product_code" in prompt

    def test_prompt_contains_order_no(self):
        from services.agent.plan_builder import build_plan_prompt
        prompt = build_plan_prompt("test", "2026-04-18")
        assert "order_no" in prompt

    def test_prompt_distinguishes_query_vs_include(self):
        """prompt 明确区分 '查刷单'(is_scalping) vs '不排除刷单'(include_invalid)"""
        from services.agent.plan_builder import build_plan_prompt
        prompt = build_plan_prompt("test", "2026-04-18")
        # 应包含 is_scalping 参数定义（查刷单用）
        assert "is_scalping" in prompt
        assert "include_invalid" in prompt


# ══════════════════════════════════════════════════════════
# 4. import 链完整性
# ══════════════════════════════════════════════════════════


class TestImportChain:
    """验证所有新增模块可正常 import"""

    def test_import_row_builders(self):
        from services.kuaimai.erp_sync_row_builders import (
            _build_aftersale_rows,
            _build_order_rows,
        )
        assert callable(_build_aftersale_rows)
        assert callable(_build_order_rows)

    def test_import_row_builders_from_handlers(self):
        """向后兼容：从 handlers re-export"""
        from services.kuaimai.erp_sync_handlers import (
            _build_aftersale_rows,
            _build_order_rows,
        )
        assert callable(_build_aftersale_rows)
        assert callable(_build_order_rows)

    def test_import_classifier(self):
        from services.kuaimai.order_classifier import (
            ClassificationResult,
            OrderClassifier,
        )
        assert callable(OrderClassifier.classify)

    def test_import_default_rules(self):
        from config.default_classification_rules import DEFAULT_ORDER_RULES
        assert len(DEFAULT_ORDER_RULES) == 5

    def test_import_validation_result_prompt(self):
        from services.agent.department_types import ValidationResult
        r = ValidationResult.missing(["时间范围"], prompt="请提供时间范围")
        assert r.prompt == "请提供时间范围"

    def test_validation_result_default_prompt(self):
        """未指定 prompt 时自动生成"""
        from services.agent.department_types import ValidationResult
        r = ValidationResult.missing(["时间范围"])
        assert "时间范围" in r.prompt


# ══════════════════════════════════════════════════════════
# 5. Row builder 新字段完整性
# ══════════════════════════════════════════════════════════


class TestRowBuilderFields:
    """验证 _build_order_rows / _build_aftersale_rows 包含所有 081 新增字段"""

    def _mock_svc(self):
        svc = MagicMock()
        svc.sort_and_assign_index.side_effect = lambda items, _: [
            {**item, "_item_index": i} for i, item in enumerate(items)
        ]
        return svc

    def test_order_rows_contain_081_fields(self):
        from services.kuaimai.erp_sync_row_builders import _build_order_rows

        doc = {
            "sid": "12345",
            "sysStatus": "PAID",
            "created": "2026-04-18 10:00:00",
            "modified": "2026-04-18 10:00:00",
            "type": "2,3,0",
            "orders": [{"num": 1, "payment": "100", "price": "100"}],
            # 081 新增字段
            "tradeTags": [{"id": 1, "tagName": "VIP"}],
            "exceptions": [101, 102],
            "scalping": 1,
            "totalFee": "120.00",
            "unifiedStatus": "WAIT_DELIVER",
            "weight": "0.5",
            "warehouseId": 42,
        }
        rows = _build_order_rows(doc, self._mock_svc())
        assert len(rows) == 1
        row = rows[0]

        # 验证 081 订单头字段
        assert row["trade_tags"] == [{"id": 1, "tagName": "VIP"}]
        assert row["exception_tags"] == '{"101","102"}'
        assert row["is_scalping"] == 1
        assert row["total_fee"] == "120.00"
        assert row["unified_status"] == "WAIT_DELIVER"
        assert row["weight"] == "0.5"
        assert row["warehouse_id"] == 42

    def test_order_rows_trade_tags_defensive(self):
        """tradeTags 非 list 时降级为 None"""
        from services.kuaimai.erp_sync_row_builders import _build_order_rows

        doc = {
            "sid": "12345", "sysStatus": "PAID",
            "orders": [{"num": 1, "payment": "100"}],
            "tradeTags": "not_a_list",
        }
        rows = _build_order_rows(doc, self._mock_svc())
        assert rows[0]["trade_tags"] is None

    def test_order_rows_item_level_fields(self):
        """验证子项级别字段"""
        from services.kuaimai.erp_sync_row_builders import _build_order_rows

        doc = {
            "sid": "12345", "sysStatus": "PAID",
            "orders": [{
                "num": 2, "payment": "200", "price": "100",
                "discountFee": "10.00", "sysTitle": "系统标题",
                "giftNum": 1, "isVirtual": 1,
                "suits": [{"id": 1}],
                "orderExt": {"key": "val"},
            }],
        }
        rows = _build_order_rows(doc, self._mock_svc())
        row = rows[0]
        assert row["item_discount_fee"] == "10.00"
        assert row["sys_title"] == "系统标题"
        assert row["gift_num"] == 1
        assert row["is_virtual"] == 1
        assert row["suits"] == [{"id": 1}]
        assert row["order_ext"] == {"key": "val"}

    def test_aftersale_rows_contain_081_fields(self):
        from services.kuaimai.erp_sync_row_builders import _build_aftersale_rows

        doc = {
            "id": "AS001", "status": "PROCESSING",
            "created": "2026-04-18", "modified": "2026-04-18",
            "shopName": "店铺A", "source": "taobao",
            "tid": "TB123", "afterSaleType": 1,
            # 081 新增头字段
            "orderSid": "ORD001",
            "reason": "质量问题",
            "orderType": "2,3",
            "buyerName": "张三",
            "handlerStatus": 2,
            "messageMemos": [{"content": "已处理"}],
            "items": [{
                "mainOuterId": "SPU001", "outerId": "SKU001",
                "title": "测试商品", "receivableCount": 1,
                "payment": "50.00",
                # 081 新增子项字段
                "refundMoney": "50.00",
                "detailId": 123456,
                "isGift": 1,
            }],
        }
        rows = _build_aftersale_rows(doc, self._mock_svc())
        assert len(rows) == 1
        row = rows[0]

        # 验证 081 售后头字段
        assert row["order_sid"] == "ORD001"
        assert row["reason"] == "质量问题"
        assert row["order_type_ref"] == "2,3"
        assert row["buyer_name"] == "张三"
        assert row["handler_status"] == 2
        assert row["message_memos"] == [{"content": "已处理"}]

        # 验证 081 售后子项字段
        assert row["item_refund_money"] == "50.00"
        assert row["item_detail_id"] == 123456
        assert row["is_gift"] == 1

    def test_aftersale_message_memos_defensive(self):
        """messageMemos 非 list 时降级为 None"""
        from services.kuaimai.erp_sync_row_builders import _build_aftersale_rows

        doc = {
            "id": "AS002", "status": "OK",
            "messageMemos": "not_a_list",
            "items": [],
        }
        rows = _build_aftersale_rows(doc, self._mock_svc())
        assert rows[0]["message_memos"] is None
