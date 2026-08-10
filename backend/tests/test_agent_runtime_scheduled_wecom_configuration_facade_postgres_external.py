from __future__ import annotations

import json
from uuid import UUID

import psycopg
import pytest

from tests.test_agent_runtime_ar17_postgres_external import database
from tests.test_agent_runtime_ar173_postgres_external import _apply, _rollback
from tests.test_agent_runtime_ar18_b7_s2_a1_owner_postgres_external import ORG, USER
from tests.test_agent_runtime_scheduled_wecom_claim_postgres_external import (
    _setup as _scheduled_wecom_setup,
)


pytestmark = pytest.mark.external
MIGRATION = "227_50_agent_runtime_scheduled_wecom_configuration_facade.sql"
ROLLBACK = (
    "227_50_agent_runtime_scheduled_wecom_configuration_facade_rollback.sql"
)
CONFIGURATION_BASE = (
    "158_configuration_control_plane_foundation.sql",
    "159_configuration_management_core.sql",
    "160_configuration_resolution_core.sql",
    "160_configuration_resolution_facades.sql",
    "201_wecom_callback_inbox.sql",
)
OTHER_ORG = UUID("99999999-9999-9999-9999-999999999999")
SIGNATURE = "get_wecom_app_bundle()"


def _setup(url: str) -> None:
    _scheduled_wecom_setup(url)
    for migration in CONFIGURATION_BASE:
        _apply(url, migration)
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        connection.execute(
            "INSERT INTO organizations(id,status) VALUES(%s,'active')",
            (OTHER_ORG,),
        )
        for org_id, suffix in ((ORG, "one"), (OTHER_ORG, "two")):
            secret_id = connection.execute(
                """
                INSERT INTO secret_records(
                    scope_kind,org_id,secret_name,payload_ciphertext,
                    wrapped_dek,kek_version,payload_version,created_by,updated_by
                ) VALUES(
                    'organization',%s,'wecom.oauth_agent_secret',%s,%s,
                    'fixture-v1',1,%s,%s
                ) RETURNING id
                """,
                (org_id, f"cipher-{suffix}", f"wrapped-{suffix}", USER, USER),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO configuration_entries(
                    scope_kind,org_id,definition_version,config_key,
                    value_json,updated_by
                ) VALUES
                    ('organization',%s,'v1','wecom.corp_id',%s::jsonb,%s),
                    ('organization',%s,'v1','wecom.oauth_agent_id',%s::jsonb,%s)
                """,
                (
                    org_id, json.dumps(f"corp-{suffix}"), USER,
                    org_id, json.dumps(f"agent-{suffix}"), USER,
                ),
            )
            connection.execute(
                """
                INSERT INTO configuration_entries(
                    scope_kind,org_id,definition_version,config_key,
                    secret_id,updated_by
                ) VALUES(
                    'organization',%s,'v1','wecom.oauth_agent_secret',%s,%s
                )
                """,
                (org_id, secret_id, USER),
            )
        connection.commit()
    _apply(url, MIGRATION)


def _call(
    url: str,
    *,
    role: str = "everydayai_wecom_runtime",
    org_id: UUID | None = ORG,
    actor_id: UUID | None = None,
    access_kind: str = "worker",
) -> dict[str, object]:
    role_url = url.replace("postgres@", f"{role}@")
    with psycopg.connect(role_url) as connection:
        connection.execute(
            "SELECT set_config('app.actor_user_id',%s,false)",
            (str(actor_id) if actor_id else "",),
        )
        connection.execute(
            "SELECT set_config('app.org_id',%s,false)",
            (str(org_id) if org_id else "",),
        )
        connection.execute(
            "SELECT set_config('app.access_kind',%s,false)",
            (access_kind,),
        )
        return connection.execute(
            "SELECT get_wecom_app_bundle()",
        ).fetchone()[0]


def _owner(url: str, sql: str, params: tuple[object, ...] = ()) -> object:
    with psycopg.connect(url) as connection:
        connection.execute("SET ROLE everydayai_owner")
        row = connection.execute(sql, params).fetchone()
        connection.commit()
        return row[0] if row else None


def test_apply_readback_isolation_acl_rollback_and_reapply(database: str) -> None:
    _setup(database)

    first = _call(database, org_id=ORG)
    second = _call(database, org_id=OTHER_ORG)
    assert first["bundle"] == second["bundle"] == "wecom.app"
    assert [item["key"] for item in first["items"]] == [
        "wecom.corp_id",
        "wecom.oauth_agent_id",
        "wecom.oauth_agent_secret",
    ]
    assert [item.get("value_json") for item in first["items"][:2]] == [
        "corp-one", "agent-one",
    ]
    assert first["items"][2]["secret_ref"]["payload_ciphertext"] == "cipher-one"
    assert all(item["scope_id"] == str(ORG) for item in first["items"])
    assert "corp-two" not in json.dumps(first)
    assert [item.get("value_json") for item in second["items"][:2]] == [
        "corp-two", "agent-two",
    ]

    for kwargs, error in (
        ({"actor_id": USER}, "CONFIG_BUNDLE_AUTHORITY_DENIED"),
        ({"org_id": None}, "CONFIG_BUNDLE_AUTHORITY_DENIED"),
        ({"access_kind": "runtime"}, "AGENT_RUNTIME_SCHEDULED_WECOM_SCOPE_REQUIRED"),
    ):
        with pytest.raises(Exception, match=error):
            _call(database, **kwargs)
    _owner(
        database,
        "UPDATE organizations SET status='inactive' WHERE id=%s RETURNING id",
        (OTHER_ORG,),
    )
    with pytest.raises(Exception, match="CONFIG_BUNDLE_AUTHORITY_DENIED"):
        _call(database, org_id=OTHER_ORG)
    _owner(
        database,
        "UPDATE organizations SET status='active' WHERE id=%s RETURNING id",
        (OTHER_ORG,),
    )

    assert _owner(
        database,
        "SELECT prosecdef AND proconfig=ARRAY['search_path=pg_catalog, public'] "
        "FROM pg_proc WHERE oid=%s::regprocedure",
        (SIGNATURE,),
    ) is True
    assert _owner(
        database,
        "SELECT has_function_privilege('everydayai_wecom_runtime',%s,'EXECUTE')",
        (SIGNATURE,),
    ) is True
    for role in ("everydayai_runtime", "everydayai_worker", "everydayai"):
        assert _owner(
            database,
            "SELECT NOT has_function_privilege(%s,%s,'EXECUTE')",
            (role, SIGNATURE),
        ) is True
        with pytest.raises((
            psycopg.errors.InsufficientPrivilege,
            psycopg.errors.UndefinedFunction,
        )):
            _call(database, role=role)
    assert _owner(
        database,
        "SELECT NOT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL "
        "aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) acl "
        "WHERE p.oid=%s::regprocedure AND acl.grantee=0 "
        "AND acl.privilege_type='EXECUTE')",
        (SIGNATURE,),
    ) is True
    for table in (
        "configuration_definitions",
        "configuration_bundle_definitions",
        "configuration_entries",
        "secret_records",
    ):
        assert _owner(
            database,
            "SELECT NOT has_table_privilege('everydayai_wecom_runtime',%s,"
            "'SELECT,INSERT,UPDATE,DELETE')",
            (table,),
        ) is True

    entry_count = _owner(database, "SELECT count(*) FROM configuration_entries")
    _rollback(database, ROLLBACK)
    assert _owner(database, "SELECT to_regprocedure(%s)", (SIGNATURE,)) is None
    assert _owner(
        database,
        "SELECT NOT active FROM configuration_bundle_definitions "
        "WHERE definition_version='v1' AND bundle_name='wecom.app'",
    ) is True
    assert _owner(database, "SELECT count(*) FROM configuration_entries") == entry_count
    assert _owner(
        database,
        "SELECT array_agg(contract_hash ORDER BY config_key) "
        "FROM configuration_definitions WHERE config_key=ANY(%s)",
        (["wecom.corp_id", "wecom.oauth_agent_id", "wecom.oauth_agent_secret"],),
    ) == [
        "3ab214a20f2b8e096b2b19bed390b37f050b517fd63b37817e0c8760a66b351a",
        "29c6e8bec9211b29aa69b94cafabac2a0f95fd1f921eee12b8ab343cdb5f2476",
        "0bcf0c906451d7f85ae319c165ab543ab0e6132e20f7b3fece2c9263ab7bf1bd",
    ]

    _apply(database, MIGRATION)
    assert _call(database)["bundle"] == "wecom.app"
