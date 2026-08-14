from __future__ import annotations

import json
import re
from pathlib import Path

from services.agent.runtime.catalog.erp_read_release import (
    build_erp_read_catalog, build_erp_read_snapshot,
)
from services.agent.runtime.executors.read_registry import SAFE_READ_TOOL_NAMES
from services.agent.runtime.executors.specialist_registry import (
    ERP_RUNTIME_READ_TOOLS,
)
from scripts.generate_agent_runtime_erp_read_seed import main as generate_seed


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/227_55_agent_runtime_erp_read_release.sql"
ROLLBACK = ROOT / "migrations/rollback/227_55_agent_runtime_erp_read_release_rollback.sql"


def test_erp_read_release_is_exact_and_excludes_unapproved_tools() -> None:
    catalog = build_erp_read_catalog()
    names = {tool.canonical_name for tool in catalog.definitions()}
    assert names == SAFE_READ_TOOL_NAMES | ERP_RUNTIME_READ_TOOLS
    assert "erp_execute" not in names
    assert "trigger_erp_sync" not in names
    assert "erp_taobao_query" not in names
    assert "generate_image" not in names
    for tool in catalog.definitions():
        if tool.canonical_name in ERP_RUNTIME_READ_TOOLS:
            assert tool.tool_group == "erp"
            assert tool.safety_level == "safe"
            assert tool.side_effect == "none"
            assert tool.capability_requirements == frozenset({
                "network.provider.read",
            })


def test_erp_read_release_seed_matches_python_ssot_and_rolls_back_v5() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    match = re.search(
        r"INSERT INTO agent_runtime_catalog_facts.*?\$seed\$(.*?)\$seed\$::JSONB",
        sql, re.DOTALL,
    )
    assert match is not None
    stored = json.loads(match.group(1))
    expected = build_erp_read_snapshot(
        scope="user", channel="web", gate_state="enabled",
    ).catalog_document
    assert stored == expected
    assert sql.count("FALSE, TRUE") == 8
    rollback = ROLLBACK.read_text(encoding="utf-8")
    assert "definition_revision='v5'" in rollback
    assert "AGENT_RUNTIME_ERP_READ_RELEASE_FACTS_EXIST" in rollback


def test_erp_read_release_generator_is_byte_deterministic(tmp_path: Path) -> None:
    generated = tmp_path / MIGRATION.name
    generate_seed(generated)
    assert generated.read_bytes() == MIGRATION.read_bytes()
