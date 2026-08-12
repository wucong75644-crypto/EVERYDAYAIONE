from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from services.agent.runtime.catalog.data_read_release import (
    build_data_read_catalog, build_data_read_snapshot,
)
from services.agent.runtime.executors.read_registry import SAFE_READ_TOOL_NAMES
from services.agent.runtime.executors.specialist_registry import (
    ARTIFACT_JOB_TOOLS, ERP_RUNTIME_READ_TOOLS,
)
from scripts.generate_agent_runtime_data_read_seed import main as generate_seed


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_58_agent_runtime_data_read_release.sql"
ROLLBACK = ROOT / "migrations/rollback/227_58_agent_runtime_data_read_release_rollback.sql"


def test_data_read_release_is_exact_and_read_only() -> None:
    catalog = build_data_read_catalog()
    names = {tool.canonical_name for tool in catalog.definitions()}
    assert names == SAFE_READ_TOOL_NAMES | ERP_RUNTIME_READ_TOOLS | ARTIFACT_JOB_TOOLS
    assert not names.intersection({
        "erp_execute", "trigger_erp_sync", "generate_image", "generate_video",
        "manage_scheduled_task", "file_delete", "restore_file",
    })
    local = next(tool for tool in catalog.definitions() if tool.canonical_name == "local_data")
    assert local.schema["properties"]["query_type"]["enum"] == [
        "trend", "compare", "cross", "distribution",
    ]


def test_data_read_disabled_toolset_excludes_confirmed_operations() -> None:
    snapshot = build_data_read_snapshot(
        scope="channel", channel="web", gate_state="disabled",
    )
    names = set(snapshot.toolset_document["tool_names"])
    assert "local_data" in names
    assert "file_analyze" not in names
    assert "fetch_all_pages" not in names


def test_data_read_release_seed_matches_python_ssot() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    match = re.search(
        r"INSERT INTO agent_runtime_catalog_facts.*?\$seed\$(.*?)\$seed\$::JSONB",
        sql, re.DOTALL,
    )
    assert match is not None
    stored = json.loads(match.group(1))
    expected = build_data_read_snapshot(
        scope="user", channel="web", gate_state="enabled",
    ).catalog_document
    assert stored == expected
    assert sql.count("FALSE, TRUE") == 8
    assert "definition_revision='v6'" in ROLLBACK.read_text(encoding="utf-8")


def test_data_read_release_generator_is_byte_deterministic(tmp_path: Path) -> None:
    generated = tmp_path / MIGRATION.name
    generate_seed(generated)
    assert generated.read_bytes() == MIGRATION.read_bytes()


@pytest.mark.parametrize("tool", ["local_data", "file_analyze", "fetch_all_pages"])
def test_production_registry_requires_explicit_data_port(tool: str) -> None:
    from services.agent.runtime.production_composition import (
        ProductionSpecialistPorts, build_production_specialist_registry,
    )

    ports = {
        "local_data": object(),
        "file_analyze": object(),
        "fetch_all_pages": object(),
    }
    ports[tool] = None
    with pytest.raises(RuntimeError, match=f"SERVICE_WIRING_NOT_READY:{tool}"):
        build_production_specialist_registry(
            ProductionSpecialistPorts(
                transport=object(), erp_dispatcher=object(), erp_search=object(),
                artifact=object(), media_task=object(), resource_mutation=object(),
                child_run=object(), local_data=ports["local_data"],
                file_analyze=ports["file_analyze"],
                fetch_all_pages=ports["fetch_all_pages"],
            ),
            facts=object(),
        )
