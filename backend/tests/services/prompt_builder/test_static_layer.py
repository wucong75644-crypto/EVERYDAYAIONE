"""StaticLayer 单元测试。"""

from __future__ import annotations

from services.prompt_builder.layers.static_layer import StaticLayer


class TestStaticLayer:
    def test_render_contains_all_five_sections(self):
        """Layer 1 必须含 5 个 XML 标签段。"""
        content = StaticLayer.render()
        assert "<role>" in content
        assert "</role>" in content
        assert "<rules>" in content
        assert "</rules>" in content
        assert "<workflow>" in content
        assert "</workflow>" in content
        assert "<tool_strategy>" in content
        assert "</tool_strategy>" in content
        assert "<permission_mode>" in content
        assert "</permission_mode>" in content

    def test_render_idempotent(self):
        """LRU 缓存: 多次调用返回完全相同内容。"""
        a = StaticLayer.render()
        b = StaticLayer.render()
        assert a is b or a == b

    def test_tool_strategy_contains_number_cite_constraint(self):
        """关键约束: 数字 cite (防止 LLM 编造数字)。"""
        content = StaticLayer.render()
        assert "数字 cite 约束" in content
        assert "tool_result" in content
        assert "凑数等式" in content or "凑数" in content

    def test_role_describes_evidence_only_behavior(self):
        """role 段必须强调"通过工具获取真实数据"。"""
        content = StaticLayer.render()
        assert "不能凭印象回答" in content
        assert "通过工具获取真实数据" in content

    def test_workflow_lists_three_modes(self):
        """workflow 段必须包含三种模式。"""
        content = StaticLayer.render()
        assert "直接模式" in content
        assert "计划模式" in content
        assert "提问模式" in content

    def test_permission_mode_lists_auto_plan_ask(self):
        """permission_mode 段必须涵盖三种权限模式。"""
        content = StaticLayer.render()
        assert "auto 模式" in content
        assert "plan 模式" in content
        assert "ask 模式" in content

    def test_no_tool_schema_duplication(self):
        """不允许 system 里重复 tools 字段的 schema (Anthropic 官方推荐)。"""
        content = StaticLayer.render()
        # tools 字段已经包含 function calling schema, system 里不应有
        assert "function_call" not in content
        # 工具说明章节标记应已删除 (旧 TOOL_SYSTEM_PROMPT 的 "## 工具说明" 是重复)
        assert "## 工具说明" not in content

    def test_render_size_bounded(self):
        """字符数应在合理范围 (4000-8000 字符 ≈ 1500-2500 token)。"""
        content = StaticLayer.render()
        assert 4000 < len(content) < 8000, f"unexpected size: {len(content)}"
