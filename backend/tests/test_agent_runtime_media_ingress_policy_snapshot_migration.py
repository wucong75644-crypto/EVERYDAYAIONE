from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/230_10_agent_runtime_media_ingress_policy_snapshot.sql"
ROLLBACK = ROOT / (
    "migrations/rollback/"
    "230_10_agent_runtime_media_ingress_policy_snapshot_rollback.sql"
)


def test_media_ingress_policy_snapshot_is_bound_to_frozen_tool_facts() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "agent_runtime_effective_toolset_facts" in sql
    assert "'safety_level',tool_fact->>'safety_level'" in sql
    assert "'side_effect',tool_fact->>'side_effect'" in sql
    assert "'authorization_requirement',tool_fact->>'authorization_requirement'" in sql
    assert "effective_toolset_hash" in sql
    assert "AGENT_RUNTIME_MEDIA_TOOL_FACT_MISSING" in sql


def test_media_activation_opens_runtime_v3_control_flags() -> None:
    script = (ROOT.parent / "deploy/enable-agent-runtime-media.sh").read_text(
        encoding="utf-8",
    )
    assert '"non_safe_actions_enabled": True' in script
    assert '"tool_confirmation_enabled": True' in script
    assert '"code_execute_enabled": True' not in script
    assert "TOOL_CONFIRMATION_V3_ENABLED" in script
    assert "TOOL_CONFIRMATION_CAPABILITY_NOT_READY" in script
    assert "set_agent_runtime_control" in script


def test_rollback_restores_the_prior_function_contract() -> None:
    sql = ROLLBACK.read_text(encoding="utf-8")
    assert "CREATE OR REPLACE FUNCTION submit_agent_runtime_media_action_v1" in sql
    assert "'capability_revision','v1'" in sql
    assert "'safety_level'" not in sql
