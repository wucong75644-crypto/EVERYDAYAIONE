"""
E2E 模拟测试：code_execute 结果分流全链路

模拟真实场景：用户上传 308×1404 宽表利润表 → AI 探索 → 计算。
验证沙盒→信封→messages 全链路的正确行为。
"""

import pytest

from services.sandbox.validators import truncate_result
from services.agent.tool_result_envelope import (
    wrap, wrap_for_erp_agent,
    set_staging_dir, clear_staging_dir,
    PERSISTED_OUTPUT_TAG, CODE_EXECUTE_BUDGET,
)


# ============================================================
# 场景 1：code_execute 探索宽表 — 结果分流链路
# ============================================================


class TestE2ECodeExecuteFlow:
    """模拟 AI 用 code_execute 探索宽表，验证结果分流"""

    @pytest.fixture(autouse=True)
    def setup_staging(self, tmp_path):
        set_staging_dir(str(tmp_path))
        self.staging_dir = tmp_path
        yield
        clear_staging_dir()

    def test_small_exploration_passes_through(self):
        """场景：AI 用 df.shape + df.columns[:20] 探索，输出 ~5K 字符"""
        # 模拟沙盒输出
        sandbox_output = (
            "形状: (308, 1404)\n"
            "前20列: ['科目', '蓝创旗舰店(淘宝)_2024-01', "
            "'蓝创旗舰店(淘宝)_2024-02', ...]\n"
            "类型分布: {'float64': 1392, 'object': 12}\n"
            + "x" * 3000  # 凑到 ~5K
        )

        # 1. 沙盒截断（50K 上限，5K 不触发）
        after_sandbox = truncate_result(sandbox_output)
        assert after_sandbox == sandbox_output  # 没截断

        # 2. 信封处理（30K 预算，5K 不触发）
        after_envelope = wrap_for_erp_agent("code_execute", after_sandbox)
        assert after_envelope == sandbox_output  # 直接回传
        assert PERSISTED_OUTPUT_TAG not in after_envelope

        # 3. is_truncated 检测
        is_truncated = (
            PERSISTED_OUTPUT_TAG in after_envelope
            or "⚠ 输出过长" in after_envelope
        )
        assert is_truncated is False

    def test_full_columns_list_passes_through(self):
        """场景：AI print(df.columns.tolist())，1404 列名 ≈ 21K 字符"""
        # 模拟 1404 个列名
        col_names = ["科目"]
        stores = [f"店铺{chr(65 + i)}" for i in range(6)]
        months = [f"2024-{m:02d}" for m in range(1, 13)]
        for s in stores:
            for m in months:
                col_names.append(f"{s}_{m}")
        # 扩展到接近 1404 列
        while len(col_names) < 1404:
            col_names.append(f"额外列_{len(col_names)}")

        sandbox_output = f"列名: {col_names}"
        output_len = len(sandbox_output)
        print(f"模拟列名输出长度: {output_len} 字符")

        # 1. 沙盒截断（50K 上限）
        after_sandbox = truncate_result(sandbox_output)
        assert "已截断" not in after_sandbox  # 21K < 50K，不截断

        # 2. 信封处理（30K 预算）
        after_envelope = wrap_for_erp_agent("code_execute", after_sandbox)
        if output_len <= CODE_EXECUTE_BUDGET:
            assert after_envelope == sandbox_output  # ≤30K 直接回传
            print("✅ 列名输出在 30K 预算内，AI 能看到全部列名")
        else:
            assert PERSISTED_OUTPUT_TAG in after_envelope  # >30K 落盘
            print(f"⚠ 列名输出 {output_len} > 30K，落盘到 staging")

    def test_large_computation_persisted(self):
        """场景：AI 计算完整利润报表，输出 40K 字符"""
        sandbox_output = "利润汇总报表\n" + "\n".join(
            f"店铺{i}: 营收={i*10000}, 利润={i*3000}, 利润率={30+i}%"
            for i in range(1500)
        )
        print(f"模拟计算输出长度: {len(sandbox_output)} 字符")

        # 1. 沙盒截断
        after_sandbox = truncate_result(sandbox_output)
        if len(sandbox_output) > 50000:
            assert "已截断" in after_sandbox
        else:
            assert after_sandbox == sandbox_output

        # 2. 信封处理
        after_envelope = wrap_for_erp_agent("code_execute", after_sandbox)
        assert PERSISTED_OUTPUT_TAG in after_envelope  # >30K 落盘
        assert "结果概览" in after_envelope  # 结构化预览
        assert "利润汇总报表" in after_envelope  # 预览包含首行

        # 3. staging 文件实际存在
        staging_files = list(self.staging_dir.glob("tool_result_code_execute_*.txt"))
        assert len(staging_files) == 1
        # 验证 staging 文件包含完整数据
        content = staging_files[0].read_text(encoding="utf-8")
        assert len(content) >= 30000

        print(f"✅ 大结果落盘到 staging: {staging_files[0].name}")
        print(f"   staging 文件大小: {len(content)} 字符")
        print(f"   AI 看到的预览: {after_envelope[:200]}...")

    def test_sandbox_truncation_then_envelope(self):
        """场景：沙盒输出 80K → 沙盒截断到 50K → 信封落盘到 staging"""
        sandbox_output = "x" * 80000

        # 1. 沙盒截断（80K > 50K → 截断）
        after_sandbox = truncate_result(sandbox_output)
        assert "已截断" in after_sandbox
        assert len(after_sandbox) < 80000

        # 2. 信封处理（截断后仍 >30K → 落盘）
        after_envelope = wrap_for_erp_agent("code_execute", after_sandbox)
        assert PERSISTED_OUTPUT_TAG in after_envelope

        # 3. is_truncated
        is_truncated = (
            PERSISTED_OUTPUT_TAG in after_envelope
            or "⚠ 输出过长" in after_envelope
        )
        assert is_truncated is True
        print("✅ 80K → 沙盒截断 50K → 信封落盘 → 预览 2K")


