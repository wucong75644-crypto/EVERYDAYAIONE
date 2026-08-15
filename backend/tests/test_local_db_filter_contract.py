"""LocalDB 过滤器 SQL 与参数绑定合同。"""

from unittest.mock import MagicMock

from core.local_db import QueryBuilder


def test_not_in_expands_each_value_as_a_parameter() -> None:
    sql, params = (
        QueryBuilder(MagicMock(), "items")
        .select("id")
        .eq("kind", "stock")
        .not_.in_("warehouse_id", ["w1", "w2"])
        ._build_select()
    )

    assert '"warehouse_id" NOT IN (%s, %s)' in sql
    assert params == ["stock", "w1", "w2"]
