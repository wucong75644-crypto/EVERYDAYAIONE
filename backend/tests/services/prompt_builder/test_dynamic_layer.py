"""DynamicLayer 单元测试。"""

from __future__ import annotations

from services.prompt_builder.layers.dynamic_layer import (
    DynamicContext,
    DynamicLayer,
)


class TestDynamicLayer:
    def test_minimal_render_has_context_block(self):
        """最小输入只有时间, 应只渲染 <context> 块。"""
        ctx = DynamicContext(
            current_time_text="2026-06-11 23:00 周三 UTC+8",
            permission_mode="auto",
        )
        out = DynamicLayer.render(ctx)
        assert "<context>" in out
        assert "<current_time>" in out
        assert "2026-06-11" in out
        assert "<permission_mode>auto</permission_mode>" in out
        # 不应有 user_preferences / user_profile / relevant_memory
        assert "<user_preferences>" not in out
        assert "<user_profile>" not in out
        assert "<relevant_memory>" not in out

    def test_location_optional(self):
        """user_location 为 None 时不渲染。"""
        ctx = DynamicContext(
            current_time_text="2026-06-11",
            permission_mode="auto",
        )
        out = DynamicLayer.render(ctx)
        assert "<user_location>" not in out

        ctx_with_loc = DynamicContext(
            current_time_text="2026-06-11",
            permission_mode="auto",
            user_location="金华市",
        )
        out2 = DynamicLayer.render(ctx_with_loc)
        assert "<user_location>金华市</user_location>" in out2

    def test_user_preferences_injection(self):
        """有 user_preferences 时渲染独立块。"""
        ctx = DynamicContext(
            current_time_text="2026-06-11",
            permission_mode="auto",
            user_preferences="我喜欢简洁回答, 不要冗长解释",
        )
        out = DynamicLayer.render(ctx)
        assert "<user_preferences>" in out
        assert "简洁回答" in out

    def test_persona_only_when_provided(self):
        """persona 必须经过 gate 后传入, 进来即注入。"""
        ctx_with = DynamicContext(
            current_time_text="2026-06-11",
            permission_mode="auto",
            persona="用户是数据分析师, 关注 ETL 协议",
        )
        out = DynamicLayer.render(ctx_with)
        assert "<user_profile>" in out
        assert "ETL 协议" in out

        ctx_without = DynamicContext(
            current_time_text="2026-06-11",
            permission_mode="auto",
            persona=None,
        )
        out_n = DynamicLayer.render(ctx_without)
        assert "<user_profile>" not in out_n

    def test_relevant_memory_injection(self):
        """L1 memory prepend 进来即注入。"""
        ctx = DynamicContext(
            current_time_text="2026-06-11",
            permission_mode="auto",
            relevant_memory="用户偏好淘宝平台",
        )
        out = DynamicLayer.render(ctx)
        assert "<relevant_memory>" in out
        assert "淘宝平台" in out

    def test_empty_string_treated_as_none(self):
        """空字符串等同于 None。"""
        ctx = DynamicContext(
            current_time_text="2026-06-11",
            permission_mode="auto",
            user_preferences="",
            persona="",
            relevant_memory="",
        )
        out = DynamicLayer.render(ctx)
        assert "<user_preferences>" not in out
        assert "<user_profile>" not in out
        assert "<relevant_memory>" not in out

    def test_plan_mode_in_context(self):
        """plan 模式正确呈现。"""
        ctx = DynamicContext(
            current_time_text="2026-06-11",
            permission_mode="plan",
        )
        out = DynamicLayer.render(ctx)
        assert "<permission_mode>plan</permission_mode>" in out
