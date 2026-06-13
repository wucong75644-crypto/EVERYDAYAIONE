"""TurnDynamicLayer (L2b) 单元测试 - V2."""

from __future__ import annotations

from services.prompt_builder.layers.turn_dynamic_layer import (
    TurnDynamicContext,
    TurnDynamicLayer,
)


class TestTurnDynamicLayer:
    def test_minimal_render_has_current_time(self):
        """最小输入只有 current_time."""
        ctx = TurnDynamicContext(current_time_text="2026-06-13 22:30 UTC+8")
        out = TurnDynamicLayer.render(ctx)
        assert "<turn>" in out
        assert "<current_time>2026-06-13 22:30 UTC+8</current_time>" in out
        assert "<user_location>" not in out

    def test_location_optional(self):
        """user_location 为 None 时不渲染."""
        ctx_with = TurnDynamicContext(
            current_time_text="2026-06-13",
            user_location="金华市",
        )
        out = TurnDynamicLayer.render(ctx_with)
        assert "<user_location>金华市</user_location>" in out

    def test_no_permission_mode(self):
        """L2b 不应包含 permission_mode (这是 L2a 的职责)."""
        ctx = TurnDynamicContext(current_time_text="2026-06-13")
        out = TurnDynamicLayer.render(ctx)
        assert "permission_mode" not in out

    def test_no_persona(self):
        """L2b 不应包含 persona/facts/memory (都是 L2a 的职责)."""
        ctx = TurnDynamicContext(current_time_text="2026-06-13")
        out = TurnDynamicLayer.render(ctx)
        assert "user_facts" not in out
        assert "user_memory" not in out
        assert "user_preferences" not in out

    def test_small_size(self):
        """L2b 必须保持很小 (< 200 字符), 因为不参与 cache."""
        ctx = TurnDynamicContext(
            current_time_text="当前时间: 2026-06-13 22:30 周五 UTC+8",
            user_location="浙江省金华市",
        )
        out = TurnDynamicLayer.render(ctx)
        assert len(out) < 200, f"L2b 太大 ({len(out)} chars), 会浪费每次请求 token"