# ============================================================
# 场景 2：对比旧行为 — 确认改善
# ============================================================


class TestE2EImprovement:
    """验证改进效果：旧行为 vs 新行为"""

    @pytest.fixture(autouse=True)
    def setup_staging(self, tmp_path):
        set_staging_dir(str(tmp_path))
        yield
        clear_staging_dir()

    def test_old_vs_new_21k_result(self):
        """21K 字符的列名输出：旧行为截断到 8K，新行为完整保留"""
        # 用真实中文列名模拟（平均 ~20 字符/列名）
        stores = ["蓝创旗舰店(淘宝)", "蓝创专卖店(京东)", "蓝创官方(拼多多)",
                  "蓝创自营(抖音)", "蓝创特卖(快手)", "蓝创精品(小红书)"]
        months = [f"2024-{m:02d}" for m in range(1, 13)]
        col_names = ["科目"]
        for s in stores:
            for m in months:
                col_names.append(f"{s}_{m}")
        result_21k = "列名: " + ", ".join(col_names)
        # 扩展到接近真实 1404 列的长度
        while len(result_21k) < 20000:
            result_21k += ", " + ", ".join(f"额外店铺{i}_{m}" for i, m in zip(range(50), months))
        print(f"模拟输出长度: {len(result_21k)} 字符")

        # 旧行为：沙盒 8K 截断
        old_sandbox = truncate_result(result_21k, max_chars=8000)
        assert "已截断" in old_sandbox
        old_visible_chars = 8000  # AI 只看到 8K

        # 新行为：沙盒 50K 不截断 + 信封 30K 通过
        new_sandbox = truncate_result(result_21k, max_chars=50000)
        assert new_sandbox == result_21k  # 不截断
        new_envelope = wrap_for_erp_agent("code_execute", new_sandbox)
        assert new_envelope == result_21k  # 30K 预算内，直接回传
        new_visible_chars = len(result_21k)  # AI 看到全部

        improvement = new_visible_chars / old_visible_chars
        print(f"旧行为: AI 看到 {old_visible_chars} 字符（截断）")
        print(f"新行为: AI 看到 {new_visible_chars} 字符（完整）")
        print(f"改善: {improvement:.1f}x")
        assert improvement > 2  # 至少 2 倍改善

    def test_erp_tools_unchanged(self):
        """ERP 内部工具行为不变：3K 预算"""
        result = "订单列表\n" + "\n".join(f"订单{i}" for i in range(200))
        wrapped = wrap_for_erp_agent("local_order_query", result)
        if len(result) > 3000:
            assert PERSISTED_OUTPUT_TAG in wrapped
        else:
            assert wrapped == result
