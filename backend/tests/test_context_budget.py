"""模型能力驱动的上下文预算测试。"""

from services.agent.runtime.context import (
    derive_context_budget,
    resolve_context_budget,
)


def test_budget_reserves_output_and_safety_margin() -> None:
    budget = derive_context_budget(1_000_000, 65_536)

    assert budget.reserved_output == 125_000
    assert budget.safety_margin == 50_000
    assert budget.usable_input == 825_000
    assert budget.soft_compaction == 618_750
    assert budget.hard_compaction == 701_250
    assert budget.emergency_trim == 759_000


def test_large_model_output_reserve_wins_over_window_ratio() -> None:
    budget = derive_context_budget(131_072, 65_536)

    assert budget.reserved_output == 65_536
    assert budget.safety_margin == 6_553
    assert budget.usable_input == 58_983
    assert budget.hard_compaction == 50_135


def test_unknown_model_uses_conservative_default_capability() -> None:
    budget = resolve_context_budget("unknown-model")

    assert budget.context_window == 128_000
    assert budget.reserved_output == 16_000
    assert budget.safety_margin == 6_400
    assert budget.hard_compaction == 89_760
