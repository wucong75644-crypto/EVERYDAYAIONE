"""Static contracts for Runtime media Action/Task bindings."""

from __future__ import annotations

from pathlib import Path

from scripts.generate_agent_runtime_media_pricing_seed import (
    build_pricing_rows,
    main as generate_pricing,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/228_04_agent_runtime_media_action_bindings.sql"
ROLLBACK = ROOT / (
    "migrations/rollback/228_04_agent_runtime_media_action_bindings_rollback.sql"
)


def test_generated_pricing_matches_kie_configuration(tmp_path: Path) -> None:
    rows = build_pricing_rows()
    assert {row["model_id"] for row in rows} == {
        "google/nano-banana", "google/nano-banana-edit",
        "nano-banana-pro", "gpt-image-2-text-to-image",
        "gpt-image-2-image-to-image",
    }
    prices = {
        (row["model_id"], row["resolution_key"]): row["user_credits"]
        for row in rows
    }
    assert prices[("nano-banana-pro", "4K")] == 49
    assert prices[("gpt-image-2-text-to-image", "2K")] == 10
    assert prices[("google/nano-banana", "default")] == 5

    generated = tmp_path / MIGRATION.name
    generated.write_bytes(MIGRATION.read_bytes())
    generate_pricing(generated)
    assert generated.read_bytes() == MIGRATION.read_bytes()


def test_binding_contract_is_narrow_fenced_and_atomic() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE agent_runtime_media_action_bindings" in sql
    assert "action_index INTEGER NOT NULL CHECK (action_index BETWEEN 0 AND 9)" in sql
    assert "action_arguments_hash TEXT NOT NULL" in sql
    assert "provider_request_hash TEXT NOT NULL" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "prepare_agent_runtime_media_batch_v1" in sql
    assert "read_agent_runtime_media_binding_v1" in sql
    assert "credits >= total_credits" in sql
    assert "AGENT_RUNTIME_MEDIA_INTERNAL_ARGUMENT_FORBIDDEN" in sql
    assert "agent_runtime_media_pricing_facts" in sql
    assert "AGENT_RUNTIME_MEDIA_PRICING_IMMUTABLE" in sql
    assert "BEFORE INSERT OR UPDATE OR DELETE" in sql
    assert "agent_runtime_provider_submission_facts" in sql
    assert "SELECT 1 FROM agent_runtime_media_action_bindings binding" in sql
    assert "TO everydayai_agent_runtime_worker" in sql
    assert "TO everydayai_projection_worker" in sql
    assert "GRANT SELECT" not in sql


def test_rollback_is_guarded_and_restores_worker_discovery() -> None:
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "AGENT_RUNTIME_MEDIA_BINDINGS_IN_USE" in rollback
    assert "DROP TABLE agent_runtime_media_action_bindings" in rollback
    assert "DROP TABLE agent_runtime_media_pricing_facts" in rollback
    assert "CREATE OR REPLACE FUNCTION worker_discover_media_tasks" in rollback
    assert "organization.status = 'active'" in rollback
    assert "agent_runtime_media_action_bindings binding" not in rollback
