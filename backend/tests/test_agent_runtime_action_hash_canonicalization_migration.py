import json
from pathlib import Path
from types import SimpleNamespace

from scripts.migration_runner import discover_migrations
from services.agent.runtime.production_model import _actions


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_NAME = "228_01_agent_runtime_action_hash_canonicalization.sql"
ROLLBACK_NAME = (
    "228_01_agent_runtime_action_hash_canonicalization_rollback.sql"
)
SQL = (ROOT / "migrations" / MIGRATION_NAME).read_text(encoding="utf-8")
ROLLBACK = (
    ROOT / "migrations" / "rollback" / ROLLBACK_NAME
).read_text(encoding="utf-8")


def test_migration_is_discovered_with_exact_rollback() -> None:
    discovered = {
        migration.identity: migration
        for migration in discover_migrations(ROOT / "migrations")
    }

    assert discovered[MIGRATION_NAME].rollback_identity == ROLLBACK_NAME


def test_wrapper_canonicalizes_hashes_in_postgres_with_minimum_acl() -> None:
    assert "CREATE OR REPLACE FUNCTION complete_model_attempt_with_raw_actions" in SQL
    assert "SET search_path=pg_catalog,public" in SQL
    assert "PERFORM _assert_agent_runtime_actor(TRUE)" in SQL
    assert "canonical:=_canonical_agent_action_batch(step,p_actions)" in SQL
    assert "batch_hash:=_agent_action_batch_hash(canonical)" in SQL
    assert "'arguments_hash',computed.item->>'arguments_hash'" in SQL
    assert "'request_hash',computed.item->>'request_hash'" in SQL
    assert "'batch_hash',batch_hash" in SQL
    assert "batch_hash,canonical_actions);" in SQL
    assert "TO everydayai_agent_runtime_worker" in SQL
    assert "ALTER TABLE" not in SQL
    assert "DISABLE ROW LEVEL SECURITY" not in SQL
    assert "DROP " not in SQL


def test_rollback_restores_223_wrapper_and_acl() -> None:
    assert "CREATE OR REPLACE FUNCTION complete_model_attempt_with_raw_actions" in ROLLBACK
    assert "SET search_path=pg_catalog,public" in ROLLBACK
    assert "PERFORM _assert_agent_runtime_actor(TRUE)" in ROLLBACK
    assert "batch_hash,p_actions);" in ROLLBACK
    assert "canonical_actions" not in ROLLBACK
    assert "TO everydayai_agent_runtime_worker" in ROLLBACK


def test_production_model_emits_ten_raw_actions_without_database_hashes() -> None:
    tool = SimpleNamespace(
        canonical_name="generate_image",
        safety_level="confirm",
        side_effect="external",
        authorization_requirement="explicit_intent",
        capability_requirements=frozenset({"media.image.generate"}),
        schema_hash="a" * 64,
        executor_revision=1,
    )
    toolset = SimpleNamespace(
        catalog_revision="catalog-v1",
        toolset_hash="b" * 64,
        definitions=(tool,),
        validate_call=lambda _name, _arguments: None,
    )
    result = SimpleNamespace(tool_calls=tuple(
        SimpleNamespace(
            index=index,
            call_id=f"call-{index}",
            provider_call_id=None,
            name="generate_image",
            arguments_json=json.dumps({"prompt": f"variant {index}"}),
        )
        for index in range(10)
    ))

    batch_hash, actions = _actions(result, "run-1", toolset)

    assert batch_hash == "0" * 64
    assert len(actions) == 10
    assert [action["index"] for action in actions] == list(range(10))
    for action in actions:
        assert "arguments_hash" not in action
        assert "request_hash" not in action
        assert "batch_hash" not in action
