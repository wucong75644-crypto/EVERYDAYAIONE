"""PersonaGate 单元测试。"""

from __future__ import annotations

from services.prompt_builder.persona_gate import PersonaGate, default_gate


class TestPersonaGate:
    def test_default_gate_enabled(self):
        assert default_gate().enabled is True

    def test_enabled_passes_non_empty(self):
        g = PersonaGate(enabled=True)
        assert g.filter("用户是数据分析师") == "用户是数据分析师"
        assert g.should_inject("user_persona") is True

    def test_enabled_blocks_none_and_empty(self):
        g = PersonaGate(enabled=True)
        assert g.filter(None) is None
        assert g.filter("") is None
        assert g.filter("   ") is None
        assert g.should_inject(None) is False

    def test_disabled_blocks_everything(self):
        g = PersonaGate(enabled=False)
        assert g.filter("a persona") is None
        assert g.filter("user data") is None
        assert g.should_inject("anything") is False
