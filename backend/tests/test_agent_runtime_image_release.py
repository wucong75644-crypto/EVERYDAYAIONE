from pathlib import Path

from services.agent.runtime.catalog.image_release import build_image_snapshot
from scripts.generate_agent_runtime_image_seed import main as generate_seed


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/230_06_agent_runtime_catalog_image_v13.sql"
ROLLBACK = ROOT / "migrations/rollback/230_06_agent_runtime_catalog_image_v13_rollback.sql"


def test_image_release_exposes_only_generate_image_when_enabled() -> None:
    snapshot = build_image_snapshot(
        scope="user", channel="web", gate_state="enabled",
    )
    catalog_names = {
        tool["canonical_name"] for tool in snapshot.catalog_document["tools"]
    }
    assert catalog_names == {"generate_image"}
    assert snapshot.toolset_document["tool_names"] == ["generate_image"]
    assert snapshot.definition.revision == "v13"
    assert snapshot.definition.prompt_revision == "agent-runtime-image-v1"


def test_image_release_gate_hides_tool_and_rollback_is_guarded() -> None:
    snapshot = build_image_snapshot(
        scope="user", channel="web", gate_state="disabled",
    )
    assert snapshot.toolset_document["tool_names"] == []
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "definition_revision='v13'" in rollback
    assert "AGENT_RUNTIME_CATALOG_IMAGE_V13_ROLLBACK_GUARD" in rollback


def test_image_release_generator_is_byte_deterministic(tmp_path: Path) -> None:
    generated = tmp_path / MIGRATION.name
    generate_seed(generated)
    assert generated.read_bytes() == MIGRATION.read_bytes()
