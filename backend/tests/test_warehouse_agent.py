"""
部门Agent框架 + 仓储Agent 单元测试。

覆盖: department_types.py / department_agent.py / departments/warehouse_agent.py
设计文档: docs/document/TECH_多Agent单一职责重构.md §6 + §9.6 + §13
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from services.agent.department_types import (
    ValidationResult,
    ValidationStatus,
)
from services.agent.tool_output import (
    ColumnMeta,
    OutputFormat,
    OutputStatus,
    ToolOutput,
)


# ============================================================
# ValidationResult 三态测试
# ============================================================


class TestValidationResult:

    def test_ok(self):
        r = ValidationResult.ok()
        assert r.is_ok
        assert not r.is_missing
        assert not r.is_conflict

    def test_missing(self):
        r = ValidationResult.missing(["平台", "时间范围"])
        assert r.is_missing
        assert not r.is_ok
        assert "平台" in r.message
        assert "时间范围" in r.message
        assert r.missing_params == ("平台", "时间范围")

    def test_conflict(self):
        r = ValidationResult.conflict("结束日期不能早于开始日期")
        assert r.is_conflict
        assert not r.is_ok
        assert "结束日期" in r.message

    def test_frozen(self):
        r = ValidationResult.ok()
        with pytest.raises(AttributeError):
            r.status = ValidationStatus.MISSING  # type: ignore[misc]


# ============================================================
# DepartmentAgent 基类测试（通过 WarehouseAgent 实例化）
# ============================================================


def _make_warehouse(db=None, org_id=None):
    from services.agent.departments.warehouse_agent import WarehouseAgent
    return WarehouseAgent(db=db or MagicMock(), org_id=org_id)


class TestDepartmentAgentBase:
    """通过 WarehouseAgent 测试基类方法"""

    # ── _validate_time_range ──

    def test_validate_time_range_ok(self):
        agent = _make_warehouse()
        result = agent._validate_time_range("2026-03-01 ~ 2026-03-31")
        assert result is None

    def test_validate_time_range_bad_format(self):
        agent = _make_warehouse()
        result = agent._validate_time_range("2026/03/01 to 2026/03/31")
        assert result is not None
        assert result.is_conflict
        assert "格式错误" in result.message

    def test_validate_time_range_end_before_start(self):
        agent = _make_warehouse()
        result = agent._validate_time_range("2026-04-01 ~ 2026-03-01")
        assert result.is_conflict
        assert "早于" in result.message

    def test_validate_time_range_over_90_days(self):
        agent = _make_warehouse()
        result = agent._validate_time_range("2026-01-01 ~ 2026-06-01")
        assert result.is_conflict
        assert "90天" in result.message

    # ── _validate_required ──

    def test_validate_required_all_present(self):
        agent = _make_warehouse()
        result = agent._validate_required(
            {"platform": "tb", "time_range": "2026-03-01 ~ 2026-03-31"},
            ["platform", "time_range"],
        )
        assert result is None

    def test_validate_required_some_missing(self):
        agent = _make_warehouse()
        result = agent._validate_required(
            {"platform": "tb"},
            ["platform", "time_range"],
        )
        assert result is not None
        assert result.is_missing
        assert "time_range" in result.missing_params

    # ── _determine_status ──

    def test_determine_status_ok(self):
        agent = _make_warehouse()
        status, meta = agent._determine_status([{"a": 1}])
        assert status == OutputStatus.OK

    def test_determine_status_empty(self):
        agent = _make_warehouse()
        status, meta = agent._determine_status([])
        assert status == OutputStatus.EMPTY

    def test_determine_status_error(self):
        agent = _make_warehouse()
        status, meta = agent._determine_status([], error=Exception("fail"))
        assert status == OutputStatus.ERROR

    def test_determine_status_partial(self):
        agent = _make_warehouse()
        status, meta = agent._determine_status(
            [{"a": 1}], is_truncated=True, total_expected=100,
        )
        assert status == OutputStatus.PARTIAL
        assert meta["total_expected"] == 100

    def test_determine_status_partial_no_total(self):
        agent = _make_warehouse()
        status, meta = agent._determine_status([{"a": 1}], is_truncated=True)
        assert status == OutputStatus.PARTIAL
        assert "total_expected" not in meta

    # ── _extract_field_from_context ──

    def test_extract_field_from_context_basic(self):
        agent = _make_warehouse()
        ctx = [
            ToolOutput(
                summary="OK",
                format=OutputFormat.TABLE,
                source="aftersale",
                columns=[ColumnMeta("product_code", "text", "商品编码")],
                data=[
                    {"product_code": "A001"},
                    {"product_code": "B002"},
                ],
            ),
        ]
        values = agent._extract_field_from_context(ctx, "product_code")
        assert values == ["A001", "B002"]

    def test_extract_field_zero_value_preserved(self):
        """零值保护：库存=0 不能被丢弃（§13.4）"""
        agent = _make_warehouse()
        ctx = [
            ToolOutput(
                summary="OK",
                format=OutputFormat.TABLE,
                source="warehouse",
                columns=[
                    ColumnMeta("product_code", "text"),
                    ColumnMeta("sellable", "integer"),
                ],
                data=[
                    {"product_code": "A001", "sellable": 0},
                    {"product_code": "A002", "sellable": 50},
                ],
            ),
        ]
        values = agent._extract_field_from_context(ctx, "sellable")
        assert 0 in values
        assert len(values) == 2

    def test_extract_field_none_values_excluded(self):
        agent = _make_warehouse()
        ctx = [
            ToolOutput(
                summary="OK",
                format=OutputFormat.TABLE,
                source="x",
                columns=[ColumnMeta("code", "text")],
                data=[
                    {"code": "A"},
                    {"code": None},
                ],
            ),
        ]
        values = agent._extract_field_from_context(ctx, "code")
        assert values == ["A"]

    def test_extract_field_empty_context(self):
        agent = _make_warehouse()
        assert agent._extract_field_from_context(None, "code") == []
        assert agent._extract_field_from_context([], "code") == []

    def test_extract_field_no_matching_column(self):
        agent = _make_warehouse()
        ctx = [
            ToolOutput(
                summary="OK",
                format=OutputFormat.TABLE,
                source="x",
                columns=[ColumnMeta("other_field", "text")],
                data=[{"other_field": "v"}],
            ),
        ]
        assert agent._extract_field_from_context(ctx, "product_code") == []

    # ── _extract_field_from_context FILE_REF 路径 ──

    def test_extract_field_from_file_ref(self, tmp_path):
        """FILE_REF 模式：从 staging parquet 提取字段值"""
        import pandas as pd
        from services.agent.tool_output import FileRef

        agent = _make_warehouse()
        parquet_path = tmp_path / "aftersale_1234.parquet"
        df = pd.DataFrame({
            "product_code": ["A001", "B002", "C003"],
            "return_qty": [15, 8, 3],
        })
        df.to_parquet(parquet_path, index=False)

        ctx = [
            ToolOutput(
                summary="退货数据",
                format=OutputFormat.FILE_REF,
                source="aftersale",
                columns=[
                    ColumnMeta("product_code", "text", "商品编码"),
                    ColumnMeta("return_qty", "integer", "退货数量"),
                ],
                file_ref=FileRef(
                    path=str(parquet_path),
                    filename="aftersale_1234.parquet",
                    format="parquet",
                    row_count=3,
                    size_bytes=1000,
                    columns=[
                        ColumnMeta("product_code", "text", "商品编码"),
                        ColumnMeta("return_qty", "integer", "退货数量"),
                    ],
                ),
            ),
        ]
        values = agent._extract_field_from_context(ctx, "product_code")
        assert values == ["A001", "B002", "C003"]

    def test_extract_field_file_ref_caches_result(self, tmp_path):
        """FILE_REF 缓存：同一个 ToolOutput 第二次提取同字段走缓存"""
        import pandas as pd
        from services.agent.tool_output import FileRef

        agent = _make_warehouse()
        parquet_path = tmp_path / "cache_test.parquet"
        df = pd.DataFrame({"product_code": ["X001"]})
        df.to_parquet(parquet_path, index=False)

        output = ToolOutput(
            summary="OK",
            format=OutputFormat.FILE_REF,
            source="test",
            columns=[ColumnMeta("product_code", "text")],
            file_ref=FileRef(
                path=str(parquet_path),
                filename="cache_test.parquet",
                format="parquet",
                row_count=1,
                size_bytes=500,
                columns=[ColumnMeta("product_code", "text")],
            ),
        )
        ctx = [output]

        # 第一次：读文件
        vals1 = agent._extract_field_from_context(ctx, "product_code")
        assert vals1 == ["X001"]
        # 验证缓存已写入
        assert "_col_cache:product_code" in output.metadata

        # 第二次：走缓存（不再读文件）
        vals2 = agent._extract_field_from_context(ctx, "product_code")
        assert vals2 == ["X001"]

    def test_extract_field_file_ref_missing_file(self, tmp_path):
        """FILE_REF 文件不存在时优雅降级"""
        from services.agent.tool_output import FileRef

        agent = _make_warehouse()
        ctx = [
            ToolOutput(
                summary="OK",
                format=OutputFormat.FILE_REF,
                source="test",
                columns=[ColumnMeta("product_code", "text")],
                file_ref=FileRef(
                    path=str(tmp_path / "nonexistent.parquet"),
                    filename="nonexistent.parquet",
                    format="parquet",
                    row_count=0,
                    size_bytes=0,
                    columns=[ColumnMeta("product_code", "text")],
                ),
            ),
        ]
        # 不崩，返回空列表
        values = agent._extract_field_from_context(ctx, "product_code")
        assert values == []

    def test_extract_field_mixed_inline_and_file_ref(self, tmp_path):
        """混合模式：context 同时包含 inline 和 FILE_REF 输出"""
        import pandas as pd
        from services.agent.tool_output import FileRef

        agent = _make_warehouse()
        parquet_path = tmp_path / "mixed.parquet"
        df = pd.DataFrame({"product_code": ["C003"]})
        df.to_parquet(parquet_path, index=False)

        ctx = [
            # inline
            ToolOutput(
                summary="OK",
                format=OutputFormat.TABLE,
                source="warehouse",
                columns=[ColumnMeta("product_code", "text")],
                data=[{"product_code": "A001"}, {"product_code": "B002"}],
            ),
            # FILE_REF
            ToolOutput(
                summary="OK",
                format=OutputFormat.FILE_REF,
                source="aftersale",
                columns=[ColumnMeta("product_code", "text")],
                file_ref=FileRef(
                    path=str(parquet_path),
                    filename="mixed.parquet",
                    format="parquet",
                    row_count=1,
                    size_bytes=500,
                    columns=[ColumnMeta("product_code", "text")],
                ),
            ),
        ]
        values = agent._extract_field_from_context(ctx, "product_code")
        assert values == ["A001", "B002", "C003"]

    # ── dag_mode 写操作约束 ──

    @pytest.mark.asyncio
    async def test_dag_mode_blocks_write_action(self):
        """dag_mode=True 时写 action 被拦截"""
        agent = _make_warehouse()
        # 模拟子类 _classify_action 返回写操作
        agent._classify_action = lambda task: "update"
        result = await agent.execute("修改库存数量", dag_mode=True)
        assert result.status == OutputStatus.ERROR
        assert "写操作" in result.summary

    @pytest.mark.asyncio
    async def test_dag_mode_blocks_write_keyword(self):
        """dag_mode=True 时任务描述含写关键词被拦截（兜底保护）"""
        agent = _make_warehouse()
        # _classify_action 返回 default，但任务描述含 "修改"
        result = await agent.execute("批量修改库存", dag_mode=True)
        assert result.status == OutputStatus.ERROR
        assert "写操作" in result.summary

    @pytest.mark.asyncio
    async def test_dag_mode_allows_read(self):
        """dag_mode=True 时读操作正常执行"""
        from services.agent.department_types import ValidationResult
        agent = _make_warehouse()
        mock_output = ToolOutput(summary="OK", source="warehouse")
        with patch.object(
            agent, "validate_params", return_value=ValidationResult.ok(),
        ), patch.object(
            agent, "_dispatch", new=AsyncMock(return_value=mock_output),
        ):
            result = await agent.execute("查库存", dag_mode=True)
            assert result.status == OutputStatus.OK

    @pytest.mark.asyncio
    async def test_no_dag_mode_allows_write(self):
        """dag_mode=False（默认）时写操作正常放行"""
        agent = _make_warehouse()
        agent._classify_action = lambda task: "update"
        mock_output = ToolOutput(summary="OK", source="warehouse")
        with patch.object(agent, "_dispatch", new=AsyncMock(return_value=mock_output)):
            result = await agent.execute("修改库存数量")
            assert result.status == OutputStatus.OK

    # ── _is_write_action / _has_write_intent 独立测试 ──

    def test_is_write_action_known_actions(self):
        """_is_write_action 覆盖所有已定义的写操作枚举值"""
        agent = _make_warehouse()
        for action in ("create", "update", "delete", "modify",
                       "adjust", "cancel", "batch_update"):
            assert agent._is_write_action(action), f"{action} should be write"

    def test_is_write_action_read_actions(self):
        """读操作不被视为写"""
        agent = _make_warehouse()
        for action in ("default", "stock_query", "warehouse_list", "query"):
            assert not agent._is_write_action(action), f"{action} should not be write"

    def test_has_write_intent_keywords(self):
        """_has_write_intent 覆盖所有中文写关键词，包括"批量"兜底"""
        agent = _make_warehouse()
        for task in ("修改库存", "删除记录", "创建订单", "调整数量",
                     "取消采购单", "新建入库单", "更新状态", "批量操作"):
            assert agent._has_write_intent(task), f"'{task}' should have write intent"

    def test_has_write_intent_read_tasks(self):
        """查询类任务无写意图"""
        agent = _make_warehouse()
        for task in ("查库存", "仓库列表", "缺货分析", "导出Excel"):
            assert not agent._has_write_intent(task), f"'{task}' should not have write intent"

    # ── _params_to_filters ──

    def test_params_to_filters_time_range_half_open(self):
        """time_range 转 filters：半开区间，结束时间为次日 00:00:00"""
        agent = _make_warehouse()
        filters = agent._params_to_filters({
            "time_range": "2026-04-17 ~ 2026-04-17",
            "time_col": "pay_time",
        })
        assert len(filters) == 2
        assert filters[0] == {
            "field": "pay_time", "op": "gte", "value": "2026-04-17T00:00:00",
        }
        assert filters[1] == {
            "field": "pay_time", "op": "lt", "value": "2026-04-18T00:00:00",
        }

    def test_params_to_filters_with_platform(self):
        """带 platform 参数追加 eq 过滤器（L1 映射 taobao→tb）"""
        agent = _make_warehouse()
        filters = agent._params_to_filters({
            "time_range": "2026-04-17 ~ 2026-04-17",
            "platform": "taobao",
        })
        assert any(f["field"] == "platform" and f["value"] == "tb" for f in filters)

    def test_params_to_filters_empty_params(self):
        """空 params → 空 filters"""
        agent = _make_warehouse()
        assert agent._params_to_filters({}) == []

    def test_params_to_filters_platform_no_mapping_needed(self):
        """jd/pdd 等两边一致的 platform 不做映射"""
        agent = _make_warehouse()
        filters = agent._params_to_filters({"platform": "jd"})
        assert any(f["field"] == "platform" and f["value"] == "jd" for f in filters)

    def test_params_to_filters_douyin_mapping(self):
        """L1 映射 douyin → fxg"""
        agent = _make_warehouse()
        filters = agent._params_to_filters({"platform": "douyin"})
        assert any(f["field"] == "platform" and f["value"] == "fxg" for f in filters)

    def test_params_to_filters_order_no(self):
        """order_no → eq 过滤器"""
        agent = _make_warehouse()
        filters = agent._params_to_filters({"order_no": "126036803257340376"})
        assert any(
            f["field"] == "order_no" and f["op"] == "eq"
            and f["value"] == "126036803257340376"
            for f in filters
        )

    def test_params_to_filters_product_code_to_outer_id(self):
        """product_code → outer_id eq 过滤器（字段名映射）"""
        agent = _make_warehouse()
        filters = agent._params_to_filters({"product_code": "DBTXL01"})
        assert any(
            f["field"] == "outer_id" and f["op"] == "eq"
            and f["value"] == "DBTXL01"
            for f in filters
        )

    def test_params_to_filters_all_new_fields(self):
        """order_no + product_code + platform 同时转换"""
        agent = _make_warehouse()
        filters = agent._params_to_filters({
            "platform": "taobao",
            "order_no": "123456789012345678",
            "product_code": "ABC-01",
        })
        fields = [f["field"] for f in filters]
        assert "platform" in fields
        assert "order_no" in fields
        assert "outer_id" in fields

    # ── L3 空结果诊断 ──

    def test_diagnose_empty_platform_filter(self):
        """平台过滤导致空结果 → 建议不限平台"""
        agent = _make_warehouse()
        result = agent._diagnose_empty([
            {"field": "platform", "op": "eq", "value": "tb"},
        ])
        assert "平台" in result
        assert "淘宝" in result

    def test_diagnose_empty_order_no(self):
        """订单号过滤导致空结果 → 建议确认号码"""
        agent = _make_warehouse()
        result = agent._diagnose_empty([
            {"field": "order_no", "op": "eq", "value": "123456"},
        ])
        assert "123456" in result
        assert "订单号" in result

    def test_diagnose_empty_product_code(self):
        """商品编码过滤导致空结果 → 建议确认编码"""
        agent = _make_warehouse()
        result = agent._diagnose_empty([
            {"field": "outer_id", "op": "eq", "value": "DBTXL01"},
        ])
        assert "DBTXL01" in result
        assert "商品编码" in result

    def test_diagnose_empty_time_only(self):
        """只有时间过滤 → 无诊断建议"""
        agent = _make_warehouse()
        result = agent._diagnose_empty([
            {"field": "doc_created_at", "op": "gte", "value": "2026-04-17T00:00:00"},
        ])
        assert result == ""

    def test_diagnose_empty_multiple_filters(self):
        """多个过滤条件 → 多条建议"""
        agent = _make_warehouse()
        result = agent._diagnose_empty([
            {"field": "platform", "op": "eq", "value": "fxg"},
            {"field": "order_no", "op": "eq", "value": "999"},
        ])
        assert "抖音" in result
        assert "999" in result

    def test_diagnose_empty_no_filters(self):
        """无过滤条件 → 空字符串"""
        agent = _make_warehouse()
        assert agent._diagnose_empty([]) == ""

    # ── L3 失败诊断 ──

    def test_diagnose_error_timeout(self):
        agent = _make_warehouse()
        assert "超时" in agent._diagnose_error("query timeout after 30s")

    def test_diagnose_error_timeout_cn(self):
        agent = _make_warehouse()
        assert "缩小时间" in agent._diagnose_error("统计查询超时")

    def test_diagnose_error_too_many_params(self):
        agent = _make_warehouse()
        assert "数据量" in agent._diagnose_error("too many parameters: 65535")

    def test_diagnose_error_invalid_doc_type(self):
        agent = _make_warehouse()
        assert "文档类型" in agent._diagnose_error("invalid doc_type: foo")

    def test_diagnose_error_no_valid_fields(self):
        agent = _make_warehouse()
        assert "字段" in agent._diagnose_error("no valid export fields")

    def test_diagnose_error_filter_issue(self):
        agent = _make_warehouse()
        assert "过滤条件" in agent._diagnose_error("unknown filter column: xyz")

    def test_diagnose_error_empty_msg(self):
        agent = _make_warehouse()
        assert agent._diagnose_error("") == ""

    def test_diagnose_error_unknown(self):
        """未知错误 → 空字符串（不乱建议）"""
        agent = _make_warehouse()
        assert agent._diagnose_error("something completely unexpected") == ""

    # ── L1 格式纠正 ──

    def test_time_range_to_separator_normalized(self):
        """'to' 分隔符自动纠正为 '~'"""
        agent = _make_warehouse()
        filters = agent._params_to_filters({
            "time_range": "2026-04-17 to 2026-04-17",
        })
        assert len(filters) == 2
        assert filters[0]["op"] == "gte"

    def test_time_range_fullwidth_separator_normalized(self):
        """全角'～'自动纠正为 '~'"""
        agent = _make_warehouse()
        filters = agent._params_to_filters({
            "time_range": "2026-04-17～2026-04-17",
        })
        assert len(filters) == 2

    def test_platform_whitespace_stripped(self):
        """platform 前后空格被去除"""
        agent = _make_warehouse()
        filters = agent._params_to_filters({"platform": " jd "})
        assert any(f["value"] == "jd" for f in filters)

    def test_order_no_whitespace_stripped(self):
        """order_no 前后空格被去除"""
        agent = _make_warehouse()
        filters = agent._params_to_filters({"order_no": " 123456 "})
        assert any(f["value"] == "123456" for f in filters)

    def test_product_code_whitespace_stripped(self):
        """product_code 前后空格被去除"""
        agent = _make_warehouse()
        filters = agent._params_to_filters({"product_code": " ABC01 "})
        assert any(f["value"] == "ABC01" for f in filters)

    # ── execute(params=) 参数合并 + 降级标注 ──

    @pytest.mark.asyncio
    async def test_execute_params_merged_with_filters(self):
        """execute(params=) 自动把 time_range 转成 filters 传给执行层"""
        from services.agent.department_types import ValidationResult
        agent = _make_warehouse()
        mock_output = ToolOutput(summary="OK", source="warehouse")
        with patch.object(
            agent, "validate_params", return_value=ValidationResult.ok(),
        ), patch.object(
            agent, "_dispatch", new=AsyncMock(return_value=mock_output),
        ) as mock_dispatch:
            await agent.execute("查库存", dag_mode=True, params={
                "mode": "summary",
                "time_range": "2026-04-17 ~ 2026-04-17",
                "time_col": "pay_time",
            })
            # _dispatch 收到的 merged params 应该包含 filters
            call_params = mock_dispatch.call_args[0][1]
            assert "filters" in call_params
            assert len(call_params["filters"]) == 2

    @pytest.mark.asyncio
    async def test_execute_degraded_notice_shown(self):
        """降级路径 _degraded=True 时结果前缀加简化查询模式提示"""
        from services.agent.department_types import ValidationResult
        agent = _make_warehouse()
        mock_output = ToolOutput(summary="库存数据", source="warehouse")
        with patch.object(
            agent, "validate_params", return_value=ValidationResult.ok(),
        ), patch.object(
            agent, "_dispatch", new=AsyncMock(return_value=mock_output),
        ):
            result = await agent.execute("查库存", dag_mode=True, params={
                "mode": "summary",
                "time_range": "2026-04-17 ~ 2026-04-17",
                "_degraded": True,
            })
            assert "简化查询模式" in result.summary
            assert "库存数据" in result.summary

    # ── _build_output + FIELD_MAP ──

    def test_build_output_inline(self):
        agent = _make_warehouse()
        rows = [
            {"outer_id": "A001", "sellable_num": 30},
            {"outer_id": "A002", "sellable_num": 0},
        ]
        cols = [
            ColumnMeta("outer_id", "text", "商品编码"),
            ColumnMeta("sellable_num", "integer", "可售库存"),
        ]
        result = agent._build_output(rows, "库存查询完成", cols)

        # FIELD_MAP 映射：outer_id → product_code
        assert result.format == OutputFormat.TABLE
        assert result.source == "warehouse"
        assert result.data[0]["product_code"] == "A001"
        assert "outer_id" not in result.data[0]
        # columns 同步映射
        col_names = [c.name for c in result.columns]
        assert "product_code" in col_names
        assert "outer_id" not in col_names

    def test_build_output_preserves_unmapped_fields(self):
        """未在 FIELD_MAP 中的字段保持原名"""
        agent = _make_warehouse()
        rows = [{"outer_id": "A001", "warehouse_id": "WH-1"}]
        cols = [
            ColumnMeta("outer_id", "text"),
            ColumnMeta("warehouse_id", "text"),
        ]
        result = agent._build_output(rows, "OK", cols)
        assert "product_code" in result.data[0]
        assert "warehouse_id" in result.data[0]

    def test_build_output_metadata(self):
        agent = _make_warehouse()
        result = agent._build_output(
            [{"x": 1}], "OK",
            [ColumnMeta("x", "integer")],
            doc_type="receipt", time_range="2026-03-01 ~ 2026-03-31",
        )
        assert result.metadata["doc_type"] == "receipt"
        assert result.metadata["time_range"] == "2026-03-01 ~ 2026-03-31"

    def test_build_output_empty_rows(self):
        agent = _make_warehouse()
        result = agent._build_output(
            [], "无数据",
            [ColumnMeta("x", "integer")],
            status=OutputStatus.EMPTY,
        )
        assert result.status == OutputStatus.EMPTY
        assert result.data == []

    def test_build_output_over_threshold_no_staging_fallback(self):
        """超过200行但没有 staging_dir → 降级为内联"""
        agent = _make_warehouse()
        rows = [{"x": i} for i in range(250)]
        result = agent._build_output(
            rows, "OK",
            [ColumnMeta("x", "integer")],
        )
        assert result.format == OutputFormat.TABLE
        assert len(result.data) == 250

    def test_build_output_over_threshold_with_staging(self, tmp_path):
        """超过200行 + staging_dir → FILE_REF"""
        agent = _make_warehouse()
        rows = [{"x": i} for i in range(250)]
        result = agent._build_output(
            rows, "OK",
            [ColumnMeta("x", "integer")],
            staging_dir=str(tmp_path),
        )
        assert result.format == OutputFormat.FILE_REF
        assert result.file_ref is not None
        assert result.file_ref.row_count == 250
        assert result.file_ref.filename.startswith("warehouse_")

    # ── _query_local_data 白名单 ──

    @pytest.mark.asyncio
    async def test_query_local_data_blocked(self):
        """非白名单 doc_type → ERROR"""
        agent = _make_warehouse()
        result = await agent._query_local_data("order")
        assert result.status == OutputStatus.ERROR
        assert "无权查询" in result.summary

    @pytest.mark.asyncio
    async def test_query_local_data_allowed(self):
        """白名单 doc_type → 转发到 UnifiedQueryEngine"""
        agent = _make_warehouse()
        mock_output = ToolOutput(summary="OK", source="erp")
        with patch(
            "services.kuaimai.erp_unified_query.UnifiedQueryEngine"
        ) as MockEngine:
            mock_inst = MagicMock()
            mock_inst.execute = AsyncMock(return_value=mock_output)
            MockEngine.return_value = mock_inst
            result = await agent._query_local_data("receipt", mode="detail")
            assert result.summary == "OK"
            MockEngine.assert_called_once()


# ============================================================
# WarehouseAgent 参数校验测试
# ============================================================


class TestWarehouseValidation:

    def test_stock_query_ok(self):
        agent = _make_warehouse()
        r = agent.validate_params("stock_query", {"product_code": "A001"})
        assert r.is_ok

    def test_stock_query_keyword_ok(self):
        agent = _make_warehouse()
        r = agent.validate_params("stock_query", {"keyword": "防晒霜"})
        assert r.is_ok

    def test_stock_query_missing(self):
        agent = _make_warehouse()
        r = agent.validate_params("stock_query", {})
        assert r.is_missing
        assert "商品编码或关键词" in r.message

    def test_shortage_query_ok(self):
        agent = _make_warehouse()
        r = agent.validate_params("shortage_query", {
            "platform": "tb",
            "time_range": "2026-03-01 ~ 2026-03-31",
        })
        assert r.is_ok

    def test_shortage_query_missing_platform(self):
        agent = _make_warehouse()
        r = agent.validate_params("shortage_query", {
            "time_range": "2026-03-01 ~ 2026-03-31",
        })
        assert r.is_missing
        assert "平台" in r.message

    def test_shortage_query_missing_time(self):
        agent = _make_warehouse()
        r = agent.validate_params("shortage_query", {"platform": "tb"})
        assert r.is_missing
        assert "时间范围" in r.message

    def test_shortage_query_bad_time_format(self):
        agent = _make_warehouse()
        r = agent.validate_params("shortage_query", {
            "platform": "tb",
            "time_range": "bad format",
        })
        assert r.is_conflict

    def test_shortage_query_time_over_90_days(self):
        agent = _make_warehouse()
        r = agent.validate_params("shortage_query", {
            "platform": "tb",
            "time_range": "2026-01-01 ~ 2026-06-01",
        })
        assert r.is_conflict
        assert "90天" in r.message

    def test_warehouse_list_ok(self):
        agent = _make_warehouse()
        r = agent.validate_params("warehouse_list", {})
        assert r.is_ok

    def test_receipt_query_ok(self):
        agent = _make_warehouse()
        r = agent.validate_params("receipt_query", {
            "time_range": "2026-03-01 ~ 2026-03-31",
        })
        assert r.is_ok

    def test_receipt_query_missing(self):
        agent = _make_warehouse()
        r = agent.validate_params("receipt_query", {})
        assert r.is_missing

    def test_unknown_action_ok(self):
        """未知 action → 默认通过（基类不强制）"""
        agent = _make_warehouse()
        r = agent.validate_params("unknown_action", {})
        assert r.is_ok


# ============================================================
# WarehouseAgent 属性测试
# ============================================================


class TestWarehouseProperties:

    def test_domain(self):
        agent = _make_warehouse()
        assert agent.domain == "warehouse"

    def test_tools(self):
        agent = _make_warehouse()
        assert "local_stock_query" in agent.tools
        assert "local_warehouse_list" in agent.tools
        assert "local_data" in agent.tools

    def test_system_prompt(self):
        agent = _make_warehouse()
        prompt = agent.system_prompt
        assert "仓储" in prompt
        assert "不负责" in prompt
        assert "采购" in prompt

    def test_allowed_doc_types(self):
        agent = _make_warehouse()
        assert "receipt" in agent.allowed_doc_types
        assert "shelf" in agent.allowed_doc_types
        assert "order" not in agent.allowed_doc_types

    def test_field_map(self):
        agent = _make_warehouse()
        assert agent.FIELD_MAP["outer_id"] == "product_code"
        assert agent.FIELD_MAP["sku_outer_id"] == "sku_code"


# ============================================================
# WarehouseAgent 查询方法测试
# ============================================================


class TestWarehouseQueries:

    @pytest.mark.asyncio
    async def test_query_stock_basic(self):
        """库存查询正常路径"""
        agent = _make_warehouse()
        mock_output = ToolOutput(
            summary="A001 库存",
            format=OutputFormat.TABLE,
            source="warehouse",
            columns=[ColumnMeta("sellable_num", "integer", "可售")],
            data=[{"sellable_num": 30}],
        )
        with patch(
            "services.kuaimai.erp_local_query.local_stock_query",
            new=AsyncMock(return_value=mock_output),
        ):
            result = await agent.query_stock("A001")
            assert result.summary == "A001 库存"
            assert result.data[0]["sellable_num"] == 30

    @pytest.mark.asyncio
    async def test_query_stock_no_code(self):
        """缺少编码 → ERROR"""
        agent = _make_warehouse()
        result = await agent.query_stock("")
        assert result.status == OutputStatus.ERROR
        assert "商品编码" in result.summary

    @pytest.mark.asyncio
    async def test_query_stock_from_context(self):
        """从上游 context 提取 product_code"""
        agent = _make_warehouse()
        ctx = [
            ToolOutput(
                summary="OK",
                format=OutputFormat.TABLE,
                source="aftersale",
                columns=[ColumnMeta("product_code", "text", "商品编码")],
                data=[{"product_code": "B003"}],
            ),
        ]
        mock_output = ToolOutput(
            summary="B003 库存",
            source="warehouse",
        )
        with patch(
            "services.kuaimai.erp_local_query.local_stock_query",
            new=AsyncMock(return_value=mock_output),
        ) as mock_fn:
            result = await agent.query_stock("", context=ctx)
            assert result.summary == "B003 库存"
            # 验证调用时传了从 context 提取的 product_code
            mock_fn.assert_called_once()
            call_kwargs = mock_fn.call_args
            assert call_kwargs[1]["product_code"] == "B003"

    @pytest.mark.asyncio
    async def test_query_warehouse_list(self):
        """仓库列表查询"""
        agent = _make_warehouse()
        mock_output = ToolOutput(summary="共8个仓库", source="warehouse")
        with patch(
            "services.kuaimai.erp_local_query.local_warehouse_list",
            new=AsyncMock(return_value=mock_output),
        ):
            result = await agent.query_warehouse_list()
            assert "仓库" in result.summary

    @pytest.mark.asyncio
    async def test_query_receipt(self):
        """收货单查询（白名单通过）"""
        agent = _make_warehouse()
        mock_output = ToolOutput(summary="收货数据", source="erp")
        with patch(
            "services.kuaimai.erp_unified_query.UnifiedQueryEngine"
        ) as MockEngine:
            mock_inst = MagicMock()
            mock_inst.execute = AsyncMock(return_value=mock_output)
            MockEngine.return_value = mock_inst
            result = await agent.query_receipt(
                mode="detail", filters=[], limit=10,
            )
            assert result.summary == "收货数据"

    @pytest.mark.asyncio
    async def test_query_order_blocked(self):
        """订单查询被白名单阻止"""
        agent = _make_warehouse()
        result = await agent._query_local_data("order")
        assert result.status == OutputStatus.ERROR
        assert "无权" in result.summary
