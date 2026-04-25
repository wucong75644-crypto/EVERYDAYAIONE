"""RPC filter 白名单同步测试。

确保 param_converter 产出的 filter 字段不会被 RPC 白名单静默丢弃。
如果新增了 filter 字段但没同步 RPC 白名单 → 测试红 → 阻止上线。

设计文档: migrations/101_rpc_filter_whitelist_sync.sql 头部注释
"""
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

# 直接加载模块，绕过 services.kuaimai.__init__ 的 pydantic 依赖链
_SCHEMA_PATH = Path(__file__).parent.parent / "services" / "kuaimai" / "erp_unified_schema.py"
_schema_spec = importlib.util.spec_from_file_location(
    "erp_unified_schema", _SCHEMA_PATH,
    submodule_search_locations=[],
)
_schema_mod = importlib.util.module_from_spec(_schema_spec)
# 注册到 sys.modules 使 dataclass 的 __module__ 能解析
sys.modules[_schema_spec.name] = _schema_mod
_schema_spec.loader.exec_module(_schema_mod)
RPC_ORDER_STATS_FILTER_FIELDS: frozenset[str] = _schema_mod.RPC_ORDER_STATS_FILTER_FIELDS

from services.agent.param_converter import (
    TEXT_EQ_FIELDS,
    TEXT_LIKE_FIELDS,
    ENUM_EQ_FIELDS,
    FLAG_FIELDS,
)


# ── erp_order_stats_grouped base_q SELECT 列（减 doc_id/org_id） ──
# 任何修改 base_q SELECT 的迁移都必须同步更新这里。
RPC_BASE_Q_COLUMNS: frozenset[str] = frozenset({
    "quantity", "amount", "outer_id", "item_name",
    "shop_name", "shop_user_id", "platform", "supplier_name", "supplier_code",
    "warehouse_name", "warehouse_id",
    "doc_status", "order_status", "order_type", "order_no",
    "sku_outer_id", "express_no", "buyer_nick", "status_name",
    "cost", "pay_amount", "gross_profit", "refund_money",
    "post_fee", "discount_fee", "aftersale_type", "refund_status",
    "is_cancel", "is_refund", "is_exception", "is_halt", "is_urgent",
    "is_scalping", "unified_status", "is_presell",
    "online_status", "handler_status",
})


class TestWhitelistSync:
    """RPC 白名单与 Python 常量的同步检查。"""

    def test_python_constant_matches_base_q(self):
        """RPC_ORDER_STATS_FILTER_FIELDS 必须 == base_q 列集合。"""
        assert RPC_ORDER_STATS_FILTER_FIELDS == RPC_BASE_Q_COLUMNS, (
            f"Python 常量与 base_q 列不一致:\n"
            f"  多了: {RPC_ORDER_STATS_FILTER_FIELDS - RPC_BASE_Q_COLUMNS}\n"
            f"  少了: {RPC_BASE_Q_COLUMNS - RPC_ORDER_STATS_FILTER_FIELDS}"
        )

    def test_dimension_fields_in_whitelist(self):
        """修复验证：维度字段必须在白名单中（101 修复的核心）。"""
        critical = {"platform", "shop_name", "supplier_name", "warehouse_name", "item_name"}
        missing = critical - RPC_ORDER_STATS_FILTER_FIELDS
        assert not missing, f"维度字段缺失: {missing}"


