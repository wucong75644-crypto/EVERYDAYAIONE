from __future__ import annotations

import json

import psycopg
import pytest

from services.agent.runtime.catalog.registry import restore_frozen_toolset
from tests.agent_runtime_scheduled_toolset_support import (
    canonical_target as _canonical_target,
    create_profile as _profile,
)
from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import (
    _create_runtime_task,
    _setup,
)


pytestmark = pytest.mark.external


@pytest.mark.parametrize(("scope", "expected_count"), (("user", 9), ("channel", 17)))
def test_profile_snapshot_restores_with_exact_runtime_hash(
    database: str, scope: str, expected_count: int,
) -> None:
    _setup(database)
    task_id, ids = _create_runtime_task(database, scope=scope)
    profile = _profile(database, task_id, ids)["profile"]
    with psycopg.connect(database) as conn:
        definition, catalog = conn.execute(
            "SELECT d.definition_document,c.catalog_document "
            "FROM agent_runtime_definition_facts d JOIN agent_runtime_catalog_facts c "
            "ON c.catalog_revision=d.catalog_revision WHERE d.agent_key=%s "
            "AND d.definition_revision=%s AND c.catalog_revision=%s",
            (profile["agent_definition_id"], profile["agent_definition_revision"],
             profile["catalog_revision"]),
        ).fetchone()
        assert conn.execute(
            "SELECT has_function_privilege('everydayai_agent_runtime_worker',"
            "'_agent_runtime_scheduled_canonical_json(jsonb)','EXECUTE')"
        ).fetchone()[0] is False
    restored = restore_frozen_toolset(
        definition, catalog, profile["toolset_snapshot"],
        catalog_revision=profile["catalog_revision"],
    )
    assert restored.toolset_hash == profile["effective_toolset_hash"]
    assert len(restored.definitions) == expected_count
    assert profile["toolset_snapshot"]["entitled_groups"]


def test_profile_rejects_tampered_groups_schema_and_hash(database: str) -> None:
    _setup(database)
    for mutation in ("entitled_groups", "schema", "hash", "serialization"):
        task_id, ids = _create_runtime_task(database)
        target = _canonical_target(database, ids)
        document = json.loads(json.dumps(target.document))
        hash_value = target.toolset_hash
        canonical = target.canonical_hash_input
        expected = "TARGET_TOOLSET_INVALID"
        if mutation == "entitled_groups":
            document["entitled_groups"] = ["artifact"]
        elif mutation == "schema":
            document["tools"][0]["schema"] = {"type": "object"}
        else:
            expected = "TOOLSET_HASH_INVALID"
            if mutation == "hash":
                hash_value = "f" * 64
                document["toolset_hash"] = hash_value
            else:
                canonical = json.dumps(json.loads(canonical), indent=1, sort_keys=True)
        with pytest.raises(Exception, match=expected):
            _profile(
                database, task_id, ids, snapshot=document,
                toolset_hash=hash_value,
                canonical_hash_input=canonical,
            )
