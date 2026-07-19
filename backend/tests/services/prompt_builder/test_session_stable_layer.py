"""SessionStableLayer (L2a) 单元测试 - V2."""

from __future__ import annotations

from services.prompt_builder.layers.session_stable_layer import (
    SessionStableContext,
    SessionStableLayer,
)


class TestSessionStableLayer:
    def test_minimal_render_has_permission_mode(self):
        """最小输入只有 permission_mode."""
        ctx = SessionStableContext(permission_mode="auto")
        out = SessionStableLayer.render(ctx)
        assert "<context>" in out
        assert "<current_mode>auto</current_mode>" in out
        assert "<user_preferences>" not in out
        assert "<user_facts>" not in out
        assert "<user_memory>" not in out

    def test_user_preferences_injection(self):
        """有 user_preferences 时渲染独立块."""
        ctx = SessionStableContext(
            permission_mode="auto",
            user_preferences="我喜欢简洁回答",
        )
        out = SessionStableLayer.render(ctx)
        assert "<user_preferences>" in out
        assert "简洁回答" in out

    def test_user_facts_injection(self):
        """user_facts 进来即注入 (gate 在 builder 上游)."""
        ctx = SessionStableContext(
            permission_mode="auto",
            user_facts="- 公司: LCWJ\n- 主营京东",
        )
        out = SessionStableLayer.render(ctx)
        assert "<user_facts>" in out
        assert "LCWJ" in out

    def test_user_memory_injection(self):
        """Curated Memory 召回结果在会话首次加载后注入。"""
        ctx = SessionStableContext(
            permission_mode="auto",
            user_memory="- 用户偏好淘宝平台",
        )
        out = SessionStableLayer.render(ctx)
        assert "<user_memory>" in out
        assert "淘宝平台" in out

    def test_plan_mode_in_context(self):
        """plan 模式正确呈现."""
        ctx = SessionStableContext(permission_mode="plan")
        out = SessionStableLayer.render(ctx)
        assert "<current_mode>plan</current_mode>" in out

    def test_no_current_time(self):
        """L2a 不应包含 current_time (这是 L2b 的职责)."""
        ctx = SessionStableContext(permission_mode="auto")
        out = SessionStableLayer.render(ctx)
        assert "current_time" not in out

    def test_empty_string_treated_as_none(self):
        """空字符串等同于 None."""
        ctx = SessionStableContext(
            permission_mode="auto",
            user_preferences="",
            user_facts="",
            user_memory="",
        )
        out = SessionStableLayer.render(ctx)
        assert "<user_preferences>" not in out
        assert "<user_facts>" not in out
        assert "<user_memory>" not in out