class TestParamConverterCoverage:
    """param_converter 产出的 filter 字段 ∩ base_q 列 ⊆ RPC 白名单。

    只检查 RPC base_q 中有的列。param_converter 也会生成 base_q 中没有的列
    （如 receiver_name/express_company），这些列本身就不在 RPC SELECT 中，
    不属于白名单的责任范围。
    """

    def _all_db_fields(self) -> set[str]:
        """收集 param_converter 所有可能产出的 DB field 名。"""
        fields: set[str] = set()
        # 显式字段
        fields.add("platform")      # params_to_filters 直接生成
        fields.add("order_no")      # params_to_filters 直接生成
        fields.add("outer_id")      # product_code → outer_id
        fields.add("is_scalping")   # params_to_filters 直接生成
        # 批量映射的 DB 列名
        fields.update(TEXT_EQ_FIELDS.values())
        fields.update(TEXT_LIKE_FIELDS.values())
        fields.update(ENUM_EQ_FIELDS.values())
        fields.update(FLAG_FIELDS)
        return fields

    def test_base_q_fields_covered(self):
        """param_converter 产出的字段，如果在 base_q 中有，就必须在白名单中。"""
        converter_fields = self._all_db_fields()
        # 只检查 base_q 中有的列（没有的列 RPC 本来就查不到）
        in_base_q = converter_fields & RPC_BASE_Q_COLUMNS
        missing = in_base_q - RPC_ORDER_STATS_FILTER_FIELDS
        assert not missing, (
            f"param_converter 产出的字段在 base_q 中存在但 RPC 白名单缺失: {missing}\n"
            f"请同时更新:\n"
            f"  1. migrations/101_rpc_filter_whitelist_sync.sql 中的 IF field_name NOT IN\n"
            f"  2. erp_unified_schema.py 中的 RPC_ORDER_STATS_FILTER_FIELDS"
        )

    def test_fields_outside_base_q_documented(self):
        """记录 param_converter 能产出但 base_q 中不存在的字段（信息性，不阻断）。"""
        converter_fields = self._all_db_fields()
        outside = converter_fields - RPC_BASE_Q_COLUMNS
        # 这些字段在 export 模式可用，summary_classified 模式下无效
        # 只做记录，不 assert（它们走 erp_global_stats_query 或 export 路径）
        if outside:
            print(
                f"\n[INFO] param_converter 字段不在 order_stats base_q 中 "
                f"（summary_classified 路径无法过滤）: {sorted(outside)}"
            )


class TestSplitNamedParamsRedundancy:
    """验证 _summary_classified 的 DSL 转换不会丢失 filter。"""

    def test_dimension_reinjection(self):
        """_split_named_params 提取的维度参数重新注入 DSL 后能被 RPC 接受。"""
        # 模拟 _split_named_params 提取再注入的字段
        reinjected_fields = {"platform", "shop_name", "supplier_name", "warehouse_name"}
        missing = reinjected_fields - RPC_ORDER_STATS_FILTER_FIELDS
        assert not missing, f"维度参数重注入后仍会被 RPC 白名单丢弃: {missing}"


class TestUnsupportedFieldFallback:
    """filter 包含 RPC 不支持的字段时，应跳过分类引擎回退到通用 RPC。"""

    def test_receiver_name_skips_classified(self):
        """receiver_name 不在 RPC 白名单 → 必须回退。"""
        assert "receiver_name" not in RPC_ORDER_STATS_FILTER_FIELDS

    def test_express_company_skips_classified(self):
        """express_company 不在 RPC 白名单 → 必须回退。"""
        assert "express_company" not in RPC_ORDER_STATS_FILTER_FIELDS

    def test_supported_fields_stay_in_classified(self):
        """platform/shop_name 等维度字段在白名单内 → 走分类引擎。"""
        supported = {"platform", "shop_name", "order_status", "outer_id"}
        assert supported.issubset(RPC_ORDER_STATS_FILTER_FIELDS)


class TestSqlMigrationWhitelist:
    """从迁移 SQL 文件解析白名单，验证与 Python 常量一致。"""

    def test_parse_migration_whitelist(self):
        """解析 101 迁移的 SQL 白名单，与 Python 常量对比。"""
        import re
        from pathlib import Path

        sql_path = Path(__file__).parent.parent / "migrations" / "101_rpc_filter_whitelist_sync.sql"
        if not sql_path.exists():
            pytest.skip("迁移文件不存在（已合并或重命名）")

        sql = sql_path.read_text()
        # 提取 IF field_name NOT IN (...) 中的字段列表
        match = re.search(
            r"IF field_name NOT IN \((.*?)\) THEN",
            sql,
            re.DOTALL,
        )
        assert match, "未找到 field_name NOT IN 白名单"

        raw = match.group(1)
        # 提取所有 'xxx' 格式的字段名
        sql_fields = set(re.findall(r"'(\w+)'", raw))

        assert sql_fields == RPC_ORDER_STATS_FILTER_FIELDS, (
            f"SQL 迁移白名单与 Python 常量不一致:\n"
            f"  SQL 多了: {sql_fields - RPC_ORDER_STATS_FILTER_FIELDS}\n"
            f"  SQL 少了: {RPC_ORDER_STATS_FILTER_FIELDS - sql_fields}"
        )
