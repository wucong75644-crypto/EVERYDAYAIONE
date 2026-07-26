"""Migration 211 transaction, role, RLS, and rollback contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

import psycopg
import pytest


pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "backend/migrations/211_worker_global_knowledge_seed_capability.sql"
).read_text(encoding="utf-8")
ROLLBACK = (
    ROOT
    / "backend/migrations/rollback/"
    "211_worker_global_knowledge_seed_capability_rollback.sql"
).read_text(encoding="utf-8")

def _database_url() -> str:
    url = os.getenv("KNOWLEDGE_SEED_TEST_DATABASE_URL")
    if not url:
        pytest.skip("KNOWLEDGE_SEED_TEST_DATABASE_URL_REQUIRED")
    return url

def _payload(*, duplicate_edge: bool = False) -> dict:
    edges = [{
        "source_key": "node:0",
        "target_key": "node:1",
        "relation_type": "related_to",
    }]
    if duplicate_edge:
        edges.append(dict(edges[0]))
    return {
        "version": 1,
        "nodes": [
            {
                "seed_key": "node:0",
                "category": "model",
                "subcategory": "chat",
                "node_type": "model",
                "title": "Seed A",
                "content": "Seed content A",
                "metadata": {"model_id": "a"},
                "confidence": 1.0,
                "embedding": [0.25] * 1024,
            },
            {
                "seed_key": "node:1",
                "category": "model",
                "subcategory": "chat",
                "node_type": "model",
                "title": "Seed B",
                "content": "Seed content B",
                "metadata": {"model_id": "b"},
                "confidence": 1.0,
                "embedding": [0.5] * 1024,
            },
        ],
        "edges": edges,
    }

def _payload_with_embedding(value) -> dict:
    payload = _payload()
    payload["nodes"][0]["embedding"] = value
    return payload

@pytest.fixture()
def migrated_database() -> str:
    setup = """
        CREATE EXTENSION IF NOT EXISTS vector;
        DROP FUNCTION IF EXISTS
            worker_replace_global_knowledge_seed(JSONB);
        DROP FUNCTION IF EXISTS
            _validate_global_knowledge_seed_payload(JSONB);
        DROP TABLE IF EXISTS knowledge_metrics, knowledge_edges,
            knowledge_nodes CASCADE;
        DROP FUNCTION IF EXISTS reject_seed_edge();
        DROP FUNCTION IF EXISTS delay_seed_delete();
        DROP FUNCTION IF EXISTS tenant_actor_user_id();
        DROP FUNCTION IF EXISTS tenant_org_id();
        DO $roles$
        BEGIN
            IF to_regrole('everydayai_owner') IS NULL THEN
                CREATE ROLE everydayai_owner NOLOGIN;
            END IF;
            IF to_regrole('everydayai_runtime') IS NULL THEN
                CREATE ROLE everydayai_runtime NOLOGIN;
            END IF;
            IF to_regrole('everydayai_wecom_runtime') IS NULL THEN
                CREATE ROLE everydayai_wecom_runtime NOLOGIN;
            END IF;
            IF to_regrole('everydayai_worker') IS NULL THEN
                CREATE ROLE everydayai_worker NOLOGIN;
            END IF;
            IF to_regrole('everydayai_sync') IS NULL THEN
                CREATE ROLE everydayai_sync NOLOGIN;
            END IF;
            IF to_regrole('everydayai') IS NULL THEN
                CREATE ROLE everydayai NOLOGIN;
            END IF;
        END
        $roles$;
        GRANT everydayai_owner, everydayai_runtime,
            everydayai_wecom_runtime, everydayai_worker,
            everydayai_sync, everydayai TO CURRENT_USER;
        GRANT USAGE, CREATE ON SCHEMA public TO everydayai_owner;
        GRANT USAGE ON SCHEMA public TO everydayai_worker;
        SET ROLE everydayai_owner;
        CREATE TABLE knowledge_nodes(
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            category TEXT NOT NULL,
            subcategory TEXT,
            node_type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata JSONB DEFAULT '{}',
            embedding vector(1024),
            source TEXT NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            scope TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            org_id UUID,
            owner_user_id UUID
        );
        CREATE UNIQUE INDEX uq_knowledge_nodes_owner
        ON knowledge_nodes(
            content_hash,
            COALESCE(org_id, '00000000-0000-0000-0000-000000000000'),
            COALESCE(
                owner_user_id,
                '00000000-0000-0000-0000-000000000000'
            )
        );
        CREATE TABLE knowledge_edges(
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id UUID NOT NULL REFERENCES knowledge_nodes(id)
                ON DELETE CASCADE,
            target_id UUID NOT NULL REFERENCES knowledge_nodes(id)
                ON DELETE CASCADE,
            relation_type TEXT NOT NULL,
            org_id UUID,
            owner_user_id UUID,
            UNIQUE(source_id, target_id, relation_type)
        );
        CREATE TABLE knowledge_metrics(
            id UUID PRIMARY KEY DEFAULT gen_random_uuid()
        );
        CREATE POLICY owner_nodes ON knowledge_nodes
            FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
        CREATE POLICY owner_edges ON knowledge_edges
            FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
        CREATE POLICY owner_metrics ON knowledge_metrics
            FOR ALL TO everydayai_owner USING (TRUE) WITH CHECK (TRUE);
        ALTER TABLE knowledge_nodes ENABLE ROW LEVEL SECURITY;
        ALTER TABLE knowledge_edges ENABLE ROW LEVEL SECURITY;
        ALTER TABLE knowledge_metrics ENABLE ROW LEVEL SECURITY;
        ALTER TABLE knowledge_nodes FORCE ROW LEVEL SECURITY;
        ALTER TABLE knowledge_edges FORCE ROW LEVEL SECURITY;
        ALTER TABLE knowledge_metrics FORCE ROW LEVEL SECURITY;
        CREATE FUNCTION tenant_actor_user_id()
        RETURNS UUID LANGUAGE sql STABLE AS $$
            SELECT NULLIF(
                current_setting('app.actor_user_id', TRUE), ''
            )::UUID
        $$;
        CREATE FUNCTION tenant_org_id()
        RETURNS UUID LANGUAGE sql STABLE AS $$
            SELECT NULLIF(current_setting('app.org_id', TRUE), '')::UUID
        $$;
        RESET ROLE;
        REVOKE ALL ON knowledge_nodes, knowledge_edges, knowledge_metrics
        FROM everydayai_worker;
    """
    url = _database_url()
    with psycopg.connect(url) as connection:
        connection.execute(setup)
        connection.execute(MIGRATION)
        connection.commit()
    return url

def _worker_call(url: str, payload: dict) -> dict:
    with psycopg.connect(url) as connection:
        connection.execute("SET SESSION AUTHORIZATION everydayai_worker")
        connection.execute(
            "SELECT set_config('app.access_kind', 'worker', false), "
            "set_config('app.actor_user_id', '', false), "
            "set_config('app.org_id', '', false)"
        )
        row = connection.execute(
            "SELECT worker_replace_global_knowledge_seed(%s::jsonb)",
            (json.dumps(payload),),
        ).fetchone()
        connection.commit()
        return row[0]

def test_worker_replaces_atomically_and_repeated_import_is_idempotent(
    migrated_database: str,
) -> None:
    first = _worker_call(migrated_database, _payload())
    second = _worker_call(migrated_database, _payload())

    assert first == {
        "outcome": "replaced",
        "edge_count": 1,
        "imported_count": 2,
    }
    assert second == first
    with psycopg.connect(migrated_database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        assert connection.execute(
            "SELECT count(*) FROM knowledge_nodes "
            "WHERE source = 'seed' AND org_id IS NULL "
            "AND owner_user_id IS NULL"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM knowledge_edges"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*), min(vector_dims(embedding)) "
            "FROM knowledge_nodes WHERE embedding IS NOT NULL"
        ).fetchone() == (2, 1024)
        assert connection.execute(
            "SELECT embedding::TEXT FROM knowledge_nodes "
            "WHERE metadata->>'model_id' = 'a'"
        ).fetchone()[0].startswith("[0.25,")

@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "nodes": {}, "edges": []},
        {
            "version": 1,
            "nodes": _payload()["nodes"],
            "edges": [{
                "source_key": "node:0",
                "target_key": "node:missing",
                "relation_type": "related_to",
            }],
        },
        _payload(duplicate_edge=True),
        _payload_with_embedding([0.1] * 1023),
        _payload_with_embedding([0.1] * 1023 + ["invalid"]),
        _payload_with_embedding("not-a-vector"),
    ],
)
def test_invalid_schema_missing_endpoint_and_duplicate_edge_are_rejected(
    migrated_database: str, payload: dict,
) -> None:
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _worker_call(migrated_database, payload)

def test_interrupted_import_rolls_back_and_preserves_non_seed_data(
    migrated_database: str,
) -> None:
    _worker_call(migrated_database, _payload())
    non_seed_id = str(uuid4())
    with psycopg.connect(migrated_database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO knowledge_nodes("
            "id, category, node_type, title, content, source, confidence, "
            "scope, content_hash, org_id, owner_user_id"
            ") VALUES (%s, 'experience', 'pattern', 'Tenant', 'Keep', "
            "'manual', 1, 'global', 'tenant-hash', %s, NULL)",
            (non_seed_id, str(uuid4())),
        )
        connection.execute("""
            CREATE FUNCTION reject_seed_edge()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'INTERRUPTED';
            END
            $$;
            CREATE TRIGGER reject_seed_edge
            BEFORE INSERT ON knowledge_edges
            FOR EACH ROW EXECUTE FUNCTION reject_seed_edge();
        """)
        connection.commit()

    with pytest.raises(psycopg.errors.RaiseException):
        _worker_call(migrated_database, _payload())

    with psycopg.connect(migrated_database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        assert connection.execute(
            "SELECT count(*) FROM knowledge_nodes WHERE id = %s",
            (non_seed_id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM knowledge_nodes WHERE source = 'seed'"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*), min(vector_dims(embedding)) "
            "FROM knowledge_nodes "
            "WHERE source = 'seed' AND embedding IS NOT NULL"
        ).fetchone() == (2, 1024)
        assert connection.execute(
            "SELECT embedding::TEXT FROM knowledge_nodes "
            "WHERE metadata->>'model_id' = 'a'"
        ).fetchone()[0].startswith("[0.25,")


def test_null_embedding_is_imported_as_null(
    migrated_database: str,
) -> None:
    payload = _payload()
    payload["nodes"][0]["embedding"] = None

    assert _worker_call(
        migrated_database, payload,
    )["imported_count"] == 2
    with psycopg.connect(migrated_database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        assert connection.execute(
            "SELECT embedding IS NULL FROM knowledge_nodes "
            "WHERE metadata->>'model_id' = 'a'"
        ).fetchone() == (True,)
        assert connection.execute(
            "SELECT vector_dims(embedding) FROM knowledge_nodes "
            "WHERE metadata->>'model_id' = 'b'"
        ).fetchone() == (1024,)


def test_empty_snapshot_preserves_tenant_facts_and_worker_has_no_table_access(
    migrated_database: str,
) -> None:
    org_node_id, personal_node_id, global_node_id = (
        str(uuid4()) for _ in range(3)
    )
    with psycopg.connect(migrated_database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO knowledge_nodes("
            "id, category, node_type, title, content, source, confidence, "
            "scope, content_hash, org_id, owner_user_id"
            ") VALUES "
            "(%s, 'experience', 'pattern', 'Org', 'Keep org', "
            "'manual', 1, 'global', 'org-hash', %s, NULL), "
            "(%s, 'experience', 'pattern', 'Personal', 'Keep personal', "
            "'manual', 1, 'user', 'personal-hash', NULL, %s), "
            "(%s, 'experience', 'pattern', 'Global', 'Keep global', "
            "'manual', 1, 'global', 'global-hash', NULL, NULL)",
            (
                org_node_id, str(uuid4()),
                personal_node_id, str(uuid4()),
                global_node_id,
            ),
        )
        connection.commit()

    assert _worker_call(migrated_database, {
        "version": 1, "nodes": [], "edges": [],
    })["imported_count"] == 0
    with psycopg.connect(migrated_database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        assert connection.execute(
            "SELECT count(*) FROM knowledge_nodes "
            "WHERE id = ANY(%s::UUID[])",
            ([org_node_id, personal_node_id, global_node_id],),
        ).fetchone() == (3,)
    for table in (
        "knowledge_nodes", "knowledge_edges", "knowledge_metrics",
    ):
        with psycopg.connect(migrated_database) as connection:
            connection.execute("SET SESSION AUTHORIZATION everydayai_worker")
            assert connection.execute(
                "SELECT session_user, current_user"
            ).fetchone() == ("everydayai_worker", "everydayai_worker")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(f"SELECT * FROM {table}")


def test_cross_scope_reference_fails_closed_and_rollback_keeps_data(
    migrated_database: str,
) -> None:
    _worker_call(migrated_database, _payload())
    with psycopg.connect(migrated_database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        seed_id = connection.execute(
            "SELECT id FROM knowledge_nodes WHERE source = 'seed' LIMIT 1"
        ).fetchone()[0]
        tenant_id = uuid4()
        org_id = uuid4()
        connection.execute(
            "INSERT INTO knowledge_nodes("
            "id, category, node_type, title, content, source, confidence, "
            "scope, content_hash, org_id, owner_user_id"
            ") VALUES (%s, 'experience', 'pattern', 'Tenant', 'Keep', "
            "'manual', 1, 'global', 'tenant-hash', %s, NULL)",
            (tenant_id, org_id),
        )
        connection.execute(
            "INSERT INTO knowledge_edges("
            "source_id, target_id, relation_type, org_id, owner_user_id"
            ") VALUES (%s, %s, 'related_to', %s, NULL)",
            (tenant_id, seed_id, org_id),
        )
        connection.commit()

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _worker_call(migrated_database, {
            "version": 1, "nodes": [], "edges": [],
        })
    with psycopg.connect(migrated_database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        assert connection.execute(
            "SELECT count(*) FROM knowledge_nodes"
        ).fetchone() == (3,)
        assert connection.execute(
            "SELECT count(*) FROM knowledge_edges"
        ).fetchone() == (2,)
        connection.execute(ROLLBACK)
        connection.commit()
        assert connection.execute(
            "SELECT to_regprocedure("
            "'worker_replace_global_knowledge_seed(jsonb)')"
        ).fetchone() == (None,)
        assert connection.execute(
            "SELECT count(*) FROM knowledge_nodes"
        ).fetchone() == (3,)


def test_seed_row_lock_prevents_concurrent_tenant_edge_from_being_cascaded(
    migrated_database: str,
) -> None:
    _worker_call(migrated_database, _payload())
    tenant_id, org_id = uuid4(), uuid4()
    with psycopg.connect(migrated_database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        seed_id = connection.execute(
            "SELECT id FROM knowledge_nodes WHERE source = 'seed' LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO knowledge_nodes("
            "id, category, node_type, title, content, source, confidence, "
            "scope, content_hash, org_id, owner_user_id"
            ") VALUES (%s, 'experience', 'pattern', 'Tenant', 'Keep', "
            "'manual', 1, 'global', 'concurrent-tenant-hash', %s, NULL)",
            (tenant_id, org_id),
        )
        connection.execute("""
            CREATE FUNCTION delay_seed_delete()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                PERFORM pg_sleep(0.5);
                RETURN OLD;
            END
            $$;
            CREATE TRIGGER delay_seed_delete
            BEFORE DELETE ON knowledge_nodes
            FOR EACH ROW WHEN (OLD.source = 'seed')
            EXECUTE FUNCTION delay_seed_delete();
        """)
        connection.commit()

    rpc_error: list[Exception] = []

    def replace_snapshot() -> None:
        try:
            _worker_call(migrated_database, {
                "version": 1, "nodes": [], "edges": [],
            })
        except Exception as exc:
            rpc_error.append(exc)

    thread = threading.Thread(target=replace_snapshot)
    thread.start()
    time.sleep(0.15)
    with psycopg.connect(migrated_database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                "INSERT INTO knowledge_edges("
                "source_id, target_id, relation_type, org_id, owner_user_id"
                ") VALUES (%s, %s, 'related_to', %s, NULL)",
                (tenant_id, seed_id, org_id),
            )
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert rpc_error == []
    with psycopg.connect(migrated_database) as connection:
        connection.execute("SET ROLE everydayai_owner")
        assert connection.execute(
            "SELECT count(*) FROM knowledge_nodes WHERE id = %s",
            (tenant_id,),
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM knowledge_edges"
        ).fetchone() == (0,)
