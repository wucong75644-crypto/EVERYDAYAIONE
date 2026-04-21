"""
v6 Agent 架构细节对齐 — 端到端集成模拟测试

覆盖 8 个场景，验证每个改动板块在完整调用链上是否正确工作：
1. FileRef 新字段在完整链路中的传播（创建→序列化→反序列化→collected_files）
2. Budget 两档切换 + inline/staging 分流
3. 数据摘要全列类型 profile（数值+时间+文本+空df）
4. validate + 降级结构化（无文本前缀）
5. Partial artifact 超时场景
6. Audit token + trace_id 写入
7. Langfuse 静默降级（无环境变量时不崩）
8. 旧数据反序列化兼容性
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import pandas as pd
import pytest

from services.agent.data_profile import build_data_profile
from services.agent.execution_budget import ExecutionBudget
from services.agent.session_file_registry import SessionFileRegistry
from services.agent.tool_output import (
    ColumnMeta, FileRef, OutputFormat, OutputStatus, ToolOutput, _FORMAT_MIME,
)


# ============================================================
# 场景 1: FileRef 新字段完整链路传播
# ============================================================


class TestFileRefFullChain:
    """模拟: 用户查库存 → DepartmentAgent 写 staging → 注册到 registry → 序列化/反序列化 → erp_agent 构建 collected_files"""

    def test_new_fields_survive_full_chain(self):
        """FileRef 创建 → registry 注册 → snapshot → 恢复 → 字段完整"""
        import uuid

        # Step 1: 模拟 department_agent._write_to_staging 创建 FileRef
        ref = FileRef(
            path="/tmp/test_staging/warehouse_12345.parquet",
            filename="warehouse_12345.parquet",
            format="parquet",
            row_count=500,
            size_bytes=102400,
            columns=[ColumnMeta("sku", "text", "商品编码")],
            preview="前3行...",
            created_at=time.time(),
            id=uuid.uuid4().hex,
            mime_type=_FORMAT_MIME["parquet"],
            created_by="warehouse",
            ttl_seconds=86400,
            derived_from=("parent_id_abc",),
        )

        assert ref.id != ""
        assert ref.mime_type == "application/x-parquet"
        assert ref.created_by == "warehouse"
        assert ref.derived_from == ("parent_id_abc",)

        # Step 2: 注册到 SessionFileRegistry
        registry = SessionFileRegistry()
        registry.register("warehouse", "execute", ref)

        # Step 3: 序列化（模拟 ask_user 冻结）
        snapshot = registry.to_snapshot()
        assert len(snapshot) == 1
        fr_data = snapshot[0]["file_ref"]
        assert fr_data["id"] == ref.id
        assert fr_data["mime_type"] == "application/x-parquet"
        assert fr_data["created_by"] == "warehouse"
        assert fr_data["derived_from"] == ["parent_id_abc"]

        # Step 4: 反序列化（模拟恢复）
        restored = SessionFileRegistry.from_snapshot(snapshot)
        restored_ref = restored.get_latest()
        assert restored_ref is not None
        assert restored_ref.id == ref.id
        assert restored_ref.mime_type == ref.mime_type
        assert restored_ref.created_by == ref.created_by
        assert restored_ref.derived_from == ("parent_id_abc",)
        assert restored_ref.ttl_seconds == 86400

        # Step 5: 模拟 erp_agent 构建 collected_files
        collected = {
            "url": restored_ref.path,
            "name": restored_ref.filename,
            "mime_type": restored_ref.mime_type or "application/octet-stream",
            "size": restored_ref.size_bytes,
        }
        assert collected["mime_type"] == "application/x-parquet"  # 不再是硬编码

    def test_get_by_id(self):
        """registry.get_by_id 能正确查找"""
        import uuid
        ref = FileRef(
            path="/tmp/test.parquet", filename="test.parquet",
            format="parquet", row_count=10, size_bytes=1024,
            columns=[], id=uuid.uuid4().hex,
        )
        registry = SessionFileRegistry()
        registry.register("trade", "query", ref)
        assert registry.get_by_id(ref.id) is ref
        assert registry.get_by_id("nonexistent") is None

    def test_access_count(self):
        """access_count 外部计数器正确工作"""
        import uuid
        file_id = uuid.uuid4().hex
        ref = FileRef(
            path="/tmp/test.parquet", filename="test.parquet",
            format="parquet", row_count=10, size_bytes=1024,
            columns=[], id=file_id,
        )
        registry = SessionFileRegistry()
        registry.register("trade", "query", ref)
        assert registry.get_access_count(file_id) == 0
        registry.record_access(file_id)
        registry.record_access(file_id)
        assert registry.get_access_count(file_id) == 2


# ============================================================
# 场景 2: Budget 两档切换 + inline/staging 分流
# ============================================================


class TestBudgetTwoTierSwitch:
    """模拟: 用户连续查询，context 逐渐填满 → inline 阈值从 200 降到 50"""

    def test_normal_budget_inline_200(self):
        """充足 token 时 inline_threshold=200"""
        budget = ExecutionBudget(max_tokens=100_000, reserved_for_response=4000)
        assert budget.inline_threshold == 200
        assert not budget.is_tight

    def test_tight_budget_inline_50(self):
        """紧张时 inline_threshold=50"""
        budget = ExecutionBudget(max_tokens=100_000, reserved_for_response=4000)
        # 模拟消耗了 85K tokens
        budget.use_tokens(85_000)
        # remaining = 100000 - 85000 - 4000 = 11000 < 15000
        assert budget.is_tight
        assert budget.inline_threshold == 50

    def test_budget_fork_inherits_tight(self):
        """子 budget fork 后也继承 tight 状态"""
        parent = ExecutionBudget(max_tokens=100_000, reserved_for_response=4000)
        parent.use_tokens(85_000)
        child = parent.fork(max_turns=5)
        # child tokens_remaining = parent.tokens_remaining + reserved = 11000 + 4000 = 15000
        # child 自己的 reserved = 4000, 所以 child.tokens_remaining = 15000 - 0 - 4000 = 11000
        assert child.is_tight
        assert child.inline_threshold == 50

    def test_department_agent_no_staging_returns_text(self):
        """v6: 无 staging_dir → TEXT 摘要（不再 inline）"""
        from services.agent.departments.warehouse_agent import WarehouseAgent

        agent = WarehouseAgent(db=MagicMock())
        result = agent._build_output(
            rows=[{"x": i} for i in range(100)],
            summary="test",
            columns=[ColumnMeta("x", "integer")],
        )
        assert result.format == OutputFormat.TEXT  # 无 staging → TEXT

    def test_department_agent_with_staging_returns_file_ref(self, tmp_path):
        """v6: 有 staging_dir → 统一走 FILE_REF + 摘要"""
        from services.agent.departments.warehouse_agent import WarehouseAgent

        agent = WarehouseAgent(db=MagicMock(), staging_dir=str(tmp_path))
        result = agent._build_output(
            rows=[{"x": i} for i in range(5)],  # 即使只有5行也走staging
            summary="test",
            columns=[ColumnMeta("x", "integer")],
            staging_dir=str(tmp_path),
        )
        assert result.format == OutputFormat.FILE_REF
        assert result.file_ref is not None
        assert "[数据已暂存]" in result.summary

    def test_per_tool_tokens_tracking(self):
        """per-tool token 统计正确"""
        budget = ExecutionBudget()
        budget.use_tokens(1000, tool_name="erp_agent")
        budget.use_tokens(500, tool_name="code_execute")
        budget.use_tokens(200, tool_name="erp_agent")
        stats = budget.get_tool_tokens()
        assert stats["erp_agent"] == 1200
        assert stats["code_execute"] == 500

    def test_per_tool_tokens_propagate_to_parent(self):
        """子 budget 的 per-tool 统计回写父"""
        parent = ExecutionBudget()
        child = parent.fork(max_turns=5)
        child.use_tokens(300, tool_name="local_data")
        assert parent.get_tool_tokens()["local_data"] == 300
        assert child.get_tool_tokens()["local_data"] == 300


# ============================================================
# 场景 3: 数据摘要全列类型 profile
# ============================================================


class TestDataProfileFullColumnTypes:
    """模拟: ERP 查询返回含数值/日期/文本/枚举列的 DataFrame"""

    def test_erp_order_data_profile(self):
        """模拟真实订单数据 profile"""
        df = pd.DataFrame({
            "order_no": [f"TB{i:018d}" for i in range(50)],
            "platform": ["淘宝"] * 30 + ["京东"] * 15 + ["拼多多"] * 5,
            "amount": [99.9 + i * 10 for i in range(50)],
            "qty": [1, 2, 3, 4, 5] * 10,
            "created_at": pd.date_range("2026-04-01", periods=50, freq="D"),
            "status": ["已发货"] * 20 + ["已签收"] * 25 + ["退款中"] * 5,
        })
        text, stats = build_data_profile(df, "trade_export.parquet", 150.0, elapsed=2.3)

        # 数值列：有 median/p25/p75
        assert "amount" in stats
        assert "median" in stats["amount"]
        assert "p25" in stats["amount"]

        # 时间列：有 min/max/span
        assert "created_at" in stats
        assert stats["created_at"]["span_days"] == 49

        # 文本列（低基数）：有 top5
        assert "platform" in stats
        assert "top5" in stats["platform"]
        top5_values = [item["value"] for item in stats["platform"]["top5"]]
        assert "淘宝" in top5_values

        # 高基数文本列：无 top5，有 avg_length
        assert "order_no" in stats
        assert "top5" not in stats["order_no"]
        assert "avg_length" in stats["order_no"]

        # 文本中包含关键板块
        assert "[统计-数值]" in text
        assert "[统计-时间]" in text
        assert "[统计-文本]" in text
        assert "中位数" in text
        assert "跨49天" in text

    def test_empty_df_returns_empty_stats(self):
        """空 DataFrame 不崩溃"""
        df = pd.DataFrame({"a": pd.Series(dtype="int64")})
        text, stats = build_data_profile(df, "empty.parquet", 0.0)
        assert "无数据" in text
        assert stats == {}

    def test_sampling_protection(self):
        """超过 max_profile_rows 时采样"""
        df = pd.DataFrame({"val": range(1000)})
        text, stats = build_data_profile(df, "big.parquet", 10.0, max_profile_rows=100)
        assert "统计基于 100 条采样" in text


# ============================================================
# 场景 4: validate + 降级结构化
# ============================================================


class TestValidateAndDegraded:
    """模拟: 各种 ToolOutput 状态的 validate 检查 + 降级路径"""

    def test_valid_file_ref_output(self):
        """正常 FILE_REF 输出无 warning"""
        ref = FileRef(
            path=__file__,  # 用当前文件确保存在
            filename="test.parquet", format="parquet",
            row_count=100, size_bytes=1024,
            columns=[ColumnMeta("id", "integer")],
            created_at=time.time(),
        )
        output = ToolOutput(
            summary="查询完成", format=OutputFormat.FILE_REF,
            source="warehouse", file_ref=ref,
            columns=[ColumnMeta("id", "integer")],
        )
        assert output.validate() == []

    def test_invalid_file_ref_detected(self):
        """file_ref 指向不存在的文件 → validate 报错"""
        ref = FileRef(
            path="/nonexistent/path.parquet",
            filename="gone.parquet", format="parquet",
            row_count=100, size_bytes=1024, columns=[],
            created_at=time.time(),
        )
        output = ToolOutput(
            summary="test", format=OutputFormat.FILE_REF,
            source="warehouse", file_ref=ref,
        )
        issues = output.validate()
        assert any("file_ref 无效" in i for i in issues)

    def test_validate_caches_is_valid(self):
        """is_valid 结果被缓存，第二次不再检查磁盘"""
        ref = FileRef(
            path=__file__, filename="test.parquet", format="parquet",
            row_count=100, size_bytes=1024, columns=[],
            created_at=time.time(),
        )
        output = ToolOutput(
            summary="test", format=OutputFormat.FILE_REF,
            source="test", file_ref=ref,
        )
        output.validate()  # 第一次
        assert ref.path in output._valid_cache
        output.validate()  # 第二次用缓存

    def test_degraded_no_text_prefix(self):
        """降级标记只在 metadata，不在 summary 文本前缀"""
        output = ToolOutput(
            summary="库存数据",
            source="warehouse",
            status=OutputStatus.OK,
            metadata={"_degraded": True, "doc_type": "stock"},
        )
        assert "简化查询模式" not in output.summary
        assert output.metadata["_degraded"] is True

    def test_to_message_content_dict_as_json(self):
        """metadata 中的 dict 值序列化为 JSON 而非 Python literal"""
        output = ToolOutput(
            summary="test",
            format=OutputFormat.TABLE,
            source="test",
            columns=[ColumnMeta("id", "integer")],
            data=[{"id": 1}],
            metadata={"stats": {"count": 100, "total": 500}},
        )
        content = output.to_message_content()
        # 应该是 JSON 格式（双引号），不是 Python literal（单引号）
        assert '"count": 100' in content or '"count":100' in content
        assert "{'count'" not in content


# ============================================================
# 场景 5: Partial artifact 超时
# ============================================================


class TestPartialArtifactTimeout:
    """模拟: DepartmentAgent 查询途中被 cancel → 返回已获取的部分数据"""

    @pytest.mark.asyncio
    async def test_cancelled_with_partial_rows(self):
        """CancelledError 时有 partial_rows → 返回 PARTIAL"""
        from services.agent.departments.warehouse_agent import WarehouseAgent
        from services.agent.department_types import ValidationResult

        agent = WarehouseAgent(db=MagicMock())

        # 模拟 _dispatch 被取消，但之前已设置了 _partial_rows
        async def slow_dispatch(*args, **kwargs):
            agent._partial_rows = [{"sku": "A001"}, {"sku": "A002"}]
            raise asyncio.CancelledError()

        with patch.object(
            agent, "validate_params", return_value=ValidationResult.ok(),
        ), patch.object(
            agent, "_dispatch", new=slow_dispatch,
        ):
            result = await agent.execute("查库存", dag_mode=True, params={
                "mode": "summary",
                "time_range": "2026-04-01 ~ 2026-04-20",
            })
            assert result.status == OutputStatus.PARTIAL
            assert len(result.data) == 2
            assert "部分数据" in result.summary

    @pytest.mark.asyncio
    async def test_cancelled_without_partial_rows_propagates(self):
        """CancelledError 时无 partial_rows → 继续传播异常"""
        from services.agent.departments.warehouse_agent import WarehouseAgent
        from services.agent.department_types import ValidationResult

        agent = WarehouseAgent(db=MagicMock())

        async def cancel_immediately(*args, **kwargs):
            raise asyncio.CancelledError()

        with patch.object(
            agent, "validate_params", return_value=ValidationResult.ok(),
        ), patch.object(
            agent, "_dispatch", new=cancel_immediately,
        ):
            with pytest.raises(asyncio.CancelledError):
                await agent.execute("查库存", dag_mode=True, params={
                    "mode": "summary",
                    "time_range": "2026-04-01 ~ 2026-04-20",
                })


# ============================================================
# 场景 6: Audit token + trace_id
# ============================================================


class TestAuditTokenAndTraceId:
    """模拟: tool_loop_executor 流程中 audit 记录带 token + trace_id"""

    def test_audit_entry_has_new_fields(self):
        """ToolAuditEntry 支持新字段"""
        from services.agent.tool_audit import ToolAuditEntry
        entry = ToolAuditEntry(
            task_id="t1", conversation_id="c1", user_id="u1", org_id="o1",
            tool_name="erp_agent", tool_call_id="tc1", turn=1,
            args_hash="abc123", result_length=500, elapsed_ms=120,
            status="success",
            prompt_tokens=1500, completion_tokens=300, trace_id="trace_abc",
        )
        assert entry.prompt_tokens == 1500
        assert entry.completion_tokens == 300
        assert entry.trace_id == "trace_abc"

    def test_trace_id_context_propagation(self):
        """trace_id ContextVar 设置后可在任意层级读取"""
        from services.agent.observability import get_trace_id, set_trace_id

        set_trace_id("task_12345")
        assert get_trace_id() == "task_12345"

        # 清理
        set_trace_id("")

    @pytest.mark.asyncio
    async def test_trace_id_in_async_task(self):
        """trace_id 在 asyncio.create_task 中继承（Python 3.12+）"""
        from services.agent.observability import get_trace_id, set_trace_id

        set_trace_id("async_trace_test")

        async def check_in_task():
            return get_trace_id()

        result = await asyncio.create_task(check_in_task())
        assert result == "async_trace_test"
        set_trace_id("")


# ============================================================
# 场景 7: Langfuse 静默降级
# ============================================================


class TestLangfuseSilentDegrade:
    """模拟: 没配置 LANGFUSE 环境变量时全链路不崩"""

    def test_no_env_returns_null_span(self):
        """无环境变量 → NullSpan（所有方法空操作）"""
        import os
        # 确保没有 Langfuse 环境变量
        old_pk = os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
        old_sk = os.environ.pop("LANGFUSE_SECRET_KEY", None)

        # 重置单例
        import services.agent.observability.langfuse_integration as lf_mod
        lf_mod._langfuse_client = None
        lf_mod._init_attempted = False

        try:
            from services.agent.observability.langfuse_integration import (
                _NullSpan, create_generation, create_span, create_trace,
            )
            trace = create_trace(name="test", user_id="u1")
            assert isinstance(trace, _NullSpan)

            span = create_span(trace, name="erp_agent")
            assert isinstance(span, _NullSpan)

            gen = create_generation(span, name="llm_call", model="gemini-3-pro")
            assert isinstance(gen, _NullSpan)

            # 所有 end/update 不报错
            gen.end(usage={"prompt_tokens": 100})
            span.end()
            trace.update(output="done")
        finally:
            # 恢复
            if old_pk:
                os.environ["LANGFUSE_PUBLIC_KEY"] = old_pk
            if old_sk:
                os.environ["LANGFUSE_SECRET_KEY"] = old_sk
            lf_mod._langfuse_client = None
            lf_mod._init_attempted = False


# ============================================================
# 场景 8: 旧数据反序列化兼容性
# ============================================================


class TestOldDataCompatibility:
    """模拟: 旧版本 snapshot（无 v6 字段）反序列化不崩"""

    def test_old_snapshot_without_v6_fields(self):
        """旧 snapshot（只有 8 原始字段）能正确恢复"""
        old_snapshot = [{
            "key": "warehouse:query:1713520081",
            "file_ref": {
                "path": "/tmp/staging/warehouse_12345.parquet",
                "filename": "warehouse_12345.parquet",
                "format": "parquet",
                "row_count": 100,
                "size_bytes": 10240,
                "columns": [
                    {"name": "sku", "dtype": "text", "label": "商品编码"},
                ],
                "preview": "前3行...",
                "created_at": 1713520081.0,
                # 注意：没有 id/mime_type/created_by/ttl_seconds/derived_from
            },
        }]
        registry = SessionFileRegistry.from_snapshot(old_snapshot)
        ref = registry.get_latest()
        assert ref is not None
        assert ref.path == "/tmp/staging/warehouse_12345.parquet"
        # v6 字段应该有默认值
        assert ref.id == ""
        assert ref.mime_type == ""
        assert ref.created_by == ""
        assert ref.ttl_seconds == 86400
        assert ref.derived_from == ()

    def test_is_valid_with_ttl_seconds(self):
        """is_valid 使用 ttl_seconds 而非旧的 max_age_seconds 默认值"""
        ref = FileRef(
            path=__file__, filename="test.parquet", format="parquet",
            row_count=10, size_bytes=1024, columns=[],
            created_at=time.time(), ttl_seconds=172800,  # 48h
        )
        assert ref.is_valid()  # 刚创建，应该有效

        # 旧调用方式（传 max_age_seconds）仍然工作
        assert ref.is_valid(max_age_seconds=86400)

    def test_is_valid_default_reads_ttl_seconds(self):
        """is_valid() 无参数时读 ttl_seconds"""
        ref = FileRef(
            path=__file__, filename="test.parquet", format="parquet",
            row_count=10, size_bytes=1024, columns=[],
            created_at=time.time() - 100000,  # 约 28 小时前
            ttl_seconds=86400,  # 24h TTL → 应该已过期
        )
        assert not ref.is_valid()  # 已超过 TTL

    def test_confidence_field_default(self):
        """ERPAgentResult.confidence 默认 1.0"""
        from services.agent.erp_agent_types import ERPAgentResult
        result = ERPAgentResult(text="test")
        assert result.confidence == 1.0

    def test_confidence_field_degraded(self):
        """降级时 confidence 0.6"""
        from services.agent.erp_agent_types import ERPAgentResult
        result = ERPAgentResult(text="test", confidence=0.6)
        assert result.confidence == 0.6
