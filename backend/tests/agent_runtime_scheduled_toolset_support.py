from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb

from services.agent.runtime.catalog.scheduled_toolset import (
    canonicalize_scheduled_toolset,
)
from tests.test_agent_runtime_ar18_b7_scheduler_control_postgres_external import _rpc


def canonical_target(url: str, ids: dict[str, str]):
    with psycopg.connect(url) as conn:
        definition, catalog, source, revision = conn.execute(
            "SELECT d.definition_document,cat.catalog_document,ts.toolset_document,"
            "cat.catalog_revision FROM agent_runs r "
            "JOIN agent_runtime_sessions s ON s.id=r.session_id "
            "JOIN agent_runtime_definition_facts d ON d.agent_key=s.agent_definition_id "
            "AND d.definition_revision=s.agent_definition_revision "
            "JOIN agent_runtime_catalog_facts cat ON cat.catalog_revision=d.catalog_revision "
            "JOIN agent_runtime_effective_toolset_facts ts ON ts.agent_key=d.agent_key "
            "AND ts.definition_revision=d.definition_revision "
            "AND ts.catalog_revision=cat.catalog_revision AND ts.scope_kind=s.scope_kind "
            "AND ts.channel=r.capability_snapshot->>'channel' "
            "AND ts.gate_state=r.capability_snapshot->>'gate_state' WHERE r.id=%s",
            (ids["run"],),
        ).fetchone()
    return canonicalize_scheduled_toolset(
        definition, catalog, source, catalog_revision=revision,
    )


def create_profile(
    url: str, task_id: str, ids: dict[str, str], *,
    snapshot: dict[str, object] | None = None,
    toolset_hash: str | None = None,
    canonical_hash_input: str | None = None,
):
    target = canonical_target(url, ids)
    return _rpc(url, "create_agent_runtime_scheduled_execution_profile_v1", (
        task_id, ids["action"], ids["run"],
        Jsonb(snapshot or target.document), toolset_hash or target.toolset_hash,
        canonical_hash_input or target.canonical_hash_input, 0,
    ))
