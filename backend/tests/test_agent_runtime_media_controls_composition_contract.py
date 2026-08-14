"""228.05 + 228.06 + 228.07 integration contract scaffold.

The 228.05/228.06 migrations live on the F/G integration commits until those
lanes are composed.  The assertions become active automatically once both
migrations are present in the candidate worktree.
"""

from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
M05 = ROOT / "migrations/228_05_agent_runtime_media_manifest_readback.sql"
M06 = ROOT / "migrations/228_06_agent_runtime_media_projection.sql"
M07 = ROOT / "migrations/228_07_agent_runtime_media_controls.sql"


def _composed_sql() -> tuple[str, str, str]:
    missing = [path.name for path in (M05, M06) if not path.exists()]
    if missing:
        pytest.skip(
            "requires 228.05 commit 1b6bce8d and 228.06 commit 0cc37ef2: "
            + ", ".join(missing),
        )
    return tuple(path.read_text(encoding="utf-8") for path in (M05, M06, M07))


def _function(sql: str, name: str) -> str:
    match = re.search(
        rf"CREATE(?: OR REPLACE)? FUNCTION {name}\b.*?\$\$(.*?)\$\$;",
        sql,
        re.DOTALL,
    )
    assert match, name
    return re.sub(r"\s+", " ", match.group(1))


def test_composed_media_retry_contract_and_lock_order() -> None:
    readback, projection, controls = _composed_sql()
    assert "read_agent_runtime_media_provider_request_v1" in readback
    assert "provider_request_hash" in readback

    action_projection = _function(
        projection, "_agent_runtime_media_action_projection_v1",
    )
    task_lock = action_projection.index("FROM tasks")
    binding_lock = action_projection.index(
        "FROM agent_runtime_media_action_bindings", task_lock,
    )
    message_lock = action_projection.index(
        "_agent_runtime_media_slot_update_v1", binding_lock,
    )
    assert task_lock < binding_lock < message_lock
    assert "FOR UPDATE" in action_projection[task_lock:binding_lock]
    assert "FOR UPDATE" in action_projection[binding_lock:message_lock]

    action_only_start = projection.index(
        "CREATE FUNCTION _agent_runtime_media_action_only_run_v1",
    )
    action_only_end = projection.index(
        "CREATE FUNCTION read_agent_runtime_media_projection_v1",
        action_only_start,
    )
    action_only = projection[action_only_start:action_only_end]
    for value in ("runtime_media_retry", "one_shot_action", "media_action_only"):
        assert value in action_only
    run_projection = _function(projection, "_agent_runtime_media_run_projection_v1")
    assert "_agent_runtime_media_action_only_run_v1" in run_projection
    apply_projection = _function(
        projection, "apply_agent_runtime_media_projection_v1",
    )
    assert "_agent_runtime_media_action_only_run_v1" in apply_projection
    assert "checkpoint_only" in apply_projection

    for fragment in (
        "'source','runtime_media_retry'",
        "'execution_mode','one_shot_action'",
        "'projection_mode','media_action_only'",
    ):
        assert fragment in controls
    assert "model_loop_enabled" not in controls
