from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT.joinpath(
    "migrations/227_50_agent_runtime_scheduled_wecom_configuration_facade.sql",
).read_text()
ROLLBACK = ROOT.joinpath(
    "migrations/rollback/227_50_agent_runtime_scheduled_wecom_configuration_facade_rollback.sql",
).read_text()
FUNCTION = "get_wecom_app_bundle"


def _function_body() -> str:
    start = MIGRATION.index(f"CREATE OR REPLACE FUNCTION {FUNCTION}()")
    end = MIGRATION.index("\nEND;\n$$;", start)
    return MIGRATION[start:end + len("\nEND;\n$$;")]


def test_facade_is_parameterless_and_uses_scheduled_wecom_worker_authority() -> None:
    body = _function_body()

    assert "SECURITY DEFINER" in body
    assert "SET search_path = pg_catalog, public" in body
    assert "_assert_agent_runtime_scheduled_wecom_actor()" in body
    assert "_assert_configuration_wecom_actor" not in body
    assert "tenant_actor_user_id() IS NOT NULL" in body
    assert "v_org IS NULL" in body
    assert "organization.id = v_org" in body
    assert "organization.status = 'active'" in body
    assert "'v1', 'wecom.app', NULL, v_org" in body


def test_registry_contract_is_exact_existing_key_bundle() -> None:
    assert MIGRATION.count("UPDATE configuration_definitions") == 3
    for key in (
        "wecom.corp_id",
        "wecom.oauth_agent_id",
        "wecom.oauth_agent_secret",
    ):
        assert f"config_key = '{key}'" in MIGRATION
    assert (
        '"required_keys":["wecom.corp_id","wecom.oauth_agent_id",'
        '"wecom.oauth_agent_secret"]'
    ) in MIGRATION
    assert '"optional_keys":[]' in MIGRATION
    assert '"allowed_consumers":["wecom_runtime"]' in MIGRATION
    assert "wecom.callback_credentials" not in MIGRATION
    assert "ON CONFLICT (definition_version, bundle_name) DO UPDATE" in MIGRATION


def test_acl_is_wecom_runtime_only_and_tables_remain_private() -> None:
    grant = MIGRATION[MIGRATION.index(f"REVOKE ALL ON FUNCTION {FUNCTION}()") :]

    assert "FROM PUBLIC, everydayai_runtime, everydayai_worker, everydayai;" in grant
    assert f"GRANT EXECUTE ON FUNCTION {FUNCTION}()\nTO everydayai_wecom_runtime;" in grant
    assert "GRANT SELECT" not in MIGRATION
    assert "GRANT INSERT" not in MIGRATION
    assert "GRANT UPDATE" not in MIGRATION
    assert "GRANT DELETE" not in MIGRATION


def test_rollback_restores_post_201_contract_without_tenant_data_deletion() -> None:
    assert f"DROP FUNCTION {FUNCTION}();" in ROLLBACK
    assert "bundle_name = 'wecom.app'" in ROLLBACK
    assert "SET active = FALSE" in ROLLBACK
    for old_hash in (
        "3ab214a20f2b8e096b2b19bed390b37f050b517fd63b37817e0c8760a66b351a",
        "29c6e8bec9211b29aa69b94cafabac2a0f95fd1f921eee12b8ab343cdb5f2476",
        "0bcf0c906451d7f85ae319c165ab543ab0e6132e20f7b3fece2c9263ab7bf1bd",
    ):
        assert old_hash in ROLLBACK
    assert "DELETE FROM configuration_entries" not in ROLLBACK
    assert "DELETE FROM secret_records" not in ROLLBACK
    assert "DROP TABLE" not in ROLLBACK
